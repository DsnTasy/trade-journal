"""Import suppliers → score them → work out true profit with each → draft emails → track replies."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import PROJECT_ROOT, Settings
from app.core.logger import log_event
from app.core.models import (
    MessageStatus,
    Metric,
    Product,
    ProductStatus,
    Supplier,
    SupplierMessage,
)
from app.modules.product_research.sources import norm
from app.modules.profit_calculator import ProfitInputs, ProfitResult, calculate
from app.modules.supplier_finder.outreach import draft_enquiry
from app.modules.supplier_finder.scoring import landed_cost, score_supplier
from app.modules.supplier_finder.sources import load_suppliers

OVERRIDABLE = set(ProfitInputs.__dataclass_fields__) - {"unit_cost_gbp", "shipping_gbp", "vat_registered", "vat_rate"}
REMINDER = "Scores are a filter, not a verdict: ratings and order counts can be faked. Order a sample before listing."


@dataclass
class SupplierReport:
    by_product: dict[int, list[Supplier]] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)
    imported: int = 0


def product_sell_price(session: Session, product: Product) -> tuple[float | None, str]:
    """Your edited price wins; otherwise the latest price from research."""
    override = (product.profit_overrides or {}).get("sell_price_gbp")
    if override is not None:
        return float(override), "your saved assumption"
    m = session.scalars(select(Metric).where(Metric.product_id == product.id, Metric.name == "sell_price_gbp")
                        .order_by(Metric.retrieved_at.desc(), Metric.id.desc())).first()
    if m is not None and m.value is not None:
        return m.value, m.source
    return None, "no sell price yet"


def profit_inputs(settings: Settings, product: Product, supplier: Supplier, sell_price: float) -> ProfitInputs:
    overrides = {k: v for k, v in (product.profit_overrides or {}).items() if k in OVERRIDABLE}
    overrides.pop("sell_price_gbp", None)
    return ProfitInputs.from_config(settings.profit, settings.business, sell_price_gbp=sell_price,
                                    unit_cost_gbp=supplier.unit_cost_gbp or 0.0,
                                    shipping_gbp=supplier.shipping_cost_gbp or 0.0, **overrides)


def profit_for(session: Session, settings: Settings, product: Product, supplier: Supplier) -> ProfitResult | None:
    price, _ = product_sell_price(session, product)
    if price is None or supplier.unit_cost_gbp is None:
        return None
    return calculate(profit_inputs(settings, product, supplier, price), settings.profit, settings.business)


def rescore_product(session: Session, settings: Settings, product: Product) -> list[Supplier]:
    """Score every supplier for one product and recalculate profit. Returns them best first."""
    suppliers = list(session.scalars(select(Supplier).where(Supplier.product_id == product.id)))
    landed = [c for c in (landed_cost(_fields(s)) for s in suppliers) if c is not None]
    cheapest = min(landed) if landed else None
    flag_ids = [f["flag"] for f in (product.compliance_flags or [])]
    price, price_source = product_sell_price(session, product)
    for s in suppliers:
        result = score_supplier(_fields(s), cheapest, settings.suppliers, flag_ids)
        s.score = result.score
        s.score_details = result.details()
        s.red_flags = result.red_flags
        if price is None:
            s.profit = {"error": "No sell price yet: add est_sell_price_gbp in candidates.csv or set it in the "
                                 "profit calculator."}
        elif s.unit_cost_gbp is None:
            s.profit = {"error": "No unit cost for this supplier."}
        else:
            p = calculate(profit_inputs(settings, product, s, price), settings.profit, settings.business)
            s.profit = {**p.to_dict(), "price_source": price_source}
    session.commit()
    suppliers.sort(key=lambda s: -(s.score or 0))
    return suppliers


def _fields(s: Supplier) -> dict:
    return {c: getattr(s, c) for c in ("unit_cost_gbp", "shipping_cost_gbp", "shipping_days_max", "warehouse",
                                       "rating", "review_count", "orders_sold", "moq", "return_policy_days",
                                       "responds_within_hours", "has_ukca_ce", "has_safety_docs",
                                       "has_branding_info")}


def import_suppliers(session: Session, settings: Settings) -> SupplierReport:
    report = SupplierReport()
    rows, problems = load_suppliers(settings.path(settings.suppliers.suppliers_file), settings.business)
    report.problems += problems
    products = {norm(p.name): p for p in session.scalars(select(Product))}
    touched: dict[int, Product] = {}
    now = datetime.now(UTC)

    for row in rows:
        product = products.get(norm(row.product))
        if product is None:
            report.problems.append(f"Line {row.line}: product '{row.product}' isn't in your research yet. "
                                   f"Add it to candidates.csv and run research first (or check the spelling).")
            continue
        existing = next((s for s in session.scalars(select(Supplier).where(Supplier.product_id == product.id))
                         if norm(s.name) == norm(row.name)), None)
        supplier = existing or Supplier(product_id=product.id, name=row.name, source="")
        # Reset every data field so a value you deleted from the sheet doesn't linger.
        for col in _fields(supplier) | {"platform": 0, "url": 0, "notes": 0, "original_price": 0}:
            setattr(supplier, col, None)
        for k, v in row.fields.items():
            setattr(supplier, k, v)
        supplier.retrieved_at = now
        if existing is None:
            session.add(supplier)
        touched[product.id] = product
        report.imported += 1
    session.flush()

    for product in touched.values():
        report.by_product[product.id] = rescore_product(session, settings, product)

    log_event(session, module="supplier_finder", action="import",
              message=f"Imported {report.imported} suppliers for {len(touched)} products.",
              source="suppliers.csv (manual entry)", dry_run=settings.dry_run, details={"problems": report.problems})
    return report


def choose_supplier(session: Session, settings: Settings, product: Product, supplier: Supplier) -> None:
    if supplier.product_id != product.id:
        raise ValueError("That supplier belongs to a different product.")
    if product.status not in (ProductStatus.RESEARCHED, ProductStatus.SHORTLISTED, ProductStatus.SUPPLIER_CHOSEN):
        raise ValueError(f"'{product.name}' is already {product.status.value}; change the supplier from there.")
    product.chosen_supplier_id = supplier.id
    product.status = ProductStatus.SUPPLIER_CHOSEN
    session.commit()
    log_event(session, module="supplier_finder", action="chosen",
              message=f"{product.name}: chose {supplier.name}. Next: order a sample.", dry_run=settings.dry_run)


def mark_sample_ordered(session: Session, settings: Settings, product: Product) -> None:
    """You order the sample yourself; this just records that you did."""
    if product.status != ProductStatus.SUPPLIER_CHOSEN:
        raise ValueError("Choose a supplier first.")
    product.status = ProductStatus.SAMPLE_ORDERED
    session.commit()
    log_event(session, module="supplier_finder", action="sample_ordered",
              message=f"{product.name}: sample ordered (recorded by you).", dry_run=settings.dry_run)


def store_name() -> str:
    brand = PROJECT_ROOT / "brand.yaml"
    if brand.exists():
        return (yaml.safe_load(brand.read_text(encoding="utf-8")) or {}).get("store_name") or "our store"
    return "our store"


def draft_email(session: Session, settings: Settings, supplier: Supplier) -> SupplierMessage:
    """Create a draft enquiry (or return the existing unsent one). Never sends anything."""
    existing = session.scalars(select(SupplierMessage).where(
        SupplierMessage.supplier_id == supplier.id, SupplierMessage.status == MessageStatus.DRAFT)).first()
    if existing is not None:
        return existing
    subject, body = draft_enquiry(supplier.product, supplier, store_name())
    msg = SupplierMessage(supplier_id=supplier.id, subject=subject, body=body)
    session.add(msg)
    session.commit()
    log_event(session, module="supplier_finder", action="email_drafted",
              message=f"Drafted enquiry to {supplier.name} about {supplier.product.name} (not sent).",
              dry_run=settings.dry_run)
    return msg


def mark_sent(session: Session, settings: Settings, msg: SupplierMessage) -> None:
    """You sent it yourself (copy/paste into your email or the supplier's chat)."""
    if msg.status != MessageStatus.DRAFT:
        raise ValueError("Only a draft can be marked as sent.")
    msg.status = MessageStatus.SENT
    msg.sent_at = datetime.now(UTC)
    session.commit()
    log_event(session, module="supplier_finder", action="email_sent_by_you",
              message=f"Marked as sent: '{msg.subject}' to {msg.supplier.name}.", dry_run=settings.dry_run)


def record_reply(session: Session, settings: Settings, msg: SupplierMessage, text: str,
                 replied_at: datetime | None = None) -> None:
    if msg.status not in (MessageStatus.SENT, MessageStatus.REPLIED):
        raise ValueError("Mark the email as sent before recording a reply.")
    if not text.strip():
        raise ValueError("Paste the supplier's reply (or a summary of it).")
    msg.reply_text = text.strip()
    msg.replied_at = replied_at or datetime.now(UTC)
    msg.status = MessageStatus.REPLIED
    if msg.sent_at:
        sent = msg.sent_at if msg.sent_at.tzinfo else msg.sent_at.replace(tzinfo=UTC)
        got = msg.replied_at if msg.replied_at.tzinfo else msg.replied_at.replace(tzinfo=UTC)
        msg.supplier.responds_within_hours = round(max((got - sent).total_seconds(), 0) / 3600, 1)
    session.commit()
    rescore_product(session, settings, msg.supplier.product)  # reply speed affects the score
    log_event(session, module="supplier_finder", action="reply_recorded",
              message=f"Reply from {msg.supplier.name} recorded.", dry_run=settings.dry_run)
