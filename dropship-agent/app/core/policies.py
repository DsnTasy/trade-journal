"""Fill the UK policy templates (policies/*.md) with your business details from brand.yaml."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from app.core.config import PROJECT_ROOT


class _Missing(dict):
    def __missing__(self, key: str) -> str:
        return f"[CHECK: {key.replace('_', ' ')}]"


def fill(template: str, brand: dict) -> str:
    biz = brand.get("business") or {}
    promises = brand.get("promises") or {}
    values = _Missing({k: v for k, v in {
        "store_name": brand.get("store_name") if brand.get("store_name") not in (None, "", "Your Store Name") else None,
        "legal_name": biz.get("legal_name"),
        "trading_address": biz.get("trading_address"),
        "contact_email": biz.get("contact_email"),
        "return_address": biz.get("return_address"),
        "returns_days": str(promises.get("returns_days", 14)),
        "date": date.today().strftime("%d %B %Y"),
        "company_number_text": f" (company number {biz['company_number']})" if biz.get("company_number") else "",
    }.items() if v not in (None, "")})
    if "company_number_text" not in values:
        values["company_number_text"] = ""
    return template.format_map(values)


def generate(brand: dict, out_dir: Path) -> list[tuple[Path, int]]:
    """Writes filled policies; returns (file, number of [CHECK] items left) for each."""
    out_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for src in sorted((PROJECT_ROOT / "policies").glob("*.md")):
        if src.name.lower() == "readme.md":
            continue
        text = fill(src.read_text(encoding="utf-8"), brand)
        dest = out_dir / src.name
        dest.write_text(text, encoding="utf-8")
        results.append((dest, text.count("[CHECK")))
    return results
