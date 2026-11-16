"""Turns sourced metrics into a 0-100 score with plain-English reasons.

How the score works (all settings in config.yaml → research):
  1. Four factors, each 0-1: demand, trend, margin, competition.
  2. Weighted average of the factors x 100. A factor with NO data counts as average
     (0.5) - not good, not bad - so a product can't rank highly just because we know
     little about it. Missing data is listed, never filled in with invented numbers.
  3. Minus penalties: saturation (too many advertisers / copied ads) and seasonality.
  4. "Confidence" = share of the total weight we had data for. Low confidence is flagged.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.config import BusinessConfig, ResearchConfig


def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


@dataclass
class ScoreResult:
    score: float
    confidence: float
    factors: dict[str, float | None]
    penalties: dict[str, float]
    rough_margin: float | None
    reasons_for: list[str] = field(default_factory=list)
    reasons_against: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

    def details(self) -> dict:
        return {"factors": self.factors, "penalties": self.penalties,
                "rough_margin": self.rough_margin, "missing": self.missing}


def rough_margin(m: dict[str, float | None], business: BusinessConfig) -> float | None:
    """Rough gross margin BEFORE payment fees, refunds and ads (phase 3 does the full sum)."""
    price, cost = m.get("sell_price_gbp"), m.get("unit_cost_gbp")
    if price is None or cost is None or price <= 0:
        return None
    net_price = price / (1 + business.vat_rate) if business.vat_registered else price
    shipping = m.get("shipping_gbp") or 0.0
    return (net_price - cost - shipping) / net_price


def score_product(m: dict[str, float | None], cfg: ResearchConfig, business: BusinessConfig) -> ScoreResult:
    """`m` maps metric name -> latest value (None/absent = unknown)."""
    w = cfg.weights.model_dump()
    factors: dict[str, float | None] = {"demand": None, "trend": None, "margin": None, "competition": None}
    pro: list[str] = []
    con: list[str] = []
    missing: list[str] = []

    # --- demand ---
    interest = m.get("search_interest")
    if interest is not None:
        factors["demand"] = clamp(interest / 100)
        if interest >= 60:
            pro.append(f"Strong search interest ({interest:.0f}/100 in its Google Trends export).")
        elif interest < 25:
            con.append(f"Low search interest ({interest:.0f}/100 in its Google Trends export).")
    else:
        missing.append("demand (add a Google Trends export)")

    # --- trend direction ---
    change = m.get("trend_change_pct")
    if change is not None:
        factors["trend"] = clamp(0.5 + change / 200)  # +100% → 1.0, -100% → 0.0, flat → 0.5
        if change >= 20:
            pro.append(f"Interest is rising ({change:+.0f}% vs the previous period).")
        elif change <= -20:
            con.append(f"Interest is falling ({change:+.0f}% vs the previous period).")
    else:
        missing.append("trend (needs a Google Trends export of 24+ data points)")

    # --- margin ---
    margin = rough_margin(m, business)
    if margin is not None:
        factors["margin"] = clamp(margin / cfg.target_gross_margin)
        if margin >= cfg.target_gross_margin:
            pro.append(f"Healthy rough margin of {margin:.0%} before fees and ads.")
        elif margin < 0.3:
            con.append(f"Thin rough margin of {margin:.0%} before fees and ads; ads will likely eat it.")
    else:
        missing.append("margin (add unit cost and sell price)")

    # --- competition ---
    stores = m.get("competitor_stores")
    if stores is not None:
        factors["competition"] = 1 - clamp(stores / cfg.competition_high)
        if stores <= cfg.competition_high * 0.2:
            pro.append(f"Few competitor stores spotted ({stores:.0f}).")
        elif stores >= cfg.competition_high * 0.8:
            con.append(f"Crowded: {stores:.0f} competitor stores spotted.")
    else:
        missing.append("competition (add competitor_stores)")

    have = {k: v for k, v in factors.items() if v is not None}
    total_w = sum(w.values())
    have_w = sum(w[k] for k in have)
    unknown = 0.5  # "no data" counts as average
    base = 100 * sum(w[k] * (v if v is not None else unknown) for k, v in factors.items()) / total_w if total_w else 0.0
    confidence = have_w / total_w if total_w else 0.0

    # --- penalties ---
    penalties: dict[str, float] = {}
    advertisers = m.get("meta_advertisers")
    adv_source = "advertisers in the Meta Ad Library"
    if advertisers is None:
        advertisers, adv_source = m.get("ads_seen"), "advertisers you spotted"
    if advertisers is not None:
        level = clamp(advertisers / cfg.saturation_high)
        dup = m.get("meta_duplicate_creative_share")
        if dup is not None:
            level = clamp(level * (1 + dup))
        if level > 0:
            penalties["saturation"] = round(level * cfg.saturation_max_penalty, 1)
        if level >= 0.6:
            extra = f", and {dup:.0%} of ads are copies of each other" if dup and dup >= 0.3 else ""
            con.append(f"Saturated: {advertisers:.0f} {adv_source}{extra}.")
        elif advertisers == 0:
            pro.append(f"No {adv_source} yet: either an opening or no proven demand.")
    else:
        missing.append("ad saturation (add ads_seen or enable Meta Ad Library)")

    season = m.get("seasonality_ratio")
    if season is not None and season >= cfg.seasonal_ratio:
        penalties["seasonality"] = cfg.seasonal_penalty
        con.append(f"Seasonal: peak interest is {season:.1f}x the average, so sales may be short-lived.")

    score = max(0.0, base - sum(penalties.values())) if have else 0.0
    if confidence < cfg.low_confidence_below:
        con.append(f"Low confidence: only {confidence:.0%} of the scoring data is available.")

    return ScoreResult(
        score=round(score, 1), confidence=round(confidence, 2),
        factors={k: (round(v, 3) if v is not None else None) for k, v in factors.items()},
        penalties=penalties, rough_margin=round(margin, 3) if margin is not None else None,
        reasons_for=pro, reasons_against=con, missing=missing,
    )
