"""Shopify publishing with approval gates.

  1. "Send as draft"  → creates/updates a DRAFT product on Shopify (hidden from customers).
                        Same product again = update, never a duplicate (sync state in StoreListing).
  2. "Request publish" → creates an approval request (Approval queue).
  3. You click Approve, then Run → product set ACTIVE and published to the Online Store.
     In dry-run mode, steps 1 and 3 only log what they would do.
"""

from __future__ import annotations

import mimetypes
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, object_session

from app.core import approvals
from app.core.config import Settings
from app.core.logger import log_event
from app.core.models import (
    ActionType,
    ApprovalRequest,
    ApprovalStatus,
    ContentItem,
    ContentStatus,
    MediaAsset,
    Product,
    ProductStatus,
    StoreListing,
)
from app.modules.content_generator import claim_checker
from app.modules.content_generator.service import build_facts, load_brand
from app.modules.store_publisher import listing as L
from app.modules.store_publisher.shopify import ShopifyClient, ShopifyError


class PublishError(RuntimeError):
    pass


def make_client(settings: Settings) -> ShopifyClient:
    s = settings.store
    return ShopifyClient(s.shop, s.api_version, client_id=settings.shopify_client_id,
                         client_secret=settings.shopify_client_secret, access_token=settings.shopify_access_token,
                         timeout=s.timeout_seconds)


def admin_url(settings: Settings, remote_id: str | None) -> str | None:
    if not remote_id or not settings.store.shop:
        return None
    return f"https://admin.shopify.com/store/{settings.store.shop}/products/{remote_id.rsplit('/', 1)[-1]}"


def approved_page(session: Session, product: Product) -> ContentItem | None:
    return session.scalars(select(ContentItem).where(
        ContentItem.product_id == product.id, ContentItem.kind == "product_page",
        ContentItem.status == ContentStatus.APPROVED).order_by(ContentItem.version.desc())).first()


def get_listing(session: Session, product: Product) -> StoreListing:
    listing = session.scalars(select(StoreListing).where(StoreListing.product_id == product.id)).first()
    if listing is None:
        listing = StoreListing(product_id=product.id)
        session.add(listing)
        session.flush()
    return listing


def readiness(session: Session, settings: Settings, product: Product) -> dict:
    """What stops this product being listed, and what you should know."""
    from app.modules.supplier_finder.service import product_sell_price

    blockers, warnings = [], []
    page = approved_page(session, product)
    if page is None:
        blockers.append("Approve a product page first (Content).")
    else:
        _, ctx = build_facts(session, settings, product)
        high = claim_checker.blocking(claim_checker.check_content(page.data, ctx))
        if high:
            blockers.append(f"The approved page now has a high-risk claim (\"{high[0]['match']}\"). Edit it on Content.")
    price, _ = product_sell_price(session, product)
    if price is None:
        blockers.append("No sell price: set it in the profit calculator.")
    if not product.chosen_supplier_id:
        blockers.append("Choose a supplier first.")
    if product.compliance_flags and product.compliance_reviewed_at is None:
        blockers.append("This product has compliance flags: confirm you've checked them (certificates on file).")
    if product.status == ProductStatus.REJECTED:
        blockers.append("Product is rejected.")
    if product.status not in (ProductStatus.CONTENT_READY, ProductStatus.APPROVED, ProductStatus.PUBLISHED):
        warnings.append("Product isn't 'content ready' yet (approve a page and an ad, after choosing a supplier).")
    images = [a for a in _images(session, product)]
    if not images:
        warnings.append("No licensed photos: the listing will have no images. Add photos on the Videos page.")
    return {"blockers": blockers, "warnings": warnings, "price": price, "page": page, "images": images}


def _images(session: Session, product: Product) -> list[MediaAsset]:
    from app.modules.video_maker.media import PRODUCT_LICENCES

    return [a for a in session.scalars(select(MediaAsset).where(MediaAsset.product_id == product.id,
                                                                MediaAsset.kind == "image").order_by(MediaAsset.path))
            if a.licence in PRODUCT_LICENCES]


def build(session: Session, settings: Settings, product: Product) -> tuple[dict, float, list[MediaAsset]]:
    r = readiness(session, settings, product)
    if r["blockers"]:
        raise PublishError(" ".join(r["blockers"]))
    brand = load_brand()
    p = L.product_input(product.name, product.category, r["page"].data, vendor=brand.get("store_name") or "Our store",
                        returns_days=int((brand.get("promises") or {}).get("returns_days", 14)))
    return p, r["price"], r["images"]


