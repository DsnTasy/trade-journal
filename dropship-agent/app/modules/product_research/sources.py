"""Where product research data comes from.

Every number becomes a `Signal` that records its source and when it was collected.
If a source has nothing for a product, it returns nothing: we never fill gaps with guesses.

Sources in this phase (all allowed under each provider's rules):
- CandidatesCsv:   your own list of product ideas + numbers you looked up (manual entry).
- GoogleTrendsCsv: CSV files you export from trends.google.com (manual export, no scraping).
- MetaAdLibrary:   Meta's official Ad Library API (optional, needs a token).
"""

from __future__ import annotations

import csv
import io
import json
import re
import statistics
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import httpx


def norm(text: str) -> str:
    """Normalise a product name or keyword for matching: lowercase, single spaces."""
    return re.sub(r"\s+", " ", text.strip().lower())


@dataclass
class Signal:
    metric: str
    value: float | None  # None = the source was checked but had no value
    source: str
    unit: str | None = None
    note: str | None = None
    retrieved_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class Candidate:
    name: str
    category: str = ""
    keywords: list[str] = field(default_factory=list)
    notes: str = ""
    signals: list[Signal] = field(default_factory=list)

    @property
    def search_terms(self) -> list[str]:
        return [norm(k) for k in (self.keywords or [self.name]) if k.strip()]


