"""Suppliers end to end: import → score → profit → choose → email drafts → replies → dashboard."""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.api.main import create_app
from app.core.models import MessageStatus, Product, ProductStatus, Supplier, SupplierMessage
from app.modules.product_research import run_research
from app.modules.supplier_finder import import_suppliers
from app.modules.supplier_finder import service
from tests.test_research_service import research_settings  # noqa: F401  (fixture)
from tests.test_research_sources import write

SUPPLIERS = (
    "product,supplier_name,platform,currency,unit_cost,shipping_cost,shipping_days_max,warehouse,rating,"
    "review_count,moq,return_policy_days,has_branding_info,source_note\n"
    "Pet hair roller,UK Fast,CJ,GBP,4,2,4,UK,4.7,400,1,30,yes,CJ 16/11\n"
    "Pet hair roller,Cheap Slow,AliExpress,GBP,2,1,25,CN,4.9,8,1,0,no,Ali 16/11\n"
    "Pet hair roller,Middle EU,Spocket,GBP,5,3,8,EU,4.5,150,1,14,yes,Spocket 16/11\n"
    "Unknown thing,Someone,CJ,GBP,1,1,5,UK,4,10,1,14,yes,x\n"
)


@pytest.fixture
def supplier_settings(research_settings, tmp_path):  # noqa: F811
    research_settings.suppliers.suppliers_file = str(write(tmp_path / "suppliers.csv", SUPPLIERS))
    return research_settings


@pytest.fixture
def imported(session, supplier_settings):
    run_research(session, supplier_settings)
    return import_suppliers(session, supplier_settings)


def roller(session):
    return session.scalars(select(Product).where(Product.name == "Pet hair roller")).one()


def test_ranks_suppliers_and_flags_risks(session, imported):
    sups = imported.by_product[roller(session).id]
    assert [s.name for s in sups][0] == "UK Fast"
    slow = next(s for s in sups if s.name == "Cheap Slow")
    assert {"slow_shipping", "thin_reviews", "no_returns", "no_branding_info"} <= {f["flag"] for f in slow.red_flags}
    assert any("isn't in your research yet" in p for p in imported.problems)


def test_true_profit_uses_supplier_costs_and_research_price(session, imported, supplier_settings):
    s = next(s for s in imported.by_product[roller(session).id] if s.name == "UK Fast")
    # research price £18 (not VAT-registered), cost 4 + ship 2, default fees/refunds, CPA £10 (test config defaults)
    assert s.profit["inputs"]["sell_price_gbp"] == 18
    assert s.profit["product_costs_gbp"] == 6.0
    assert s.profit["verdict"] in ("go", "marginal", "no-go")
    assert "CJ 16/11" in s.profit["price_source"]


def test_reimport_updates_instead_of_duplicating(session, imported, supplier_settings):
    import_suppliers(session, supplier_settings)
    assert session.scalar(select(func.count()).select_from(Supplier)) == 3


def test_saved_assumptions_change_profit(session, imported, supplier_settings):
    p = roller(session)
    s = next(s for s in imported.by_product[p.id] if s.name == "UK Fast")
    before = s.profit["profit_gbp"]
    p.profit_overrides = {"sell_price_gbp": 25.0, "cpa_gbp": 6.0}
    session.commit()
    service.rescore_product(session, supplier_settings, p)
    assert s.profit["profit_gbp"] > before
    assert s.profit["price_source"] == "your saved assumption"


def test_no_price_means_no_profit_not_a_guess(session, imported, supplier_settings, tmp_path):
    p = roller(session)
    from app.core.models import Metric
    for m in session.scalars(select(Metric).where(Metric.product_id == p.id, Metric.name == "sell_price_gbp")):
        session.delete(m)
    session.commit()
    sups = service.rescore_product(session, supplier_settings, p)
    assert all("No sell price" in s.profit["error"] for s in sups)


