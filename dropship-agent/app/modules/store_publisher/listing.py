"""Build the Shopify product from your APPROVED content. Pure functions, easy to test."""

from __future__ import annotations

import hashlib
import json
import re

from markupsafe import escape

from app.modules.video_maker.media import slug


def description_html(page: dict, returns_days: int) -> str:
    """Product page → simple, safe HTML (all text escaped)."""
    parts = [f"<p>{escape(page.get('short_description', ''))}</p>"] if page.get("short_description") else []
    bullets = page.get("bullets") or []
    if bullets:
        parts.append("<ul>" + "".join(f"<li>{escape(b)}</li>" for b in bullets) + "</ul>")
    for para in re.split(r"\n\s*\n", page.get("description", "").strip()):
        if para.strip():
            parts.append(f"<p>{escape(para.strip())}</p>")
    faqs = page.get("faqs") or []
    if faqs:
        parts.append("<h3>FAQs</h3>")
        parts += [f"<p><strong>{escape(f['question'])}</strong><br>{escape(f['answer'])}</p>" for f in faqs]
    if f"{returns_days}-day" not in " ".join(parts) and f"{returns_days} day" not in " ".join(parts):
        parts.append(f"<p>Returns accepted within {returns_days} days.</p>")
    return "\n".join(parts)


def product_input(product_name: str, category: str | None, page: dict, *, vendor: str, returns_days: int) -> dict:
    """ProductCreateInput / ProductUpdateInput fields (always created as DRAFT)."""
    return {
        "title": page["title"],
        "descriptionHtml": description_html(page, returns_days),
        "productType": (category or "").capitalize() or None,
        "vendor": vendor,
        "tags": sorted({t.strip().lower() for t in page.get("tags", []) if t.strip()}),
        "seo": {"title": page.get("seo_title") or page["title"], "description": page.get("meta_description") or ""},
        "handle": slug(page["title"]) or slug(product_name),
    }


def variant_input(variant_id: str, price: float, *, track_inventory: bool) -> dict:
    """ProductVariantsBulkInput. No compareAtPrice: we never show a 'was' price."""
    return {
        "id": variant_id,
        "price": f"{price:.2f}",
        "inventoryPolicy": "DENY" if track_inventory else "CONTINUE",
        "inventoryItem": {"tracked": track_inventory, "requiresShipping": True},
    }


def payload_hash(product: dict, price: float, media_keys: list[str]) -> str:
    blob = json.dumps({"p": product, "price": round(price, 2), "m": sorted(media_keys)}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()
