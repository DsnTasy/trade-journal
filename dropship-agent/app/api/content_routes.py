"""Dashboard pages for content: generate, preview, edit, claim-check, approve (phase 5)."""

from __future__ import annotations

import re

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from markupsafe import Markup, escape
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.supplier_routes import form_data
from app.core.config import Settings
from app.core.models import ContentItem, ContentStatus, Product, ProductStatus
from app.modules.content_generator import prompts, service


# ---------- converting between stored data and simple form fields ----------
def to_form(item: ContentItem) -> dict[str, str]:
    d = item.data
    if item.kind == "product_page":
        return {"title": d.get("title", ""), "short_description": d.get("short_description", ""),
                "bullets": "\n".join(d.get("bullets", [])), "description": d.get("description", ""),
                "faqs": "\n\n".join(f"Q: {f['question']}\nA: {f['answer']}" for f in d.get("faqs", [])),
                "seo_title": d.get("seo_title", ""), "meta_description": d.get("meta_description", ""),
                "tags": ", ".join(d.get("tags", []))}
    notes = d.get("platform_notes", {})
    return {"hook": d.get("hook", ""), "body": d.get("body", ""), "cta": d.get("cta", ""),
            "on_screen_text": "\n".join(d.get("on_screen_text", [])), "shots": "\n".join(d.get("shots", [])),
            "duration_seconds": str(d.get("duration_seconds", "")),
            "tiktok": notes.get("tiktok", ""), "reels": notes.get("reels", ""), "shorts": notes.get("shorts", "")}


def _lines(text: str) -> list[str]:
    return [ln.strip() for ln in text.splitlines() if ln.strip()]


def parse_faqs(text: str) -> list[dict]:
    faqs = []
    for block in re.split(r"\n\s*\n", text.strip()):
        q = re.search(r"^\s*Q:\s*(.+)$", block, re.M)
        a = re.search(r"^\s*A:\s*([\s\S]+)$", block, re.M)
        if q and a:
            faqs.append({"question": q.group(1).strip(), "answer": a.group(1).strip()})
    return faqs


def from_form(item: ContentItem, f: dict[str, str]) -> dict:
    if item.kind == "product_page":
        return {"title": f.get("title", "").strip(), "short_description": f.get("short_description", "").strip(),
                "bullets": _lines(f.get("bullets", "")), "description": f.get("description", "").strip(),
                "faqs": parse_faqs(f.get("faqs", "")), "seo_title": f.get("seo_title", "").strip(),
                "meta_description": f.get("meta_description", "").strip(),
                "tags": [t.strip().lower() for t in f.get("tags", "").split(",") if t.strip()]}
    try:
        duration = int(float(f.get("duration_seconds") or 0))
    except ValueError:
        duration = item.data.get("duration_seconds", 0)
    return {"angle": item.angle, "hook": f.get("hook", "").strip(), "body": f.get("body", "").strip(),
            "cta": f.get("cta", "").strip(), "on_screen_text": _lines(f.get("on_screen_text", "")),
            "shots": _lines(f.get("shots", "")), "duration_seconds": duration,
            "platform_notes": {k: f.get(k, "").strip() for k in ("tiktok", "reels", "shorts")}}


def highlight(text: str, findings: list[dict] | None, field_name: str) -> Markup:
    """Escape the text, then mark each risky phrase the claim checker found in this field."""
    out = str(escape(text or ""))
    for f in findings or []:
        if f["field"] != field_name or f["rule"] == "length":
            continue
        safe = str(escape(f["match"]))
        out = re.sub(re.escape(safe), lambda m: f'<mark class="sev-{f["severity"]}" title="{escape(f["message"])}">'
                                                f'{m.group(0)}</mark>', out, flags=re.I)
    return Markup(out)


def register(app: FastAPI, templates, settings: Settings, get_session, render, require_htmx, llm_factory=None) -> None:
    htmx = [Depends(require_htmx)]
    templates.env.filters["highlight"] = highlight
    make_llm = llm_factory or (lambda session: None)  # None = the real Claude client

    def get_item(session: Session, item_id: int) -> ContentItem:
        item = session.get(ContentItem, item_id)
        if item is None:
            raise HTTPException(404, "Content not found")
        return item

    def back(product_id: int) -> Response:
        return Response(status_code=204, headers={"HX-Redirect": f"/content/{product_id}"})

    @app.get("/content", response_class=HTMLResponse)
    def content_list(request: Request, session: Session = Depends(get_session)):
        order = [ProductStatus.CONTENT_READY, ProductStatus.SAMPLE_ORDERED, ProductStatus.SUPPLIER_CHOSEN,
                 ProductStatus.SHORTLISTED, ProductStatus.RESEARCHED]
        products = [p for p in session.scalars(select(Product)) if p.status in order]
        products.sort(key=lambda p: (order.index(p.status), -(p.score or 0)))
        rows = [{"p": p, **service.current(session, p)} for p in products]
        return render(request, "content.html", session, rows=rows)

    @app.get("/content/{product_id}", response_class=HTMLResponse)
    def content_product(request: Request, product_id: int, session: Session = Depends(get_session)):
        product = session.get(Product, product_id)
        if product is None:
            raise HTTPException(404, "Product not found")
        cur = service.current(session, product)
        facts, _ = service.build_facts(session, settings, product)
        return render(request, "content_product.html", session, p=product, page=cur["page"], ads=cur["ads"],
                      facts=facts, to_form=to_form, angles=prompts.ANGLES, limits=prompts.LIMITS)

    @app.post("/content/{product_id}/generate/{what}", response_class=HTMLResponse, dependencies=htmx)
    def generate(product_id: int, what: str, session: Session = Depends(get_session)):
        product = session.get(Product, product_id)
        if product is None or what not in ("page", "ads"):
            raise HTTPException(404)
        try:
            if what == "page":
                service.generate_page(session, settings, product, llm=make_llm(session))
            else:
                service.generate_ads(session, settings, product, llm=make_llm(session))
        except RuntimeError as exc:  # no API key, budget cap, refusal, cut off...
            return HTMLResponse(f'<p class="banner warn">Not generated: {exc}</p>')
        return back(product_id)

    @app.post("/content/item/{item_id}/save", response_class=HTMLResponse, dependencies=htmx)
    async def save(item_id: int, request: Request, session: Session = Depends(get_session)):
        item = get_item(session, item_id)
        try:
            service.update(session, settings, item, from_form(item, await form_data(request)))
        except service.ContentError as exc:
            raise HTTPException(409, str(exc)) from exc
        return back(item.product_id)

    @app.post("/content/item/{item_id}/{decision}", dependencies=htmx)
    def decide(item_id: int, decision: str, session: Session = Depends(get_session)):
        if decision not in ("approve", "reject"):
            raise HTTPException(404)
        item = get_item(session, item_id)
        try:
            service.decide(session, settings, item, approve=decision == "approve")
        except service.ContentError as exc:
            raise HTTPException(409, str(exc)) from exc
        return back(item.product_id)

    @app.get("/content/item/{item_id}/history", response_class=HTMLResponse)
    def history(request: Request, item_id: int, session: Session = Depends(get_session)):
        item = get_item(session, item_id)
        versions = list(session.scalars(select(ContentItem).where(
            ContentItem.product_id == item.product_id, ContentItem.kind == item.kind, ContentItem.angle == item.angle)
            .order_by(ContentItem.version.desc())))
        return render(request, "content_history.html", session, item=item, versions=versions, status=ContentStatus)
