"""True profit per sale, after every cost we know about.

    Customer pays (inc. VAT)
  − VAT you owe HMRC (only if VAT-registered)
  = Net revenue
  − Product cost, shipping, packaging
  − Card/payment fees            (charged on what the customer pays)
  − Platform fees                (Shopify plan spread per order + any gateway fee)
  − Expected refunds             (refund rate x net revenue; goods usually not recovered)
  − Expected chargebacks         (rate x (net revenue + chargeback fee))
  = Break-even CPA               (the most you can spend on ads per sale without losing money)
  − Ad cost per sale (CPA)       (an assumption you set)
  = Profit per sale

Break-even ROAS = order value / break-even CPA. Ad platforms report ROAS on order value,
so that's what we compare against.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from app.core.config import BusinessConfig, ProfitConfig


@dataclass
class ProfitInputs:
    sell_price_gbp: float  # what the customer pays, inc. VAT
    unit_cost_gbp: float
    shipping_gbp: float = 0.0
    packaging_gbp: float = 0.0
    payment_fee_pct: float = 2.0
    payment_fee_fixed_gbp: float = 0.25
    third_party_gateway_fee_pct: float = 0.0
    platform_monthly_fee_gbp: float = 25.0
    expected_orders_per_month: int = 50
    refund_rate: float = 0.05
    chargeback_rate: float = 0.005
    chargeback_fee_gbp: float = 15.0
    cpa_gbp: float = 10.0
    vat_registered: bool = False
    vat_rate: float = 0.20

    @classmethod
    def from_config(cls, profit: ProfitConfig, business: BusinessConfig, *, sell_price_gbp: float,
                    unit_cost_gbp: float, shipping_gbp: float = 0.0, **overrides) -> ProfitInputs:
        base = {k: v for k, v in profit.model_dump().items() if k in cls.__dataclass_fields__}
        base.update(vat_registered=business.vat_registered, vat_rate=business.vat_rate)
        base.update({k: v for k, v in overrides.items() if v is not None and k in cls.__dataclass_fields__})
        return cls(sell_price_gbp=sell_price_gbp, unit_cost_gbp=unit_cost_gbp, shipping_gbp=shipping_gbp, **base)

    def validate(self) -> list[str]:
        errors = []
        if self.sell_price_gbp <= 0:
            errors.append("Sell price must be more than £0.")
        for name in ("unit_cost_gbp", "shipping_gbp", "packaging_gbp", "payment_fee_pct", "payment_fee_fixed_gbp",
                     "third_party_gateway_fee_pct", "platform_monthly_fee_gbp", "chargeback_fee_gbp", "cpa_gbp"):
            if getattr(self, name) < 0:
                errors.append(f"{name} can't be negative.")
        for name in ("refund_rate", "chargeback_rate"):
            if not 0 <= getattr(self, name) <= 1:
                errors.append(f"{name} must be between 0 and 1 (e.g. 0.05 for 5%).")
        if self.expected_orders_per_month <= 0:
            errors.append("Expected orders per month must be at least 1.")
        return errors


@dataclass
class ProfitResult:
    inputs: ProfitInputs
    vat_gbp: float
    net_revenue_gbp: float
    product_costs_gbp: float
    payment_fees_gbp: float
    platform_fees_gbp: float
    refund_cost_gbp: float
    chargeback_cost_gbp: float
    costs_before_ads_gbp: float
    break_even_cpa_gbp: float
    profit_gbp: float
    margin: float  # profit / net revenue
    break_even_roas: float | None  # None = can't break even at any ad spend
    verdict: str  # "go" | "marginal" | "no-go"
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["inputs"] = asdict(self.inputs)
        return d


def calculate(i: ProfitInputs, cfg: ProfitConfig, business: BusinessConfig | None = None) -> ProfitResult:
    errors = i.validate()
    if errors:
        raise ValueError(" ".join(errors))

    price = i.sell_price_gbp
    vat = price - price / (1 + i.vat_rate) if i.vat_registered else 0.0
    net = price - vat
    product = i.unit_cost_gbp + i.shipping_gbp + i.packaging_gbp
    payment = price * i.payment_fee_pct / 100 + i.payment_fee_fixed_gbp
    platform = i.platform_monthly_fee_gbp / i.expected_orders_per_month + price * i.third_party_gateway_fee_pct / 100
    refunds = i.refund_rate * net
    chargebacks = i.chargeback_rate * (net + i.chargeback_fee_gbp)
    costs = product + payment + platform + refunds + chargebacks
    break_even_cpa = net - costs
    profit = break_even_cpa - i.cpa_gbp
    margin = profit / net
    roas = price / break_even_cpa if break_even_cpa > 0 else None

    notes: list[str] = []
    if profit <= 0:
        verdict = "no-go"
        notes.append(f"Loses £{-profit:.2f} per sale at a £{i.cpa_gbp:.2f} ad cost per sale."
                     if profit < 0 else "Exactly breaks even: no profit.")
    elif profit >= cfg.go_min_profit_gbp and margin >= cfg.go_min_margin:
        verdict = "go"
    else:
        verdict = "marginal"
        notes.append(f"Profitable but thin: aim for at least £{cfg.go_min_profit_gbp:.2f} and "
                     f"{cfg.go_min_margin:.0%} margin per sale after ads.")
    if roas is None:
        notes.append("Costs are higher than revenue before any ads: can't break even.")
    elif roas > cfg.max_comfortable_roas:
        notes.append(f"Break-even ROAS is {roas:.1f}: ads must return £{roas:.2f} per £1 spent just to break "
                     f"even. Above {cfg.max_comfortable_roas:.0f} is hard for a new store.")
    if i.platform_monthly_fee_gbp and i.expected_orders_per_month < 20:
        notes.append("Few orders per month: the Shopify fee weighs heavily on each sale.")
    if not i.vat_registered and business is not None:
        notes.append(f"Not VAT-registered: you must register if your 12-month turnover passes "
                     f"£{business.vat_registration_threshold_gbp:,.0f}. That would cut net revenue by about "
                     f"{business.vat_rate / (1 + business.vat_rate):.0%}.")

    r = lambda x: round(x, 2)  # noqa: E731  (money to the penny)
    return ProfitResult(
        inputs=i, vat_gbp=r(vat), net_revenue_gbp=r(net), product_costs_gbp=r(product),
        payment_fees_gbp=r(payment), platform_fees_gbp=r(platform), refund_cost_gbp=r(refunds),
        chargeback_cost_gbp=r(chargebacks), costs_before_ads_gbp=r(costs), break_even_cpa_gbp=r(break_even_cpa),
        profit_gbp=r(profit), margin=round(margin, 4), break_even_roas=round(roas, 2) if roas else None,
        verdict=verdict, notes=notes,
    )
