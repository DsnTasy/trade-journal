"""Competitor analysis, research only.

Order of checks (we stop at the first "no"):
  1. robots.txt: does it let our tool visit the store at all?
  2. Terms of service: do they forbid automated collection (scraping/crawling)?
     Not found → we ask you to read them yourself before going further.
  3. Only then: product feed, best-seller order and policy pages, politely.
If automatic collection isn't allowed, you can enter what you see on their site by hand.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.http_fetch import DisallowedByRobots, FetchError, PoliteFetcher
from app.core.llm_client import LLMClient, LLMError
from app.core.logger import log_event
from app.core.models import CompetitorReport, Product
from app.modules.competitor_analyzer import extract, insights, reviews

TERMS_PATHS = ["/policies/terms-of-service", "/pages/terms-of-service", "/pages/terms-and-conditions",
               "/pages/terms", "/terms-of-service", "/terms-and-conditions", "/terms"]
SHIPPING_PATHS = ["/policies/shipping-policy", "/pages/shipping", "/pages/delivery", "/pages/shipping-policy"]
RETURNS_PATHS = ["/policies/refund-policy", "/pages/returns", "/pages/refund-policy", "/pages/returns-policy"]

# Fields you can enter by hand (label, type)
MANUAL_FIELDS = {
    "product_count": ("Number of products", int),
    "price_min": ("Cheapest price £", float),
    "price_median": ("Typical price £", float),
    "price_max": ("Most expensive £", float),
    "discounted_share": ("Share of products 'on sale' (0-1)", float),
    "free_shipping_threshold_gbp": ("Free delivery over £ (0 = always free)", float),
    "delivery_days_min": ("Delivery days (fastest)", int),
    "delivery_days_max": ("Delivery days (slowest)", int),
    "return_window_days": ("Return window (days)", int),
    "ships_from_uk": ("Ships from the UK? (yes/no)", bool),
}

AI_SYSTEM = (
    "You write short competitor-research notes for a beginner UK dropshipper. Use ONLY the facts given. "
    "Never invent numbers. Write in your own words, British English, 3-4 sentences: their positioning, "
    "what they do well, and the clearest gap a new store could fill honestly."
)


class CompetitorError(RuntimeError):
    pass


def normalise_url(url: str) -> tuple[str, str]:
    url = url.strip()
    if not url:
        raise CompetitorError("Enter a store address, e.g. https://example-store.co.uk")
    if "://" not in url:
        url = "https://" + url
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc or "." not in parts.netloc:
        raise CompetitorError(f"'{url}' doesn't look like a website address.")
    return f"{parts.scheme}://{parts.netloc.lower()}", parts.netloc.lower().removeprefix("www.")


def make_fetcher(settings: Settings) -> PoliteFetcher:
    c = settings.competitors
    return PoliteFetcher(c.user_agent, settings.path(settings.http_cache_dir), c.request_delay_seconds,
                         c.cache_hours, c.timeout_seconds)


def _first_ok(fetcher: PoliteFetcher, origin: str, paths: list[str]):
    for path in paths:
        try:
            r = fetcher.fetch(origin + path)
        except DisallowedByRobots:
            continue
        if r.ok and "html" in r.content_type.lower() and len(extract.strip_html(r.text)) > 200:
            return r
    return None


def _our_products(session: Session) -> tuple[dict[str, list[str]], dict[str, float | None]]:
    from app.modules.supplier_finder.service import product_sell_price

    keywords, prices = {}, {}
    for p in session.scalars(select(Product)):
        term = (p.score_details or {}).get("trends_term") or p.name
        keywords[p.name] = [term]
        prices[p.name], _ = product_sell_price(session, p)
    return keywords, prices


def _report_for(session: Session, origin: str, domain: str) -> CompetitorReport:
    existing = session.scalars(select(CompetitorReport).where(CompetitorReport.domain == domain)).first()
    if existing:
        return existing
    r = CompetitorReport(url=origin, domain=domain, status="new")
    session.add(r)
    return r


def refresh_insights(session: Session, report: CompetitorReport) -> None:
    _, our_prices = _our_products(session)
    report.insights = insights.build(report.facts, report.review_themes, our_prices)
    session.commit()


def analyse(session: Session, settings: Settings, url: str, *, terms_checked_by_you: bool = False,
            fetcher: PoliteFetcher | None = None) -> CompetitorReport:
    origin, domain = normalise_url(url)
    fetcher = fetcher or make_fetcher(settings)
    report = _report_for(session, origin, domain)
    report.url = origin

    def finish(status: str, reason: str | None) -> CompetitorReport:
        report.status, report.status_reason = status, reason
        report.sources = fetcher.log
        refresh_insights(session, report)
        log_event(session, module="competitor_analyzer", action=status,
                  message=f"{domain}: {reason or 'collected public facts'}", source=origin, dry_run=settings.dry_run)
        return report

    try:
        # 1. robots.txt
        if not fetcher.allowed(origin + "/"):
            return finish("forbidden", "Their robots.txt asks automated tools not to visit this store. "
                                       "Enter what you see by hand below, or use a paid store-intelligence tool "
                                       "that licenses this data.")
        # 2. terms of service
        terms = _first_ok(fetcher, origin, TERMS_PATHS)
        if terms is not None:
            phrases = extract.terms_forbid_automation(terms.text)
            if phrases:
                return finish("forbidden", f"Their terms of service forbid automated collection (they mention: "
                                           f"{', '.join(phrases)}). Nothing else was downloaded. Enter what you see "
                                           f"by hand below, or use a paid tool that licenses this data.")
        elif not terms_checked_by_you:
            return finish("needs_terms_check", "Couldn't find their terms of service automatically. Read them "
                                               "yourself; if they don't forbid automated access, run again with "
                                               "\"I've checked their terms\".")

        # 3. collect facts (allowed)
        facts: dict = {}
        products: list[dict] = []
        for page in range(1, settings.competitors.max_product_pages + 1):
            try:
                r = fetcher.fetch(f"{origin}/products.json?limit=250&page={page}")
            except DisallowedByRobots:
                break
            if not r.ok:
                break
            try:
                batch = r.json().get("products", [])
            except (json.JSONDecodeError, AttributeError):
                break
            products += batch
            if len(batch) < 250:
                break
        if products:
            report.platform = "Shopify"
            facts.update(extract.catalogue_facts(products))
            keywords, _ = _our_products(session)
            facts["overlap"] = extract.keyword_overlap(products, keywords)
            try:
                coll = fetcher.fetch(f"{origin}/collections/all?sort_by=best-selling")
                if coll.ok:
                    price_by_handle = {p.get("handle"): min((float(v["price"]) for v in p.get("variants", [])
                                                             if v.get("price")), default=None) for p in products}
                    facts["bestsellers"] = [{"handle": h, "price": price_by_handle.get(h)}
                                            for h in extract.bestseller_handles(coll.text)]
            except DisallowedByRobots:
                pass
        else:
            report.platform = "unknown (no public product feed)"

        ship = _first_ok(fetcher, origin, SHIPPING_PATHS)
        rets = _first_ok(fetcher, origin, RETURNS_PATHS)
        facts.update(extract.policy_facts(ship.text if ship else "", rets.text if rets else ""))
        try:
            home = fetcher.fetch(origin + "/")
            if home.ok:
                cur = extract.homepage_currency(home.text)
                if cur:
                    facts["currency"] = cur
        except DisallowedByRobots:
            pass

        facts["collected_at"] = datetime.now(UTC).isoformat()
        report.auto_facts = facts
        missing = [] if products else ["product range and prices (no public product feed)"]
        if not ship:
            missing.append("delivery policy")
        if not rets:
            missing.append("returns policy")
        reason = ("Collected public facts." + (f" Not found: {'; '.join(missing)}. You can add these by hand."
                                                if missing else ""))
        if facts.get("currency") and facts["currency"] != "GBP":
            reason += f" Note: prices are in {facts['currency']}, not GBP."
        return finish("allowed", reason)
    except FetchError as exc:
        return finish("error", f"Couldn't reach the store: {exc}")


def save_manual(session: Session, settings: Settings, report: CompetitorReport, form: dict[str, str],
                notes: str | None = None) -> list[str]:
    """Save facts you looked up yourself. Returns problems (bad values are skipped, not guessed)."""
    problems: list[str] = []
    manual = dict(report.manual_facts or {})
    for key, (label, kind) in MANUAL_FIELDS.items():
        raw = (form.get(key) or "").strip().replace("£", "")
        if raw == "":
            manual.pop(key, None)
            continue
        try:
            if kind is bool:
                if raw.lower() not in ("yes", "y", "no", "n", "true", "false"):
                    raise ValueError
                manual[key] = raw.lower() in ("yes", "y", "true")
            else:
                value = kind(float(raw))
                if value < 0:
                    raise ValueError
                if key == "discounted_share" and value > 1:
                    value = value / 100  # someone typed 60 meaning 60%
                manual[key] = value
        except ValueError:
            problems.append(f"{label}: '{raw}' isn't valid; skipped.")
    report.manual_facts = manual
    if notes is not None:
        report.notes = notes.strip() or None
    # A "forbidden" store stays marked forbidden (so you remember why), but its facts now show as yours.
    if report.status in ("new", "needs_terms_check", "error") and manual:
        report.status = "manual"
    refresh_insights(session, report)
    log_event(session, module="competitor_analyzer", action="manual_facts",
              message=f"{report.domain}: saved {len(manual)} facts entered by you.", dry_run=settings.dry_run)
    return problems


def add_reviews(session: Session, settings: Settings, report: CompetitorReport, text: str) -> dict:
    """Count review themes. The pasted text itself is NOT saved."""
    result = reviews.themes(text)
    if result["reviews_analysed"] == 0:
        raise CompetitorError("Paste at least one review (one per line or separated by blank lines).")
    report.review_themes = result
    refresh_insights(session, report)
    log_event(session, module="competitor_analyzer", action="review_themes",
              message=f"{report.domain}: analysed {result['reviews_analysed']} pasted reviews (text not stored).",
              dry_run=settings.dry_run)
    return result


def ai_summary(session: Session, settings: Settings, report: CompetitorReport, llm: LLMClient | None = None) -> str:
    llm = llm or LLMClient(settings, session)
    facts = {k: v for k, v in report.facts.items() if k not in ("collected_at",)}
    payload = {"store": report.domain, "facts": facts, "review_themes": report.review_themes,
               "insights": report.insights}
    try:
        result = llm.complete(task="competitor_summary", system=AI_SYSTEM,
                              prompt=json.dumps(payload, sort_keys=True, default=str), max_tokens=600)
    except LLMError as exc:
        raise CompetitorError(str(exc)) from exc
    report.summary = result.text.strip()
    session.commit()
    return report.summary
