"""Competitor analyser tests against FAKE stores (no real websites are contacted)."""

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.main import create_app
from app.core.http_fetch import DisallowedByRobots, PoliteFetcher
from app.core.models import CompetitorReport, Product
from app.modules.competitor_analyzer import extract, insights, reviews, service

LONG = " Lorem ipsum policy text." * 20  # policy pages must have some substance

NOW = datetime.now(UTC)
PRODUCTS = [
    {"title": "Pet Hair Remover Roller", "handle": "pet-roller", "product_type": "Pet",
     "created_at": (NOW - timedelta(days=10)).isoformat(),
     "variants": [{"price": "14.99", "compare_at_price": "29.99"}]},
    {"title": "Lint roller XL", "handle": "lint-xl", "product_type": "Home",
     "created_at": (NOW - timedelta(days=400)).isoformat(),
     "variants": [{"price": "9.99", "compare_at_price": None}, {"price": "12.99", "compare_at_price": None}]},
    {"title": "Dog bed", "handle": "dog-bed", "product_type": "Pet",
     "created_at": (NOW - timedelta(days=20)).isoformat(),
     "variants": [{"price": "39.00", "compare_at_price": "60.00"}]},
]


def fake_store(pages: dict[str, tuple[int, str, str]]):
    """pages: path(+query) -> (status, content_type, body). Unknown paths 404."""
    calls: list[str] = []

    def handler(request: httpx.Request):
        key = request.url.raw_path.decode()
        calls.append(key)
        status, ctype, body = pages.get(key, (404, "text/html", "not found"))
        return httpx.Response(status, headers={"content-type": ctype}, text=body)

    return httpx.Client(transport=httpx.MockTransport(handler)), calls


def html(body):
    return (200, "text/html; charset=utf-8", f"<html><body>{body}{LONG}</body></html>")


ALLOWED_STORE = {
    "/robots.txt": (200, "text/plain", "User-agent: *\nDisallow: /checkout\n"),
    "/policies/terms-of-service": html("<h1>Terms</h1><p>Be nice. Pay for things.</p>"),
    "/products.json?limit=250&page=1": (200, "application/json", json.dumps({"products": PRODUCTS})),
    "/collections/all?sort_by=best-selling": html('<a href="/products/dog-bed">x</a><a href="/products/pet-roller">y</a>'
                                                  '<a href="/products/dog-bed">dup</a>'),
    "/policies/shipping-policy": html("<p>Free UK delivery on orders over £30. Orders arrive in 7-12 working days."
                                      " Ships from our international warehouse.</p>"),
    "/policies/refund-policy": html("<p>You can return items within 30 days.</p>"),
    "/": html('<script>Shopify.currency = {"active":"GBP","rate":"1.0"};</script>'),
}


def fetcher_for(client, tmp_path):
    return PoliteFetcher("test-agent", tmp_path / "cache", delay_seconds=0, client=client)


# ---------------- polite fetcher ----------------
def test_robots_disallow_blocks_fetch(tmp_path):
    client, calls = fake_store({"/robots.txt": (200, "text/plain", "User-agent: *\nDisallow: /private\n")})
    f = fetcher_for(client, tmp_path)
    with pytest.raises(DisallowedByRobots):
        f.fetch("https://shop.test/private/page")
    assert calls == ["/robots.txt"]  # never requested the disallowed page


def test_missing_robots_means_allowed_and_cache_used(tmp_path):
    client, calls = fake_store({"/a": (200, "text/html", "hello")})
    f = fetcher_for(client, tmp_path)
    assert f.fetch("https://shop.test/a").text == "hello"
    again = f.fetch("https://shop.test/a")
    assert again.from_cache and calls.count("/a") == 1


def test_rate_limit_waits_between_requests(tmp_path):
    client, _ = fake_store({"/a": (200, "text/html", "a"), "/b": (200, "text/html", "b")})
    waits = []
    f = PoliteFetcher("ua", tmp_path / "c", delay_seconds=5, client=client, sleep=waits.append)
    f.fetch("https://shop.test/a")
    f.fetch("https://shop.test/b")
    assert waits and all(0 < w <= 5 for w in waits)


def test_honest_user_agent_sent(tmp_path):
    seen = []
    client = httpx.Client(transport=httpx.MockTransport(lambda r: seen.append(r.headers["user-agent"]) or httpx.Response(404)))
    PoliteFetcher("my-honest-agent", tmp_path, delay_seconds=0, client=client).fetch("https://shop.test/x")
    assert set(seen) == {"my-honest-agent"}


# ---------------- extraction ----------------
def test_terms_detection():
    assert extract.terms_forbid_automation("<p>You may not spider, crawl, or scrape the site.</p>") == ["crawl", "scrape", "spider"]
    assert extract.terms_forbid_automation("<p>No automated means may be used.</p>") == ["automated means"]
    assert extract.terms_forbid_automation("<p>We sell robot vacuums and scrapers? no: scrapbooks.</p>") == []


