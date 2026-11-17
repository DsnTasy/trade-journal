"""Video maker: planning, captions, ffmpeg commands, and a REAL small render (needs ffmpeg)."""

import json
import shutil
import subprocess

import pytest
from sqlalchemy import select

from app.core.models import ContentItem, ContentStatus, MediaAsset, Product, ProductStatus, Supplier, Video
from app.modules.video_maker import media, service, voice
from app.modules.video_maker.plan import build_plan, clean_caption
from app.modules.video_maker.render import Style, _ass_colour, ass_subtitles, final_command

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")

SCRIPT = {"angle": "demo", "hook": "Pet hair everywhere?", "body": "Roll it, lift it, rinse it.",
          "cta": "See it on our store", "on_screen_text": ["Roll", "[REAL CUSTOMER CLIP, only with permission]", "Rinse"],
          "shots": ["sofa"], "duration_seconds": 20, "platform_notes": {"tiktok": "", "reels": "", "shorts": ""}}
ASSETS = [{"path": "/a.mp4", "kind": "video", "duration": 6.0}, {"path": "/b.jpg", "kind": "image", "duration": None}]


# ---------------- planning ----------------
def test_plan_timing_and_cuts():
    p = build_plan(SCRIPT, ASSETS, store_name="Shop", end_card_seconds=2.5, max_shot_seconds=4)
    assert p.total_seconds == 20 and p.body_seconds == 17.5
    assert len(p.shots) == 5 and all(s.duration <= 4 for s in p.shots)
    assert sum(s.duration for s in p.shots) == pytest.approx(17.5, abs=0.01)
    assert [s.asset_path for s in p.shots[:3]] == ["/a.mp4", "/b.jpg", "/a.mp4"]
    assert p.shots[2].source_offset == pytest.approx(2.5)  # reused 6s clip: its last 3.5s, not a repeat


def test_placeholders_never_rendered():
    p = build_plan(SCRIPT, ASSETS, store_name="Shop")
    texts = [c.text for c in p.cues]
    assert texts == ["Pet hair everywhere?", "Roll", "Rinse", "See it on our store"]
    assert any("placeholder" in w for w in p.warnings)
    assert clean_caption("[CHECK: x]") is None and clean_caption("Hi [x] there") == "Hi there"


def test_captions_cover_body_without_overlap():
    p = build_plan(SCRIPT, ASSETS, store_name="Shop")
    assert p.cues[0].start == 0 and p.cues[0].end == 3
    for a, b in zip(p.cues, p.cues[1:]):
        assert a.end <= b.start + 1e-6
    assert p.cues[-1].end == pytest.approx(p.body_seconds)


def test_voiceover_sets_length():
    p = build_plan(SCRIPT, ASSETS, store_name="Shop", voice_seconds=24.0, end_card_seconds=2.5)
    assert p.body_seconds == 24.5 and p.total_seconds == 27.0


def test_no_assets_is_an_error_and_one_asset_warns():
    with pytest.raises(ValueError):
        build_plan(SCRIPT, [], store_name="Shop")
    assert any("repetitive" in w for w in build_plan(SCRIPT, ASSETS[:1], store_name="S").warnings)


# ---------------- captions & commands ----------------
def test_ass_file():
    p = build_plan(SCRIPT, ASSETS, store_name="Shop")
    ass = ass_subtitles(p, Style())
    assert "PlayResX: 1080" in ass and "PlayResY: 1920" in ass
    assert "Dialogue: 0,0:00:00.00,0:00:03.00,Caption,Pet hair everywhere?" in ass
    assert _ass_colour("#FF8800") == "&H000088FF"


def test_final_command_silence_vs_music_and_voice():
    p = build_plan(SCRIPT, ASSETS, store_name="Shop")
    silent = " ".join(final_command("ffmpeg", p, Style(), None, None, "out.mp4"))
    assert "anullsrc" in silent and "subtitles=captions.ass" in silent and "+faststart" in silent
    mixed = " ".join(final_command("ffmpeg", p, Style(), "m.mp3", "v.mp3", "out.mp4"))
    assert "amix=inputs=2" in mixed and "volume=0.15" in mixed and "anullsrc" not in mixed


