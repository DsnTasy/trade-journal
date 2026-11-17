"""Setup checklist: what's ready, what's missing, and how to fix it. Read-only; changes nothing."""

from __future__ import annotations

import importlib.metadata as md
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from app.core.config import PROJECT_ROOT, Settings


@dataclass
class Check:
    area: str
    name: str
    ok: bool
    detail: str
    fix: str = ""
    needed_for: str = "everything"  # which feature needs it


def _pkg_ok(requirements: Path) -> tuple[bool, str]:
    missing, wrong = [], []
    for line in requirements.read_text(encoding="utf-8").splitlines():
        line = line.split("#")[0].strip()
        if "==" not in line:
            continue
        name, want = line.split("==")
        try:
            have = md.version(name)
        except md.PackageNotFoundError:
            missing.append(name)
            continue
        if have != want:
            wrong.append(f"{name} {have} (want {want})")
    if missing or wrong:
        return False, "; ".join([*(f"missing {m}" for m in missing), *wrong])
    return True, "all pinned packages installed"


def _show(path: Path) -> str:
    """Path relative to the project if it's inside it, otherwise the full path (never crashes)."""
    try:
        return path.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return str(path)


def run_checks(settings: Settings) -> list[Check]:
    c: list[Check] = []
    v = sys.version_info
    c.append(Check("Basics", "Python 3.11+", v >= (3, 11), f"Python {v.major}.{v.minor}.{v.micro}",
                   "Install Python 3.11 or newer from python.org (tick 'Add to PATH' on Windows)."))
    ok, detail = _pkg_ok(PROJECT_ROOT / "requirements.txt")
    c.append(Check("Basics", "Packages", ok, detail, "Run: pip install -r requirements.txt"))
    env = PROJECT_ROOT / ".env"
    c.append(Check("Basics", ".env file", env.exists(), "found" if env.exists() else "not found",
                   "Copy .env.example to .env (then fill in keys as you need them)."))
    c.append(Check("Basics", "Safety mode", True,
                   "DRY RUN: nothing real happens" if settings.dry_run else "LIVE: approved actions really happen"))
    c.append(Check("Basics", "Monthly API budget", settings.budget.monthly_cap_gbp > 0,
                   f"£{settings.budget.monthly_cap_gbp:.2f} cap, warning at {settings.budget.warn_at_fraction:.0%}"))

    cand = settings.path(settings.research.candidates_file)
    trends = settings.path(settings.research.trends_dir)
    n_trends = len(list(trends.glob("*.csv"))) if trends.exists() else 0
    c.append(Check("Research", "Product ideas list", cand.exists(), _show(cand) if cand.exists()
                   else f"{_show(cand)} not found",
                   "Copy research/candidates.example.csv to research/candidates.csv and add ideas.", "research"))
    c.append(Check("Research", "Google Trends exports", n_trends > 0, f"{n_trends} file(s) in {_show(trends)}/",
                   "Export 'Interest over time' CSVs from trends.google.com into research/trends/.", "research"))
    sup = settings.path(settings.suppliers.suppliers_file)
    c.append(Check("Suppliers", "Suppliers list", sup.exists(), _show(sup) if sup.exists() else f"{_show(sup)} not found",
                   "Copy research/suppliers.example.csv to research/suppliers.csv.", "suppliers"))

    c.append(Check("AI", "Anthropic API key", bool(settings.anthropic_api_key),
                   "set" if settings.anthropic_api_key else "not set",
                   "Get a key at console.anthropic.com → API Keys, paste after ANTHROPIC_API_KEY= in .env.",
                   "product pages, ad scripts, AI summaries"))

    ff = shutil.which(settings.video.ffmpeg_path) or (Path(settings.video.ffmpeg_path).exists() and settings.video.ffmpeg_path)
    fp = shutil.which(settings.video.ffprobe_path) or (Path(settings.video.ffprobe_path).exists() and settings.video.ffprobe_path)
    c.append(Check("Video", "ffmpeg + ffprobe", bool(ff and fp), "found" if ff and fp else "not found",
                   "Windows: winget install Gyan.FFmpeg · Mac: brew install ffmpeg (then reopen the terminal).", "videos"))

    shop_ok = bool(settings.store.shop)
    creds_ok = bool(settings.shopify_access_token or (settings.shopify_client_id and settings.shopify_client_secret))
    c.append(Check("Shopify", "Store name", shop_ok, settings.store.shop or "store.shop is empty in config.yaml",
                   "Set store.shop in config.yaml to your myshopify subdomain.", "publishing"))
    c.append(Check("Shopify", "App credentials", creds_ok, "set" if creds_ok else "not set",
                   "README → Connect Shopify (Dev Dashboard app, then SHOPIFY_CLIENT_ID / SECRET in .env).", "publishing"))

    meta = settings.research.meta_enabled
    c.append(Check("Optional", "Meta Ad Library", (not meta) or bool(settings.meta_ad_library_token),
                   "off" if not meta else ("token set" if settings.meta_ad_library_token else "enabled but no token"),
                   "Add META_AD_LIBRARY_TOKEN to .env, or set research.meta_enabled: false.", "ad saturation data"))
    n = settings.notifications
    email_ok = (not n.email_enabled) or bool(n.email_to and n.smtp_host and n.smtp_user and settings.smtp_password)
    c.append(Check("Optional", "Email notifications", email_ok,
                   "off (dashboard only)" if not n.email_enabled else ("set up" if email_ok else "enabled but incomplete"),
                   "Fill notifications.* in config.yaml and SMTP_PASSWORD in .env.", "email alerts"))
    s = settings.schedule
    c.append(Check("Optional", "Scheduled research", True,
                   f"{s.research.every} at {s.research.at} ({'on' if s.enabled else 'off'})"))
    return c
