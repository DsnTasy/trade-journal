"""Render a Plan to an MP4 with ffmpeg.

Steps (each a separate, simple ffmpeg command, run inside a temporary work folder):
  1. each shot → a normalised 9:16 clip (scaled and cropped to fill the frame, no audio)
  2. end card  → brand colour + store name + call to action
  3. join everything (concat)
  4. burn in captions, add music and/or voiceover (or silence), export H.264 + AAC MP4

We run ffmpeg *inside* the work folder and use short relative file names, so file paths
with spaces or Windows drive letters never break the filters.
"""

from __future__ import annotations

import shutil
import subprocess
import textwrap
from dataclasses import dataclass
from pathlib import Path

from app.modules.video_maker.plan import Plan


class RenderError(RuntimeError):
    pass


@dataclass
class Style:
    width: int = 1080
    height: int = 1920
    fps: int = 30
    font: str = "DejaVu Sans"
    caption_size: int = 64
    brand_colour: str = "#1d2330"
    text_colour: str = "#ffffff"
    music_volume: float = 0.15


def _ass_colour(hex_colour: str) -> str:
    """#RRGGBB → ASS &H00BBGGRR."""
    h = hex_colour.lstrip("#")
    return f"&H00{h[4:6]}{h[2:4]}{h[0:2]}".upper()


def _ts(seconds: float) -> str:
    cs = int(round(seconds * 100))
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def ass_subtitles(plan: Plan, style: Style) -> str:
    """Captions as an ASS file: big, bold, outlined text in the lower-middle (clear of app buttons)."""
    wrap = max(12, int(style.width / (style.caption_size * 0.55)))
    margin_v = int(style.height * 0.28)
    lines = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {style.width}", f"PlayResY: {style.height}",
        "WrapStyle: 0", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV",
        f"Style: Caption,{style.font},{style.caption_size},{_ass_colour(style.text_colour)},&H00000000,&H80000000,"
        f"-1,1,4,0,2,60,60,{margin_v}", "",
        "[Events]", "Format: Layer, Start, End, Style, Text",
    ]
    for c in plan.cues:
        text = "\\N".join(textwrap.wrap(c.text.replace("{", "(").replace("}", ")"), wrap))
        lines.append(f"Dialogue: 0,{_ts(c.start)},{_ts(c.end)},Caption,{text}")
    return "\n".join(lines) + "\n"


def _scale_crop(style: Style) -> str:
    return (f"scale={style.width}:{style.height}:force_original_aspect_ratio=increase,"
            f"crop={style.width}:{style.height},setsar=1,fps={style.fps},format=yuv420p")


