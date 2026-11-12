"""Record the animation page in real time and mux it with the narration."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import List, Sequence

from .animation import HEIGHT, WIDTH


class RenderError(RuntimeError):
    pass


async def _ffmpeg(*args: str) -> None:
    process = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-loglevel", "error", *args,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await process.communicate()
    if process.returncode != 0:
        raise RenderError(f"ffmpeg 失败：{stderr.decode('utf-8', 'ignore').strip()[-400:]}")


async def mix_narration(audio_paths: Sequence[Path], starts: Sequence[float], total: float, out_path: Path) -> Path:
    """Place each segment at its start time on one track (sample rates may differ)."""
    inputs: List[str] = []
    chains: List[str] = []
    for index, (path, start) in enumerate(zip(audio_paths, starts)):
        inputs += ["-i", str(path)]
        delay = int(round(start * 1000))
        chains.append(
            f"[{index}:a]aresample=44100,aformat=channel_layouts=mono,adelay={delay}|{delay}[a{index}]"
        )
    mixed = "".join(f"[a{i}]" for i in range(len(audio_paths)))
    graph = ";".join(chains) + f";{mixed}amix=inputs={len(audio_paths)}:normalize=0,apad[out]"
    await _ffmpeg(*inputs, "-filter_complex", graph, "-map", "[out]", "-t", f"{total:.2f}",
                  "-c:a", "aac", "-b:a", "160k", str(out_path))
    return out_path


async def record_animation(html_path: Path, total: float, work_dir: Path) -> tuple[Path, float]:
    """Return (raw webm, seconds to trim from its head)."""
    from ..pyppeteer_pdf_converter import get_pdf_converter

    converter = get_pdf_converter()
    if not converter.is_available():
        raise RenderError("Playwright 不可用，无法录制视频")
    browser = await converter._get_or_create_browser()
    record_dir = work_dir / "record"
    record_dir.mkdir(parents=True, exist_ok=True)
    context = await browser.new_context(
        viewport={"width": WIDTH, "height": HEIGHT},
        device_scale_factor=1,
        record_video_dir=str(record_dir),
        record_video_size={"width": WIDTH, "height": HEIGHT},
    )
    try:
        started = time.monotonic()
        page = await context.new_page()
        await page.goto(html_path.resolve().as_uri(), wait_until="load")
        await page.wait_for_function("window.__ready === true", timeout=15000)
        await page.wait_for_timeout(400)
        offset = time.monotonic() - started
        await page.evaluate("window.__start()")
        await page.wait_for_timeout(int((total + 0.8) * 1000))
        video = page.video
    finally:
        await context.close()
    raw = Path(await video.path())
    if not raw.exists() or raw.stat().st_size == 0:
        raise RenderError("录制结果为空")
    return raw, offset


async def compose_mp4(raw_video: Path, offset: float, audio: Path, total: float, out_path: Path) -> Path:
    await _ffmpeg(
        "-ss", f"{offset:.2f}", "-i", str(raw_video), "-i", str(audio),
        "-map", "0:v:0", "-map", "1:a:0", "-vf", "fps=30", "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-crf", "20", "-preset", "veryfast", "-c:a", "copy", "-t", f"{total:.2f}", "-movflags", "+faststart",
        str(out_path),
    )
    return out_path


async def extract_cover(video: Path, at_seconds: float, out_path: Path) -> Path:
    await _ffmpeg("-ss", f"{max(0.0, at_seconds):.2f}", "-i", str(video), "-frames:v", "1",
                  "-vf", f"scale={WIDTH}:{HEIGHT}", str(out_path))
    return out_path
