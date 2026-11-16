"""Dashboard pages for suppliers, outreach emails and the profit calculator (phase 3)."""

from __future__ import annotations

from urllib.parse import parse_qs

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.logger import log_event
from app.core.models import Product, Supplier, SupplierMessage
from app.modules.profit_calculator import ProfitInputs, calculate
from app.modules.supplier_finder import REMINDER, service

# Fields you can edit in the profit calculator, with labels and help text.
PROFIT_FIELDS = [
    ("sell_price_gbp", "Sell price (inc. VAT) £", "What the customer pays."),
    ("unit_cost_gbp", "Product cost £", "From the supplier."),
    ("shipping_gbp", "Shipping to customer £", "From the supplier."),
    ("packaging_gbp", "Packaging £", "Extra packaging or inserts per order."),
    ("cpa_gbp", "Ad cost per sale (CPA) £", "Your biggest guess: update it once ads are running."),
    ("refund_rate", "Refund rate", "0.05 = 5% of orders refunded."),
    ("chargeback_rate", "Chargeback rate", "0.005 = 0.5% of orders disputed."),
    ("chargeback_fee_gbp", "Chargeback fee £", "Charged per dispute."),
    ("payment_fee_pct", "Card fee %", "Shopify Payments rate for your plan."),
    ("payment_fee_fixed_gbp", "Card fee per order £", ""),
    ("third_party_gateway_fee_pct", "Extra gateway fee %", "Only if you don't use Shopify Payments."),
    ("platform_monthly_fee_gbp", "Shopify + apps per month £", ""),
    ("expected_orders_per_month", "Orders per month", "Spreads the monthly fee per order."),
]
PRODUCT_LEVEL = {"sell_price_gbp", "packaging_gbp", "cpa_gbp", "refund_rate", "chargeback_rate", "chargeback_fee_gbp",
                 "payment_fee_pct", "payment_fee_fixed_gbp", "third_party_gateway_fee_pct",
                 "platform_monthly_fee_gbp", "expected_orders_per_month"}


async def form_data(request: Request) -> dict[str, str]:
    """Read a submitted form (without needing an extra library)."""
    raw = (await request.body()).decode("utf-8")
    return {k: v[-1] for k, v in parse_qs(raw, keep_blank_values=True).items()}


