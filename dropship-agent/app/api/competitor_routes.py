"""Dashboard pages for competitor research (phase 4)."""

from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.supplier_routes import form_data
from app.core.config import Settings
from app.core.models import CompetitorReport
from app.modules.competitor_analyzer import service

STATUS_TEXT = {
    "allowed": "collected automatically",
    "forbidden": "automatic collection not allowed",
    "needs_terms_check": "check their terms",
    "manual": "entered by you",
    "error": "couldn't reach",
    "new": "new",
}


def register(app: FastAPI, templates, settings: Settings, get_session, render, require_htmx,
             fetcher_factory=None) -> None:
    htmx = [Depends(require_htmx)]
    make_fetcher = fetcher_factory or (lambda: service.make_fetcher(settings))

    def get_report(session: Session, report_id: int) -> CompetitorReport:
        r = session.get(CompetitorReport, report_id)
        if r is None:
            raise HTTPException(404, "Report not found")
        return r

    def to_report(report_id: int) -> Response:
        return Response(status_code=204, headers={"HX-Redirect": f"/competitors/{report_id}"})

    @app.get("/competitors", response_class=HTMLResponse)
    def competitors(request: Request, session: Session = Depends(get_session)):
        reports = list(session.scalars(select(CompetitorReport).order_by(CompetitorReport.updated_at.desc())))
        return render(request, "competitors.html", session, reports=reports, status_text=STATUS_TEXT)

    @app.post("/competitors", dependencies=htmx)
    async def add(request: Request, session: Session = Depends(get_session)):
        form = await form_data(request)
        try:
            report = service.analyse(session, settings, form.get("url", ""),
                                     terms_checked_by_you=form.get("terms_checked") == "yes", fetcher=make_fetcher())
        except service.CompetitorError as exc:
            raise HTTPException(422, str(exc)) from exc
        return to_report(report.id)

    @app.get("/competitors/{report_id}", response_class=HTMLResponse)
    def detail(request: Request, report_id: int, session: Session = Depends(get_session)):
        r = get_report(session, report_id)
        return render(request, "competitor_detail.html", session, r=r, status_text=STATUS_TEXT,
                      manual_fields=service.MANUAL_FIELDS, facts=r.facts,
                      bool_fields={k for k, (_, kind) in service.MANUAL_FIELDS.items() if kind is bool},
                      auto_keys=set((r.auto_facts or {}).keys()))

    @app.post("/competitors/{report_id}/rerun", dependencies=htmx)
    async def rerun(report_id: int, request: Request, session: Session = Depends(get_session)):
        r = get_report(session, report_id)
        form = await form_data(request)
        service.analyse(session, settings, r.url, terms_checked_by_you=form.get("terms_checked") == "yes",
                        fetcher=make_fetcher())
        return to_report(report_id)

    @app.post("/competitors/{report_id}/manual", response_class=HTMLResponse, dependencies=htmx)
    async def manual(report_id: int, request: Request, session: Session = Depends(get_session)):
        r = get_report(session, report_id)
        form = await form_data(request)
        problems = service.save_manual(session, settings, r, form, notes=form.get("notes"))
        if problems:
            return HTMLResponse('<p class="banner warn">' + "<br>".join(problems) + "</p>")
        return to_report(report_id)

    @app.post("/competitors/{report_id}/reviews", dependencies=htmx)
    async def add_reviews(report_id: int, request: Request, session: Session = Depends(get_session)):
        r = get_report(session, report_id)
        try:
            service.add_reviews(session, settings, r, (await form_data(request)).get("reviews", ""))
        except service.CompetitorError as exc:
            raise HTTPException(422, str(exc)) from exc
        return to_report(report_id)

    @app.post("/competitors/{report_id}/summary", response_class=HTMLResponse, dependencies=htmx)
    def summary(report_id: int, session: Session = Depends(get_session)):
        r = get_report(session, report_id)
        try:
            service.ai_summary(session, settings, r)
        except RuntimeError as exc:  # no API key, budget cap, refusal...
            return HTMLResponse(f'<p class="banner warn">AI summary not created: {exc}</p>')
        return to_report(report_id)

    @app.post("/competitors/{report_id}/delete", dependencies=htmx)
    def delete(report_id: int, session: Session = Depends(get_session)):
        session.delete(get_report(session, report_id))
        session.commit()
        return Response(status_code=204, headers={"HX-Redirect": "/competitors"})
