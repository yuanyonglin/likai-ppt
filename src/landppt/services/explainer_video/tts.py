"""Synthesize one text segment with the user's configured TTS provider.

NarrationService only exposes project/slide-bound generation, so this reuses its
config getters and request helpers for providers whose call is a single request.
"""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path
from typing import Optional, Tuple

from ...core.config import app_config
from .. import narration_service as ns

SUPPORTED_PROVIDERS = {"auto", "edge_tts", "cloud_tts", "custom_tts_api"}


class TTSError(RuntimeError):
    pass


async def resolve_provider(user_id: int, provider: Optional[str]) -> str:
    provider = (provider or "auto").strip().lower()
    if provider not in SUPPORTED_PROVIDERS:
        raise TTSError(f"讲解视频暂不支持语音服务 {provider}，请选择 云端语音 / Edge-TTS / 自定义 TTS 接口")
    if provider == "auto":
        from ..auto_narration import resolve_auto_narration_provider

        provider = await resolve_auto_narration_provider(user_id) or "edge_tts"
    return provider


async def synthesize(
    *, user_id: int, provider: str, voice: Optional[str], text: str, out_path: Path, language: str = "zh"
) -> Tuple[Path, int, str]:
    """Write audio for ``text`` and return (path, duration_ms, voice_used)."""
    service = ns.NarrationService(user_id=user_id)
    voice = (voice or "").strip()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    import aiohttp

    if provider == "edge_tts":
        if not ns.is_edge_tts_available():
            raise TTSError("edge-tts 未安装")
        import edge_tts

        voice = voice or await service._get_default_voice_from_config(language=language) or (
            ns.DEFAULT_VOICE_ZH if language.startswith("zh") else ns.DEFAULT_VOICE_EN
        )
        out_path = out_path.with_suffix(".mp3")
        try:
            await edge_tts.Communicate(text=text, voice=voice).save(str(out_path))
        except aiohttp.WSServerHandshakeError as exc:
            raise TTSError(f"Edge-TTS 连接失败（HTTP {exc.status}），请稍后重试") from None

    elif provider == "cloud_tts":
        cfg = await service._get_cloud_tts_settings_from_config()

        def pick(key: str, default: str) -> str:
            value = cfg.get(key) or getattr(app_config, f"cloud_tts_{key}", None) or default
            return (str(value) if value is not None else "").strip() or default

        api_key = (cfg.get("api_key") or getattr(app_config, "cloud_tts_api_key", None) or os.environ.get("CLOUD_TTS_API_KEY") or "").strip()
        base_url = pick("base_url", ns.DEFAULT_CLOUD_TTS_BASE_URL).rstrip("/")
        if not api_key or not base_url:
            raise TTSError("云端语音未配置 API Key 或接口地址")
        protocol = pick("protocol", ns.DEFAULT_CLOUD_TTS_PROTOCOL).lower()
        model = pick("model", ns.DEFAULT_CLOUD_TTS_MODEL)
        # An Edge-style voice name is meaningless to cloud vendors.
        if not voice or re.fullmatch(r"[a-z]{2,3}(?:-[A-Za-z0-9]+)+Neural", voice):
            voice = pick("voice", ns.DEFAULT_CLOUD_TTS_VOICE)
        url, headers, payload = ns.NarrationService._cloud_tts_build_request(
            protocol=protocol, base_url=base_url, api_key=api_key, model=model, voice=voice, speed=1.0, text=text
        )
        out_path = out_path.with_suffix(".mp3")
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=300)) as session:
            for attempt in range(3):
                async with session.post(url, json=payload, headers=headers) as resp:
                    body, status, content_type = await resp.read(), resp.status, resp.headers.get("Content-Type", "")
                if status in (429, 500, 502, 503, 504) and attempt < 2:
                    await asyncio.sleep(2 * (attempt + 1))
                    continue
                out_path.write_bytes(
                    ns.NarrationService._cloud_tts_parse_response(
                        protocol=protocol, status=status, body=body, content_type=content_type
                    )
                )
                break

    elif provider == "custom_tts_api":
        cfg = await service._get_custom_tts_api_settings_from_config()
        api_url = cfg.get("url") or getattr(app_config, "custom_tts_api_url", None) or ns.DEFAULT_CUSTOM_TTS_API_URL
        voice = voice or cfg.get("speaker") or getattr(app_config, "custom_tts_api_speaker", None) or ns.DEFAULT_CUSTOM_TTS_API_SPEAKER
        params = {
            "text": text,
            "speaker": voice,
            "speed": cfg.get("speed") or ns.DEFAULT_CUSTOM_TTS_API_SPEED,
            "novasr": cfg.get("novasr") or ns.DEFAULT_CUSTOM_TTS_API_NOVASR,
        }
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=600)) as session:
            async with session.get(api_url, params=params) as resp:
                body = await resp.read()
                if resp.status >= 400 or not body:
                    raise TTSError(f"自定义 TTS 接口失败：HTTP {resp.status} {body[:300].decode('utf-8', 'ignore')}")
                out_path = out_path.with_suffix(ns._audio_extension_from_content_type(resp.headers.get("Content-Type", "")))
        out_path.write_bytes(body)

    else:
        raise TTSError(f"讲解视频暂不支持语音服务 {provider}")

    if not out_path.exists() or out_path.stat().st_size == 0:
        raise TTSError("语音合成结果为空")
    duration_ms = await ns.ffprobe_duration_ms(str(out_path))
    if not duration_ms:
        raise TTSError("无法读取配音时长")
    return out_path, int(duration_ms), voice
