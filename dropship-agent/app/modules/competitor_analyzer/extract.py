"""Turn public pages into FACTS (numbers, yes/no). We never store their wording, images or code.

Everything here is pure functions on text, so it's easy to test.
"""

from __future__ import annotations

import html
import re
import statistics
from collections import Counter
from datetime import UTC, datetime, timedelta

# Phrases in terms of service that forbid automated collection.
FORBIDS_AUTOMATION = re.compile(
    r"\b(scrap(e|es|ed|ing)|crawl(s|ed|ing)?|spider(s|ed|ing)?|data[\s-]?mining|harvest(ing)?\s+(data|information)"
    r"|automated\s+(means|systems?|tools?|software|access|scripts?|queries))\b", re.I)


def strip_html(page: str) -> str:
    page = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", page)
    text = re.sub(r"(?s)<[^>]+>", " ", page)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def terms_forbid_automation(terms_html: str) -> list[str]:
    """Return the matching phrases (lowercased, deduplicated). Empty = nothing found."""
    found = {m.group(0).lower() for m in FORBIDS_AUTOMATION.finditer(strip_html(terms_html))}
    return sorted(found)


def _money(s: str) -> float | None:
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


def policy_facts(shipping_text: str = "", returns_text: str = "") -> dict:
    """Pull delivery/returns facts out of policy pages. Missing facts are left out."""
    facts: dict = {}
    ship = strip_html(shipping_text)
    ret = strip_html(returns_text)

    m = re.search(r"free\s+(?:uk\s+)?(?:delivery|shipping)[^.£]{0,40}?(?:over|above|on orders? (?:over|above|of))\s*£\s?(\d+(?:\.\d{1,2})?)",
                  ship, re.I)
    if m:
        facts["free_shipping_threshold_gbp"] = _money(m.group(1))
    elif re.search(r"free\s+(?:uk\s+)?(?:delivery|shipping)\s+on\s+(?:all|every)", ship, re.I):
        facts["free_shipping_threshold_gbp"] = 0.0

    days = [(int(a), int(b)) for a, b in re.findall(r"(\d{1,2})\s*(?:-|–|to)\s*(\d{1,2})\s*(?:working\s+|business\s+)?days", ship, re.I)]
    if days:
        facts["delivery_days_min"] = min(a for a, _ in days)
        facts["delivery_days_max"] = max(b for _, b in days)

    windows = [int(d) for d in re.findall(r"(\d{1,3})[\s-]*days?", ret, re.I)
               if 7 <= int(d) <= 365]
    if windows:
        facts["return_window_days"] = max(windows)

    uk_markers = re.findall(r"\b(royal mail|evri|dpd uk|parcelforce|uk warehouse|dispatched from the uk|ships from the uk)\b",
                            ship, re.I)
    abroad = re.search(r"\b(ships? from (china|overseas|our international)|international warehouse|customs)\b", ship, re.I)
    if uk_markers and not abroad:
        facts["ships_from_uk"] = True
    elif abroad:
        facts["ships_from_uk"] = False
    return facts


def catalogue_facts(products: list[dict], now: datetime | None = None) -> dict:
    """Facts from a Shopify products.json feed. Titles are NOT stored, only counts and prices."""
    now = now or datetime.now(UTC)
    prices, discounts, types, new = [], [], Counter(), 0
    for p in products:
        types[(p.get("product_type") or "uncategorised").strip().lower() or "uncategorised"] += 1
        variant_prices = [float(v["price"]) for v in p.get("variants", []) if v.get("price") not in (None, "")]
        if not variant_prices:
            continue
        price = min(variant_prices)
        prices.append(price)
        compare = [float(v["compare_at_price"]) for v in p.get("variants", [])
                   if v.get("compare_at_price") not in (None, "", "0.00")]
        if compare and max(compare) > price:
            discounts.append(1 - price / max(compare))
        created = p.get("created_at")
        if created:
            try:
                if now - datetime.fromisoformat(created.replace("Z", "+00:00")) <= timedelta(days=90):
                    new += 1
            except ValueError:
                pass
    facts: dict = {"product_count": len(products), "product_types": dict(types.most_common(8))}
    if prices:
        facts.update(price_min=round(min(prices), 2), price_median=round(statistics.median(prices), 2),
                     price_max=round(max(prices), 2),
                     discounted_share=round(len(discounts) / len(prices), 3),
                     new_last_90_days=new)
        if discounts:
            facts["avg_discount_pct"] = round(100 * statistics.fmean(discounts), 1)
    return facts


def bestseller_handles(collection_html: str, limit: int = 10) -> list[str]:
    """Product handles (URL slugs) in the order Shopify's 'best selling' sort shows them."""
    seen: list[str] = []
    for handle in re.findall(r'href="[^"]*/products/([a-z0-9][a-z0-9\-]*)', collection_html, re.I):
        handle = handle.lower()
        if handle not in seen:
            seen.append(handle)
        if len(seen) >= limit:
            break
    return seen


def keyword_overlap(products: list[dict], keywords_by_product: dict[str, list[str]]) -> dict:
    """How many of their products match each of OUR product's keywords, and at what prices.
    Computed on the fly from titles, which are then discarded."""
    out: dict = {}
    for ours, keywords in keywords_by_product.items():
        pats = [re.compile(rf"\b{re.escape(k)}\b", re.I) for k in keywords if k]
        prices = []
        for p in products:
            if any(pat.search(p.get("title", "")) for pat in pats):
                vp = [float(v["price"]) for v in p.get("variants", []) if v.get("price")]
                if vp:
                    prices.append(min(vp))
        if prices:
            out[ours] = {"matches": len(prices), "price_median": round(statistics.median(prices), 2),
                         "price_min": round(min(prices), 2), "price_max": round(max(prices), 2)}
    return out


def homepage_currency(homepage_html: str) -> str | None:
    m = re.search(r'Shopify\.currency\s*=\s*\{"active":"([A-Z]{3})"', homepage_html)
    if m:
        return m.group(1)
    m = re.search(r'"priceCurrency"\s*:\s*"([A-Z]{3})"', homepage_html)
    return m.group(1) if m else None
