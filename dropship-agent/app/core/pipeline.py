"""`python run.py all`: run every step that's safe to automate, and stop at every approval gate.

Automatic (free): research → suppliers → profit → render videos for approved scripts → listing readiness.
Paid, only if you ask (--with-content): write pages and ad scripts with Claude (budget-guarded).
Never automatic: approving content, sending to Shopify, publishing, emailing suppliers, ordering.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import approvals
from app.core.config import Settings
from app.core.logger import log_event
from app.core.models import ContentItem, ContentStatus, Product, ProductStatus, Supplier, Video


@dataclass
class Step:
    name: str
    status: str  # done | skipped | failed
    lines: list[str] = field(default_factory=list)


def run_all(session: Session, settings: Settings, *, with_content: bool = False) -> list[Step]:
    steps: list[Step] = []

    # 1. Research
    from app.modules.product_research import run_research
    from app.modules.product_research.sources import SourceError

    s = Step("Research", "done")
    try:
        rep = run_research(session, settings)
        top = [r for r in rep.rows if not r.excluded][:3]
        s.lines = [f"{len(rep.rows)} products scored."] + [f"{r.name}: {r.score:.0f}" for r in top]
    except SourceError as exc:
        s.status, s.lines = "skipped", [str(exc)]
    steps.append(s)

    # 2. Suppliers + 3. Profit
    from app.modules.supplier_finder import import_suppliers

    s = Step("Suppliers & profit", "done")
    try:
        rep = import_suppliers(session, settings)
        for pid, sups in rep.by_product.items():
            best = sups[0] if sups else None
            if best is not None:
                pr = best.profit or {}
                money = f"£{pr['profit_gbp']:.2f}/sale, {pr['verdict']}" if "profit_gbp" in pr else pr.get("error", "")
                s.lines.append(f"{session.get(Product, pid).name}: best supplier {best.name} ({best.score:.0f}), {money}")
        if not s.lines:
            s.lines.append("No suppliers matched your researched products.")
    except SourceError as exc:
        s.status, s.lines = "skipped", [str(exc)]
    steps.append(s)

    # 4. Content (paid, opt-in)
    s = Step("Content", "skipped", ["Not requested (add --with-content to write pages and ads with Claude; paid)."])
    if with_content:
        from app.modules.content_generator import service as content

        s = Step("Content", "done")
        ready = [p for p in session.scalars(select(Product))
                 if p.status in (ProductStatus.SUPPLIER_CHOSEN, ProductStatus.SAMPLE_ORDERED)]
        for p in ready:
            cur = content.current(session, p)
            try:
                if cur["page"] is None:
                    content.generate_page(session, settings, p)
                    s.lines.append(f"{p.name}: product page drafted.")
                if not cur["ads"]:
                    content.generate_ads(session, settings, p)
                    s.lines.append(f"{p.name}: 3 ad scripts drafted.")
            except RuntimeError as exc:
                s.status = "failed"
                s.lines.append(f"{p.name}: {exc}")
                break
        if not ready:
            s.status, s.lines = "skipped", ["No products with a chosen supplier."]
    steps.append(s)

    # 5. Videos for approved scripts (free)
    from app.modules.video_maker import service as video

    s = Step("Videos", "done")
    if not video.ffmpeg_available(settings):
        s.status, s.lines = "skipped", ["ffmpeg not installed."]
    else:
        done = {v.content_item_id for v in session.scalars(select(Video))}
        todo = [i for i in session.scalars(select(ContentItem).where(ContentItem.kind == "ad_script",
                                                                     ContentItem.status == ContentStatus.APPROVED))
                if i.id not in done]
        for item in todo:
            try:
                v = video.render_item(session, settings, item)
                s.lines.append(f"{item.product.name}: {item.angle} video rendered ({v.duration_seconds:.0f}s).")
            except video.VideoError as exc:
                s.lines.append(f"{item.product.name}: {item.angle} not rendered: {exc}")
        if not todo:
            s.status, s.lines = "skipped", ["No approved ad scripts without a video."]
    steps.append(s)

    # 6. Store readiness (never sends anything)
    from app.modules.store_publisher import service as store

    s = Step("Store readiness", "done")
    for p in session.scalars(select(Product)):
        if p.status in (ProductStatus.CONTENT_READY, ProductStatus.APPROVED):
            r = store.readiness(session, settings, p)
            s.lines.append(f"{p.name}: " + ("ready to send as a draft" if not r["blockers"] else r["blockers"][0]))
    if not s.lines:
        s.status, s.lines = "skipped", ["No products with approved content yet."]
    steps.append(s)

    # Your to-do list
    todo: list[str] = []
    pending = approvals.pending(session)
    if pending:
        todo.append(f"{len(pending)} request(s) waiting in the Approval queue.")
    drafts = session.scalars(select(ContentItem).where(ContentItem.status == ContentStatus.DRAFT)).all()
    if drafts:
        todo.append(f"{len(drafts)} content draft(s) to review on the Content page.")
    unreviewed = session.scalars(select(Video).where(Video.status == "rendered")).all()
    if unreviewed:
        todo.append(f"{len(unreviewed)} video(s) to watch and approve.")
    shortlisted = [p for p in session.scalars(select(Product)) if p.status == ProductStatus.SHORTLISTED]
    no_sup = [p for p in shortlisted if not session.scalars(select(Supplier).where(Supplier.product_id == p.id)).first()]
    if no_sup:
        todo.append(f"Find suppliers for {len(no_sup)} shortlisted product(s) (research/suppliers.csv).")
    steps.append(Step("Your to-do list", "done", todo or ["Nothing waiting for you."]))

    log_event(session, module="pipeline", action="all", dry_run=settings.dry_run,
              message="; ".join(f"{st.name}: {st.status}" for st in steps))
    return steps
