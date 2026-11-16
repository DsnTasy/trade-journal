"""End-to-end research runs against a temporary database and files. No real API calls."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.api.main import create_app
from app.core.models import Metric, Product, ProductStatus
from app.modules.product_research import run_research
from tests.test_llm_client import FakeAnthropic
from tests.test_research_sources import trends_csv, write

from app.core.llm_client import LLMClient

CANDIDATES = (
    "name,category,keywords,est_unit_cost_gbp,est_shipping_gbp,est_sell_price_gbp,competitor_stores,ads_seen,source_note\n"
    "Pet hair roller,pet,pet hair remover,3,2,18,5,2,CJ 16/11\n"
    "Plain mug,home,plain mug,4,3,8,60,40,CJ 16/11\n"
    "Kids slime toy,toys,slime,1,1,10,5,1,AliExpress 16/11\n"
)


@pytest.fixture
def research_settings(settings, tmp_path):
    settings.research.candidates_file = str(write(tmp_path / "candidates.csv", CANDIDATES))
    settings.research.trends_dir = str(tmp_path / "trends")
    settings.research.avoid_categories = ["toys"]
    write(tmp_path / "trends" / "uk.csv",
          trends_csv({"pet hair remover": [30] * 12 + [60] * 12, "plain mug": [40] * 12 + [20] * 12}))
    return settings


def test_ranks_and_explains(session, research_settings):
    report = run_research(session, research_settings)
    names = [r.name for r in report.rows]
    assert names[0] == "Pet hair roller"
    assert names[-1] == "Kids slime toy"  # avoided category goes last
    assert report.rows[-1].excluded == ["toys"]
    mug = next(r for r in report.rows if r.name == "Plain mug")
    assert any("falling" in s for s in mug.reasons_against)
    assert any("compliance review" in s for s in mug.reasons_against)  # mug = food contact
    assert "Google Trends CSV exports" in report.sources_used


def test_every_metric_has_source_and_date(session, research_settings):
    run_research(session, research_settings)
    metrics = session.scalars(select(Metric)).all()
    assert metrics
    assert all(m.source and m.retrieved_at for m in metrics)
    assert any("Google Trends" in m.source for m in metrics)


def test_rerun_updates_not_duplicates_and_keeps_status(session, research_settings):
    run_research(session, research_settings)
    p = session.scalars(select(Product).where(Product.name == "Pet hair roller")).one()
    p.status = ProductStatus.SHORTLISTED
    session.commit()
    run_research(session, research_settings)
    assert session.scalar(select(func.count()).select_from(Product)) == 3
    assert session.get(Product, p.id).status == ProductStatus.SHORTLISTED


def test_missing_trends_reported(session, research_settings, tmp_path):
    research_settings.research.trends_dir = str(tmp_path / "empty")
    report = run_research(session, research_settings)
    assert any("No Google Trends exports" in p for p in report.problems)
    assert all(r.confidence < 1 for r in report.rows)


def test_meta_enabled_without_token_is_reported(session, research_settings):
    research_settings.research.meta_enabled = True
    research_settings.meta_ad_library_token = None
    report = run_research(session, research_settings)
    assert any("META_AD_LIBRARY_TOKEN" in p for p in report.problems)


def test_ai_summaries_for_top_n_only(session, research_settings):
    fake = FakeAnthropic(text="Rising interest and a healthy margin. Order a sample first.")
    llm = LLMClient(research_settings, session, client=fake)
    report = run_research(session, research_settings, explain_with_ai=1, llm=llm)
    assert report.rows[0].summary.startswith("Rising interest")
    assert report.rows[1].summary is None
    assert len(fake.calls) == 1
    assert "Use ONLY the data given" in fake.calls[0]["system"]


def test_ai_summary_without_key_is_skipped_with_message(session, research_settings):
    research_settings.anthropic_api_key = None
    report = run_research(session, research_settings, explain_with_ai=3)
    assert all(r.summary is None for r in report.rows)
    assert any("ANTHROPIC_API_KEY" in p for p in report.problems)


def test_dashboard_opportunities_and_shortlist(session, research_settings, engine):
    run_research(session, research_settings)
    client = TestClient(create_app(research_settings, engine))
    page = client.get("/products").text
    assert page.index("Pet hair roller") < page.index("Plain mug") < page.index("Kids slime toy")
    assert "avoided: toys" in page

    p = session.scalars(select(Product).where(Product.name == "Pet hair roller")).one()
    detail = client.get(f"/products/{p.id}").text
    assert "Google Trends CSV export" in detail and "CJ 16/11" in detail
    assert "Earlier values" not in detail
    run_research(session, research_settings)  # second run: older values move into history
    assert "Earlier values" in client.get(f"/products/{p.id}").text

    assert client.post(f"/products/{p.id}/status/shortlisted").status_code == 403  # needs dashboard button
    resp = client.post(f"/products/{p.id}/status/shortlisted", headers={"HX-Request": "true"})
    assert resp.status_code == 200 and "shortlisted" in resp.text
    session.expire_all()
    assert session.get(Product, p.id).status == ProductStatus.SHORTLISTED

    assert client.post(f"/products/{p.id}/status/published", headers={"HX-Request": "true"}).status_code == 404
