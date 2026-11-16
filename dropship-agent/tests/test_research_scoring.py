import pytest

from app.core.config import BusinessConfig, ResearchConfig
from app.modules.product_research import compliance
from app.modules.product_research.scoring import rough_margin, score_product

CFG = ResearchConfig()
BIZ = BusinessConfig(vat_registered=False)

FULL = {
    "search_interest": 80, "trend_change_pct": 40, "unit_cost_gbp": 3, "shipping_gbp": 2,
    "sell_price_gbp": 20, "competitor_stores": 5, "ads_seen": 3,
}


def test_rough_margin_not_vat_registered():
    assert rough_margin({"sell_price_gbp": 20, "unit_cost_gbp": 3, "shipping_gbp": 2}, BIZ) == pytest.approx(0.75)


def test_rough_margin_vat_registered_removes_vat():
    biz = BusinessConfig(vat_registered=True, vat_rate=0.2)
    # £24 inc VAT = £20 net; (20 - 5) / 20 = 75%
    assert rough_margin({"sell_price_gbp": 24, "unit_cost_gbp": 3, "shipping_gbp": 2}, biz) == pytest.approx(0.75)


def test_rough_margin_unknown_when_price_missing():
    assert rough_margin({"unit_cost_gbp": 3}, BIZ) is None


def test_full_data_strong_product_scores_high():
    r = score_product(FULL, CFG, BIZ)
    assert r.confidence == 1.0
    assert r.score > 75
    assert any("rising" in s for s in r.reasons_for)
    assert not r.missing


def test_missing_data_is_left_out_not_guessed():
    r = score_product({"unit_cost_gbp": 3, "sell_price_gbp": 20}, CFG, BIZ)
    assert r.factors["demand"] is None and r.factors["trend"] is None
    assert r.confidence == pytest.approx(0.3)
    assert any("Low confidence" in s for s in r.reasons_against)
    assert len(r.missing) == 4  # demand, trend, competition, saturation


def test_little_data_cannot_outrank_full_data():
    # Great margin but nothing else known vs. a well-researched good product.
    sparse = score_product({"unit_cost_gbp": 1, "sell_price_gbp": 20}, CFG, BIZ)
    assert sparse.score < score_product(FULL, CFG, BIZ).score
    assert sparse.score == pytest.approx(65.0)  # margin 1.0 x 0.3 + 0.5 x 0.7 = 0.65


def test_no_data_scores_zero():
    r = score_product({}, CFG, BIZ)
    assert r.score == 0 and r.confidence == 0


def test_saturation_penalty_lowers_score():
    calm = score_product(FULL, CFG, BIZ)
    crowded = score_product({**FULL, "ads_seen": 30}, CFG, BIZ)
    assert crowded.penalties["saturation"] == pytest.approx(25.0)
    assert crowded.score == pytest.approx(calm.score - 25.0 + calm.penalties.get("saturation", 0), abs=0.2)
    assert any("Saturated" in s for s in crowded.reasons_against)


def test_copied_ads_increase_saturation():
    base = {**FULL, "meta_advertisers": 10}
    plain = score_product(base, CFG, BIZ)
    copied = score_product({**base, "meta_duplicate_creative_share": 0.5}, CFG, BIZ)
    assert copied.penalties["saturation"] > plain.penalties["saturation"]


def test_meta_data_preferred_over_manual_count():
    r = score_product({**FULL, "ads_seen": 0, "meta_advertisers": 30}, CFG, BIZ)
    assert r.penalties["saturation"] == pytest.approx(25.0)


def test_seasonal_penalty():
    r = score_product({**FULL, "seasonality_ratio": 3.0}, CFG, BIZ)
    assert r.penalties["seasonality"] == CFG.seasonal_penalty
    assert any("Seasonal" in s for s in r.reasons_against)


def test_falling_trend_and_thin_margin_explained():
    r = score_product({**FULL, "trend_change_pct": -50, "sell_price_gbp": 6}, CFG, BIZ)
    assert any("falling" in s for s in r.reasons_against)
    assert any("Thin rough margin" in s for s in r.reasons_against)


def test_score_never_negative():
    bad = {"search_interest": 1, "trend_change_pct": -100, "unit_cost_gbp": 9, "sell_price_gbp": 10,
           "competitor_stores": 99, "ads_seen": 100, "seasonality_ratio": 9}
    r = score_product(bad, CFG, BIZ)
    assert sum(r.penalties.values()) > 0 and r.score == 0


# ---------------- compliance ----------------
def test_compliance_flags_electrical_and_batteries():
    flags = {f["flag"] for f in compliance.check("Rechargeable LED desk lamp")}
    assert {"electrical", "batteries"} <= flags


def test_compliance_whole_words_only():
    assert compliance.check("Lightweight cupboard organiser") == []


def test_compliance_uses_category_and_keywords():
    flags = {f["flag"] for f in compliance.check("Glow serum", "beauty", ["face cream"])}
    assert "cosmetics" in flags


def test_avoided_categories():
    flags = compliance.check("Kids building toy")
    assert compliance.avoided(flags, "", ["toys"]) == ["toys"]
    assert compliance.avoided([], "Supplements", ["supplements"]) == ["supplements"]
    assert compliance.avoided(flags, "", []) == []
