"""Store publisher tests against a FAKE Shopify (no real store is contacted)."""

import json

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.main import create_app
from app.core import approvals
from app.core.models import (ActionType, ApprovalStatus, ContentItem, ContentStatus, MediaAsset, Product,
                             ProductStatus, Supplier)
from app.modules.store_publisher import listing as L
from app.modules.store_publisher import service
from app.modules.store_publisher.shopify import ShopifyClient, ShopifyError, ShopifyNotConfigured

PAGE = {"title": "Reusable Pet Hair Roller", "short_description": "Lifts pet hair <fast>.",
        "bullets": ["Rinse & reuse"], "description": "Para one.\n\nDelivered in 2-4 working days.",
        "faqs": [{"question": "Delivery?", "answer": "2-4 working days."}], "seo_title": "Pet Hair Roller",
        "meta_description": "Lifts pet hair.", "tags": ["Pets", "cleaning"]}


class FakeShopify:
    """Answers the GraphQL operations our client uses, and records every call."""

    def __init__(self, throttle_first=False, user_error=None):
        self.calls, self.products, self.throttle_first, self.user_error = [], {}, throttle_first, user_error
        self.uploads = 0

    def __call__(self, request: httpx.Request):
        url = str(request.url)
        if url.endswith("/admin/oauth/access_token"):
            self.calls.append("token")
            return httpx.Response(200, json={"access_token": "tok", "scope": "write_products", "expires_in": 86399})
        if url.startswith("https://upload.test/"):
            self.uploads += 1
            return httpx.Response(204)
        assert request.headers["X-Shopify-Access-Token"] == "tok"
        body = json.loads(request.content)
        q, v = body["query"], body["variables"]
        op = q.split("(")[0].split()[-1] if q.lstrip().startswith("mutation") else q.split("{")[1].split("(")[0].strip()
        self.calls.append(op)
        if self.throttle_first:
            self.throttle_first = False
            return httpx.Response(200, json={"errors": [{"message": "Throttled", "extensions": {"code": "THROTTLED"}}]})
        if self.user_error and op == self.user_error:
            return httpx.Response(200, json={"data": {op: {"userErrors": [{"field": ["title"], "message": "is bad"}]}}})
        if op == "shop":
            return httpx.Response(200, json={"data": {"shop": {"name": "Test Shop"}}})
        if op == "productCreate":
            gid = f"gid://shopify/Product/{100 + len(self.products)}"
            self.products[gid] = {**v["product"], "media": v["media"] or []}
            return httpx.Response(200, json={"data": {"productCreate": {"userErrors": [], "product": {
                "id": gid, "handle": v["product"]["handle"], "status": v["product"]["status"],
                "variants": {"nodes": [{"id": gid.replace("Product", "ProductVariant")}]}}}}})
        if op == "productUpdate":
            gid = v["product"]["id"]
            self.products[gid].update({k: x for k, x in v["product"].items() if k != "id"})
            self.products[gid]["media"] += v["media"] or []
            return httpx.Response(200, json={"data": {"productUpdate": {"userErrors": [], "product": {
                "id": gid, "handle": self.products[gid]["handle"], "status": self.products[gid]["status"],
                "variants": {"nodes": [{"id": gid.replace("Product", "ProductVariant")}]}}}}})
        if op == "productVariantsBulkUpdate":
            self.products[v["productId"]]["variant"] = v["variants"][0]
            return httpx.Response(200, json={"data": {"productVariantsBulkUpdate": {"userErrors": [], "productVariants": []}}})
        if op == "publications":
            return httpx.Response(200, json={"data": {"publications": {"nodes": [
                {"id": "gid://shopify/Publication/1", "catalog": {"title": "Point of Sale"}},
                {"id": "gid://shopify/Publication/2", "catalog": {"title": "Online Store"}}]}}})
        if op == "publishablePublish":
            self.products[v["id"]]["published_to"] = v["input"][0]["publicationId"]
            return httpx.Response(200, json={"data": {"publishablePublish": {"userErrors": []}}})
        if op == "stagedUploadsCreate":
            return httpx.Response(200, json={"data": {"stagedUploadsCreate": {"userErrors": [], "stagedTargets": [
                {"url": "https://upload.test/bucket", "resourceUrl": f"https://upload.test/r/{v['input'][0]['filename']}",
                 "parameters": [{"name": "key", "value": "abc"}]}]}}})
        raise AssertionError(f"unexpected operation {op}")


