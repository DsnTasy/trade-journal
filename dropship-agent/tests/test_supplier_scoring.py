import pytest

from app.core.config import BusinessConfig, SupplierConfig
from app.modules.supplier_finder.scoring import delivery_factor, score_supplier
from app.modules.supplier_finder.sources import load_suppliers

CFG = SupplierConfig()

GOOD = {"unit_cost_gbp": 4.0, "shipping_cost_gbp": 2.0, "shipping_days_max": 4, "warehouse": "UK",
        "rating": 4.7, "review_count": 500, "orders_sold": 3000, "moq": 1, "return_policy_days": 30,
        "responds_within_hours": 12, "has_branding_info": "yes"}


def flags(result):
    return {f["flag"] for f in result.red_flags}


def test_good_uk_supplier_scores_high_without_flags():
    r = score_supplier(GOOD, cheapest_landed=6.0, cfg=CFG)
    assert r.red_flags == []
    assert r.score > 85
    assert any("UK warehouse" in s for s in r.strengths)


def test_price_relative_to_cheapest():
    r = score_supplier(GOOD, cheapest_landed=3.0, cfg=CFG)  # landed 6 = twice the cheapest
    assert r.factors["price"] == 0.5


def test_delivery_curve():
    assert delivery_factor(7, CFG) == 1.0
    assert delivery_factor(15, CFG) == 0.1
    assert 0.1 < delivery_factor(11, CFG) < 1.0


def test_slow_shipping_flagged():
    r = score_supplier({**GOOD, "shipping_days_max": 20, "warehouse": "CN"}, 6.0, CFG)
    assert "slow_shipping" in flags(r)


def test_missing_delivery_time_is_vague_shipping():
    r = score_supplier({**GOOD, "shipping_days_max": None}, 6.0, CFG)
    assert "vague_shipping" in flags(r)
    assert r.factors["delivery"] is None


def test_perfect_rating_from_few_reviews_flagged_and_not_trusted():
    r = score_supplier({**GOOD, "rating": 5.0, "review_count": 6}, 6.0, CFG)
    assert "thin_reviews" in flags(r)
    assert r.factors["reliability"] < 0.75  # 5 stars barely trusted with 6 reviews


def test_inflated_orders_flagged():
    r = score_supplier({**GOOD, "orders_sold": 50000, "review_count": 40}, 6.0, CFG)
    assert "orders_vs_reviews" in flags(r)


def test_terms_flags():
    r = score_supplier({**GOOD, "return_policy_days": 0, "moq": 50, "responds_within_hours": 120}, 6.0, CFG)
    assert {"no_returns", "moq", "slow_replies"} <= flags(r)
    assert r.factors["terms"] == pytest.approx((0 + 0.2 + 0.2) / 3, abs=0.01)


def test_regulated_product_needs_ukca_ce():
    r = score_supplier(GOOD, 6.0, CFG, compliance_flags=["electrical"])
    assert "no_ukca_ce" in flags(r)
    ok = score_supplier({**GOOD, "has_ukca_ce": "yes"}, 6.0, CFG, compliance_flags=["electrical"])
    assert "no_ukca_ce" not in flags(ok)


def test_each_red_flag_costs_points():
    clean = score_supplier(GOOD, 6.0, CFG)
    flagged = score_supplier({**GOOD, "has_branding_info": "no"}, 6.0, CFG)
    assert clean.score - flagged.score == pytest.approx(CFG.red_flag_penalty)


def test_unknown_supplier_is_average_minus_flags():
    r = score_supplier({}, None, CFG)
    assert r.score == pytest.approx(50 - CFG.red_flag_penalty)  # only "vague shipping" flagged


# ---------------- suppliers.csv ----------------
def test_csv_converts_currency_and_parses(tmp_path):
    f = tmp_path / "s.csv"
    f.write_text("product,supplier_name,currency,unit_cost,shipping_cost,shipping_days_min,shipping_days_max,"
                 "warehouse,rating,has_ukca_ce,source_note\n"
                 "Roller,Acme,USD,$10,5,9,3,germany,4.5,Y,CJ 16/11\n", encoding="utf-8")
    rows, problems = load_suppliers(f, BusinessConfig(usd_to_gbp=0.8))
    assert problems == []
    r = rows[0].fields
    assert r["unit_cost_gbp"] == 8.0 and r["shipping_cost_gbp"] == 4.0
    assert (r["shipping_days_min"], r["shipping_days_max"]) == (3, 9)  # swapped into order
    assert r["warehouse"] == "EU" and r["has_ukca_ce"] == "yes"
    assert "converted from USD at 0.8" in r["source"] and "CJ 16/11" in r["source"]


def test_csv_problems_reported(tmp_path):
    f = tmp_path / "s.csv"
    f.write_text("product,supplier_name,currency,unit_cost,rating\n"
                 "Roller,Acme,JPY,10,4\n"
                 "Roller,Beta,GBP,abc,9\n"
                 "Roller,Acme,GBP,1,4\n"
                 ",NoProduct,GBP,1,4\n", encoding="utf-8")
    rows, problems = load_suppliers(f, BusinessConfig())
    text = " ".join(problems)
    assert "unknown currency 'JPY'" in text
    assert "not a number" in text
    assert "above 5" in text
    assert "listed twice" in text
    assert "needs both" in text
    assert [r.name for r in rows] == ["Acme", "Beta"]