def test_choose_supplier_then_sample(session, imported, supplier_settings):
    p = roller(session)
    s = imported.by_product[p.id][0]
    service.choose_supplier(session, supplier_settings, p, s)
    assert p.status == ProductStatus.SUPPLIER_CHOSEN and p.chosen_supplier_id == s.id
    service.mark_sample_ordered(session, supplier_settings, p)
    assert p.status == ProductStatus.SAMPLE_ORDERED
    with pytest.raises(ValueError):
        service.choose_supplier(session, supplier_settings, p, s)  # past that stage now


def test_email_draft_flow_and_reply_speed(session, imported, supplier_settings):
    s = imported.by_product[roller(session).id][0]
    msg = service.draft_email(session, supplier_settings, s)
    assert msg.status == MessageStatus.DRAFT
    assert "sample" in msg.body.lower() and "UK" in msg.body
    assert service.draft_email(session, supplier_settings, s).id == msg.id  # no duplicate drafts

    with pytest.raises(ValueError):
        service.record_reply(session, supplier_settings, msg, "hi")  # not sent yet
    service.mark_sent(session, supplier_settings, msg)
    service.record_reply(session, supplier_settings, msg, "Yes, samples cost £5.",
                         replied_at=msg.sent_at + timedelta(hours=30))
    assert msg.status == MessageStatus.REPLIED
    assert s.responds_within_hours == 30.0
    assert s.score_details["factors"]["terms"] is not None


def test_regulated_product_email_asks_for_ukca(session, supplier_settings):
    run_research(session, supplier_settings)
    mug = session.scalars(select(Product).where(Product.name == "Plain mug")).one()
    s = Supplier(product_id=mug.id, name="MugCo", source="test")
    session.add(s)
    session.commit()
    msg = service.draft_email(session, supplier_settings, s)
    assert "UKCA or CE" in msg.body


# ---------------- dashboard ----------------
def test_dashboard_supplier_pages(session, imported, supplier_settings, engine):
    client = TestClient(create_app(supplier_settings, engine))
    hx = {"HX-Request": "true"}
    page = client.get("/suppliers").text
    assert "UK Fast" in page and "slow shipping" in page and "filter, not a verdict" in page

    s = session.scalars(select(Supplier).where(Supplier.name == "UK Fast")).one()
    detail = client.get(f"/suppliers/{s.id}").text
    assert "Profit breakdown" in detail and "never sent automatically" in detail

    assert client.post(f"/suppliers/{s.id}/choose").status_code == 403
    assert client.post(f"/suppliers/{s.id}/choose", headers=hx).status_code == 204
    assert client.post(f"/suppliers/{s.id}/draft-email", headers=hx).status_code == 204
    msg = session.scalars(select(SupplierMessage)).one()
    assert client.post(f"/messages/{msg.id}/sent", headers=hx).status_code == 204
    r = client.post(f"/messages/{msg.id}/reply", headers=hx, data={"reply_text": "Samples £5, 3 days to UK"})
    assert r.status_code == 204
    session.expire_all()
    assert session.get(SupplierMessage, msg.id).reply_text == "Samples £5, 3 days to UK"


def test_dashboard_profit_calculator(session, imported, supplier_settings, engine):
    client = TestClient(create_app(supplier_settings, engine))
    hx = {"HX-Request": "true"}
    p = roller(session)
    page = client.get(f"/profit?product_id={p.id}").text
    assert "Save for Pet hair roller" in page and 'value="18.0"' in page

    r = client.post("/profit/calc", headers=hx, data={"sell_price_gbp": "20", "unit_cost_gbp": "4",
                                                      "shipping_gbp": "3", "cpa_gbp": "5"})
    assert "GO" in r.text and "Break-even" in r.text
    bad = client.post("/profit/calc", headers=hx, data={"sell_price_gbp": "abc", "unit_cost_gbp": "4"})
    assert "isn&#39;t a number" in bad.text or "isn't a number" in bad.text

    saved = client.post(f"/profit/save/{p.id}", headers=hx,
                        data={"sell_price_gbp": "24", "unit_cost_gbp": "4", "cpa_gbp": "7"})
    assert saved.status_code == 200 and "Saved" in saved.text
    session.expire_all()
    assert session.get(Product, p.id).profit_overrides["sell_price_gbp"] == 24
