"""Profit maths, checked by hand. If any of these change, money calculations changed."""

import pytest

from app.core.config import BusinessConfig, ProfitConfig
from app.modules.profit_calculator import ProfitInputs, calculate

CFG = ProfitConfig()
BIZ = BusinessConfig(vat_registered=False)


def inputs(**kw):
    base = dict(sell_price_gbp=20.0, unit_cost_gbp=4.0, shipping_gbp=3.0, packaging_gbp=0.0,
                payment_fee_pct=2.0, payment_fee_fixed_gbp=0.25, third_party_gateway_fee_pct=0.0,
                platform_monthly_fee_gbp=25.0, expected_orders_per_month=50, refund_rate=0.05,
                chargeback_rate=0.0, chargeback_fee_gbp=15.0, cpa_gbp=5.0, vat_registered=False)
    base.update(kw)
    return ProfitInputs(**base)


def test_worked_example_not_vat_registered():
    r = calculate(inputs(), CFG)
    # net 20.00; product 7.00; payment 0.40+0.25=0.65; platform 25/50=0.50; refunds 5% of 20 = 1.00
    assert r.net_revenue_gbp == 20.00
    assert r.payment_fees_gbp == 0.65
    assert r.platform_fees_gbp == 0.50
    assert r.refund_cost_gbp == 1.00
    assert r.costs_before_ads_gbp == 9.15
    assert r.break_even_cpa_gbp == 10.85
    assert r.profit_gbp == 5.85
    assert r.margin == pytest.approx(0.2925)
    assert r.break_even_roas == pytest.approx(20 / 10.85, abs=0.01)
    assert r.verdict == "go"


def test_vat_registered_removes_one_sixth():
    r = calculate(inputs(sell_price_gbp=24.0, vat_registered=True), CFG)
    assert r.vat_gbp == 4.00
    assert r.net_revenue_gbp == 20.00
    # payment fee is charged on the full £24 the customer pays
    assert r.payment_fees_gbp == pytest.approx(24 * 0.02 + 0.25, abs=0.01)


def test_chargebacks_include_fee():
    r = calculate(inputs(chargeback_rate=0.01), CFG)
    assert r.chargeback_cost_gbp == pytest.approx(0.01 * (20 + 15), abs=0.01)


def test_third_party_gateway_fee():
    r = calculate(inputs(third_party_gateway_fee_pct=2.0), CFG)
    assert r.platform_fees_gbp == pytest.approx(0.50 + 0.40, abs=0.01)


def test_marginal_when_profit_thin():
    r = calculate(inputs(cpa_gbp=8.0), CFG)  # profit 2.85
    assert r.verdict == "marginal"
    assert any("thin" in n for n in r.notes)


def test_no_go_when_ads_cost_more_than_margin():
    r = calculate(inputs(cpa_gbp=15.0), CFG)
    assert r.profit_gbp == pytest.approx(-4.15)
    assert r.verdict == "no-go"
    assert any("Loses £4.15" in n for n in r.notes)


def test_cant_break_even_before_ads():
    r = calculate(inputs(sell_price_gbp=8.0, cpa_gbp=0), CFG)
    assert r.break_even_cpa_gbp < 0 and r.break_even_roas is None and r.verdict == "no-go"


def test_high_break_even_roas_warning():
    r = calculate(inputs(sell_price_gbp=20, unit_cost_gbp=9, shipping_gbp=4, cpa_gbp=0), CFG)
    assert r.break_even_roas > 3
    assert any("Break-even ROAS" in n for n in r.notes)


def test_vat_threshold_note_when_not_registered():
    r = calculate(inputs(), CFG, BIZ)
    assert any("90,000" in n for n in r.notes)


def test_rejects_bad_inputs():
    with pytest.raises(ValueError, match="Sell price"):
        calculate(inputs(sell_price_gbp=0), CFG)
    with pytest.raises(ValueError, match="refund_rate"):
        calculate(inputs(refund_rate=5), CFG)  # someone typed 5 meaning 5%
    with pytest.raises(ValueError, match="negative"):
        calculate(inputs(cpa_gbp=-1), CFG)


def test_from_config_uses_defaults_and_overrides():
    i = ProfitInputs.from_config(ProfitConfig(cpa_gbp=12), BusinessConfig(vat_registered=True),
                                 sell_price_gbp=30, unit_cost_gbp=5, refund_rate=0.1, unknown_field=1)
    assert i.cpa_gbp == 12 and i.refund_rate == 0.1 and i.vat_registered is True
