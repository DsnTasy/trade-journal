"""Claim checker: flags risky or unprovable claims in product pages and ads, for UK rules.

Rule-based on purpose: predictable, free, and testable. It catches common problems; it is
NOT legal advice and can't prove a claim is fine. Severity:
  high   = must fix before approving (likely breaks UK advertising/consumer law)
  medium = needs evidence or a closer look
  low    = worth a second look

Main sources of the rules (in plain terms):
- ASA / CAP Code: you must hold evidence for any objective claim; no misleading ads;
  strict rules on health and medical claims.
- Consumer protection law (CPRs, now the DMCC Act 2024): bans fake reviews, false
  "limited time" or "only X left" pressure, and misleading prices.
- CMA Green Claims Code: environmental claims must be specific and provable.
- Consumer Contracts Regulations: 14-day right to cancel online orders.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


@dataclass
class Context:
    """Facts that decide whether some claims are true."""
    delivery_days_max: int | None = None  # chosen supplier's slowest delivery time
    returns_days: int = 14  # your returns promise (brand.yaml)
    free_delivery: bool = False  # do you really offer free delivery?
    guarantee: str = ""  # e.g. "30-day money-back guarantee" if you really offer one
    safety_docs_confirmed: bool = False  # supplier confirmed UKCA/CE + documents
    regulated: bool = False  # product has safety-rule compliance flags


@dataclass
class Rule:
    id: str
    severity: str
    pattern: re.Pattern
    message: str
    fix: str
    applies: object = None  # optional function(match, context) -> bool | str (str overrides severity)


def _re(words: str) -> re.Pattern:
    return re.compile(words, re.I)


RULES: list[Rule] = [
    Rule("placeholder", "high", _re(r"\[CHECK:[^\]]*\]"),
         "A fact still needs checking.", "Replace this with the real fact from the supplier, or remove the sentence."),
    Rule("fake_reviews", "high",
         _re(r"(\b\d[\d,]*\+?\s*(happy|satisfied|5[- ]star)\s*(customers|reviews|buyers)\b|\brated\s+\d(\.\d)?\s*(/\s*5|stars?|out of 5)"
             r"|\bcustomers (say|love|rave)\b|\b(thousands|millions) of (happy )?(customers|reviews|people)\b"
             r"|[\"“][^\"”]{10,}[\"”]\s*[-–—]\s*[A-Z][a-z]+)"),
         "Looks like a review, rating or customer count. Fake or unverifiable reviews are banned in the UK.",
         "Only use real reviews you can prove, shown honestly. Otherwise remove it."),
    Rule("fake_urgency", "high",
         _re(r"\b(only \d+ left|limited stock|selling (out )?fast|almost (gone|sold out)|ends (tonight|today|soon|at midnight)"
             r"|today only|last chance|hurry|while stocks? last|don'?t miss out|before it'?s gone|\d+ (people|others) (are )?(viewing|watching)"
             r"|limited[- ]time)\b"),
         "Pressure or scarcity claim. False urgency is banned under UK consumer law.",
         "Remove it, or use it only when it's literally true and you can prove it."),
    Rule("health_claim", "high",
         _re(r"\b(cures?|heals?|treats?|treatment|pain[- ]relief|reliev(e|es|ing)|prevents?|therapeutic|clinically"
             r"|medical[- ]grade|detox|boosts? (your )?immun\w*|weight[- ]loss|lose weight|anti[- ]?ageing|fat[- ]burn\w*"
             r"|reduces? (inflammation|anxiety|stress)|improves? (posture|sleep|circulation))\b"),
         "Health or medical claim. Only allowed with strong evidence, and medical claims only for authorised medical products.",
         "Describe what the product physically is and does, without health benefits."),
    Rule("safety_claim", "high",
         _re(r"\b(child[- ]?safe|safe for (kids|children|babies)|non[- ]toxic|bpa[- ]free|fda[- ]approved|ce[- ]approved"
             r"|ukca|ce mark(ed|ing)?|certified|tested (safe|to)|hypoallergenic|fire[- ]?proof)\b"),
         "Safety or certification claim. You need documents from the supplier to back it up.",
         "Keep it only if the supplier sent certificates or test reports; otherwise remove it.",
         applies=lambda m, c: "medium" if c.safety_docs_confirmed else True),
    Rule("superlative", "medium",
         _re(r"(\bbest\b(?! before)|\bbest[- ]selling\b|#1\b|\bno\.? ?1|number one|world'?s (best|first|leading)|unbeatable|the only"
             r"|guaranteed results|revolutionary|miracle|perfect for everyone|100% (effective|guaranteed|safe))(?!\w)"),
         "Unprovable 'best/only/#1' style claim. You'd need evidence to back it up.",
         "Say something specific and true instead, e.g. what it's made of or how it works."),
    Rule("absolute", "medium",
         _re(r"\b(indestructible|unbreakable|lasts forever|never (breaks|fails|wears)|always works|100% (waterproof|leak[- ]?proof))\b"),
         "Absolute claim. One customer proving it wrong makes it misleading.",
         "Use a measured, specific claim (e.g. 'water-resistant to IPX4' if the supplier confirms it)."),
    Rule("price_claim", "medium",
         _re(r"(\bwas £\s?\d|\brrp\b|\bsave £\s?\d|\b\d{1,2}% off\b|\bhalf price\b|\bslashed\b|\bclearance\b)"),
         "Price comparison. 'Was' prices and discounts must be genuine (sold at that price for a fair period).",
         "Only show a discount you can prove; otherwise just show the price."),
    Rule("green_claim", "medium",
         _re(r"\b(eco[- ]?friendly|sustainable|environmentally friendly|go green|greener|biodegradable|compostable|carbon[- ]neutral"
             r"|planet[- ]friendly|plastic[- ]free|recyclable|natural)\b"),
         "Environmental claim. It must be specific and provable (CMA Green Claims Code).",
         "Be specific (e.g. 'box made from recycled card') and only if the supplier confirms it."),
    Rule("no_returns", "high",
         _re(r"\b(no returns|non[- ]refundable|all sales final|no refunds)\b"),
         "UK online shoppers have a 14-day right to cancel. You can't remove it.",
         "State your real returns policy (at least 14 days)."),
    Rule("free_claim", "medium", _re(r"\bfree (uk |next[- ]day )?(delivery|shipping|p&p|postage)\b"),
         "Free delivery claim. Only allowed if you really offer it.",
         "Remove it, or turn on free_delivery in brand.yaml if you offer it.",
         applies=lambda m, c: not c.free_delivery),
    Rule("guarantee", "medium",
         _re(r"\b(money[- ]back guarantee|lifetime (warranty|guarantee)|\d+[- ]day guarantee|satisfaction guaranteed)\b"),
         "Guarantee claim. You must honour it exactly as stated.",
         "Only use the guarantee set in brand.yaml.",
         applies=lambda m, c: not (c.guarantee and m.group(0).lower() in c.guarantee.lower())),
    Rule("delivery_promise", "medium",
         _re(r"\b(next[- ]day|same[- ]day|24[- ]?h(ou)?r|(\d{1,2})[- ](working[- ])?days?) (delivery|shipping|dispatch)\b"
             r"|\b(fast|quick|speedy|express) (uk )?(delivery|shipping)\b|\barrives? in (\d{1,2}) days?\b"),
         "Delivery promise. It must match your supplier's real delivery time.",
         "Use the supplier's real delivery range.",
         applies=lambda m, c: _delivery_check(m, c)),
]


def _delivery_check(m: re.Match, c: Context):
    text = m.group(0).lower()
    promised = 1 if ("next" in text or "same" in text or "24" in text) else None
    num = re.search(r"(\d{1,2})", text)
    if promised is None and num:
        promised = int(num.group(1))
    if c.delivery_days_max is None:
        return True  # unknown supplier time: medium, check it
    if promised is not None and c.delivery_days_max > promised:
        return "high"  # promise is faster than the supplier can deliver
    if promised is None and c.delivery_days_max > 7:
        return "high"  # "fast delivery" with a slow supplier
    return False  # consistent with the supplier: fine


@dataclass
class Finding:
    rule: str
    severity: str
    field: str
    match: str
    message: str
    fix: str

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def check_text(text: str, field_name: str, ctx: Context) -> list[Finding]:
    findings: list[Finding] = []
    for rule in RULES:
        for m in rule.pattern.finditer(text or ""):
            severity = rule.severity
            if rule.applies is not None:
                verdict = rule.applies(m, ctx)
                if verdict is False:
                    continue
                if isinstance(verdict, str):
                    severity = verdict
            findings.append(Finding(rule.id, severity, field_name, m.group(0), rule.message, rule.fix))
    return findings


def _walk(value, path: str):
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from _walk(v, f"{path}.{k}" if path else k)
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from _walk(v, f"{path}[{i}]")


def check_content(data: dict, ctx: Context) -> list[dict]:
    """Check every piece of text in a generated content item. Highest severity first."""
    findings = [f for path, text in _walk(data, "") for f in check_text(text, path, ctx)]
    # One finding per rule+field+match is enough
    unique = {(f.rule, f.field, f.match.lower()): f for f in findings}
    out = sorted(unique.values(), key=lambda f: (SEVERITY_ORDER[f.severity], f.field))
    return [f.to_dict() for f in out]


def blocking(findings: list[dict]) -> list[dict]:
    """High-severity findings: content can't be approved until these are fixed."""
    return [f for f in findings if f["severity"] == "high"]
