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


def _gbp(v: float) -> str:
    return f"{'-' if v < 0 else ''}£{abs(v):,.2f}"


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
def suppliers(
    draft_emails: bool = typer.Option(False, "--draft-emails",
                                      help="Draft (never send) enquiry emails to the top suppliers of shortlisted products."),
):
    """Score the suppliers in research/suppliers.csv and show true profit with each."""
    from app.core.models import ProductStatus
    from app.modules.product_research.sources import SourceError
    from app.modules.supplier_finder import REMINDER, import_suppliers
    from app.modules.supplier_finder.service import draft_email

    settings, session = _session()
    try:
        report = import_suppliers(session, settings)
    except SourceError as exc:
        typer.echo(f"Can't load suppliers: {exc}")
        raise typer.Exit(1) from exc

    top_n = settings.suppliers.top_n
    typer.echo(f"Imported {report.imported} suppliers. {REMINDER}\n")
    for product_id, sups in report.by_product.items():
        product = session.get(Product, product_id)
        typer.echo(f"{product.name}  [{product.status.value}]")
        for i, s in enumerate(sups[:top_n], 1):
            pr = s.profit or {}
            money = (f"{_gbp(pr['profit_gbp'])}/sale ({pr['verdict']})" if "profit_gbp" in pr
                     else pr.get("error", "profit unknown"))
            days = f"{s.shipping_days_max}d" if s.shipping_days_max is not None else "?d"
            typer.echo(f"  {i}. {s.score:5.1f}  {s.name:<28} {s.warehouse or '?':<3} {days:<4} {money}")
            for f in (s.red_flags or [])[:3]:
                typer.echo(f"        ! {f['message']}")
        if len(sups) > top_n:
            typer.echo(f"     (+{len(sups) - top_n} more in the dashboard)")
        if draft_emails and product.status in (ProductStatus.SHORTLISTED, ProductStatus.SUPPLIER_CHOSEN):
            for s in sups[:top_n]:
                draft_email(session, settings, s)
            typer.echo(f"     Drafted emails to the top {min(top_n, len(sups))} (NOT sent: see dashboard).")
        typer.echo("")
    if report.problems:
        typer.echo("Things to know:")
        for p in report.problems:
            typer.echo(f"  ! {p}")
    typer.echo("Details, emails and profit breakdowns: python run.py dashboard → Suppliers")


@cli.command()
def profit(
    price: float = typer.Option(..., help="Sell price the customer pays, inc. VAT (£)."),
    cost: float = typer.Option(..., help="Product cost from the supplier (£)."),
    shipping: float = typer.Option(0.0, help="Shipping cost per order (£)."),
    cpa: float = typer.Option(None, help="Ad cost per sale (£). Default from config.yaml."),
):
    """Quick profit check, e.g.  python run.py profit --price 19.99 --cost 4 --shipping 3"""
    from app.modules.profit_calculator import ProfitInputs, calculate

    settings = get_settings()
    i = ProfitInputs.from_config(settings.profit, settings.business, sell_price_gbp=price, unit_cost_gbp=cost,
                                 shipping_gbp=shipping, cpa_gbp=cpa)
    try:
        r = calculate(i, settings.profit, settings.business)
    except ValueError as exc:
        typer.echo(f"Can't calculate: {exc}")
        raise typer.Exit(1) from exc
    rows = [("Customer pays (inc. VAT)", price), ("- VAT", r.vat_gbp), ("= Net revenue", r.net_revenue_gbp),
            ("- Product + shipping + packaging", r.product_costs_gbp), ("- Card fees", r.payment_fees_gbp),
            ("- Shopify fees per order", r.platform_fees_gbp), ("- Expected refunds", r.refund_cost_gbp),
            ("- Expected chargebacks", r.chargeback_cost_gbp), ("= Break-even ad cost per sale", r.break_even_cpa_gbp),
            ("- Ad cost per sale (assumed)", i.cpa_gbp), ("= PROFIT PER SALE", r.profit_gbp)]
    for label, value in rows:
        typer.echo(f"  {label:<34} {_gbp(value):>9}")
    typer.echo(f"  {'Margin':<34} {r.margin:>9.0%}")
    typer.echo(f"  {'Break-even ROAS':<34} {r.break_even_roas if r.break_even_roas else 'n/a':>9}")
    typer.echo(f"\n  Verdict: {r.verdict.upper()}")
    for n in r.notes:
        typer.echo(f"  ! {n}")
    typer.echo("\n  Assumptions come from config.yaml → profit. Edit them there or in the dashboard.")


@cli.command()
def competitor(
    url: str = typer.Argument(..., help="The competitor store's address, e.g. https://example-store.co.uk"),
    terms_checked: bool = typer.Option(False, "--terms-checked",
                                       help="You've read their terms and they don't forbid automated access."),
):
    """Research a competitor store (facts only; checks robots.txt and their terms first)."""
    from app.modules.competitor_analyzer import CompetitorError, analyse

    settings, session = _session()
    try:
        r = analyse(session, settings, url, terms_checked_by_you=terms_checked)
    except CompetitorError as exc:
        typer.echo(f"Can't analyse: {exc}")
        raise typer.Exit(1) from exc
    typer.echo(f"{r.domain}: {r.status}")
    if r.status_reason:
        typer.echo(f"  {r.status_reason}")
    ins = r.insights or {}
    for title, key in (("Does well", "does_well"), ("Gaps you could fill", "gaps"), ("Vs your products", "vs_your_products")):
        if ins.get(key):
            typer.echo(f"\n{title}:")
            for line in ins[key]:
                typer.echo(f"  - {line}")
    typer.echo("\nFull report, manual facts and review themes: python run.py dashboard → Competitor reports")


@cli.command()
def content(
    product: str = typer.Option(None, "--product", help="Product name. Default: every product with a chosen supplier."),
    what: str = typer.Option("all", "--what", help="page, ads or all"),
):
    """Write product pages and 3 ad scripts with Claude, then check every claim (paid: about 5-7p each)."""
    from sqlalchemy import select

    from app.core.models import ProductStatus
    from app.modules.content_generator import claim_checker, service

    if what not in ("page", "ads", "all"):
        typer.echo("--what must be page, ads or all")
        raise typer.Exit(1)
    settings, session = _session()
    products = list(session.scalars(select(Product)))
    if product:
        products = [p for p in products if p.name.lower() == product.lower()]
        if not products:
            typer.echo(f"No product called '{product}'.")
            raise typer.Exit(1)
    else:
        products = [p for p in products if p.status in (ProductStatus.SUPPLIER_CHOSEN, ProductStatus.SAMPLE_ORDERED)]
        if not products:
            typer.echo("No products with a chosen supplier yet. Choose one in the dashboard, or use --product NAME.")
            raise typer.Exit(1)

    for p in products:
        typer.echo(f"{p.name}")
        try:
            items = []
            if what in ("page", "all"):
                items.append(service.generate_page(session, settings, p))
            if what in ("ads", "all"):
                items += service.generate_ads(session, settings, p)
        except RuntimeError as exc:
            typer.echo(f"  Not generated: {exc}")
            continue
        for item in items:
            label = "product page" if item.kind == "product_page" else f"{item.angle.replace('_', ' ')} ad"
            high = claim_checker.blocking(item.findings or [])
            typer.echo(f"  {label}: {len(item.findings or [])} issue(s), {len(high)} must fix")
            for f in high[:3]:
                typer.echo(f"     ! \"{f['match']}\" ({f['field']}): {f['message']}")
    typer.echo("\nReview, edit and approve: python run.py dashboard → Content")


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
