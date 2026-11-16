"""Generate product pages and ad scripts, check every claim, and manage draft → approved."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import PROJECT_ROOT, Settings
from app.core.llm_client import LLMClient
from app.core.logger import log_event
from app.core.models import CompetitorReport, ContentItem, ContentStatus, Product, ProductStatus, Supplier
from app.modules.content_generator import claim_checker, prompts
from app.modules.content_generator.claim_checker import Context

REGULATED = {"electrical", "batteries", "toys", "cosmetics", "food_contact"}


class ContentError(RuntimeError):
    pass


def load_brand() -> dict:
    path = PROJECT_ROOT / "brand.yaml"
    return (yaml.safe_load(path.read_text(encoding="utf-8")) or {}) if path.exists() else {}


def build_facts(session: Session, settings: Settings, product: Product, brand: dict | None = None) -> tuple[dict, Context]:
    """Everything the AI is allowed to say, plus the context the claim checker needs."""
    from app.modules.supplier_finder.service import product_sell_price

    brand = brand if brand is not None else load_brand()
    promises = brand.get("promises") or {}
    supplier = session.get(Supplier, product.chosen_supplier_id) if product.chosen_supplier_id else None
    price, _ = product_sell_price(session, product)
    flags = {f["flag"] for f in (product.compliance_flags or [])}

    facts: dict = {
        "product": product.name,
        "category": product.category,
        "search_term": (product.score_details or {}).get("trends_term"),
        "price_gbp_inc_vat": price,
        "returns": f"{promises.get('returns_days', 14)}-day returns",
        "free_delivery": bool(promises.get("free_delivery")),
        "guarantee": promises.get("guarantee") or None,
        "brand": {k: brand.get(k) for k in ("store_name", "tone", "audience", "spelling", "avoid")},
    }
    if supplier is not None:
        if supplier.shipping_days_min is not None or supplier.shipping_days_max is not None:
            lo, hi = supplier.shipping_days_min, supplier.shipping_days_max
            facts["delivery"] = f"{lo}-{hi} working days" if lo is not None and hi is not None else f"up to {hi} days"
        if supplier.warehouse == "UK":
            facts["dispatched_from"] = "UK"
        if supplier.has_ukca_ce == "yes":
            facts["certification"] = "Supplier confirms UKCA/CE marking (keep the documents)."
        # Facts YOU confirmed with the supplier (pasted replies) are useful but still need checking.
        replies = [m.reply_text for m in supplier.messages if m.reply_text]
        if replies:
            facts["supplier_said_in_writing"] = replies[-1][:1500]
    else:
        facts["delivery"] = "[CHECK: delivery time, no supplier chosen yet]"
    if flags:
        facts["compliance_notes"] = [f["message"] for f in product.compliance_flags]
    gaps = [g for r in session.scalars(select(CompetitorReport)) for g in (r.insights or {}).get("gaps", [])]
    if gaps:
        facts["competitor_gaps_to_consider"] = gaps[:5]

    ctx = Context(
        delivery_days_max=supplier.shipping_days_max if supplier else None,
        returns_days=int(promises.get("returns_days", 14)),
        free_delivery=bool(promises.get("free_delivery")),
        guarantee=promises.get("guarantee") or "",
        safety_docs_confirmed=bool(supplier and supplier.has_ukca_ce == "yes" and supplier.has_safety_docs == "yes"),
        regulated=bool(flags & REGULATED),
    )
    return facts, ctx


def check(item: ContentItem, ctx: Context) -> list[dict]:
    findings = claim_checker.check_content(item.data, ctx)
    if item.kind == "product_page":
        for field_name, limit in prompts.LIMITS.items():
            value = item.data.get(field_name) or ""
            if len(value) > limit:
                findings.append({"rule": "length", "severity": "low", "field": field_name, "match": f"{len(value)} chars",
                                 "message": f"Longer than {limit} characters; it may be cut off in search results or on Shopify.",
                                 "fix": "Shorten it."})
    item.findings = findings
    return findings


def _new_version(session: Session, product: Product, kind: str, angle: str | None) -> int:
    """Older drafts of the same thing are marked superseded; approved ones are kept for history."""
    old = list(session.scalars(select(ContentItem).where(ContentItem.product_id == product.id,
                                                         ContentItem.kind == kind, ContentItem.angle == angle)))
    for o in old:
        if o.status == ContentStatus.DRAFT:
            o.status = ContentStatus.SUPERSEDED
    return max((o.version for o in old), default=0) + 1


def _require_ok(product: Product) -> None:
    if product.status == ProductStatus.REJECTED:
        raise ContentError(f"'{product.name}' is rejected; un-reject it before writing content.")


def generate_page(session: Session, settings: Settings, product: Product, llm: LLMClient | None = None) -> ContentItem:
    _require_ok(product)
    llm = llm or LLMClient(settings, session)
    facts, ctx = build_facts(session, settings, product)
    data, result = llm.complete_json(task="content_product_page", system=prompts.SYSTEM,
                                     prompt=prompts.PAGE_TASK.format(facts=json.dumps(facts, indent=1, default=str)),
                                     schema=prompts.PRODUCT_PAGE_SCHEMA, max_tokens=3000)
    item = ContentItem(product_id=product.id, kind="product_page", angle=None, data=data,
                       version=_new_version(session, product, "product_page", None),
                       model=result.model, cost_gbp=result.cost_gbp, facts_used=facts)
    check(item, ctx)
    session.add(item)
    session.commit()
    _log(session, settings, item, "generated")
    return item


def generate_ads(session: Session, settings: Settings, product: Product, llm: LLMClient | None = None) -> list[ContentItem]:
    _require_ok(product)
    llm = llm or LLMClient(settings, session)
    facts, ctx = build_facts(session, settings, product)
    angles = "\n".join(f"- {k}: {v}" for k, v in prompts.ANGLES.items())
    data, result = llm.complete_json(task="content_ad_scripts", system=prompts.SYSTEM,
                                     prompt=prompts.ADS_TASK.format(angles=angles, facts=json.dumps(facts, indent=1, default=str)),
                                     schema=prompts.AD_SCRIPTS_SCHEMA, max_tokens=4000)
    items = []
    by_angle = {s["angle"]: s for s in data["scripts"]}
    missing = [a for a in prompts.ANGLES if a not in by_angle]
    for angle, script in by_angle.items():
        item = ContentItem(product_id=product.id, kind="ad_script", angle=angle, data=script,
                           version=_new_version(session, product, "ad_script", angle), model=result.model,
                           cost_gbp=round(result.cost_gbp / max(len(by_angle), 1), 4), facts_used=facts)
        check(item, ctx)
        session.add(item)
        items.append(item)
    session.commit()
    for item in items:
        _log(session, settings, item, "generated")
    if missing:
        log_event(session, module="content_generator", action="incomplete", level="WARNING",
                  message=f"{product.name}: no script returned for {', '.join(missing)}. Generate again.",
                  dry_run=settings.dry_run)
    return items


def update(session: Session, settings: Settings, item: ContentItem, data: dict) -> list[dict]:
    """Save your edits. Re-checks claims and puts the item back to draft."""
    if item.status == ContentStatus.SUPERSEDED:
        raise ContentError("This is an old version; edit the latest one.")
    item.data = data
    item.edited = True
    item.status = ContentStatus.DRAFT
    item.decided_at = None
    _, ctx = build_facts(session, settings, item.product)
    findings = check(item, ctx)
    session.commit()
    _log(session, settings, item, "edited")
    return findings


def recheck(session: Session, settings: Settings, item: ContentItem) -> list[dict]:
    _, ctx = build_facts(session, settings, item.product)
    findings = check(item, ctx)
    session.commit()
    return findings


def decide(session: Session, settings: Settings, item: ContentItem, approve: bool) -> None:
    if item.status not in (ContentStatus.DRAFT, ContentStatus.APPROVED, ContentStatus.REJECTED):
        raise ContentError("This is an old version.")
    if approve:
        findings = recheck(session, settings, item)  # always check against the latest facts
        blockers = claim_checker.blocking(findings)
        if blockers:
            raise ContentError(f"Fix {len(blockers)} high-risk issue(s) first, e.g. \"{blockers[0]['match']}\": "
                               f"{blockers[0]['message']}")
    item.status = ContentStatus.APPROVED if approve else ContentStatus.REJECTED
    item.decided_at = datetime.now(UTC)
    session.commit()
    _log(session, settings, item, item.status.value)
    _maybe_content_ready(session, settings, item.product)


def _maybe_content_ready(session: Session, settings: Settings, product: Product) -> None:
    """Product becomes 'content ready' once its page and at least one ad script are approved."""
    approved = list(session.scalars(select(ContentItem).where(ContentItem.product_id == product.id,
                                                              ContentItem.status == ContentStatus.APPROVED)))
    ready = any(i.kind == "product_page" for i in approved) and any(i.kind == "ad_script" for i in approved)
    if ready and product.status in (ProductStatus.SUPPLIER_CHOSEN, ProductStatus.SAMPLE_ORDERED):
        product.status = ProductStatus.CONTENT_READY
        session.commit()
        log_event(session, module="content_generator", action="content_ready",
                  message=f"{product.name}: page and ad script approved, content ready.", dry_run=settings.dry_run)


def current(session: Session, product: Product) -> dict:
    """Latest non-superseded items for a product."""
    items = list(session.scalars(select(ContentItem).where(ContentItem.product_id == product.id,
                                                           ContentItem.status != ContentStatus.SUPERSEDED)
                                 .order_by(ContentItem.version.desc())))
    page = next((i for i in items if i.kind == "product_page"), None)
    ads = {}
    for i in items:
        if i.kind == "ad_script" and i.angle not in ads:
            ads[i.angle] = i
    return {"page": page, "ads": [ads[a] for a in prompts.ANGLES if a in ads]}


def _log(session: Session, settings: Settings, item: ContentItem, action: str) -> None:
    what = "product page" if item.kind == "product_page" else f"{item.angle.replace('_', ' ')} ad script"
    high = len(claim_checker.blocking(item.findings or []))
    log_event(session, module="content_generator", action=action,
              message=f"{item.product.name}: {what} v{item.version} {action}"
                      + (f" ({high} high-risk claim(s) to fix)" if high and action != "approved" else ""),
              cost_gbp=item.cost_gbp if action == "generated" else None, dry_run=settings.dry_run)