def client_for(fake: FakeShopify, **kw) -> ShopifyClient:
    return ShopifyClient("test-shop", "2026-10", client_id="id", client_secret="secret", sleep=lambda s: None,
                         http=httpx.Client(transport=httpx.MockTransport(fake)), **kw)


@pytest.fixture
def shop_settings(settings, tmp_path):
    settings.store.shop = "test-shop"
    settings.shopify_client_id, settings.shopify_client_secret = "id", "secret"
    settings.video.media_dir = str(tmp_path / "media")
    return settings


@pytest.fixture
def product(session, tmp_path):
    p = Product(name="Pet roller", category="pet", status=ProductStatus.CONTENT_READY, compliance_flags=[],
                profit_overrides={"sell_price_gbp": 17.99})
    session.add(p)
    session.flush()
    s = Supplier(product_id=p.id, name="UK", source="t", shipping_days_min=2, shipping_days_max=4, warehouse="UK")
    session.add(s)
    session.flush()
    p.chosen_supplier_id = s.id
    session.add(ContentItem(product_id=p.id, kind="product_page", data=PAGE, status=ContentStatus.APPROVED))
    img = tmp_path / "media" / "pet-roller" / "photo.jpg"
    img.parent.mkdir(parents=True)
    img.write_bytes(b"\xff\xd8fakejpeg")
    session.add(MediaAsset(product_id=p.id, kind="image", path="pet-roller/photo.jpg", licence="self_shot"))
    session.commit()
    return p


# ---------------- client ----------------
def test_client_needs_configuration():
    with pytest.raises(ShopifyNotConfigured, match="store.shop"):
        ShopifyClient("", "2026-10", access_token="x")
    with pytest.raises(ShopifyNotConfigured, match="SHOPIFY_CLIENT_ID"):
        ShopifyClient("shop", "2026-10")


def test_token_exchanged_once_and_cached():
    fake = FakeShopify()
    c = client_for(fake)
    c.shop_name()
    c.shop_name()
    assert fake.calls.count("token") == 1


def test_legacy_token_skips_exchange():
    fake = FakeShopify()
    c = ShopifyClient("test-shop.myshopify.com", "2026-10", access_token="tok",
                      http=httpx.Client(transport=httpx.MockTransport(fake)))
    assert c.shop_name() == "Test Shop" and "token" not in fake.calls
    assert c.endpoint == "https://test-shop.myshopify.com/admin/api/2026-10/graphql.json"


def test_throttled_request_is_retried():
    fake = FakeShopify(throttle_first=True)
    assert client_for(fake).shop_name() == "Test Shop"
    assert fake.calls.count("shop") == 2


def test_user_errors_raise():
    fake = FakeShopify(user_error="productCreate")
    with pytest.raises(ShopifyError, match="title is bad"):
        client_for(fake).create_product({"title": "x"}, [])


def test_missing_scope_message():
    c = ShopifyClient("s", "2026-10", access_token="tok",
                      http=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(403))))
    with pytest.raises(ShopifyError, match="write_products"):
        c.shop_name()


# ---------------- listing builder ----------------
def test_description_is_escaped_and_has_returns():
    html = L.description_html(PAGE, 14)
    assert "&lt;fast&gt;" in html and "Rinse &amp; reuse" in html and "<li>" in html
    assert "Returns accepted within 14 days." in html


def test_product_and_variant_inputs():
    p = L.product_input("Pet roller", "pet", PAGE, vendor="My Shop", returns_days=14)
    assert p["tags"] == ["cleaning", "pets"] and p["handle"] == "reusable-pet-hair-roller" and p["productType"] == "Pet"
    v = L.variant_input("gid://v/1", 17.999, track_inventory=False)
    assert v["price"] == "18.00" and "compareAtPrice" not in v and v["inventoryPolicy"] == "CONTINUE"


# ---------------- service ----------------
def test_dry_run_sends_nothing(session, shop_settings, product):
    fake = FakeShopify()
    result = service.send_draft(session, shop_settings, product, client_for(fake))
    assert result["action"] == "dry_run" and result["would"] == "create" and fake.calls == []
    assert service.get_listing(session, product).remote_id is None