def shot_command(ffmpeg: str, shot, index: int, style: Style) -> list[str]:
    out = f"shot{index:02d}.mp4"
    if shot.kind == "image":
        frames = max(1, int(round(shot.duration * style.fps)))
        # Gentle slow zoom so still images don't look frozen.
        vf = (f"scale={style.width * 2}:{style.height * 2}:force_original_aspect_ratio=increase,"
              f"crop={style.width * 2}:{style.height * 2},"
              f"zoompan=z='min(zoom+0.0007,1.08)':d={frames}:s={style.width}x{style.height}:fps={style.fps},"
              f"setsar=1,format=yuv420p")
        return [ffmpeg, "-y", "-loglevel", "error", "-loop", "1", "-i", shot.asset_path, "-vf", vf,
                "-frames:v", str(frames), "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", out]
    return [ffmpeg, "-y", "-loglevel", "error", "-stream_loop", "-1", "-ss", f"{shot.source_offset:.3f}",
            "-i", shot.asset_path, "-t", f"{shot.duration:.3f}", "-vf", _scale_crop(style), "-an",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", out]


def end_card_command(ffmpeg: str, seconds: float, style: Style, font_file: str | None) -> list[str]:
    big, small = int(style.caption_size * 1.3), style.caption_size
    font = f"fontfile='{font_file}':" if font_file else f"font='{style.font}':"
    vf = (f"drawtext={font}textfile=endcard_title.txt:fontsize={big}:fontcolor={style.text_colour}:"
          f"x=(w-text_w)/2:y=h*0.40,"
          f"drawtext={font}textfile=endcard_cta.txt:fontsize={small}:fontcolor={style.text_colour}:"
          f"x=(w-text_w)/2:y=h*0.52:line_spacing=12,format=yuv420p")
    return [ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi", "-i",
            f"color=c={style.brand_colour}:s={style.width}x{style.height}:r={style.fps}:d={seconds:.3f}",
            "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "endcard.mp4"]


def final_command(ffmpeg: str, plan: Plan, style: Style, music: str | None, voice: str | None, out: str) -> list[str]:
    cmd = [ffmpeg, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", "list.txt"]
    audio_inputs = []
    if voice:
        cmd += ["-i", voice]
        audio_inputs.append(("voice", len(audio_inputs) + 1))
    if music:
        cmd += ["-stream_loop", "-1", "-i", music]
        audio_inputs.append(("music", len(audio_inputs) + 1))
    t = plan.total_seconds
    fade_at = max(t - 1.5, 0)
    filters = ["[0:v]subtitles=captions.ass[v]"]
    if audio_inputs:
        labels = []
        for name, idx in audio_inputs:
            vol = 1.0 if name == "voice" else style.music_volume
            filters.append(f"[{idx}:a]volume={vol},atrim=0:{t:.3f},apad,afade=t=out:st={fade_at:.3f}:d=1.5[{name}]")
            labels.append(f"[{name}]")
        filters.append(f"{''.join(labels)}amix=inputs={len(labels)}:normalize=0:duration=longest[a]")
    else:
        cmd += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"]
        filters.append(f"[{len(audio_inputs) + 1}:a]anull[a]")
    cmd += ["-filter_complex", ";".join(filters), "-map", "[v]", "-map", "[a]", "-t", f"{t:.3f}",
            "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-movflags", "+faststart", out]
    return cmd


def find_font_file(font: str) -> str | None:
    """Ask fontconfig for the font file (drawtext is more reliable with a file). None = let ffmpeg decide."""
    if not shutil.which("fc-match"):
        return None
    try:
        out = subprocess.run(["fc-match", "-f", "%{file}", font], capture_output=True, text=True, timeout=10)
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def run(cmd: list[str], cwd: Path) -> None:
    try:
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=600)
    except FileNotFoundError as exc:
        raise RenderError("ffmpeg isn't installed or isn't on your PATH. See README → Video.") from exc
    except subprocess.TimeoutExpired as exc:
        raise RenderError("ffmpeg took more than 10 minutes and was stopped.") from exc
    if proc.returncode != 0:
        raise RenderError(f"ffmpeg failed: {(proc.stderr or proc.stdout).strip()[-600:]}")


def render(plan: Plan, style: Style, workdir: Path, out_path: Path, *, ffmpeg: str = "ffmpeg",
           music: str | None = None, voice: str | None = None) -> Path:
    workdir.mkdir(parents=True, exist_ok=True)
    names = []
    for i, shot in enumerate(plan.shots):
        run(shot_command(ffmpeg, shot, i, style), workdir)
        names.append(f"shot{i:02d}.mp4")
    if plan.end_card_seconds > 0:
        (workdir / "endcard_title.txt").write_text(plan.end_card_text[0], encoding="utf-8")
        cta = "\n".join(textwrap.wrap(" ".join(plan.end_card_text[1:]), 28))
        (workdir / "endcard_cta.txt").write_text(cta or " ", encoding="utf-8")
        font_file = find_font_file(style.font)
        if font_file:
            shutil.copy(font_file, workdir / ("font" + Path(font_file).suffix))
            font_file = "font" + Path(font_file).suffix  # relative: avoids path escaping problems
        run(end_card_command(ffmpeg, plan.end_card_seconds, style, font_file), workdir)
        names.append("endcard.mp4")
    (workdir / "list.txt").write_text("".join(f"file '{n}'\n" for n in names), encoding="utf-8")
    (workdir / "captions.ass").write_text(ass_subtitles(plan, style), encoding="utf-8")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_out = "final.mp4"
    run(final_command(ffmpeg, plan, style, music, voice, tmp_out), workdir)
    shutil.move(str(workdir / tmp_out), out_path)
    return out_path
