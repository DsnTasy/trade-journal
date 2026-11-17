"""Voiceover providers, behind one interface so you can switch later.

Built in:
- NoVoice:       captions only (very common for TikTok/Reels ads, and free).
- RecordedVoice: your own recording, e.g. media/<product>/voice-demo.mp3 (free, sounds most human).

Paid AI voices (ElevenLabs, OpenAI, Google, Azure, ...) can be added as another class with the
same three methods. Each must report its cost so the budget guard and approval rules apply.
We'll add one once you've picked a provider and have a key, so it can be tested for real.
"""

from __future__ import annotations

from pathlib import Path


class VoiceProvider:
    name = "base"
    paid = False

    def estimate_cost_gbp(self, text: str) -> float:
        return 0.0

    def voice_for(self, text: str, angle: str, product_dir: Path, workdir: Path) -> Path | None:
        """Return an audio file path, or None for no voiceover."""
        raise NotImplementedError


class NoVoice(VoiceProvider):
    name = "none"

    def voice_for(self, text, angle, product_dir, workdir):
        return None


class RecordedVoice(VoiceProvider):
    """Looks for voice-<angle>.(mp3|m4a|wav) in the product's media folder."""

    name = "recorded"

    def voice_for(self, text, angle, product_dir, workdir):
        for ext in ("mp3", "m4a", "wav", "aac"):
            f = product_dir / f"voice-{angle}.{ext}"
            if f.exists():
                return f
        return None


PROVIDERS = {"none": NoVoice, "recorded": RecordedVoice}


def get_provider(name: str) -> VoiceProvider:
    if name not in PROVIDERS:
        raise ValueError(f"Unknown voice provider '{name}'. Available: {', '.join(PROVIDERS)}.")
    return PROVIDERS[name]()
