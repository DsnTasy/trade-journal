"""Supplier scoring and red flags.

IMPORTANT: ratings and "orders sold" can be faked or inflated. The score is a FILTER
to decide who to contact, not a verdict. Always order a sample before listing.

Factors (each 0-1, weights in config.yaml → suppliers):
  price        landed cost (unit + shipping) vs the cheapest option for the same product
  delivery     slowest delivery time to the UK (≤ target days = great, 15+ = poor)
  warehouse    UK > EU > US > CN
  reliability  star rating, trusted more when there are many reviews
  terms        returns policy, minimum order, how fast they reply
Unknown factors count as average (0.5). Each red flag removes points.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from app.core.config import SupplierConfig

WAREHOUSE_SCORE = {"UK": 1.0, "EU": 0.8, "US": 0.4, "CN": 0.3}
UNKNOWN = 0.5


def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


@dataclass
class SupplierScore:
    score: float
    factors: dict[str, float | None]
    red_flags: list[dict] = field(default_factory=list)
    strengths: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

    def details(self) -> dict:
        return {"factors": self.factors, "strengths": self.strengths, "missing": self.missing}


def landed_cost(s: dict) -> float | None:
    if s.get("unit_cost_gbp") is None:
        return None
    return s["unit_cost_gbp"] + (s.get("shipping_cost_gbp") or 0.0)


def delivery_factor(days: int, cfg: SupplierConfig) -> float:
    """1.0 at or under target, falling to 0.1 at the slow-shipping limit."""
    if days <= cfg.target_delivery_days:
        return 1.0
    if days >= cfg.slow_shipping_days:
        return 0.1
    span = cfg.slow_shipping_days - cfg.target_delivery_days
    return 1.0 - 0.9 * (days - cfg.target_delivery_days) / span


def score_supplier(s: dict, cheapest_landed: float | None, cfg: SupplierConfig,
                   compliance_flags: list[str] | None = None) -> SupplierScore:
    """`s` = supplier fields (see Supplier model). `compliance_flags` = product's flag ids."""
    f: dict[str, float | None] = dict.fromkeys(("price", "delivery", "warehouse", "reliability", "terms"))
    flags: list[dict] = []
    good: list[str] = []
    missing: list[str] = []

    def flag(code: str, msg: str):
        flags.append({"flag": code, "message": msg})

    # price
    landed = landed_cost(s)
    if landed is not None and cheapest_landed:
        f["price"] = clamp(cheapest_landed / landed) if landed > 0 else 1.0
        if landed <= cheapest_landed:
            good.append(f"Cheapest landed cost (£{landed:.2f} inc. shipping).")
    else:
        missing.append("price")

    # delivery
    days = s.get("shipping_days_max")
    if days is not None:
        f["delivery"] = delivery_factor(days, cfg)
        if days >= cfg.slow_shipping_days:
            flag("slow_shipping", f"Up to {days} days to deliver: UK customers expect under a week.")
        elif days <= cfg.target_delivery_days:
            good.append(f"Fast UK delivery (up to {days} days).")
    else:
        missing.append("delivery")
        flag("vague_shipping", "No delivery time given: ask before ordering.")

    # warehouse
    wh = s.get("warehouse")
    if wh in WAREHOUSE_SCORE:
        f["warehouse"] = WAREHOUSE_SCORE[wh]
        if wh in ("UK", "EU"):
            good.append(f"{wh} warehouse.")
    else:
        missing.append("warehouse")

    # reliability: rating trusted more with more reviews (100+ reviews = full trust)
    rating, reviews, orders = s.get("rating"), s.get("review_count"), s.get("orders_sold")
    if rating is not None:
        base = clamp((rating - 3.5) / 1.5)  # 3.5★ → 0, 5★ → 1
        trust = clamp(math.log10((reviews or 0) + 1) / 2)  # 0 reviews → 0, 100 → 1
        f["reliability"] = round(UNKNOWN + (base - UNKNOWN) * trust, 3)
        if rating >= 4.8 and (reviews or 0) < 20:
            flag("thin_reviews", f"{rating}★ from only {reviews or 0} reviews: too few to trust, and could be manipulated.")
        elif rating < 4.0:
            flag("low_rating", f"Low rating ({rating}★).")
        elif rating >= 4.5 and (reviews or 0) >= 100:
            good.append(f"{rating}★ from {reviews} reviews.")
    else:
        missing.append("reliability")
    if orders is not None and reviews is not None and reviews > 0 and orders / reviews > 200:
        flag("orders_vs_reviews", f"{orders:,} orders but only {reviews} reviews: the order count may be inflated.")
    elif orders is not None and orders > 1000 and not reviews:
        flag("orders_vs_reviews", f"{orders:,} orders claimed with no reviews: treat as unverified.")

    # terms
    parts: list[float] = []
    ret = s.get("return_policy_days")
    if ret is not None:
        parts.append(1.0 if ret >= 14 else 0.5 if ret > 0 else 0.0)
        if ret == 0:
            flag("no_returns", "No returns accepted: you'd pay for UK customers' 14-day returns yourself.")
    moq = s.get("moq")
    if moq is not None:
        parts.append(1.0 if moq <= 1 else 0.6 if moq <= 10 else 0.2)
        if moq > 1:
            flag("moq", f"Minimum order of {moq} units: not true dropshipping, you'd buy stock up front.")
    resp = s.get("responds_within_hours")
    if resp is not None:
        parts.append(1.0 if resp <= 24 else 0.6 if resp <= 72 else 0.2)
        if resp > 72:
            flag("slow_replies", f"Slow to reply ({resp:.0f}h).")
    if parts:
        f["terms"] = round(sum(parts) / len(parts), 3)
    else:
        missing.append("terms")

    # compliance documents for regulated products
    regulated = {"electrical", "batteries", "toys", "cosmetics", "food_contact"} & set(compliance_flags or [])
    if regulated and s.get("has_ukca_ce") != "yes":
        state = "says no" if s.get("has_ukca_ce") == "no" else "unknown"
        flag("no_ukca_ce", f"Product needs safety marking ({', '.join(sorted(regulated))}) but UKCA/CE is {state}.")
    if regulated and s.get("has_safety_docs") == "no":
        flag("no_safety_docs", "No safety/test documents available.")
    if s.get("has_branding_info") == "no":
        flag("no_branding_info", "No brand/manufacturer details: UK law needs a traceable manufacturer or importer.")

    weights = cfg.weights.model_dump()
    total = sum(weights.values())
    base_score = 100 * sum(weights[k] * (v if v is not None else UNKNOWN) for k, v in f.items()) / total
    score = max(0.0, base_score - cfg.red_flag_penalty * len(flags))
    return SupplierScore(score=round(score, 1), factors={k: (round(v, 3) if v is not None else None) for k, v in f.items()},
                         red_flags=flags, strengths=good, missing=missing)
