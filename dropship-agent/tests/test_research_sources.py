import json
from datetime import UTC, datetime

import httpx
import pytest

from app.modules.product_research.sources import (
    MetaAdLibrary,
    SourceError,
    load_candidates,
    load_trends,
    parse_trends_csv,
    trend_signals,
)


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def trends_csv(terms: dict[str, list]) -> str:
    """Build a file in Google Trends' export format."""
    header = "Week," + ",".join(f"{t}: (United Kingdom)" for t in terms)
    n = len(next(iter(terms.values())))
    rows = [f"2025-{(i // 4) % 12 + 1:02d}-{i % 4 * 7 + 1:02d}," + ",".join(str(v[i]) for v in terms.values())
            for i in range(n)]
    return "Category: All categories\n\n" + header + "\n" + "\n".join(rows) + "\n"


# ---------------- candidates.csv ----------------
def test_candidates_parsed_with_sources(tmp_path):
    f = write(tmp_path / "c.csv",
              "name,category,keywords,est_unit_cost_gbp,est_sell_price_gbp,competitor_stores,source_note\n"
              "Pet roller,pet,pet hair remover;lint roller,3.20,£17.99,12,CJ 16/11\n")
    cands, problems = load_candidates(f)
    assert problems == []
    c = cands[0]
    assert c.search_terms == ["pet hair remover", "lint roller"]
    values = {s.metric: s.value for s in c.signals}
    assert values == {"unit_cost_gbp": 3.2, "sell_price_gbp": 17.99, "competitor_stores": 12}
    assert all("CJ 16/11" in s.source for s in c.signals)


def test_blank_is_unknown_and_bad_values_reported(tmp_path):
    f = write(tmp_path / "c.csv", "name,est_unit_cost_gbp,est_sell_price_gbp\nThing,,abc\n")
    cands, problems = load_candidates(f)
    assert cands[0].signals == []
    assert any("not a number" in p for p in problems)


def test_example_and_duplicate_rows_skipped(tmp_path):
    f = write(tmp_path / "c.csv", "name\nEXAMPLE thing\nWidget\nwidget \n")
    cands, problems = load_candidates(f)
    assert [c.name for c in cands] == ["Widget"]
    assert len(problems) == 2


def test_missing_candidates_file_is_clear_error(tmp_path):
    with pytest.raises(SourceError, match="candidates.example.csv"):
        load_candidates(tmp_path / "nope.csv")


# ---------------- Google Trends ----------------
def test_parse_trends_export(tmp_path):
    f = write(tmp_path / "t.csv", trends_csv({"Cat Bed": [10, "<1", 30, 40], "dog bed": [5, 5, 5, 5]}))
    series = {s.term: s for s in parse_trends_csv(f)}
    assert series["cat bed"].values == [10, 0.5, 30, 40]
    assert series["cat bed"].geo == "United Kingdom"


def test_trend_signals_need_enough_points(tmp_path):
    f = write(tmp_path / "t.csv", trends_csv({"x": [50] * 4}))
    names = {s.metric for s in trend_signals(parse_trends_csv(f)[0], datetime.now(UTC))}
    assert names == {"search_interest"}  # too short for trend or seasonality


def test_trend_change_and_seasonality(tmp_path):
    values = [20] * 40 + [40] * 12  # 52 weeks, rising at the end
    f = write(tmp_path / "t.csv", trends_csv({"x": values}))
    sig = {s.metric: s.value for s in trend_signals(parse_trends_csv(f)[0], datetime.now(UTC))}
    assert sig["search_interest"] == 40
    assert sig["trend_change_pct"] == 100.0
    assert sig["seasonality_ratio"] == pytest.approx(40 / (sum(values) / 52), abs=0.01)


def test_non_trends_file_reported_not_crashing(tmp_path):
    write(tmp_path / "trends" / "bad.csv", "hello,world\n1,2\n")
    found, problems = load_trends(tmp_path / "trends")
    assert found == {} and "doesn't look like" in problems[0]


# ---------------- Meta Ad Library (mocked: no real calls) ----------------
def meta_client(pages: list[dict], status=200):
    calls = []

    def handler(request: httpx.Request):
        calls.append(request)
        idx = len(calls) - 1
        return httpx.Response(status, json=pages[idx] if status == 200 else {"error": "bad token"})

    return httpx.Client(transport=httpx.MockTransport(handler)), calls


def test_meta_counts_ads_advertisers_and_copies():
    page1 = {"data": [{"page_id": "1", "ad_creative_bodies": ["Buy now"]},
                      {"page_id": "2", "ad_creative_bodies": ["Buy now"]},
                      {"page_id": "2", "ad_creative_bodies": ["Different text"]}],
             "paging": {"next": "https://graph.facebook.com/next"}}
    page2 = {"data": [{"page_id": "3", "ad_creative_bodies": ["Another"]}]}
    client, calls = meta_client([page1, page2])
    m = MetaAdLibrary("tok", "v21.0", ["IE"], max_pages=5, delay_seconds=0, client=client)
    sig = {s.metric: s for s in m.signals_for("cat bed")}
    assert sig["meta_ads_active"].value == 4
    assert sig["meta_advertisers"].value == 3
    assert sig["meta_duplicate_creative_share"].value == 0.25
    assert "EU used as UK proxy" in sig["meta_ads_active"].source
    first = calls[0].url.params
    assert first["search_terms"] == "cat bed" and json.loads(first["ad_reached_countries"]) == ["IE"]


def test_meta_page_limit_marks_counts_as_minimum():
    page = {"data": [{"page_id": "1"}], "paging": {"next": "https://graph.facebook.com/next"}}
    client, calls = meta_client([page, page, page])
    m = MetaAdLibrary("tok", "v21.0", ["IE"], max_pages=2, delay_seconds=0, client=client)
    sig = {s.metric: s for s in m.signals_for("x")}
    assert len(calls) == 2
    assert "At least" in sig["meta_ads_active"].note


def test_meta_no_ads_is_real_zero():
    client, _ = meta_client([{"data": []}])
    sig = MetaAdLibrary("tok", "v21.0", ["IE"], delay_seconds=0, client=client).signals_for("x")
    assert sig[0].metric == "meta_ads_active" and sig[0].value == 0


def test_meta_error_raises():
    client, _ = meta_client([{}], status=400)
    with pytest.raises(SourceError, match="400"):
        MetaAdLibrary("tok", "v21.0", ["IE"], delay_seconds=0, client=client).signals_for("x")
