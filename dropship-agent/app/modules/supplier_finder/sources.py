"""Supplier data sources.

Phase 3 reads research/suppliers.csv: numbers you copy from supplier listings
(CJ Dropshipping, AliExpress, Alibaba, Zendrop, Spocket, UK/EU wholesalers...).

Why manual? CJ's API needs your account key (we'll add a connector once you have one,
and test it against the real API), AliExpress needs an approved developer app, and
Zendrop/Spocket have no public API. Copying ~10 numbers per supplier is quick and allowed.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from pathlib import Path

from app.core.config import BusinessConfig
from app.modules.product_research.sources import SourceError, norm

WAREHOUSES = {"uk": "UK", "gb": "UK", "united kingdom": "UK", "eu": "EU", "us": "US", "usa": "US",
              "cn": "CN", "china": "CN",
              # EU countries count as EU
              **{c: "EU" for c in ("de", "germany", "fr", "france", "es", "spain", "it", "italy", "nl",
                                   "netherlands", "pl", "poland", "ie", "ireland", "cz", "be", "belgium",
                                   "pt", "portugal", "at", "austria")}}
YES = {"yes", "y", "true", "1"}
NO = {"no", "n", "false", "0"}


@dataclass
class SupplierRow:
    product: str
    name: str
    fields: dict = field(default_factory=dict)  # matches Supplier model column names
    line: int = 0


def _yes_no(raw: str) -> str | None:
    v = raw.strip().lower()
    return "yes" if v in YES else "no" if v in NO else None


def load_suppliers(path: Path, business: BusinessConfig) -> tuple[list[SupplierRow], list[str]]:
    if not path.exists():
        raise SourceError(f"No suppliers file at {path}. Copy research/suppliers.example.csv to "
                          f"research/suppliers.csv and add suppliers for your shortlisted products.")
    rates = {"GBP": 1.0, "USD": business.usd_to_gbp, "EUR": business.eur_to_gbp}
    rows: list[SupplierRow] = []
    problems: list[str] = []
    seen: set[tuple[str, str]] = set()
    text = path.read_text(encoding="utf-8-sig")
    for line, raw in enumerate(csv.DictReader(io.StringIO(text)), start=2):
        r = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()}
        product, name = r.get("product", ""), r.get("supplier_name", "")
        if not product or not name:
            if any(r.values()):
                problems.append(f"Line {line}: needs both 'product' and 'supplier_name'; skipped.")
            continue
        if product.upper().startswith("EXAMPLE"):
            continue
        key = (norm(product), norm(name))
        if key in seen:
            problems.append(f"Line {line}: '{name}' is listed twice for '{product}'; second one skipped.")
            continue
        seen.add(key)

        currency = (r.get("currency") or "GBP").upper()
        if currency not in rates:
            problems.append(f"Line {line}: unknown currency '{currency}' (use GBP, USD or EUR); prices ignored.")
        rate = rates.get(currency)
        f: dict = {
            "platform": r.get("platform") or None,
            "url": r.get("url") or None,
            "notes": r.get("notes") or None,
            "warehouse": WAREHOUSES.get(r.get("warehouse", "").lower(), r.get("warehouse").upper() or None)
            if r.get("warehouse") else None,
            "has_ukca_ce": _yes_no(r.get("has_ukca_ce", "")),
            "has_safety_docs": _yes_no(r.get("has_safety_docs", "")),
            "has_branding_info": _yes_no(r.get("has_branding_info", "")),
            "source": f"manual entry ({r.get('source_note') or 'no source given'})",
        }

        def number(col: str, kind=float, r=r, line=line):  # bind this row explicitly
            val = r.get(col, "").replace("£", "").replace("$", "").replace("€", "").replace(",", "")
            if val == "":
                return None
            try:
                n = kind(float(val))
            except ValueError:
                problems.append(f"Line {line}: '{col}' value '{val}' is not a number; ignored.")
                return None
            if n < 0:
                problems.append(f"Line {line}: '{col}' is negative; ignored.")
                return None
            return n

        for col in ("unit_cost", "shipping_cost"):
            n = number(col)
            if n is not None and rate is not None:
                f[f"{col}_gbp"] = round(n * rate, 2)
                if currency != "GBP":
                    f.setdefault("original_price", "")
                    f["original_price"] = (f["original_price"] + f" {col.replace('_', ' ')} {n:.2f} {currency}").strip()
        if currency != "GBP" and rate is not None:
            f["source"] += f"; converted from {currency} at {rate}"
        for col, kind in (("shipping_days_min", int), ("shipping_days_max", int), ("review_count", int),
                          ("orders_sold", int), ("moq", int), ("return_policy_days", int),
                          ("rating", float), ("responds_within_hours", float)):
            n = number(col, kind)
            if n is not None:
                f[col] = n
        if f.get("rating") is not None and f["rating"] > 5:
            problems.append(f"Line {line}: rating {f['rating']} is above 5; ignored (use the 0-5 star rating).")
            f["rating"] = None
        if (f.get("shipping_days_min") is not None and f.get("shipping_days_max") is not None
                and f["shipping_days_min"] > f["shipping_days_max"]):
            f["shipping_days_min"], f["shipping_days_max"] = f["shipping_days_max"], f["shipping_days_min"]
        rows.append(SupplierRow(product=product, name=name, fields=f, line=line))
    return rows, problems