def send_draft(session: Session, settings: Settings, product: Product, client: ShopifyClient | None = None) -> dict:
    """Create or update the DRAFT listing. Returns {"action": created|updated|unchanged|dry_run, ...}."""
    p, price, images = build(session, settings, product)
    listing = get_listing(session, product)
    h = L.payload_hash(p, price, [a.path for a in images])
    if settings.dry_run:
        action = "update" if listing.remote_id else "create"
        log_event(session, module="store_publisher", action="dry_run_draft", dry_run=True,
                  message=f"Would {action} a DRAFT Shopify product for {product.name} at £{price:.2f} "
                          f"with {len(images)} image(s). Nothing was sent.", details={"product": p})
        session.commit()
        return {"action": "dry_run", "would": action, "product": p, "price": price, "images": len(images)}
    if listing.remote_id and listing.payload_hash == h:
        return {"action": "unchanged"}

    client = client or make_client(settings)
    root = settings.path(settings.video.media_dir)
    uploaded = dict(listing.media or {})
    try:
        media = []
        for a in images:  # upload only images not sent before
            key = str(a.id)
            if key in uploaded:
                continue
            mime = mimetypes.guess_type(a.path)[0] or "image/jpeg"
            url = client.upload_image(root / a.path, mime)
            media.append({"originalSource": url, "mediaContentType": "IMAGE", "alt": p["title"]})
            uploaded[key] = a.path
        if listing.remote_id:
            update = {k: v for k, v in p.items() if k != "handle"}  # keep the URL stable
            remote = client.update_product({"id": listing.remote_id, **update}, media)
            action = "updated"
        else:
            remote = client.create_product({**p, "status": "DRAFT"}, media)
            action = "created"
        variant_id = remote["variants"]["nodes"][0]["id"]
        client.update_variant(remote["id"], L.variant_input(variant_id, price, track_inventory=settings.store.track_inventory))
    except ShopifyError as exc:
        listing.last_error = str(exc)
        session.commit()
        log_event(session, module="store_publisher", action="sync_failed", level="ERROR", dry_run=False,
                  message=f"{product.name}: {exc}")
        raise PublishError(str(exc)) from exc

    listing.remote_id, listing.variant_id, listing.handle = remote["id"], variant_id, remote["handle"]
    listing.remote_status = remote["status"]
    listing.payload_hash, listing.media = h, uploaded
    listing.last_synced_at, listing.last_error = datetime.now(UTC), None
    session.commit()
    log_event(session, module="store_publisher", action=f"draft_{action}", dry_run=False,
              source=admin_url(settings, listing.remote_id),
              message=f"{product.name}: Shopify product {action} ({listing.remote_status}), £{price:.2f}.")
    return {"action": action, "url": admin_url(settings, listing.remote_id)}


def request_publish(session: Session, settings: Settings, product: Product) -> ApprovalRequest:
    build(session, settings, product)  # same checks as sending a draft
    listing = get_listing(session, product)
    if not settings.dry_run and not listing.remote_id:
        raise PublishError("Send it to Shopify as a draft first, and check it there.")
    if listing.approval_id:
        existing = session.get(ApprovalRequest, listing.approval_id)
        if existing and existing.status in (ApprovalStatus.PENDING, ApprovalStatus.APPROVED):
            return existing
    req = approvals.request_approval(
        session, action_type=ActionType.PUBLISH_PRODUCT, product_id=product.id,
        title=f"Publish '{product.name}' on Shopify",
        description="Makes the product live (ACTIVE) and visible on your Online Store. Check the draft in Shopify first.",
        payload={"product_id": product.id, "shopify_id": listing.remote_id}, dry_run=settings.dry_run)
    listing.approval_id = req.id
    session.commit()
    return req


def on_publish_decided(session: Session, req: ApprovalRequest) -> None:
    """Approval queue hook: an approved publish request marks the product APPROVED."""
    product = session.get(Product, req.product_id) if req.product_id else None
    if product and req.status == ApprovalStatus.APPROVED and product.status == ProductStatus.CONTENT_READY:
        product.status = ProductStatus.APPROVED
        session.commit()


def make_publish_executor(settings: Settings, client_factory=None):
    """The function the approval system runs for an APPROVED publish request (live mode only)."""

    def execute(req: ApprovalRequest) -> dict:
        session = object_session(req)
        product = session.get(Product, req.product_id)
        client = (client_factory or make_client)(settings)
        send_draft(session, settings, product, client)  # make sure Shopify has the latest approved content
        listing = get_listing(session, product)
        try:
            remote = client.update_product({"id": listing.remote_id, "status": "ACTIVE"}, [])
            client.publish(listing.remote_id, client.publication_id(settings.store.publication_name))
        except ShopifyError as exc:
            listing.last_error = str(exc)
            session.commit()
            raise
        listing.remote_status, listing.published = remote["status"], True
        listing.last_synced_at, listing.last_error = datetime.now(UTC), None
        product.status = ProductStatus.PUBLISHED
        session.commit()
        return {"shopify_id": listing.remote_id, "url": admin_url(settings, listing.remote_id)}

    return execute


def unpublish(session: Session, settings: Settings, product: Product, client: ShopifyClient | None = None) -> None:
    """Take a product off sale (back to DRAFT). Reduces exposure, so no approval needed: just your click."""
    listing = get_listing(session, product)
    if settings.dry_run:
        log_event(session, module="store_publisher", action="dry_run_unpublish", dry_run=True,
                  message=f"Would set {product.name} back to DRAFT on Shopify.")
        return
    if not listing.remote_id:
        raise PublishError("This product isn't on Shopify.")
    client = client or make_client(settings)
    try:
        remote = client.update_product({"id": listing.remote_id, "status": "DRAFT"}, [])
    except ShopifyError as exc:
        raise PublishError(str(exc)) from exc
    listing.remote_status, listing.published = remote["status"], False
    if product.status == ProductStatus.PUBLISHED:
        product.status = ProductStatus.CONTENT_READY
    session.commit()
    log_event(session, module="store_publisher", action="unpublished", dry_run=False,
              message=f"{product.name}: set back to DRAFT on Shopify (hidden from customers).")


def confirm_compliance(session: Session, settings: Settings, product: Product) -> None:
    product.compliance_reviewed_at = datetime.now(UTC)
    session.commit()
    log_event(session, module="store_publisher", action="compliance_confirmed", dry_run=settings.dry_run,
              message=f"{product.name}: you confirmed compliance checks "
                      f"({', '.join(f['flag'] for f in product.compliance_flags or [])}).")


def register(settings: Settings, client_factory=None) -> None:
    approvals.register_executor(ActionType.PUBLISH_PRODUCT, make_publish_executor(settings, client_factory))
    approvals.register_decision_hook(ActionType.PUBLISH_PRODUCT, on_publish_decided)
