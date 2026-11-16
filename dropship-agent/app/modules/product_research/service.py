"""Runs product research: gather signals → save with sources → score → rank."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.llm_client import LLMClient, LLMError
from app.core.logger import log_event
from app.core.models import Metric, Product, ProductStatus
from app.modules.product_research import compliance
from app.modules.product_research.scoring import score_product
from app.modules.product_research.sources import (
    MetaAdLibrary,
    Signal,
    SourceError,
    load_candidates,
    load_trends,
    norm,
)


@dataclass
class ResearchRow:
    product_id: int
    name: str
    score: float
    confidence: float
    excluded: list[str]
    compliance_flags: list[dict]
    reasons_for: list[str]
    reasons_against: list[str]
    summary: str | None = None


@dataclass
class ResearchReport:
    rows: list[ResearchRow] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    sources_used: set[str] = field(default_factory=set)


AI_SYSTEM = (
    "You explain product-research results to a beginner UK dropshipper. Use ONLY the data given. "
    "Do not invent numbers, trends or facts. If data is missing, say so. "
    "Write 2-3 short sentences in British English: why it might work, why it might not, "
    "and the single most useful next check. No hype."
)


def _upsert_product(session: Session, name: str, category: str, notes: str) -> Product:
    key = norm(name)
    for p in session.scalars(select(Product)):
        if norm(p.name) == key:
            if category:
                p.category = category
            return p
    p = Product(name=name, category=category or None, notes=notes or None, status=ProductStatus.RESEARCHED)
    session.add(p)
    session.flush()
    return p


def run_research(
    session: Session,
    settings: Settings,
    *,
    meta: MetaAdLibrary | None = None,
    explain_with_ai: int = 0,
    llm: LLMClient | None = None,
) -> ResearchReport:
    cfg = settings.research
    report = ResearchReport()

    candidates, problems = load_candidates(settings.path(cfg.candidates_file))
    report.problems += problems
    if not candidates:
        report.problems.append("The candidates file has no products in it.")
        return report
    report.sources_used.add("candidates file (manual entry)")

    trends, problems = load_trends(settings.path(cfg.trends_dir))
    report.problems += problems
    if trends:
        report.sources_used.add("Google Trends CSV exports")
    else:
        report.problems.append(f"No Google Trends exports found in {cfg.trends_dir}/: demand and trend are unknown.")

    if cfg.meta_enabled and meta is None:
        if settings.meta_ad_library_token:
            meta = MetaAdLibrary(settings.meta_ad_library_token, cfg.meta_api_version, cfg.meta_countries,
                                 cfg.meta_max_pages, cfg.request_delay_seconds)
        else:
            report.problems.append("Meta Ad Library is enabled but META_AD_LIBRARY_TOKEN is not set in .env.")

    now = datetime.now(UTC)
    rows: list[ResearchRow] = []
    for cand in candidates:
        signals: list[Signal] = list(cand.signals)

        matched_term = next((t for t in cand.search_terms if t in trends), None)
        if matched_term:
            signals += trends[matched_term]
        elif trends:
            report.problems.append(f"'{cand.name}': none of its keywords ({', '.join(cand.search_terms)}) "
                                   f"appear in your Google Trends exports.")

        if meta is not None:
            term = cand.search_terms[0]
            try:
                signals += meta.signals_for(term)
                report.sources_used.add("Meta Ad Library API")
            except (SourceError, OSError) as exc:  # network problems are reported, not hidden
                report.problems.append(f"'{cand.name}': Meta Ad Library failed: {exc}")

        product = _upsert_product(session, cand.name, cand.category, cand.notes)
        for s in signals:
            session.add(Metric(product_id=product.id, name=s.metric, value=s.value, unit=s.unit,
                               source=s.source, retrieved_at=s.retrieved_at, note=s.note))

        latest = {s.metric: s.value for s in signals}  # later signals override earlier ones
        result = score_product(latest, cfg, settings.business)
        flags = compliance.check(cand.name, cand.category, cand.keywords)
        excluded = compliance.avoided(flags, cand.category, cfg.avoid_categories)

        against = list(result.reasons_against)
        if flags:
            against.append("Needs manual compliance review: " + "; ".join(f["flag"].replace("_", " ") for f in flags) + ".")
        if excluded:
            against.insert(0, f"In a category you chose to avoid ({', '.join(excluded)}).")

        product.score = result.score
        product.confidence = result.confidence
        product.score_details = {**result.details(), "excluded": excluded, "trends_term": matched_term}
        product.reasons = {"for": result.reasons_for, "against": against,
                           "summary": (product.reasons or {}).get("summary")}
        product.compliance_flags = flags
        product.researched_at = now

        rows.append(ResearchRow(product.id, product.name, result.score, result.confidence, excluded,
                                flags, result.reasons_for, against))
    session.commit()

    # Rank: products you haven't excluded first, then by score.
    rows.sort(key=lambda r: (bool(r.excluded), -r.score))
    report.rows = rows

    if explain_with_ai:
        _add_ai_summaries(session, settings, report, explain_with_ai, llm)

    log_event(session, module="product_research", action="run",
              message=f"Researched {len(rows)} products. Sources: {', '.join(sorted(report.sources_used))}.",
              source=", ".join(sorted(report.sources_used)), dry_run=settings.dry_run,
              details={"problems": report.problems})
    return report


def _add_ai_summaries(session: Session, settings: Settings, report: ResearchReport, top_n: int,
                      llm: LLMClient | None) -> None:
    llm = llm or LLMClient(settings, session)
    for row in [r for r in report.rows if not r.excluded][:top_n]:
        product = session.get(Product, row.product_id)
        facts = {
            "product": product.name, "category": product.category, "score_0_100": row.score,
            "data_confidence": row.confidence, "details": product.score_details,
            "reasons_for": row.reasons_for, "reasons_against": row.reasons_against,
            "compliance_flags": [f["message"] for f in row.compliance_flags],
        }
        try:
            result = llm.complete(task="research_explanation", system=AI_SYSTEM,
                                  prompt=json.dumps(facts, sort_keys=True), max_tokens=600)
        except LLMError as exc:
            report.problems.append(f"AI summary skipped for '{product.name}': {exc}")
            if "ANTHROPIC_API_KEY" in str(exc):
                return  # no point trying the rest
            continue
        except RuntimeError as exc:  # e.g. budget cap reached
            report.problems.append(f"AI summaries stopped: {exc}")
            return
        row.summary = result.text.strip()
        product.reasons = {**(product.reasons or {}), "summary": row.summary}
        session.commit()
