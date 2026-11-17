"""Render approved ad scripts into vertical videos, and approve/reject the results.

Safety rules enforced here:
- only APPROVED ad scripts are rendered (and their claims are re-checked first);
- only media with a recorded licence is used;
- placeholders such as "[REAL CUSTOMER CLIP...]" are dropped, never faked;
- paid voice providers must report a cost, which goes through the budget guard.
"""

from __future__ import annotations

import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.cost_tracker import CostTracker
from app.core.logger import log_event
from app.core.models import ContentItem, ContentStatus, MediaAsset, Video
from app.modules.content_generator import claim_checker
from app.modules.content_generator.service import build_facts, load_brand
from app.modules.video_maker import media, voice
from app.modules.video_maker.plan import build_plan
from app.modules.video_maker.render import RenderError, Style, render


class VideoError(RuntimeError):
    pass


def style_from(settings: Settings) -> Style:
    v = settings.video
    return Style(width=v.width, height=v.height, fps=v.fps, font=v.font, caption_size=v.caption_size,
                 brand_colour=v.brand_colour, text_colour=v.text_colour, music_volume=v.music_volume)


def render_item(session: Session, settings: Settings, item: ContentItem, *, music_id: int | None = None,
                voice_name: str | None = None, approved_cost: bool = False) -> Video:
    if item.kind != "ad_script":
        raise VideoError("Only ad scripts can be turned into videos.")
    if item.status != ContentStatus.APPROVED:
        raise VideoError("Approve the ad script first (Content page).")
    _, ctx = build_facts(session, settings, item.product)
    blockers = claim_checker.blocking(claim_checker.check_content(item.data, ctx))
    if blockers:
        raise VideoError(f"The script now has a high-risk claim (\"{blockers[0]['match']}\"). Fix it on the Content page.")

    root = settings.path(settings.video.media_dir)
    assets = list(session.scalars(select(MediaAsset).where(MediaAsset.product_id == item.product_id,
                                                           MediaAsset.kind.in_(["video", "image"]))
                                  .order_by(MediaAsset.path)))
    assets = [a for a in assets if a.licence in media.PRODUCT_LICENCES and (root / a.path).exists()]
    if not assets:
        raise VideoError(f"No licensed footage yet. Put clips/photos in media/{media.slug(item.product.name)}/ "
                         f"and record their licence on the Videos page.")

    music_path = None
    music_asset = None
    if music_id:
        music_asset = session.get(MediaAsset, music_id)
        if music_asset is None or music_asset.product_id is not None or music_asset.licence not in media.MUSIC_LICENCES:
            raise VideoError("That music track isn't registered with a licence.")
        music_path = str((root / music_asset.path).resolve())

    provider = voice.get_provider(voice_name or settings.video.voice_provider)
    text = " ".join([item.data.get("hook", ""), item.data.get("body", ""), item.data.get("cta", "")])
    cost = provider.estimate_cost_gbp(text) if provider.paid else 0.0
    if cost:
        tracker = CostTracker(session, settings.budget, dry_run=settings.dry_run)
        tracker.check(cost, operation=f"voice:{provider.name}")
        if cost > settings.video.paid_approval_threshold_gbp and not approved_cost:
            raise VideoError(f"This voiceover would cost about £{cost:.2f}; approve the cost first.")

    with tempfile.TemporaryDirectory(prefix="render-") as tmp:
        workdir = Path(tmp)
        voice_file = provider.voice_for(text, item.angle, media.product_dir(root, item.product), workdir)
        voice_seconds = None
        if voice_file is not None:
            voice_seconds = media.probe(settings.video.ffprobe_path, voice_file).get("duration_seconds")
        plan = build_plan(item.data, [{"path": str((root / a.path).resolve()), "kind": a.kind,
                                       "duration": a.duration_seconds} for a in assets],
                          store_name=load_brand().get("store_name") or "Our store",
                          end_card_seconds=settings.video.end_card_seconds,
                          max_shot_seconds=settings.video.max_shot_seconds, voice_seconds=voice_seconds)
        if voice_name == "recorded" and voice_file is None:
            plan.warnings.append(f"No recording found (media/{media.slug(item.product.name)}/voice-{item.angle}.mp3), "
                                 f"so this video has captions only.")
        out_root = settings.path(settings.video.output_dir)
        rel = f"{media.slug(item.product.name)}/{item.angle}-v{item.version}-{datetime.now(UTC):%Y%m%d-%H%M%S}.mp4"
        try:
            render(plan, style_from(settings), workdir, out_root / rel, ffmpeg=settings.video.ffmpeg_path,
                   music=music_path, voice=str(voice_file.resolve()) if voice_file else None)
        except RenderError as exc:
            log_event(session, module="video_maker", action="render_failed", level="ERROR",
                      message=f"{item.product.name} {item.angle}: {exc}", dry_run=settings.dry_run)
            raise VideoError(str(exc)) from exc

    if cost:
        CostTracker(session, settings.budget, dry_run=settings.dry_run).record(
            service=provider.name, operation=f"voice:{item.angle}", cost_gbp=cost)
    plan_dict = plan.to_dict()
    for shot in plan_dict["shots"]:  # store paths relative to the media folder (portable)
        try:
            shot["asset_path"] = Path(shot["asset_path"]).relative_to(root.resolve()).as_posix()
        except ValueError:
            pass
    uses_ai = any(a.licence == "ai_generated" for a in assets) or bool(music_asset and music_asset.licence == "ai_generated")
    if uses_ai:
        plan.warnings.append(media.AI_RULES)
    plan_dict.update(music=music_asset.path if music_asset else None, voice=provider.name if voice_file else "none",
                     licences={a.path: a.licence for a in assets}, contains_ai=uses_ai)
    video = Video(product_id=item.product_id, content_item_id=item.id, path=rel, duration_seconds=plan.total_seconds,
                  plan=plan_dict, warnings=plan.warnings, cost_gbp=cost)
    session.add(video)
    session.commit()
    log_event(session, module="video_maker", action="rendered",
              message=f"{item.product.name}: {item.angle.replace('_', ' ')} video ({plan.total_seconds:.0f}s, "
                      f"{len(plan.shots)} shots)", source=", ".join(sorted({a.licence for a in assets})),
              cost_gbp=cost or None, dry_run=settings.dry_run)
    return video


def decide(session: Session, settings: Settings, video: Video, approve: bool) -> None:
    video.status = "approved" if approve else "rejected"
    video.decided_at = datetime.now(UTC)
    session.commit()
    log_event(session, module="video_maker", action=video.status,
              message=f"{video.product.name}: video {video.path} {video.status}.", dry_run=settings.dry_run)


def ffmpeg_available(settings: Settings) -> bool:
    return shutil.which(settings.video.ffmpeg_path) is not None or Path(settings.video.ffmpeg_path).exists()
