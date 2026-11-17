"""Dashboard pages for the Shopify store publisher (phase 7)."""

from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from markupsafe import escape
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import approvals
from app.core.config import Settings
from app.core.models import ApprovalRequest, Product, ProductStatus, StoreListing
from app.modules.store_publisher import service
from app.modules.store_publisher.shopify import ShopifyError

SHOWN = [ProductStatus.PUBLISHED, ProductStatus.APPROVED, ProductStatus.CONTENT_READY, ProductStatus.SAMPLE_ORDERED,
         ProductStatus.SUPPLIER_CHOSEN]


def register(app: FastAPI, templates, settings: Settings, get_session, render, require_htmx, client_factory=None) -> None:
    htmx = [Depends(require_htmx)]
    make_client = lambda: (client_factory or service.make_client)(settings)  # noqa: E731
    service.register(settings, client_factory)

    def msg(text: str, kind: str = "dry") -> HTMLResponse:
        return HTMLResponse(f'<p class="banner {kind}">{escape(text)}</p>')

    def refresh() -> Response:
        return Response(status_code=204, headers={"HX-Refresh": "true"})

    def product_or_404(session: Session, pid: int) -> Product:
        p = session.get(Product, pid)
        if p is None:
            raise HTTPException(404)
        return p

    @app.get("/store", response_class=HTMLResponse)
    def store(request: Request, session: Session = Depends(get_session)):
        products = [p for p in session.scalars(select(Product)) if p.status in SHOWN]
        products.sort(key=lambda p: SHOWN.index(p.status))
        rows = []
        for p in products:
            listing = session.scalars(select(StoreListing).where(StoreListing.product_id == p.id)).first()
            req = session.get(ApprovalRequest, listing.approval_id) if listing and listing.approval_id else None
            rows.append({"p": p, "r": service.readiness(session, settings, p), "listing": listing, "req": req,
                         "url": service.admin_url(settings, listing.remote_id if listing else None)})
        configured = bool(settings.store.shop and (settings.shopify_access_token or
                                                   (settings.shopify_client_id and settings.shopify_client_secret)))
        return render(request, "store.html", session, rows=rows, configured=configured)

    @app.post("/store/test-connection", response_class=HTMLResponse, dependencies=htmx)
    def test_connection():
        try:
            name = make_client().shop_name()
        except ShopifyError as exc:
            return msg(f"Not connected: {exc}", "warn")
        except OSError as exc:
            return msg(f"Couldn't reach Shopify: {exc}", "warn")
        return msg(f"Connected to '{name}'. (Reading the shop name only, nothing was changed.)")

    @app.post("/store/{pid}/draft", response_class=HTMLResponse, dependencies=htmx)
    def draft(pid: int, session: Session = Depends(get_session)):
        p = product_or_404(session, pid)
        try:
            result = service.send_draft(session, settings, p, None if settings.dry_run else make_client())
        except (service.PublishError, ShopifyError) as exc:
            return msg(f"Not sent: {exc}", "warn")
        if result["action"] == "dry_run":
            return msg(f"Dry run: would {result['would']} a DRAFT product \"{result['product']['title']}\" at "
                       f"£{result['price']:.2f} with {result['images']} image(s). Nothing was sent to Shopify.")
        if result["action"] == "unchanged":
            return msg("No changes since the last sync.")
        return refresh()

    @app.post("/store/{pid}/request-publish", response_class=HTMLResponse, dependencies=htmx)
    def request_publish(pid: int, session: Session = Depends(get_session)):
        try:
            service.request_publish(session, settings, product_or_404(session, pid))
        except service.PublishError as exc:
            return msg(f"Can't request yet: {exc}", "warn")
        return refresh()

    @app.post("/store/{pid}/unpublish", response_class=HTMLResponse, dependencies=htmx)
    def unpublish(pid: int, session: Session = Depends(get_session)):
        try:
            service.unpublish(session, settings, product_or_404(session, pid), None if settings.dry_run else make_client())
        except service.PublishError as exc:
            return msg(str(exc), "warn")
        return msg("Dry run: would set it back to DRAFT. Nothing was sent.") if settings.dry_run else refresh()

    @app.post("/store/{pid}/compliance", dependencies=htmx)
    def compliance(pid: int, session: Session = Depends(get_session)):
        service.confirm_compliance(session, settings, product_or_404(session, pid))
        return refresh()

    @app.post("/approvals/{request_id}/run", response_class=HTMLResponse, dependencies=htmx)
    def run(request: Request, request_id: int, session: Session = Depends(get_session)):
        try:
            req = approvals.execute(session, request_id, dry_run=settings.dry_run)
        except (approvals.ApprovalError, ShopifyError, service.PublishError, OSError) as exc:
            return msg(f"Didn't run: {exc}", "warn")
        if settings.dry_run:
            return msg(f"Dry run: would now run #{req.id} ({req.title}). Nothing was sent. "
                       f"Set dry_run: false in config.yaml to do it for real.")
        return refresh()
