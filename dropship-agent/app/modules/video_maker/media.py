"""Your footage, images and music.

Put files in:
  media/<product-folder>/   clips and photos for that product (self-shot or supplier-licensed)
  media/music/              royalty-free music you have a licence for
Then record each file's licence in the dashboard (Videos → Media). Unlicensed files are never used.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.models import MediaAsset, Product

VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}
AUDIO_EXT = {".mp3", ".m4a", ".wav", ".aac"}

LICENCES = {
    "self_shot": "I filmed/photographed it myself",
    "supplier_licensed": "Supplier gave permission to use it (keep their message)",
    "royalty_free": "Royalty-free with a licence that allows ads (keep the licence link)",
    "own_recording": "My own voice recording",
    "ai_generated": "Made with an AI tool whose terms allow commercial use (say which tool)",
}
PRODUCT_LICENCES = {"self_shot", "supplier_licensed", "royalty_free", "ai_generated"}
MUSIC_LICENCES = {"royalty_free", "self_shot", "ai_generated"}

# Shown wherever AI-generated media is used.
AI_RULES = ("Contains AI-generated media: label it as AI-generated when you post (TikTok and Meta require "
            "this for realistic AI content), and make sure no AI clip shows the product looking or working "
            "better than the real one.")


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "product"


def product_dir(media_root: Path, product: Product) -> Path:
    return media_root / slug(product.name)


def kind_of(path: Path) -> str | None:
    ext = path.suffix.lower()
    return "video" if ext in VIDEO_EXT else "image" if ext in IMAGE_EXT else "audio" if ext in AUDIO_EXT else None


def probe(ffprobe: str, path: Path) -> dict:
    """Duration and size of a media file (empty dict if ffprobe isn't available or fails)."""
    try:
        out = subprocess.run([ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams",
                              str(path)], capture_output=True, text=True, timeout=30)
        data = json.loads(out.stdout or "{}")
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return {}
    info: dict = {}
    dur = data.get("format", {}).get("duration")
    if dur:
        info["duration_seconds"] = round(float(dur), 2)
    for s in data.get("streams", []):
        if s.get("codec_type") == "video":
            info["width"], info["height"] = s.get("width"), s.get("height")
            break
    return info


def scan(session: Session, media_root: Path) -> dict:
    """Files on disk vs registered assets. Returns {"unregistered": [...], "missing": [...]}."""
    media_root.mkdir(parents=True, exist_ok=True)
    (media_root / "music").mkdir(exist_ok=True)
    registered = {a.path: a for a in session.scalars(select(MediaAsset))}
    products = {slug(p.name): p for p in session.scalars(select(Product))}
    for s in products:
        (media_root / s).mkdir(exist_ok=True)
    unregistered = []
    for f in sorted(media_root.rglob("*")):
        if not f.is_file() or kind_of(f) is None or f.name.startswith("."):
            continue
        rel = f.relative_to(media_root).as_posix()
        if rel in registered:
            continue
        folder = rel.split("/")[0]
        if f.name.startswith("voice-"):
            continue  # voiceovers are picked up automatically by name
        product = products.get(folder)
        unregistered.append({"path": rel, "kind": kind_of(f), "folder": folder,
                             "product_id": product.id if product else None, "is_music": folder == "music",
                             "unknown_folder": folder != "music" and product is None})
    missing = [a.path for a in registered.values() if not (media_root / a.path).exists()]
    return {"unregistered": unregistered, "missing": missing}


def register(session: Session, media_root: Path, ffprobe: str, rel_path: str, licence: str, note: str) -> MediaAsset:
    path = (media_root / rel_path).resolve()
    if media_root.resolve() not in path.parents:
        raise ValueError("That file isn't inside the media folder.")
    if not path.exists():
        raise ValueError(f"{rel_path} doesn't exist.")
    kind = kind_of(path)
    if kind is None:
        raise ValueError("Unsupported file type.")
    folder = rel_path.split("/")[0]
    is_music = folder == "music"
    allowed = MUSIC_LICENCES if is_music else PRODUCT_LICENCES
    if licence not in allowed:
        raise ValueError(f"Choose one of: {', '.join(LICENCES[x] for x in sorted(allowed))}.")
    if licence in ("supplier_licensed", "royalty_free") and not note.strip():
        raise ValueError("Add a note saying where the permission or licence is (e.g. supplier email date, licence link).")
    if licence == "ai_generated" and not note.strip():
        raise ValueError("Add a note saying which AI tool made it and that its terms allow commercial use "
                         "(e.g. 'Runway, paid plan, commercial use allowed').")
    product = None
    if not is_music:
        product = next((p for p in session.scalars(select(Product)) if slug(p.name) == folder), None)
        if product is None:
            raise ValueError(f"Folder '{folder}' doesn't match a product name.")
    asset = MediaAsset(product_id=product.id if product else None, kind=kind, path=rel_path, licence=licence,
                       licence_note=note.strip() or None, **probe(ffprobe, path))
    session.add(asset)
    session.commit()
    return asset
