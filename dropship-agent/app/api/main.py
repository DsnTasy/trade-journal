"""The web app: dashboard pages + a small JSON API.

Run it with:  python run.py dashboard   then open http://127.0.0.1:8000
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.api import competitor_routes, content_routes, store_routes, supplier_routes, video_routes
from app.core import approvals
from app.core.config import Settings, get_settings
from app.core.cost_tracker import CostTracker
from app.core.db import init_db, make_engine, make_session_factory
from app.core.logger import log_event, setup_file_logging
from app.core.models import (
    PIPELINE,
    ApprovalRequest,
    ApprovalStatus,
    CostEntry,
    LogEntry,
    Metric,
    Product,
    ProductStatus,
    Supplier,
)

DASHBOARD_DIR = Path(__file__).resolve().parents[1] / "dashboard"

# Pages in the side menu. Ones with a phase number are not built yet.
NAV = [
    ("Overview", "/", None),
    ("Opportunities", "/products", None),
    ("Approval queue", "/approvals", None),
    ("Logs & costs", "/logs", None),
    ("Suppliers", "/suppliers", None),
    ("Profit calculator", "/profit", None),
    ("Competitor reports", "/competitors", None),
    ("Content", "/content", None),
    ("Videos", "/videos", None),
    ("Store (Shopify)", "/store", None),
]


def create_app(settings: Settings | None = None, engine: Engine | None = None, fetcher_factory=None,
               llm_factory=None, shopify_factory=None) -> FastAPI:
    """The factories let tests swap in a fake website fetcher / fake Claude / fake Shopify."""
    settings = settings or get_settings()
    engine = engine or make_engine(settings.database_url)
    init_db(engine)
    setup_file_logging(settings.path(settings.log_file))
    session_factory = make_session_factory(engine)

    app = FastAPI(title="Dropship Agent", docs_url="/api/docs")
    app.mount("/static", StaticFiles(directory=DASHBOARD_DIR / "static"), name="static")
    templates = Jinja2Templates(directory=DASHBOARD_DIR / "templates")
    # Show 2.0 as "2" and 17.99 as "17.99" (no pointless trailing zeros).
    templates.env.filters["gbp"] = lambda v: "–" if v is None else f"{'-' if v < 0 else ''}£{abs(v):,.2f}"
    templates.env.filters["num"] = lambda v: "no data" if v is None else f"{v:,.2f}".rstrip("0").rstrip(".")

    def get_session() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    def require_htmx(request: Request) -> None:
        # Safety: buttons in our dashboard send this header. A random website can't add
        # it to a request aimed at your computer without the browser blocking it, so
        # this stops other sites from clicking "Approve" on your behalf.
        if request.headers.get("HX-Request") != "true":
            raise HTTPException(status_code=403, detail="Use the dashboard buttons to make decisions.")

    def render(request: Request, name: str, session: Session, **ctx) -> HTMLResponse:
        budget = CostTracker(session, settings.budget, dry_run=settings.dry_run).status()
        pending_count = session.scalar(
            select(func.count()).select_from(ApprovalRequest)
            .where(ApprovalRequest.status == ApprovalStatus.PENDING)
        )
        return templates.TemplateResponse(
            request, name,
            {"settings": settings, "budget": budget, "nav": NAV, "pending_count": pending_count,
             "path": request.url.path, **ctx},
        )

    # ---------------- pages ----------------
    @app.get("/", response_class=HTMLResponse)
    def overview(request: Request, session: Session = Depends(get_session)):
        counts = dict(session.execute(select(Product.status, func.count()).group_by(Product.status)).all())
        pipeline = [(s.value.replace("_", " ").capitalize(), counts.get(s, 0)) for s in PIPELINE]
        recent = list(session.scalars(select(LogEntry).order_by(LogEntry.id.desc()).limit(8)))
        return render(request, "overview.html", session, pipeline=pipeline, recent=recent)

    @app.get("/products", response_class=HTMLResponse)
    def products(request: Request, session: Session = Depends(get_session)):
        rows = list(session.scalars(select(Product)))
        # Ranked: not-excluded first, then highest score; unscored products last.
        rows.sort(key=lambda p: (bool((p.score_details or {}).get("excluded")), p.score is None, -(p.score or 0)))
        return render(request, "products.html", session, products=rows)

    @app.get("/products/{product_id}", response_class=HTMLResponse)
    def product_detail(request: Request, product_id: int, session: Session = Depends(get_session)):
        product = session.get(Product, product_id)
        if product is None:
            raise HTTPException(404, "Product not found")
        metrics = list(session.scalars(
            select(Metric).where(Metric.product_id == product_id).order_by(Metric.retrieved_at.desc(), Metric.id.desc())
        ))
        latest, history, seen = [], [], set()
        for m in metrics:  # newest first: the first of each name is the latest value
            (history if m.name in seen else latest).append(m)
            seen.add(m.name)
        latest.sort(key=lambda m: m.name)
        suppliers = sorted(session.scalars(select(Supplier).where(Supplier.product_id == product_id)),
                           key=lambda s: -(s.score or 0))
        return render(request, "product_detail.html", session, p=product, metrics=latest, history=history,
                      suppliers=suppliers)

    @app.post("/products/{product_id}/status/{new_status}", response_class=HTMLResponse,
              dependencies=[Depends(require_htmx)])
    def set_status(request: Request, product_id: int, new_status: str, session: Session = Depends(get_session)):
        # Only research-stage moves here. Later stages (approved/published) go through the approval queue.
        allowed = {"shortlisted": ProductStatus.SHORTLISTED, "researched": ProductStatus.RESEARCHED,
                   "rejected": ProductStatus.REJECTED}
        product = session.get(Product, product_id)
        if product is None or new_status not in allowed:
            raise HTTPException(404)
        if product.status not in (ProductStatus.RESEARCHED, ProductStatus.SHORTLISTED, ProductStatus.REJECTED):
            raise HTTPException(409, "This product is past the research stage.")
        old = product.status
        product.status = allowed[new_status]
        session.commit()
        log_event(session, module="dashboard", action="status_change",
                  message=f"{product.name}: {old.value} → {product.status.value}", dry_run=settings.dry_run)
        return templates.TemplateResponse(request, "_status_controls.html", {"p": product})

    @app.get("/approvals", response_class=HTMLResponse)
    def approvals_page(request: Request, session: Session = Depends(get_session)):
        pending = approvals.pending(session)
        decided = list(session.scalars(
            select(ApprovalRequest).where(ApprovalRequest.status != ApprovalStatus.PENDING)
            .order_by(ApprovalRequest.id.desc()).limit(50)
        ))
        return render(request, "approvals.html", session, pending=pending, decided=decided)

    def decide(request: Request, request_id: int, decision: str, session: Session):
        try:
            req = approvals.decide(session, request_id, approve=decision == "approve",
                                   dry_run=settings.dry_run)
        except approvals.ApprovalError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return templates.TemplateResponse(request, "_approval_row.html", {"r": req, "settings": settings})

    @app.post("/approvals/{request_id}/approve", response_class=HTMLResponse, dependencies=[Depends(require_htmx)])
    def approve(request: Request, request_id: int, session: Session = Depends(get_session)):
        return decide(request, request_id, "approve", session)

    @app.post("/approvals/{request_id}/reject", response_class=HTMLResponse, dependencies=[Depends(require_htmx)])
    def reject(request: Request, request_id: int, session: Session = Depends(get_session)):
        return decide(request, request_id, "reject", session)

    @app.get("/logs", response_class=HTMLResponse)
    def logs(request: Request, session: Session = Depends(get_session)):
        entries = list(session.scalars(select(LogEntry).order_by(LogEntry.id.desc()).limit(200)))
        costs = list(session.scalars(select(CostEntry).order_by(CostEntry.id.desc()).limit(100)))
        return render(request, "logs.html", session, entries=entries, costs=costs)

    supplier_routes.register(app, templates, settings, get_session, render, require_htmx)
    competitor_routes.register(app, templates, settings, get_session, render, require_htmx, fetcher_factory)
    content_routes.register(app, templates, settings, get_session, render, require_htmx, llm_factory)
    video_routes.register(app, templates, settings, get_session, render, require_htmx)
    store_routes.register(app, templates, settings, get_session, render, require_htmx, shopify_factory)

    # ---------------- JSON API ----------------
    @app.get("/api/status")
    def api_status(session: Session = Depends(get_session)):
        b = CostTracker(session, settings.budget).status()
        return {
            "dry_run": settings.dry_run,
            "budget": {"spent_gbp": round(b.spent_gbp, 4), "cap_gbp": b.cap_gbp,
                       "fraction_used": round(b.fraction_used, 4), "warning": b.warning},
            "pending_approvals": len(approvals.pending(session)),
        }

    return app
