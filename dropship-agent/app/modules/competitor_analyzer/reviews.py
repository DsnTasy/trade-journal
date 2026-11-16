"""Review THEMES from reviews you paste in. Only counts are kept; the review text is discarded.

Simple keyword rules (free, predictable). Each review is classed positive or negative,
then we count which themes come up in each.
"""

from __future__ import annotations

import re

THEMES: dict[str, list[str]] = {
    "delivery speed": ["delivery", "deliver", "arrived", "shipping", "dispatch", "took weeks", "late", "slow", "quick", "fast"],
    "product quality": ["quality", "well made", "cheap", "flimsy", "sturdy", "durable", "build"],
    "broke / faulty": ["broke", "broken", "faulty", "stopped working", "defective", "fell apart", "damaged"],
    "as described": ["as described", "not as described", "looks like the picture", "different from", "smaller than",
                     "not what i", "misleading"],
    "value for money": ["value", "price", "worth", "overpriced", "expensive", "bargain"],
    "size / fit": ["size", "fit", "fits", "too small", "too big", "sizing"],
    "customer service": ["customer service", "support", "replied", "response", "refund", "return", "contact"],
    "packaging": ["packaging", "packaged", "box", "wrapped"],
}
NEGATIVE = re.compile(r"\b(not|never|no|broke|broken|poor|bad|terrible|awful|disappoint\w*|waste|refund|late|"
                      r"slow|cheap|flimsy|faulty|useless|worst|1 star|one star|damaged|wrong)\b", re.I)
POSITIVE = re.compile(r"\b(great|love|excellent|perfect|amazing|good|happy|recommend|brilliant|fast|quick|"
                      r"sturdy|well made|5 stars?|five stars?)\b", re.I)


def split_reviews(text: str) -> list[str]:
    parts = re.split(r"\n\s*\n|\n(?=\s*[-•*★])", text.strip())
    if len(parts) == 1:
        parts = text.strip().splitlines()
    return [p.strip() for p in parts if len(p.strip()) > 3]


def themes(text: str) -> dict:
    reviews = split_reviews(text)
    counts = {t: {"positive": 0, "negative": 0} for t in THEMES}
    neg_reviews = 0
    for r in reviews:
        negative = len(NEGATIVE.findall(r)) > len(POSITIVE.findall(r))
        neg_reviews += negative
        low = r.lower()
        for theme, words in THEMES.items():
            if any(re.search(rf"\b{re.escape(w)}\b", low) for w in words):
                counts[theme]["negative" if negative else "positive"] += 1
    counts = {t: c for t, c in counts.items() if c["positive"] or c["negative"]}
    return {"reviews_analysed": len(reviews), "negative_reviews": neg_reviews, "themes": counts}
