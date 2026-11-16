"""Content generator: generation (fake Claude), claim checks, editing, approval gate, dashboard."""

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.content_routes import from_form, parse_faqs, to_form
from app.api.main import create_app
from app.core.llm_client import LLMClient
from app.core.models import ContentItem, ContentStatus, Product, ProductStatus, Supplier, SupplierMessage, MessageStatus
from app.modules.content_generator import service

PAGE = {
    "title": "Reusable Pet Hair Roller",
    "short_description": "Lifts pet hair from sofas and clothes. Rinse and reuse.",
    "bullets": ["Reusable: rinse under the tap", "Works on fabric sofas", "Made from [CHECK: material]"],
    "description": "Lifts hair from fabric.\n\nDelivered in 2-4 working days. 14-day returns.",
    "faqs": [{"question": "How long is delivery?", "answer": "2-4 working days."}],
    "seo_title": "Reusable Pet Hair Roller | Your Store",
    "meta_description": "A reusable roller that lifts pet hair from sofas and clothes.",
    "tags": ["pets", "cleaning"],
}
RISKY_PAGE = {**PAGE, "title": "The Best Pet Hair Roller", "bullets": ["Only 3 left!", "Rated 4.9/5"]}
ADS = {"scripts": [
    {"angle": a, "hook": "Pet hair on everything?", "body": "Roll, lift, rinse.", "cta": "See it on our store.",
     "on_screen_text": ["Roll", "Rinse"], "shots": ["Close-up of sofa"], "duration_seconds": 20,
     "platform_notes": {"tiktok": "t", "reels": "r", "shorts": "s"}}
    for a in ("problem_solution", "social_proof", "demo")]}


class FakeClaude:
    """Returns the page or ads JSON depending on which schema was requested."""

    def __init__(self, page=PAGE, ads=ADS):
        self.calls = []
        self.page, self.ads = page, ads
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.calls.append(kw)
        schema = kw["output_config"]["format"]["schema"]
        text = json.dumps(self.ads if "scripts" in schema["properties"] else self.page)
        return SimpleNamespace(model="test-model", stop_reason="end_turn",
                               content=[SimpleNamespace(type="text", text=text)],
                               usage=SimpleNamespace(input_tokens=2000, output_tokens=1500,
                                                     cache_read_input_tokens=0, cache_creation_input_tokens=0))


@pytest.fixture
def product(session):
    p = Product(name="Pet roller", category="pet", status=ProductStatus.SUPPLIER_CHOSEN,
                score_details={"trends_term": "pet hair remover"},
                compliance_flags=[], profit_overrides={"sell_price_gbp": 17.99})
    session.add(p)
    session.flush()
    s = Supplier(product_id=p.id, name="UK Fast", source="test", shipping_days_min=2, shipping_days_max=4,
                 warehouse="UK")
    session.add(s)
    session.flush()
    p.chosen_supplier_id = s.id
    session.commit()
    return p


def llm(session, settings, fake=None):
    return LLMClient(settings, session, client=fake or FakeClaude())


def test_facts_come_from_our_data_only(session, settings, product):
    facts, ctx = service.build_facts(session, settings, product)
    assert facts["delivery"] == "2-4 working days" and facts["dispatched_from"] == "UK"
    assert facts["price_gbp_inc_vat"] == 17.99 and facts["returns"] == "14-day returns"
    assert ctx.delivery_days_max == 4


def test_supplier_reply_is_passed_as_a_fact(session, settings, product):
    s = session.get(Supplier, product.chosen_supplier_id)
    session.add(SupplierMessage(supplier_id=s.id, subject="x", body="y", status=MessageStatus.REPLIED,
                                reply_text="Material is silicone. UKCA not needed."))
    session.commit()
    facts, _ = service.build_facts(session, settings, product)
    assert "silicone" in facts["supplier_said_in_writing"]


def test_generate_page_checks_claims_and_records_cost(session, settings, product):
    fake = FakeClaude()
    item = service.generate_page(session, settings, product, llm=llm(session, settings, fake))
    assert item.status == ContentStatus.DRAFT and item.cost_gbp > 0
    assert any(f["rule"] == "placeholder" for f in item.findings)  # [CHECK: material]
    sent = fake.calls[0]
    assert "Use ONLY the facts provided" in sent["system"]
    assert "2-4 working days" in sent["messages"][0]["content"]


def test_cannot_approve_with_high_risk_claims(session, settings, product):
    item = service.generate_page(session, settings, product, llm=llm(session, settings, FakeClaude(page=RISKY_PAGE)))
    with pytest.raises(service.ContentError, match="high-risk"):
        service.decide(session, settings, item, approve=True)
    assert item.status == ContentStatus.DRAFT