def test_voice_providers(tmp_path):
    assert voice.get_provider("none").voice_for("x", "demo", tmp_path, tmp_path) is None
    (tmp_path / "voice-demo.m4a").write_bytes(b"x")
    assert voice.get_provider("recorded").voice_for("x", "demo", tmp_path, tmp_path).name == "voice-demo.m4a"
    with pytest.raises(ValueError):
        voice.get_provider("robot")


# ---------------- media registration ----------------
@pytest.fixture
def product(session):
    p = Product(name="Pet roller", status=ProductStatus.CONTENT_READY, compliance_flags=[],
                profit_overrides={"sell_price_gbp": 17.99})
    session.add(p)
    session.flush()
    s = Supplier(product_id=p.id, name="UK", source="t", shipping_days_max=4)
    session.add(s)
    session.flush()
    p.chosen_supplier_id = s.id
    session.commit()
    return p


@pytest.fixture
def video_settings(settings, tmp_path):
    settings.video.media_dir = str(tmp_path / "media")
    settings.video.output_dir = str(tmp_path / "out")
    settings.video.width, settings.video.height, settings.video.fps = 216, 384, 15  # small = fast tests
    settings.video.caption_size = 16
    return settings


def test_scan_and_register_rules(session, video_settings, product, tmp_path):
    root = tmp_path / "media"
    (root / "pet-roller").mkdir(parents=True)
    (root / "pet-roller" / "clip.mp4").write_bytes(b"not really a video")
    (root / "pet-roller" / "voice-demo.mp3").write_bytes(b"x")
    (root / "music").mkdir()
    (root / "music" / "track.mp3").write_bytes(b"x")
    found = media.scan(session, root)
    assert {f["path"] for f in found["unregistered"]} == {"pet-roller/clip.mp4", "music/track.mp3"}
    with pytest.raises(ValueError, match="note"):
        media.register(session, root, "ffprobe", "pet-roller/clip.mp4", "supplier_licensed", "")
    with pytest.raises(ValueError):
        media.register(session, root, "ffprobe", "music/track.mp3", "supplier_licensed", "x")  # music must be royalty-free
    with pytest.raises(ValueError, match="inside"):
        media.register(session, root, "ffprobe", "../../etc/passwd", "self_shot", "")
    a = media.register(session, root, "ffprobe", "pet-roller/clip.mp4", "self_shot", "")
    assert a.product_id == product.id and a.kind == "video"
    assert media.scan(session, root)["unregistered"] == [
        {"path": "music/track.mp3", "kind": "audio", "folder": "music", "product_id": None, "is_music": True,
         "unknown_folder": False}]


def test_refuses_unapproved_script_and_missing_media(session, video_settings, product):
    item = ContentItem(product_id=product.id, kind="ad_script", angle="demo", data=SCRIPT, status=ContentStatus.DRAFT)
    session.add(item)
    session.commit()
    with pytest.raises(service.VideoError, match="Approve"):
        service.render_item(session, video_settings, item)
    item.status = ContentStatus.APPROVED
    session.commit()
    with pytest.raises(service.VideoError, match="No licensed footage"):
        service.render_item(session, video_settings, item)


def test_refuses_script_that_now_has_risky_claims(session, video_settings, product):
    item = ContentItem(product_id=product.id, kind="ad_script", angle="demo", status=ContentStatus.APPROVED,
                       data={**SCRIPT, "hook": "Only 2 left!"})
    session.add(item)
    session.commit()
    with pytest.raises(service.VideoError, match="high-risk"):
        service.render_item(session, video_settings, item)


# ---------------- a real render ----------------
def make_media(root):
    d = root / "pet-roller"
    d.mkdir(parents=True, exist_ok=True)
    run = lambda *a: subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *a], check=True)  # noqa: E731
    run("-f", "lavfi", "-i", "testsrc=size=320x240:rate=15:duration=3", str(d / "a-landscape.mp4"))
    run("-f", "lavfi", "-i", "color=c=orange:s=200x300:d=1", "-frames:v", "1", str(d / "b-photo.png"))
    (root / "music").mkdir(exist_ok=True)
    run("-f", "lavfi", "-i", "sine=frequency=440:duration=4", str(root / "music" / "tone.mp3"))