def test_policy_facts():
    f = extract.policy_facts("Free delivery on orders over £25.00. Royal Mail 2-3 working days.",
                             "Returns accepted within 14 days of delivery.")
    assert f == {"free_shipping_threshold_gbp": 25.0, "delivery_days_min": 2, "delivery_days_max": 3,
                 "return_window_days": 14, "ships_from_uk": True}
    assert extract.policy_facts("Free UK shipping on all orders")["free_shipping_threshold_gbp"] == 0.0
    assert extract.policy_facts("", "") == {}


def test_catalogue_facts_store_numbers_not_titles():
    f = extract.catalogue_facts(PRODUCTS, now=NOW)
    assert f["product_count"] == 3
    assert (f["price_min"], f["price_median"], f["price_max"]) == (9.99, 14.99, 39.0)
    assert f["discounted_share"] == pytest.approx(2 / 3, abs=0.001)
    assert f["new_last_90_days"] == 2
    assert "Pet Hair Remover Roller" not in json.dumps(f)


def test_overlap_with_our_products():
    o = extract.keyword_overlap(PRODUCTS, {"My roller": ["lint roller", "pet hair remover"], "Mug": ["mug"]})
    assert o == {"My roller": {"matches": 2, "price_median": 12.49, "price_min": 9.99, "price_max": 14.99}}


def test_bestseller_handles_in_order_deduplicated():
    assert extract.bestseller_handles('<a href="/products/b-1">x</a><a href="/products/a">y</a><a href="/products/b-1">')== ["b-1", "a"]


# ---------------- insights & reviews ----------------
def test_insights_rules():
    ins = insights.build({"price_median": 12, "price_min": 5, "price_max": 30, "product_count": 400,
                          "discounted_share": 0.8, "avg_discount_pct": 45, "delivery_days_max": 14,
                          "return_window_days": 7, "ships_from_uk": False,
                          "overlap": {"Roller": {"matches": 3, "price_median": 12.0, "price_min": 9, "price_max": 15}}},
                         None, {"Roller": 15.0})
    gaps = " ".join(ins["gaps"])
    assert "Broad general store" in gaps and "UK pricing rules" in gaps and "Slow delivery" in gaps
    assert "below the 14-day" in gaps and "ship from abroad" in gaps and "No free-delivery" in gaps
    assert any("Budget positioning" in x for x in ins["does_well"])
    assert ins["vs_your_products"] == ["They sell 3 item(s) like your 'Roller' at a typical £12.00 (£9.00-£15.00). "
                                       "Your planned £15.00 is £3.00 above theirs."]


def test_review_themes_counts_only():
    text = ("Took 3 weeks to arrive, really slow delivery. Disappointed.\n\n"
            "Great quality, love it!\n\n"
            "Broke after two days, poor quality, want a refund.\n\n"
            "Fast delivery and great value")
    r = reviews.themes(text)
    assert r["reviews_analysed"] == 4 and r["negative_reviews"] == 2
    assert r["themes"]["delivery speed"] == {"positive": 1, "negative": 1}
    assert r["themes"]["broke / faulty"]["negative"] == 1
    assert "Disappointed" not in json.dumps(r)


# ---------------- service ----------------
def test_allowed_store_collects_facts_only(session, settings, tmp_path):
    session.add(Product(name="Pet hair remover", score_details={"trends_term": "pet hair remover"}))
    session.commit()
    client, calls = fake_store(ALLOWED_STORE)
    r = service.analyse(session, settings, "shop.test", fetcher=fetcher_for(client, tmp_path))
    assert r.status == "allowed" and r.platform == "Shopify"
    f = r.facts
    assert f["product_count"] == 3 and f["free_shipping_threshold_gbp"] == 30.0
    assert f["delivery_days_max"] == 12 and f["return_window_days"] == 30 and f["ships_from_uk"] is False
    assert [b["handle"] for b in f["bestsellers"]] == ["dog-bed", "pet-roller"]
    assert f["bestsellers"][0]["price"] == 39.0
    assert f["overlap"]["Pet hair remover"]["matches"] == 1
    assert "/robots.txt" in calls and "/checkout" not in calls
    stored = json.dumps({"a": r.auto_facts, "i": r.insights})
    assert "Pet Hair Remover Roller" not in stored and "Lorem ipsum" not in stored  # no copied text
    assert len(r.sources) >= 5


def test_terms_forbidding_scraping_stop_everything(session, settings, tmp_path):
    store = {**ALLOWED_STORE, "/policies/terms-of-service": html("<p>You may not spider, crawl or scrape this site.</p>")}
    client, calls = fake_store(store)
    r = service.analyse(session, settings, "https://shop.test", fetcher=fetcher_for(client, tmp_path))
    assert r.status == "forbidden" and "scrape" in r.status_reason
    assert not any(c.startswith("/products.json") for c in calls)
    assert r.auto_facts is None