def test_edit_fixes_issues_then_approve(session, settings, product):
    item = service.generate_page(session, settings, product, llm=llm(session, settings))
    fixed = {**PAGE, "bullets": ["Reusable: rinse under the tap", "Works on fabric sofas"]}
    findings = service.update(session, settings, item, fixed)
    assert item.edited and not [f for f in findings if f["severity"] == "high"]
    service.decide(session, settings, item, approve=True)
    assert item.status == ContentStatus.APPROVED


def test_editing_approved_item_returns_it_to_draft(session, settings, product):
    item = service.generate_page(session, settings, product, llm=llm(session, settings))
    service.update(session, settings, item, {**PAGE, "bullets": ["fine"]})
    service.decide(session, settings, item, approve=True)
    service.update(session, settings, item, {**PAGE, "bullets": ["still fine"]})
    assert item.status == ContentStatus.DRAFT


def test_ads_three_angles_and_content_ready(session, settings, product):
    page = service.generate_page(session, settings, product, llm=llm(session, settings))
    ads = service.generate_ads(session, settings, product, llm=llm(session, settings))
    assert sorted(a.angle for a in ads) == ["demo", "problem_solution", "social_proof"]
    service.update(session, settings, page, {**PAGE, "bullets": ["ok"]})
    service.decide(session, settings, page, approve=True)
    assert product.status == ProductStatus.SUPPLIER_CHOSEN  # needs an approved ad too
    service.decide(session, settings, ads[0], approve=True)
    assert product.status == ProductStatus.CONTENT_READY


def test_regenerating_supersedes_old_draft(session, settings, product):
    first = service.generate_page(session, settings, product, llm=llm(session, settings))
    settings.llm.effort = "high"  # different cache key, so a fresh call
    second = service.generate_page(session, settings, product, llm=llm(session, settings))
    assert first.status == ContentStatus.SUPERSEDED and second.version == 2
    assert service.current(session, product)["page"].id == second.id
    with pytest.raises(service.ContentError):
        service.update(session, settings, first, PAGE)


def test_rejected_product_cannot_get_content(session, settings, product):
    product.status = ProductStatus.REJECTED
    session.commit()
    with pytest.raises(service.ContentError):
        service.generate_page(session, settings, product, llm=llm(session, settings))


def test_long_seo_fields_flagged(session, settings, product):
    item = service.generate_page(session, settings, product,
                                 llm=llm(session, settings, FakeClaude(page={**PAGE, "seo_title": "x" * 80})))
    assert any(f["rule"] == "length" and f["field"] == "seo_title" for f in item.findings)


def test_form_round_trip():
    item = ContentItem(kind="product_page", data=PAGE)
    assert from_form(item, to_form(item)) == PAGE
    ad = ContentItem(kind="ad_script", angle="demo", data=ADS["scripts"][2])
    assert from_form(ad, to_form(ad)) == ADS["scripts"][2]
    assert parse_faqs("Q: One?\nA: Yes.\n\nQ: Two?\nA: Line one\nline two") == [
        {"question": "One?", "answer": "Yes."}, {"question": "Two?", "answer": "Line one\nline two"}]


def test_dashboard_content_flow(session, settings, product, engine):
    fake = FakeClaude(page=RISKY_PAGE)
    web = TestClient(create_app(settings, engine, llm_factory=lambda s: LLMClient(settings, s, client=fake)))
    hx = {"HX-Request": "true"}
    assert web.post(f"/content/{product.id}/generate/page").status_code == 403
    assert web.post(f"/content/{product.id}/generate/page", headers=hx).status_code == 204
    page = web.get(f"/content/{product.id}").text
    assert '<mark class="sev-high"' in page and "must fix" in page and "disabled" in page
    item = session.scalars(select(ContentItem)).one()
    blocked = web.post(f"/content/item/{item.id}/approve", headers=hx)
    assert blocked.status_code == 409 and "high-risk" in blocked.json()["detail"]
    form = to_form(ContentItem(kind="product_page", data={**PAGE, "bullets": ["Lifts hair"]}))
    assert web.post(f"/content/item/{item.id}/save", headers=hx, data=form).status_code == 204
    assert web.post(f"/content/item/{item.id}/approve", headers=hx).status_code == 204
    assert "approved" in web.get("/content").text
    assert web.get(f"/content/item/{item.id}/history").status_code == 200


def test_dashboard_generate_without_key_shows_message(session, settings, product, engine):
    settings.anthropic_api_key = None
    web = TestClient(create_app(settings, engine))
    r = web.post(f"/content/{product.id}/generate/ads", headers={"HX-Request": "true"})
    assert "Not generated" in r.text and "ANTHROPIC_API_KEY" in r.text


def test_highlight_escapes_html():
    from app.api.content_routes import highlight
    out = highlight("<b>Best</b> roller", [{"field": "t", "rule": "superlative", "match": "Best",
                                             "severity": "medium", "message": "m"}], "t")
    assert "&lt;b&gt;" in out and '<mark class="sev-medium"' in out
