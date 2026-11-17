"""Turn an approved ad script + your licensed media into a timed plan. Pure logic, no ffmpeg.

    script → shots (which clip/image, when, how long) → captions (what text, when) → end card
"""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field

PLACEHOLDER = re.compile(r"\[[^\]]*\]")


@dataclass
class Shot:
    asset_path: str  # absolute path
    kind: str  # video | image
    start: float
    duration: float
    source_offset: float = 0.0  # where to start inside a video clip


@dataclass
class Cue:
    start: float
    end: float
    text: str


@dataclass
class Plan:
    total_seconds: float
    body_seconds: float
    end_card_seconds: float
    shots: list[Shot]
    cues: list[Cue]
    end_card_text: list[str]
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def clean_caption(text: str) -> str | None:
    """Remove placeholders like [REAL CUSTOMER CLIP...]; skip lines that were only a placeholder."""
    cleaned = PLACEHOLDER.sub("", text or "").strip(" -–—:")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or None


def build_plan(script: dict, assets: list[dict], *, store_name: str, end_card_seconds: float = 2.5,
               max_shot_seconds: float = 4.0, voice_seconds: float | None = None) -> Plan:
    """`assets`: [{"path", "kind": video|image, "duration": seconds or None}] in the order to use them."""
    if not assets:
        raise ValueError("No licensed footage or images for this product yet.")
    warnings: list[str] = []
    wanted = float(script.get("duration_seconds") or 20)
    wanted = min(max(wanted, 10.0), 60.0)
    body = voice_seconds + 0.5 if voice_seconds else wanted - end_card_seconds
    if voice_seconds and voice_seconds > 58:
        warnings.append("Voiceover is longer than a minute; short ads usually work better.")
    body = max(body, 5.0)
    total = round(body + end_card_seconds, 2)

    # Shots: cycle through assets, cutting at least every max_shot_seconds.
    n = max(len(assets), math.ceil(body / max_shot_seconds))
    seg = body / n
    shots: list[Shot] = []
    used_offset: dict[str, float] = {}
    for i in range(n):
        a = assets[i % len(assets)]
        offset = 0.0
        if a["kind"] == "video" and a.get("duration"):
            # Re-using a clip? Start further in, so viewers see different footage.
            used = used_offset.get(a["path"], 0.0)
            span = a["duration"] - seg  # latest start that still leaves a full shot
            offset = 0.0 if span <= 0 or used >= a["duration"] else min(used, span)
            used_offset[a["path"]] = offset + seg
        shots.append(Shot(a["path"], a["kind"], round(i * seg, 3), round(seg, 3), round(offset, 3)))
    if len(assets) == 1:
        warnings.append("Only one clip/image: the video will feel repetitive. Add 3-6 different shots.")

    # Captions: hook first 3 seconds, then on-screen lines spread across the rest of the body, CTA last.
    cues: list[Cue] = []
    hook = clean_caption(script.get("hook", ""))
    hook_end = min(3.0, body)
    if hook:
        cues.append(Cue(0.0, hook_end, hook))
    lines = [c for c in (clean_caption(t) for t in script.get("on_screen_text", [])) if c]
    skipped = len(script.get("on_screen_text", [])) - len(lines)
    if skipped:
        warnings.append(f"{skipped} caption line(s) were placeholders (e.g. a real customer clip you don't have) "
                        f"and were left out.")
    cta = clean_caption(script.get("cta", ""))
    middle = [*lines, *([cta] if cta else [])]
    if middle:
        span = (body - hook_end) / len(middle)
        for i, text in enumerate(middle):
            cues.append(Cue(round(hook_end + i * span, 3), round(hook_end + (i + 1) * span, 3), text))

    end = [store_name] + ([cta] if cta else [])
    return Plan(total, round(body, 3), end_card_seconds, shots, cues, end, warnings)
