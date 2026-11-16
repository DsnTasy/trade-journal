"""UK compliance flags.

This is a keyword screen, not legal advice. Anything flagged here is marked
"needs manual compliance review" before it can move forward.
"""

from __future__ import annotations

import re

# (flag id, words to look for, what to check)
RULES: list[tuple[str, list[str], str]] = [
    ("electrical", ["electric", "usb", "led", "charger", "plug", "lamp", "light", "heater", "bluetooth",
                    "speaker", "fan", "rechargeable", "wireless", "gadget", "electronic"],
     "Electrical product: needs UKCA/CE marking and must meet the Electrical Equipment (Safety) "
     "Regulations; check plugs are UK-type and fused."),
    ("batteries", ["battery", "batteries", "lithium", "power bank", "rechargeable"],
     "Contains batteries: shipping restrictions apply, and battery regulations (labelling, take-back) apply."),
    ("toys", ["toy", "toys", "kids", "kid", "child", "children", "baby", "infant", "toddler"],
     "Children's/toy product: Toy Safety Regulations, UKCA/CE marking, age warnings and choking-hazard checks."),
    ("cosmetics", ["cream", "serum", "skincare", "skin care", "cosmetic", "cosmetics", "lotion", "makeup",
                   "hair dye", "shampoo", "perfume", "nail"],
     "Cosmetic: UK Cosmetics Regulation needs a UK Responsible Person, a safety assessment and SCPN notification."),
    ("food_contact", ["kitchen", "cup", "mug", "bottle", "lunch box", "utensil", "cutting board", "food",
                      "baking", "straw", "plate", "bowl"],
     "Food-contact item: materials must be food-safe (food contact materials rules); ask the supplier for test reports."),
    ("health_claims", ["posture", "massage", "massager", "pain", "therapy", "medical", "orthopedic", "relief",
                       "slimming", "weight loss"],
     "Health-related: no medical claims allowed unless it's a registered medical device; ad copy needs care."),
    ("supplements", ["supplement", "supplements", "vitamin", "vitamins", "protein", "capsule", "gummies"],
     "Food supplement: food law, labelling and health-claim rules apply. High risk for a new store."),
    ("hazardous", ["laser", "magnet", "magnets", "knife", "knives", "blade", "pepper spray", "vape", "e-cig"],
     "Restricted or hazardous item: may be illegal to sell or ship, or need age checks. Check before anything else."),
]

# Map compliance flags to the plain category names used in config `avoid_categories`.
CATEGORY_OF_FLAG = {
    "electrical": "electrical", "batteries": "electrical", "toys": "toys",
    "cosmetics": "cosmetics", "supplements": "supplements",
}


def check(name: str, category: str = "", keywords: list[str] | None = None) -> list[dict]:
    """Return a list of {"flag", "message", "matched"} for anything that needs review."""
    text = " ".join([name, category, *(keywords or [])]).lower()
    flags = []
    for flag_id, words, message in RULES:
        matched = sorted({w for w in words if re.search(rf"\b{re.escape(w)}\b", text)})
        if matched:
            flags.append({"flag": flag_id, "message": message, "matched": matched})
    return flags


def avoided(flags: list[dict], category: str, avoid_categories: list[str]) -> list[str]:
    """Which of your 'avoid' categories this product falls into (by flag or by its category column)."""
    avoid = {c.strip().lower() for c in avoid_categories}
    hits = {CATEGORY_OF_FLAG[f["flag"]] for f in flags if CATEGORY_OF_FLAG.get(f["flag"]) in avoid}
    if category.strip().lower() in avoid:
        hits.add(category.strip().lower())
    return sorted(hits)
