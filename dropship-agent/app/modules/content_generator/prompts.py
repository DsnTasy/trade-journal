"""What we ask Claude to write, and the exact shape the answer must have."""

from __future__ import annotations

ANGLES = {
    "problem_solution": "Problem → solution: show the everyday annoyance, then the product fixing it.",
    "social_proof": ("Social proof WITHOUT fake reviews: no quotes, ratings, customer numbers or testimonials. "
                     "Instead show the relatable situation and why someone would choose it, and leave a clearly "
                     "marked slot '[REAL CUSTOMER CLIP, only if you have one with permission]'."),
    "demo": "Demo: a straightforward show-and-tell of how it works, step by step.",
}

RULES = """Hard rules (UK advertising and consumer law):
- Use ONLY the facts provided. Never invent specifications, materials, sizes, results, statistics or awards.
- If a useful detail is unknown, write a placeholder like [CHECK: material] instead of guessing.
- No superlatives you can't prove ("best", "#1", "the only", "revolutionary", "guaranteed").
- No health, medical, safety, certification or environmental claims unless they are listed in the facts.
- No reviews, ratings, testimonials, customer counts or quotes. They don't exist yet.
- No urgency or scarcity ("only X left", "selling fast", "today only", "limited time").
- No "was" prices, discounts or "free delivery" unless listed in the facts.
- Delivery times must match the supplier facts exactly. Returns: state the store's returns window.
- Write all copy fresh in your own words. Never reuse a competitor's wording."""

SYSTEM = (
    "You are a careful UK e-commerce copywriter. You write clear, honest, original copy in the brand "
    "voice provided. Honesty beats hype: a beginner store must not get into trouble with the ASA or "
    "Trading Standards.\n\n" + RULES
)

_str = {"type": "string"}
_strs = {"type": "array", "items": _str}

PRODUCT_PAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "title": _str,
        "short_description": _str,
        "bullets": _strs,
        "description": _str,
        "faqs": {"type": "array", "items": {
            "type": "object", "properties": {"question": _str, "answer": _str},
            "required": ["question", "answer"], "additionalProperties": False}},
        "seo_title": _str,
        "meta_description": _str,
        "tags": _strs,
    },
    "required": ["title", "short_description", "bullets", "description", "faqs", "seo_title",
                 "meta_description", "tags"],
    "additionalProperties": False,
}

AD_SCRIPTS_SCHEMA = {
    "type": "object",
    "properties": {
        "scripts": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "angle": {"type": "string", "enum": list(ANGLES)},
                "hook": _str,
                "body": _str,
                "cta": _str,
                "on_screen_text": _strs,
                "shots": _strs,
                "duration_seconds": {"type": "integer"},
                "platform_notes": {"type": "object", "properties": {"tiktok": _str, "reels": _str, "shorts": _str},
                                   "required": ["tiktok", "reels", "shorts"], "additionalProperties": False},
            },
            "required": ["angle", "hook", "body", "cta", "on_screen_text", "shots", "duration_seconds",
                         "platform_notes"],
            "additionalProperties": False}},
    },
    "required": ["scripts"],
    "additionalProperties": False,
}

PAGE_TASK = """Write a product page for our Shopify store.

Requirements:
- title: max 70 characters, plain and descriptive.
- short_description: 1-2 sentences.
- bullets: 4-6 short, concrete benefit/feature bullets.
- description: 2-4 short paragraphs (plain text, blank line between paragraphs). Include delivery and returns.
- faqs: 3-5 genuinely useful questions (delivery, returns, use, care) with factual answers.
- seo_title: max 60 characters. meta_description: max 155 characters.
- tags: 3-8 lowercase tags.

Facts (the only things you may state as true):
{facts}"""

ADS_TASK = """Write exactly 3 short vertical (9:16) video ad scripts, one for each angle:
{angles}

Each script:
- hook: what's said/shown in the first 3 seconds (max ~10 words).
- body: the voiceover after the hook (15-35 seconds when read aloud).
- cta: a calm call to action (no urgency).
- on_screen_text: 3-6 short caption lines.
- shots: 4-8 simple shots someone could film with a phone or take from supplier-licensed footage.
  Never fake customers or fake unboxings by "customers".
- duration_seconds: 15-45.
- platform_notes: one line each for TikTok, Instagram Reels and YouTube Shorts (length, style, caption tips).

Facts (the only things you may state as true):
{facts}"""

LIMITS = {"title": 70, "seo_title": 60, "meta_description": 155}