def test_live_create_then_unchanged_then_update(session, shop_settings, product):
    shop_settings.dry_run = False
    fake = FakeShopify()
    c = client_for(fake)
    assert service.send_draft(session, shop_settings, product, c)["action"] == "created"
    listing = service.get_listing(session, product)
    gid = listing.remote_id
    created = fake.products[gid]
    assert created["status"] == "DRAFT" and created["variant"]["price"] == "17.99"
    assert len(created["media"]) == 1 and fake.uploads == 1

    calls = len(fake.calls)
    assert service.send_draft(session, shop_settings, product, c)["action"] == "unchanged"
    assert len(fake.calls) == calls  # nothing sent

    page = session.scalars(select(ContentItem)).one()
    page.data = {**PAGE, "title": "Reusable Pet Hair Roller (Large)"}
    session.commit()
    assert service.send_draft(session, shop_settings, product, c)["action"] == "updated"
    assert len(fake.products) == 1 and service.get_listing(session, product).remote_id == gid  # no duplicate
    assert fake.products[gid]["title"].endswith("(Large)") and fake.products[gid]["handle"] == "reusable-pet-hair-roller"
    assert fake.uploads == 1  # image not uploaded twice


def test_blockers_stop_sending(session, shop_settings, product):
    product.compliance_flags = [{"flag": "electrical", "message": "Needs UKCA", "matched": ["usb"]}]
    session.commit()
    with pytest.raises(service.PublishError, match="compliance"):
        service.send_draft(session, shop_settings, product)
    service.confirm_compliance(session, shop_settings, product)
    page = session.scalars(select(ContentItem)).one()
    page.data = {**PAGE, "bullets": ["Only 3 left!"]}
    session.commit()
    with pytest.raises(service.PublishError, match="high-risk"):
        service.send_draft(session, shop_settings, product)
    page.status = ContentStatus.DRAFT
    session.commit()
    assert any("Approve a product page" in b for b in service.readiness(session, shop_settings, product)["blockers"])


def test_full_publish_flow_needs_approval(session, shop_settings, product):
    shop_settings.dry_run = False
    fake = FakeShopify()
    service.register(shop_settings, client_factory=lambda s: client_for(fake))
    service.send_draft(session, shop_settings, product, client_for(fake))
    req = service.request_publish(session, shop_settings, product)
    assert req.status == ApprovalStatus.PENDING and req.action_type == ActionType.PUBLISH_PRODUCT
    assert service.request_publish(session, shop_settings, product).id == req.id  # no duplicate requests

    with pytest.raises(approvals.ApprovalError):
        approvals.execute(session, req.id, dry_run=False)  # not approved yet
    approvals.decide(session, req.id, approve=True, dry_run=False)
    assert product.status == ProductStatus.APPROVED

    approvals.execute(session, req.id, dry_run=True)  # dry run: nothing happens
    gid = service.get_listing(session, product).remote_id
    assert fake.products[gid]["status"] == "DRAFT"

    approvals.execute(session, req.id, dry_run=False)
    assert fake.products[gid]["status"] == "ACTIVE"
    assert fake.products[gid]["published_to"] == "gid://shopify/Publication/2"  # the Online Store
    assert product.status == ProductStatus.PUBLISHED and req.status == ApprovalStatus.EXECUTED
    assert req.result["url"].startswith("https://admin.shopify.com/store/test-shop/products/")

    service.unpublish(session, shop_settings, product, client_for(fake))
    assert fake.products[gid]["status"] == "DRAFT" and product.status == ProductStatus.CONTENT_READY


def test_live_publish_request_needs_draft_first(session, shop_settings, product):
    shop_settings.dry_run = False
    with pytest.raises(service.PublishError, match="draft first"):
        service.request_publish(session, shop_settings, product)


# ---------------- dashboard ----------------
def test_dashboard_store_flow_dry_run(session, shop_settings, product, engine):
    fake = FakeShopify()
    web = TestClient(create_app(shop_settings, engine, shopify_factory=lambda s: client_for(fake)))
    hx = {"HX-Request": "true"}
    page = web.get("/store").text
    assert "Pet roller" in page and "Send to Shopify as draft (dry run)" in page
    r = web.post(f"/store/{product.id}/draft", headers=hx)
    assert "Dry run: would create a DRAFT product" in r.text and fake.calls == []
    assert web.post(f"/store/{product.id}/request-publish", headers=hx).status_code == 204
    req = session.scalars(select(approvals.ApprovalRequest)).one()
    assert web.post(f"/approvals/{req.id}/approve", headers=hx).status_code == 200
    run = web.post(f"/approvals/{req.id}/run", headers=hx)
    assert "Dry run: would now run" in run.text and fake.calls == []
    assert "Connected to &#39;Test Shop&#39;" in web.post("/store/test-connection", headers=hx).text
    assert web.post(f"/store/{product.id}/draft").status_code == 403