def test_robots_disallow_all_stops(session, settings, tmp_path):
    client, calls = fake_store({"/robots.txt": (200, "text/plain", "User-agent: *\nDisallow: /\n")})
    r = service.analyse(session, settings, "shop.test", fetcher=fetcher_for(client, tmp_path))
    assert r.status == "forbidden" and "robots.txt" in r.status_reason
    assert calls == ["/robots.txt"]


def test_terms_not_found_asks_you_first(session, settings, tmp_path):
    store = {k: v for k, v in ALLOWED_STORE.items() if "terms" not in k}
    client, calls = fake_store(store)
    r = service.analyse(session, settings, "shop.test", fetcher=fetcher_for(client, tmp_path))
    assert r.status == "needs_terms_check"
    assert not any(c.startswith("/products.json") for c in calls)
    r = service.analyse(session, settings, "shop.test", terms_checked_by_you=True, fetcher=fetcher_for(client, tmp_path))
    assert r.status == "allowed"
    assert session.scalars(select(CompetitorReport)).all() == [r]  # same report updated, not duplicated


def test_unreachable_store_is_error_not_crash(session, settings, tmp_path):
    def boom(request):
        raise httpx.ConnectError("no route")
    client = httpx.Client(transport=httpx.MockTransport(boom))
    r = service.analyse(session, settings, "shop.test", fetcher=fetcher_for(client, tmp_path))
    assert r.status == "error" and "Couldn't reach" in r.status_reason


def test_manual_facts_and_reviews(session, settings, tmp_path):
    client, _ = fake_store({"/robots.txt": (200, "text/plain", "User-agent: *\nDisallow: /\n")})
    r = service.analyse(session, settings, "shop.test", fetcher=fetcher_for(client, tmp_path))
    problems = service.save_manual(session, settings, r, {"price_median": "£19.99", "return_window_days": "7",
                                                         "discounted_share": "60", "ships_from_uk": "maybe",
                                                         "delivery_days_max": "-3"}, notes="Uses UGC-style ads")
    assert len(problems) == 2  # 'maybe' and negative days rejected, not guessed
    assert r.facts["price_median"] == 19.99 and r.facts["discounted_share"] == 0.6
    assert r.status == "forbidden"  # still remembered as forbidden
    assert any("below the 14-day" in g for g in r.insights["gaps"])
    service.add_reviews(session, settings, r, "Slow delivery, never arrived on time\nterrible, broke")
    assert any("complain" in g for g in r.insights["gaps"])
    with pytest.raises(service.CompetitorError):
        service.add_reviews(session, settings, r, "  ")


def test_ai_summary_gets_facts_only(session, settings, tmp_path):
    from app.core.llm_client import LLMClient
    from tests.test_llm_client import FakeAnthropic

    client, _ = fake_store(ALLOWED_STORE)
    r = service.analyse(session, settings, "shop.test", fetcher=fetcher_for(client, tmp_path))
    fake = FakeAnthropic(text="Budget general store with slow overseas delivery.")
    service.ai_summary(session, settings, r, llm=LLMClient(settings, session, client=fake))
    assert r.summary.startswith("Budget")
    prompt = fake.calls[0]["messages"][0]["content"]
    assert "Pet Hair Remover Roller" not in prompt and "Lorem ipsum" not in prompt


def test_bad_urls_rejected():
    for bad in ("", "ftp://x.com", "nodot"):
        with pytest.raises(service.CompetitorError):
            service.normalise_url(bad)
    assert service.normalise_url("WWW.Shop.co.uk/some/page") == ("https://www.shop.co.uk", "shop.co.uk")


# ---------------- dashboard ----------------
def test_dashboard_competitor_flow(session, settings, engine, tmp_path):
    client, _ = fake_store(ALLOWED_STORE)
    app = create_app(settings, engine, fetcher_factory=lambda: fetcher_for(client, tmp_path))
    web = TestClient(app)
    hx = {"HX-Request": "true"}
    assert web.post("/competitors", data={"url": "shop.test"}).status_code == 403
    r = web.post("/competitors", headers=hx, data={"url": "shop.test"})
    assert r.status_code == 204 and r.headers["HX-Redirect"].startswith("/competitors/")
    page = web.get(r.headers["HX-Redirect"]).text
    assert "Gaps you could fill" in page and "Free delivery over £30.00" in page
    assert "shop.test" in web.get("/competitors").text
    rid = r.headers["HX-Redirect"].rsplit("/", 1)[1]
    assert web.post(f"/competitors/{rid}/manual", headers=hx, data={"price_median": "abc"}).text.count("isn") == 1
    assert web.post(f"/competitors/{rid}/reviews", headers=hx, data={"reviews": "Slow delivery, poor"}).status_code == 204
    summary = web.post(f"/competitors/{rid}/summary", headers=hx)  # no API key in tests
    assert "AI summary not created" in summary.text
    assert web.post("/competitors", headers=hx, data={"url": "nodot"}).status_code == 422
