"""Turn competitor facts into plain-English insights: what they do well, and gaps we could fill.

All wording here is ours. Inputs are only numbers/yes-no facts and review theme counts.
"""

from __future__ import annotations


def price_band(median: float) -> str:
    return "budget" if median < 15 else "mid-range" if median <= 40 else "premium"


def build(facts: dict, review_themes: dict | None = None, our_prices: dict[str, float | None] | None = None) -> dict:
    well: list[str] = []
    gaps: list[str] = []
    compare: list[str] = []
    f = facts

    if f.get("price_median") is not None:
        band = price_band(f["price_median"])
        well.append(f"{band.capitalize()} positioning: typical price £{f['price_median']:.2f}"
                    + (f" (range £{f['price_min']:.2f}-£{f['price_max']:.2f})." if f.get("price_min") is not None else "."))

    count = f.get("product_count")
    if count is not None:
        if count <= 10:
            well.append(f"Focused range of {count} products: clear, simple store.")
        elif count >= 200:
            gaps.append(f"Broad general store ({count} products): a focused niche brand can feel more trustworthy.")

    share = f.get("discounted_share")
    if share is not None:
        if share >= 0.5:
            gaps.append(f"{share:.0%} of products shown as 'on sale'"
                        + (f" (average {f['avg_discount_pct']:.0f}% off)" if f.get("avg_discount_pct") else "")
                        + ". Permanent sales can breach UK pricing rules: compete with honest, stable prices.")
        elif share > 0:
            well.append(f"Uses discounts selectively ({share:.0%} of products).")

    new = f.get("new_last_90_days")
    if new and count:
        if new / count >= 0.3:
            well.append(f"Adds products often ({new} in the last 90 days): likely testing what sells.")

    thr = f.get("free_shipping_threshold_gbp")
    if thr is not None:
        well.append("Free UK delivery on everything." if thr == 0 else f"Free delivery over £{thr:.2f}.")
    elif any(k in f for k in ("delivery_days_max", "return_window_days")):
        gaps.append("No free-delivery offer found: a simple free-delivery threshold could be an edge.")

    dmax = f.get("delivery_days_max")
    if dmax is not None:
        if dmax <= 5:
            well.append(f"Fast delivery promise (up to {dmax} days): you'd need a UK/EU supplier to match.")
        elif dmax >= 10:
            gaps.append(f"Slow delivery (up to {dmax} days): a UK-warehouse supplier could beat this clearly.")

    if f.get("ships_from_uk") is False:
        gaps.append("Appears to ship from abroad: 'dispatched from the UK' is a real selling point if you can offer it.")
    elif f.get("ships_from_uk") is True:
        well.append("Ships from the UK.")

    ret = f.get("return_window_days")
    if ret is not None:
        if ret < 14:
            gaps.append(f"Return window of {ret} days is below the 14-day cancellation right UK online shoppers have.")
        elif ret >= 30:
            well.append(f"Generous {ret}-day returns: customers may expect similar.")

    # Review themes: what customers complain about = what we should get right.
    themes = (review_themes or {}).get("themes", {})
    complaints = sorted(((t, c["negative"]) for t, c in themes.items() if c["negative"]), key=lambda x: -x[1])
    praise = sorted(((t, c["positive"]) for t, c in themes.items() if c["positive"]), key=lambda x: -x[1])
    for theme, n in complaints[:3]:
        gaps.append(f"Customers complain about {theme} ({n} negative review{'s' if n > 1 else ''}): get this right.")
    for theme, n in praise[:2]:
        well.append(f"Customers praise {theme} ({n} review{'s' if n > 1 else ''}).")

    # Our products vs theirs
    for ours, o in (f.get("overlap") or {}).items():
        line = (f"They sell {o['matches']} item(s) like your '{ours}' at a typical £{o['price_median']:.2f} "
                f"(£{o['price_min']:.2f}-£{o['price_max']:.2f}).")
        mine = (our_prices or {}).get(ours)
        if mine is not None:
            diff = mine - o["price_median"]
            if abs(diff) < 0.01:
                line += f" Your planned £{mine:.2f} matches theirs."
            else:
                line += f" Your planned £{mine:.2f} is £{abs(diff):.2f} {'above' if diff > 0 else 'below'} theirs."
        compare.append(line)

    return {"does_well": well, "gaps": gaps, "vs_your_products": compare}
