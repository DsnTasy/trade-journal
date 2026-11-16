"""Command-line entry point. Try:  python run.py --help"""

from __future__ import annotations

import typer

from app.core import approvals
from app.core.config import get_settings
from app.core.cost_tracker import CostTracker
from app.core.db import init_db, make_engine, make_session_factory
from app.core.logger import log_event, setup_file_logging
from app.core.models import ActionType, Product

cli = typer.Typer(help="Dropship Agent: research and admin assistant. Dry-run by default.",
                  no_args_is_help=True)


def _session(force_dry_run: bool = False):
    settings = get_settings()
    if force_dry_run:
        settings.dry_run = True
    setup_file_logging(settings.path(settings.log_file))
    engine = make_engine(settings.database_url)
    init_db(engine)
    return settings, make_session_factory(engine)()


@cli.callback()
def main(dry_run: bool = typer.Option(False, "--dry-run", help="Force dry-run even if config.yaml says live.")):
    if dry_run:
        get_settings().dry_run = True


@cli.command()
def init():
    """Create the database and folders."""
    settings, session = _session()
    log_event(session, module="cli", action="init", message="Database ready.", dry_run=settings.dry_run)
    typer.echo(f"Database ready: {settings.database_url}")
    typer.echo(f"Mode: {'DRY RUN' if settings.dry_run else 'LIVE'}")
    if not settings.anthropic_api_key:
        typer.echo("Note: no ANTHROPIC_API_KEY in .env yet (only needed from phase 2 onwards).")


@cli.command()
def status():
    """Show mode, budget and pending approvals."""
    settings, session = _session()
    b = CostTracker(session, settings.budget).status()
    typer.echo(f"Mode:     {'DRY RUN' if settings.dry_run else 'LIVE'}")
    typer.echo(f"Budget:   £{b.spent_gbp:.2f} of £{b.cap_gbp:.2f} used this month ({b.fraction_used:.0%})"
               + ("  WARNING" if b.warning else ""))
    pend = approvals.pending(session)
    typer.echo(f"Pending approvals: {len(pend)}")
    for r in pend:
        typer.echo(f"  #{r.id} {r.action_type.value}: {r.title}")


@cli.command()
def dashboard(port: int = 8000):
    """Start the dashboard at http://127.0.0.1:8000 (Ctrl+C to stop)."""
    import uvicorn

    from app.api.main import create_app

    typer.echo(f"Open http://127.0.0.1:{port} in your browser. Press Ctrl+C to stop.")
    # 127.0.0.1 = only reachable from your own computer, not your network.
    uvicorn.run(create_app(), host="127.0.0.1", port=port)


@cli.command()
def demo():
    """Add a clearly-labelled DEMO product and approval request so you can try the dashboard."""
    settings, session = _session()
    product = Product(name="DEMO: example product (not real data)", notes="Created by `run.py demo`.")
    session.add(product)
    session.commit()
    req = approvals.request_approval(
        session, action_type=ActionType.PUBLISH_PRODUCT, product_id=product.id,
        title="DEMO: publish example product", description="Safe to approve or reject: it's only a demo.",
        payload={"demo": True}, dry_run=settings.dry_run,
    )
    typer.echo(f"Created demo approval request #{req.id}. Open the dashboard → Approval queue.")


def _not_yet(name: str, phase: int) -> None:
    typer.echo(f"'{name}' is built in phase {phase}. Nothing was run.")


@cli.command()
def research(
    explain: int = typer.Option(0, "--explain", help="Ask Claude for a plain-English summary of the top N "
                                                     "(paid, needs ANTHROPIC_API_KEY). Default: off."),
):
    """Score and rank the product ideas in research/candidates.csv."""
    from app.modules.product_research import run_research
    from app.modules.product_research.sources import SourceError

    settings, session = _session()
    try:
        report = run_research(session, settings, explain_with_ai=explain)
    except SourceError as exc:
        typer.echo(f"Can't run research: {exc}")
        raise typer.Exit(1) from exc

    typer.echo(f"Sources used: {', '.join(sorted(report.sources_used)) or 'none'}\n")
    typer.echo(f"{'#':>2}  {'Score':>5}  {'Data':>4}  Product")
    for i, r in enumerate(report.rows, 1):
        tag = f"  [AVOIDED: {', '.join(r.excluded)}]" if r.excluded else ""
        review = "  [compliance review]" if r.compliance_flags else ""
        typer.echo(f"{i:>2}  {r.score:>5.1f}  {r.confidence:>4.0%}  {r.name}{tag}{review}")
        for reason in r.reasons_for[:2]:
            typer.echo(f"            + {reason}")
        for reason in r.reasons_against[:2]:
            typer.echo(f"            - {reason}")
        if r.summary:
            typer.echo(f"            AI: {r.summary}")
    if report.problems:
        typer.echo("\nThings to know:")
        for p in report.problems:
            typer.echo(f"  ! {p}")
    typer.echo("\nFull details and sources: python run.py dashboard → Opportunities")


@cli.command()
def suppliers():
    """Find and score suppliers (phase 3)."""
    _not_yet("suppliers", 3)


@cli.command()
def profit():
    """Calculate true profit (phase 3)."""
    _not_yet("profit", 3)


@cli.command()
def content():
    """Write product pages and ad scripts (phase 5)."""
    _not_yet("content", 5)


@cli.command()
def video():
    """Render video ads (phase 6)."""
    _not_yet("video", 6)


@cli.command()
def publish():
    """Publish approved products (phase 7)."""
    _not_yet("publish", 7)


@cli.command(name="all")
def run_all():
    """Run the whole pipeline (available once all phases are built)."""
    _not_yet("all", 8)


if __name__ == "__main__":
    cli()
