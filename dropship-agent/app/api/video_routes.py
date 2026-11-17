"""Dashboard pages for media licences, rendering and approving videos (phase 6)."""

from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.supplier_routes import form_data
from app.core.config import Settings
from app.core.models import ContentItem, ContentStatus, MediaAsset, Product, Video
from app.modules.video_maker import media, service, voice


def register(app: FastAPI, templates, settings: Settings, get_session, render, require_htmx) -> None:
    htmx = [Depends(require_htmx)]
    media_root = settings.path(settings.video.media_dir)
    out_root = settings.path(settings.video.output_dir)
    out_root.mkdir(parents=True, exist_ok=True)
    # Rendered videos are served from here for the in-dashboard preview (local only).
    app.mount("/video-files", StaticFiles(directory=out_root), name="video-files")

    def refresh() -> Response:
        return Response(status_code=204, headers={"HX-Refresh": "true"})

    @app.get("/videos", response_class=HTMLResponse)
    def videos(request: Request, session: Session = Depends(get_session)):
        scan = media.scan(session, media_root)
        assets = list(session.scalars(select(MediaAsset).order_by(MediaAsset.path)))
        music = [a for a in assets if a.product_id is None and a.kind == "audio"]
        scripts = list(session.scalars(select(ContentItem).where(ContentItem.kind == "ad_script",
                                                                 ContentItem.status == ContentStatus.APPROVED)))
        vids = list(session.scalars(select(Video).order_by(Video.id.desc())))
        products = {p.id: p for p in session.scalars(select(Product))}
        footage = {pid: [a for a in assets if a.product_id == pid] for pid in products}
        return render(request, "videos.html", session, scan=scan, assets=assets, music=music, scripts=scripts,
                      videos=vids, products=products, footage=footage, licences=media.LICENCES,
                      product_licences=media.PRODUCT_LICENCES, music_licences=media.MUSIC_LICENCES,
                      voices=list(voice.PROVIDERS), media_root=media_root, slug=media.slug,
                      ffmpeg_ok=service.ffmpeg_available(settings))

    @app.post("/media/register", response_class=HTMLResponse, dependencies=htmx)
    async def register_media(request: Request, session: Session = Depends(get_session)):
        f = await form_data(request)
        try:
            media.register(session, media_root, settings.video.ffprobe_path, f.get("path", ""),
                           f.get("licence", ""), f.get("note", ""))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return refresh()

    @app.post("/media/{asset_id}/remove", dependencies=htmx)
    def remove_media(asset_id: int, session: Session = Depends(get_session)):
        a = session.get(MediaAsset, asset_id)
        if a is None:
            raise HTTPException(404)
        session.delete(a)  # forgets the licence record only; your file stays on disk
        session.commit()
        return refresh()

    @app.post("/videos/render/{item_id}", response_class=HTMLResponse, dependencies=htmx)
    async def render_video(item_id: int, request: Request, session: Session = Depends(get_session)):
        item = session.get(ContentItem, item_id)
        if item is None:
            raise HTTPException(404)
        f = await form_data(request)
        try:
            service.render_item(session, settings, item, music_id=int(f["music_id"]) if f.get("music_id") else None,
                                voice_name=f.get("voice") or None)
        except (service.VideoError, ValueError, RuntimeError) as exc:
            return HTMLResponse(f'<p class="banner warn">Not rendered: {exc}</p>')
        return refresh()

    @app.post("/videos/{video_id}/{decision}", dependencies=htmx)
    def decide(video_id: int, decision: str, session: Session = Depends(get_session)):
        v = session.get(Video, video_id)
        if v is None or decision not in ("approve", "reject"):
            raise HTTPException(404)
        service.decide(session, settings, v, approve=decision == "approve")
        return refresh()
