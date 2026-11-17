"""The web app: dashboard pages + a small JSON API.

Run it with:  python run.py dashboard   then open http://127.0.0.1:8000
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
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
from app.core.doctor import run_checks
from app.core.logger import log_event, setup_file_logging
from app.core.notify import unread_count
from app.core.scheduler import BackgroundScheduler, run_job
from app.core.models import (
    PIPELINE,
    ApprovalRequest,
    ApprovalStatus,
    CostEntry,
    JobRun,
    LogEntry,
    Metric,
    Notification,
    Product,
    ProductStatus,
    Supplier,
)

DASHBOARD_DIR = Path(__file__).resolve().parents[1] / "dashboard"

# Pages in the side menu.
NAV = [
    ("Overview", "/", None),
    ("Setup checklist", "/setup", None),
    ("Notifications", "/notifications", None),
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
               llm_factory=None, shopify_factory=None, start_scheduler: bool | None = None) -> FastAPI:
    """The factories let tests swap in a fake website fetcher / fake Claude / fake Shopify."""
    settings = settings or get_settings()
    engine = engine or make_engine(settings.database_url)
    init_db(engine)
    setup_file_logging(settings.path(settings.log_file))
    session_factory = make_session_factory(engine)
    run_scheduler = settings.schedule.enabled if start_scheduler is None else start_scheduler

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        # Scheduled research runs in the background while the dashboard is open (if enabled).
        sched = BackgroundScheduler(session_factory, settings) if run_scheduler else None
        if sched:
            sched.start()
        yield
        if sched:
            sched.stop()

    app = FastAPI(title="Dropship Agent", docs_url="/api/docs", lifespan=lifespan)

    @app.exception_handler(Exception)
    async def friendly_errors(request: Request, exc: Exception):
        # Anything unexpected: log it, and show a readable message instead of a blank error page.
        with session_factory() as session:
            log_event(session, module="dashboard", action="error", level="ERROR", dry_run=settings.dry_run,
                      message=f"{request.method} {request.url.path}: {type(exc).__name__}: {exc}")
        return JSONResponse(status_code=500, content={
            "detail": f"Something went wrong ({type(exc).__name__}: {exc}). It's recorded on the Logs page."})
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
             "unread_count": unread_count(session), "path": request.url.path, **ctx},
        )

    # ---------------- pages ----------------
    @app.get("/", response_class=HTMLResponse)
    def overview(request: Request, session: Session = Depends(get_session)):
        counts = dict(session.execute(select(Product.status, func.count()).group_by(Product.status)).all())
        pipeline = [(s.value.replace("_", " ").capitalize(), counts.get(s, 0)) for s in PIPELINE]
        recent = list(session.scalars(select(LogEntry).order_by(LogEntry.id.desc()).limit(8)))
        missing = [c for c in run_checks(settings) if not c.ok]
        notes = list(session.scalars(select(Notification).where(Notification.read_at.is_(None))
                                     .order_by(Notification.id.desc()).limit(5)))
        last_runs = list(session.scalars(select(JobRun).order_by(JobRun.id.desc()).limit(3)))
        return render(request, "overview.html", session, pipeline=pipeline, recent=recent, missing=missing,
                      notes=notes, last_runs=last_runs)

    @app.get("/setup", response_class=HTMLResponse)
    def setup_page(request: Request, session: Session = Depends(get_session)):
        return render(request, "setup.html", session, checks=run_checks(settings))

    @app.get("/notifications", response_class=HTMLResponse)
    def notifications(request: Request, session: Session = Depends(get_session)):
        items = list(session.scalars(select(Notification).order_by(Notification.id.desc()).limit(100)))
        runs = list(session.scalars(select(JobRun).order_by(JobRun.id.desc()).limit(10)))
        return render(request, "notifications.html", session, items=items, runs=runs)

    @app.post("/notifications/read", dependencies=[Depends(require_htmx)])
    def mark_read(session: Session = Depends(get_session)):
        now = datetime.now(UTC)
        for n in session.scalars(select(Notification).where(Notification.read_at.is_(None))):
            n.read_at = now
        session.commit()
        return Response(status_code=204, headers={"HX-Refresh": "true"})

    @app.post("/schedule/run-now", dependencies=[Depends(require_htmx)])
    def run_now(session: Session = Depends(get_session)):
        run_job("research", session, settings)  # free, read-only job
        return Response(status_code=204, headers={"HX-Refresh": "true"})

    @app.get("/products", response_class=HTMLResponse)
    def products(request: Request, session: Session = Depends(get_session)):
        rows = list(session.scalars(select(Product)))
        # Ranked: not-excluded first, then highest score; unscored products last.
        rows.sort(key=lambda p: (bool((p.score_details or {}).get("excluded")), p.score is None, -(p.score or 0)))
        # Show what the last research run couldn't use (e.g. an unreadable Trends file), not just in the logs.
        last = session.scalars(select(LogEntry).where(LogEntry.module == "product_research", LogEntry.action == "run")
                               .order_by(LogEntry.id.desc())).first()
        problems = (last.details or {}).get("problems", []) if last else []
        return render(request, "products.html", session, products=rows, problems=problems, last_run=last)

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