@needs_ffmpeg
def test_real_render_produces_valid_vertical_mp4(session, video_settings, product, tmp_path):
    root = tmp_path / "media"
    make_media(root)
    media.scan(session, root)
    media.register(session, root, "ffprobe", "pet-roller/a-landscape.mp4", "self_shot", "")
    media.register(session, root, "ffprobe", "pet-roller/b-photo.png", "supplier_licensed", "Email from supplier 16/11")
    music = media.register(session, root, "ffprobe", "music/tone.mp3", "royalty_free", "test tone, made by us")
    item = ContentItem(product_id=product.id, kind="ad_script", angle="demo", data={**SCRIPT, "duration_seconds": 10},
                       status=ContentStatus.APPROVED)
    session.add(item)
    session.commit()

    v = service.render_item(session, video_settings, item, music_id=music.id)
    out = tmp_path / "out" / v.path
    assert out.exists() and out.stat().st_size > 1000
    info = json.loads(subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams",
                                      "-show_format", str(out)], capture_output=True, text=True).stdout)
    vstream = next(s for s in info["streams"] if s["codec_type"] == "video")
    astream = next(s for s in info["streams"] if s["codec_type"] == "audio")
    assert (vstream["width"], vstream["height"]) == (216, 384)  # 9:16
    assert vstream["codec_name"] == "h264" and astream["codec_name"] == "aac"
    assert float(info["format"]["duration"]) == pytest.approx(10, abs=0.5)
    assert v.plan["music"] == "music/tone.mp3" and v.plan["licences"]["pet-roller/b-photo.png"] == "supplier_licensed"
    assert not any(c["text"].startswith("[") for c in v.plan["cues"])

    service.decide(session, video_settings, v, approve=True)
    assert session.scalars(select(Video)).one().status == "approved"


@needs_ffmpeg
def test_render_without_music_has_silent_audio(session, video_settings, product, tmp_path):
    root = tmp_path / "media"
    make_media(root)
    media.register(session, root, "ffprobe", "pet-roller/a-landscape.mp4", "self_shot", "")
    item = ContentItem(product_id=product.id, kind="ad_script", angle="demo", data={**SCRIPT, "duration_seconds": 10},
                       status=ContentStatus.APPROVED)
    session.add(item)
    session.commit()
    v = service.render_item(session, video_settings, item)
    info = subprocess.run(["ffprobe", "-v", "error", "-show_streams", str(tmp_path / "out" / v.path)],
                          capture_output=True, text=True).stdout
    assert "codec_type=audio" in info  # platforms reject videos with no audio track at all


@needs_ffmpeg
def test_dashboard_videos_flow(session, video_settings, product, engine, tmp_path):
    from fastapi.testclient import TestClient
    from app.api.main import create_app

    root = tmp_path / "media"
    make_media(root)
    item = ContentItem(product_id=product.id, kind="ad_script", angle="demo", data={**SCRIPT, "duration_seconds": 10},
                       status=ContentStatus.APPROVED)
    session.add(item)
    session.commit()
    web = TestClient(create_app(video_settings, engine))
    hx = {"HX-Request": "true"}
    page = web.get("/videos").text
    assert "pet-roller/a-landscape.mp4" in page and "record their licence" in page
    assert web.post("/media/register", headers=hx, data={"path": "pet-roller/a-landscape.mp4", "licence": "supplier_licensed",
                                                         "note": ""}).status_code == 422
    assert web.post("/media/register", headers=hx, data={"path": "pet-roller/a-landscape.mp4", "licence": "self_shot",
                                                         "note": ""}).status_code == 204
    r = web.post(f"/videos/render/{item.id}", headers=hx, data={"voice": "none"})
    assert r.status_code == 204, r.text
    v = session.scalars(select(Video)).one()
    assert web.get(f"/video-files/{v.path}").status_code == 200
    assert web.post(f"/videos/{v.id}/approve").status_code == 403
    assert web.post(f"/videos/{v.id}/approve", headers=hx).status_code == 204
    session.expire_all()
    assert session.get(Video, v.id).status == "approved"