def register(app: FastAPI, templates, settings: Settings, get_session, render, require_htmx) -> None:
    htmx = [Depends(require_htmx)]

    def get_or_404(session: Session, model, obj_id: int):
        obj = session.get(model, obj_id)
        if obj is None:
            raise HTTPException(404, f"{model.__name__} not found")
        return obj

    def refresh() -> Response:
        return Response(status_code=204, headers={"HX-Refresh": "true"})

    # ---------------- suppliers ----------------
    @app.get("/suppliers", response_class=HTMLResponse)
    def suppliers_page(request: Request, session: Session = Depends(get_session)):
        products = [p for p in session.scalars(select(Product))
                    if session.scalars(select(Supplier.id).where(Supplier.product_id == p.id)).first()]
        order = {"supplier_chosen": 0, "shortlisted": 1, "sample_ordered": 2}
        products.sort(key=lambda p: (order.get(p.status.value, 3), -(p.score or 0)))
        groups = []
        for p in products:
            sups = sorted(session.scalars(select(Supplier).where(Supplier.product_id == p.id)), key=lambda s: -(s.score or 0))
            price, price_source = service.product_sell_price(session, p)
            groups.append({"product": p, "suppliers": sups, "price": price, "price_source": price_source})
        return render(request, "suppliers.html", session, groups=groups, reminder=REMINDER,
                      top_n=settings.suppliers.top_n)

    @app.get("/suppliers/{supplier_id}", response_class=HTMLResponse)
    def supplier_detail(request: Request, supplier_id: int, session: Session = Depends(get_session)):
        s = get_or_404(session, Supplier, supplier_id)
        messages = sorted(s.messages, key=lambda m: m.id, reverse=True)
        return render(request, "supplier_detail.html", session, s=s, p=s.product, messages=messages, reminder=REMINDER)

    @app.post("/suppliers/{supplier_id}/choose", dependencies=htmx)
    def choose(supplier_id: int, session: Session = Depends(get_session)):
        s = get_or_404(session, Supplier, supplier_id)
        try:
            service.choose_supplier(session, settings, s.product, s)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return refresh()

    @app.post("/products/{product_id}/sample-ordered", dependencies=htmx)
    def sample_ordered(product_id: int, session: Session = Depends(get_session)):
        try:
            service.mark_sample_ordered(session, settings, get_or_404(session, Product, product_id))
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return refresh()

    # ---------------- outreach ----------------
    @app.post("/suppliers/{supplier_id}/draft-email", dependencies=htmx)
    def draft(supplier_id: int, session: Session = Depends(get_session)):
        service.draft_email(session, settings, get_or_404(session, Supplier, supplier_id))
        return refresh()

    @app.post("/messages/{message_id}/sent", dependencies=htmx)
    def sent(message_id: int, session: Session = Depends(get_session)):
        try:
            service.mark_sent(session, settings, get_or_404(session, SupplierMessage, message_id))
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return refresh()

    @app.post("/messages/{message_id}/reply", dependencies=htmx)
    async def reply(message_id: int, request: Request, session: Session = Depends(get_session)):
        text = (await form_data(request)).get("reply_text", "")
        try:
            service.record_reply(session, settings, get_or_404(session, SupplierMessage, message_id), text)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return refresh()

    # ---------------- profit calculator ----------------
    def defaults_for(session: Session, product: Product | None, supplier: Supplier | None) -> dict:
        values = {k: v for k, v in settings.profit.model_dump().items() if k in ProfitInputs.__dataclass_fields__}
        values.update(sell_price_gbp=None, unit_cost_gbp=None, shipping_gbp=None)
        if product is not None:
            values["sell_price_gbp"], _ = service.product_sell_price(session, product)
            values.update({k: v for k, v in (product.profit_overrides or {}).items() if k in values})
        if supplier is not None:
            values.update(unit_cost_gbp=supplier.unit_cost_gbp, shipping_gbp=supplier.shipping_cost_gbp)
        return values

    def parse_inputs(form: dict[str, str]) -> tuple[ProfitInputs | None, str | None]:
        vals: dict = {}
        for key, label, _ in PROFIT_FIELDS:
            raw = form.get(key, "").strip().replace("£", "")
            if raw == "":
                if key in ("sell_price_gbp", "unit_cost_gbp"):
                    return None, f"Enter the {label.rstrip(' £').lower()}."
                continue
            try:
                vals[key] = int(float(raw)) if key == "expected_orders_per_month" else float(raw)
            except ValueError:
                return None, f"'{raw}' isn't a number ({label})."
        i = ProfitInputs.from_config(settings.profit, settings.business, **vals)
        errors = i.validate()
        return (None, " ".join(errors)) if errors else (i, None)

    @app.get("/profit", response_class=HTMLResponse)
    def profit_page(request: Request, product_id: int | None = None, supplier_id: int | None = None,
                    session: Session = Depends(get_session)):
        product = session.get(Product, product_id) if product_id else None
        supplier = session.get(Supplier, supplier_id) if supplier_id else None
        if supplier is not None and product is None:
            product = supplier.product
        if product is not None and supplier is None and product.chosen_supplier_id:
            supplier = session.get(Supplier, product.chosen_supplier_id)
        values = defaults_for(session, product, supplier)
        result, error = None, None
        if values.get("sell_price_gbp") is not None and values.get("unit_cost_gbp") is not None:
            try:
                result = calculate(ProfitInputs.from_config(settings.profit, settings.business, **values),
                                   settings.profit, settings.business)
            except ValueError as exc:
                error = str(exc)
        products = list(session.scalars(select(Product).order_by(Product.name)))
        return render(request, "profit.html", session, fields=PROFIT_FIELDS, values=values, result=result,
                      error=error, product=product, supplier=supplier, products=products)

    @app.post("/profit/calc", response_class=HTMLResponse, dependencies=htmx)
    async def profit_calc(request: Request):
        inputs, error = parse_inputs(await form_data(request))
        result = calculate(inputs, settings.profit, settings.business) if inputs else None
        return templates.TemplateResponse(request, "_profit_result.html", {"result": result, "error": error})

    @app.post("/profit/save/{product_id}", response_class=HTMLResponse, dependencies=htmx)
    async def profit_save(product_id: int, request: Request, session: Session = Depends(get_session)):
        product = get_or_404(session, Product, product_id)
        inputs, error = parse_inputs(await form_data(request))
        if error:
            raise HTTPException(422, error)
        saved = {k: getattr(inputs, k) for k in PRODUCT_LEVEL}
        product.profit_overrides = saved
        session.commit()
        service.rescore_product(session, settings, product)
        log_event(session, module="profit_calculator", action="assumptions_saved",
                  message=f"{product.name}: saved profit assumptions (CPA £{inputs.cpa_gbp:.2f}, "
                          f"price £{inputs.sell_price_gbp:.2f}).", dry_run=settings.dry_run, details=saved)
        return HTMLResponse('<span class="saved">Saved. Supplier profits updated.</span>')