class SourceError(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# 1. Your candidates file
# --------------------------------------------------------------------------- #
CANDIDATE_NUMERIC_COLUMNS = {
    # csv column        -> (metric name, unit)
    "est_unit_cost_gbp": ("unit_cost_gbp", "GBP"),
    "est_shipping_gbp": ("shipping_gbp", "GBP"),
    "est_sell_price_gbp": ("sell_price_gbp", "GBP inc. VAT"),
    "competitor_stores": ("competitor_stores", "stores"),
    "ads_seen": ("ads_seen", "advertisers"),
}


def load_candidates(path: Path) -> tuple[list[Candidate], list[str]]:
    """Read research/candidates.csv. Returns (candidates, problems found)."""
    if not path.exists():
        raise SourceError(
            f"No candidates file at {path}. Copy research/candidates.example.csv to "
            f"research/candidates.csv and add your product ideas."
        )
    problems: list[str] = []
    candidates: dict[str, Candidate] = {}
    text = path.read_text(encoding="utf-8-sig")  # utf-8-sig handles Excel's hidden BOM
    for line_no, row in enumerate(csv.DictReader(io.StringIO(text)), start=2):
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
        name = row.get("name", "")
        if not name:
            continue
        if name.upper().startswith("EXAMPLE"):
            problems.append(f"Line {line_no}: skipped example row '{name}'.")
            continue
        key = norm(name)
        if key in candidates:
            problems.append(f"Line {line_no}: duplicate product '{name}' skipped.")
            continue
        source_note = row.get("source_note") or "no source given"
        cand = Candidate(
            name=name,
            category=row.get("category", ""),
            keywords=[k.strip() for k in row.get("keywords", "").split(";") if k.strip()],
            notes=row.get("notes", ""),
        )
        for col, (metric, unit) in CANDIDATE_NUMERIC_COLUMNS.items():
            raw = row.get(col, "").replace("£", "").replace(",", "")
            if raw == "":
                continue  # blank = unknown; not the same as zero
            try:
                value = float(raw)
            except ValueError:
                problems.append(f"Line {line_no}: '{col}' value '{raw}' is not a number; ignored.")
                continue
            if value < 0:
                problems.append(f"Line {line_no}: '{col}' is negative; ignored.")
                continue
            cand.signals.append(Signal(metric=metric, value=value, unit=unit,
                                       source=f"manual entry ({source_note})"))
        candidates[key] = cand
    return list(candidates.values()), problems


# --------------------------------------------------------------------------- #
# 2. Google Trends CSV exports
# --------------------------------------------------------------------------- #
@dataclass
class TrendSeries:
    term: str
    geo: str
    values: list[float]
    file: str


def parse_trends_csv(path: Path) -> list[TrendSeries]:
    """Parse a Google Trends 'Interest over time' export (multiTimeline.csv).

    The file has a title line, a blank line, then a header like:
        Week,cat bed: (United Kingdom),dog bed: (United Kingdom)
    Values are 0-100 and only comparable WITHIN one export. '<1' means below 1.
    """
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    header_idx = next((i for i, ln in enumerate(lines)
                       if ln.split(",")[0].strip() in ("Week", "Month", "Day", "Time")), None)
    if header_idx is None:
        raise SourceError(f"{path.name} doesn't look like a Google Trends 'interest over time' export.")
    rows = list(csv.reader(lines[header_idx:]))
    header, data = rows[0], [r for r in rows[1:] if r and r[0].strip()]
    series: list[TrendSeries] = []
    for col in range(1, len(header)):
        m = re.match(r"\s*(.+?):\s*\((.+)\)\s*$", header[col])
        term, geo = (m.group(1), m.group(2)) if m else (header[col], "unknown")
        values: list[float] = []
        for r in data:
            if col >= len(r):
                continue
            cell = r[col].strip()
            if cell == "<1":
                values.append(0.5)
            elif cell:
                try:
                    values.append(float(cell))
                except ValueError:
                    continue
        series.append(TrendSeries(term=norm(term), geo=geo, values=values, file=path.name))
    return series


def trend_signals(series: TrendSeries, file_date: datetime) -> list[Signal]:
    src = f"Google Trends CSV export '{series.file}' ({series.geo})"
    v = series.values
    out: list[Signal] = []
    if len(v) >= 4:
        out.append(Signal("search_interest", round(statistics.fmean(v[-4:]), 1), src, unit="0-100",
                          note="Average of the last 4 data points. Only comparable within the same export.",
                          retrieved_at=file_date))
    if len(v) >= 24:
        recent, before = statistics.fmean(v[-12:]), statistics.fmean(v[-24:-12])
        change = (recent - before) / max(before, 1.0)
        out.append(Signal("trend_change_pct", round(change * 100, 1), src, unit="%",
                          note="Last 12 points vs the 12 before.", retrieved_at=file_date))
    if len(v) >= 52 and statistics.fmean(v) > 0:
        out.append(Signal("seasonality_ratio", round(max(v) / statistics.fmean(v), 2), src, unit="peak/avg",
                          note="Peak interest divided by average over the whole export.", retrieved_at=file_date))
    return out


def load_trends(trends_dir: Path) -> tuple[dict[str, list[Signal]], list[str]]:
    """Read every CSV in the trends folder. Returns ({search term: signals}, problems)."""
    found: dict[str, list[Signal]] = {}
    problems: list[str] = []
    if not trends_dir.exists():
        return found, problems
    for path in sorted(trends_dir.glob("*.csv")):
        file_date = datetime.fromtimestamp(path.stat().st_mtime, UTC)
        try:
            for s in parse_trends_csv(path):
                if s.term in found:
                    problems.append(f"'{s.term}' appears in more than one trends file; using {path.name}.")
                found[s.term] = trend_signals(s, file_date)
        except SourceError as exc:
            problems.append(str(exc))
    return found, problems


# --------------------------------------------------------------------------- #
# 3. Meta Ad Library API (optional)
# --------------------------------------------------------------------------- #
class MetaAdLibrary:
    """Counts active ads for a search term using Meta's official Ad Library API.

    Important limits (stated honestly):
    - For non-political ads, the API only covers ads delivered in the EU, so we
      check EU countries as a stand-in for the UK.
    - We read at most `max_pages` pages, so counts are "at least" numbers.
    """

    BASE = "https://graph.facebook.com"

    def __init__(self, token: str, api_version: str, countries: list[str], max_pages: int = 3,
                 delay_seconds: float = 1.0, client: httpx.Client | None = None):
        self.token = token
        self.url = f"{self.BASE}/{api_version}/ads_archive"
        self.countries = countries
        self.max_pages = max_pages
        self.delay = delay_seconds
        self.client = client or httpx.Client(timeout=30, headers={"User-Agent": "dropship-agent research tool"})

    def signals_for(self, term: str) -> list[Signal]:
        params = {
            "access_token": self.token,
            "search_terms": term,
            "ad_reached_countries": json.dumps(self.countries),
            "ad_active_status": "ACTIVE",
            "ad_type": "ALL",
            "fields": "page_id,ad_creative_bodies",
            "limit": "100",
        }
        ads: list[dict] = []
        url: str | None = self.url
        pages = 0
        complete = False
        while url and pages < self.max_pages:
            resp = self.client.get(url, params=params if pages == 0 else None)
            if resp.status_code != 200:
                raise SourceError(f"Meta Ad Library returned {resp.status_code}: {resp.text[:200]}")
            body = resp.json()
            ads.extend(body.get("data", []))
            url = body.get("paging", {}).get("next")
            pages += 1
            if not url:
                complete = True
            elif self.delay:
                time.sleep(self.delay)  # be polite: don't hammer the API

        src = f"Meta Ad Library API (active ads in {', '.join(self.countries)}; EU used as UK proxy)"
        qualifier = "" if complete else f" At least: only the first {pages} page(s) were read."
        if not ads:
            return [Signal("meta_ads_active", 0, src, unit="ads", note="No active ads found.")]
        advertisers = {a.get("page_id") for a in ads if a.get("page_id")}
        bodies = [" ".join(a.get("ad_creative_bodies") or []).strip().lower() for a in ads]
        bodies = [b for b in bodies if b]
        dup_share = (1 - len(set(bodies)) / len(bodies)) if bodies else None
        out = [
            Signal("meta_ads_active", len(ads), src, unit="ads", note=qualifier.strip() or None),
            Signal("meta_advertisers", len(advertisers), src, unit="advertisers", note=qualifier.strip() or None),
        ]
        if dup_share is not None:
            out.append(Signal("meta_duplicate_creative_share", round(dup_share, 3), src, unit="0-1",
                              note="Share of ads whose text is a copy of another ad (same-ad saturation)."))
        return out
