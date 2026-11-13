"""LLM steps: expand the source into a segmented script, then plan scenes."""

from __future__ import annotations

import json
from typing import Any, Dict, List

from ...ai.base import AIMessage, MessageRole
from ...ai.providers import strip_think_content
from ..slide.edit_agent.protocol import extract_json_payload
from .animation import TEMPLATES
from .sources import SourceContent

DURATIONS = {
    "1m": ("60-90 秒", "4-6"),
    "2m": ("110-140 秒", "7-9"),
    "3m": ("170-200 秒", "10-13"),
}

SCRIPT_SYSTEM = """你是企业汇报讲解视频的编剧。根据给定资料写一段约 {seconds} 的讲解视频脚本。
要求：
- 讲解资料内容，并补充必要的背景、原因或意义，但不得编造资料中没有的具体数字、人名、日期。
- 拆成 {segments} 段，每段 2-4 句口语化中文，适合配音，不要使用 Markdown、括号注释或表情。
- 每段给出画面要表达的视觉意图 visual，以及画面上要出现的要点 bullets（每条不超过 14 个字，0-4 条）。
- 只输出 JSON：{{"title": "视频标题", "segments": [{{"text": "配音文字", "visual": "画面意图", "bullets": ["要点"]}}]}}"""

SCENE_SYSTEM = """你是讲解视频的分镜设计师。为每个分段选择一个画面模板并填写画面文字，画面会按配音时间轴自动播放。
可用模板（template）：
- title：开场/总结大标题。字段 heading、subheading。
- bullets：要点列表。字段 heading、items（2-5 条，{{"title": 短句, "text": 可选补充}}）。
- cards：并列的 2-4 个概念卡片。字段 heading、items（{{"title", "text"}}）。
- steps：流程/阶段/步骤 3-5 步。字段 heading、items（{{"title", "text"}}）。
- compare：前后对比或两方对比。字段 heading、left/right（{{"title": 列标题, "items": ["短句"]}}）。
- highlight：一句核心结论。字段 heading（结论句，不超过 24 字）、subheading、items（0-4 个关键词）。
要求：
- 分段数量与顺序必须与输入一致，每段输出一个场景；相邻场景尽量换不同模板，第一段适合 title 或 highlight。
- 画面只放标题、要点、关键词，不要放整段配音原文；heading 不超过 18 字，item title 不超过 14 字，text 不超过 30 字。
- 不得编造资料中没有的数字。
- 只输出 JSON：{{"scenes": [{{"template": "...", "heading": "...", ...}}]}}"""


class ScriptError(RuntimeError):
    pass


async def _role_provider(user_id: int, role: str):
    from ..db_config_service import get_user_role_provider

    return await get_user_role_provider(user_id, role)


async def _complete_json(user_id: int, role: str, system: str, prompt: str) -> Dict[str, Any]:
    provider, settings = await _role_provider(user_id, role)
    kwargs: Dict[str, Any] = {"temperature": 0.6}
    if settings.get("model"):
        kwargs["model"] = settings["model"]
    last = ""
    for _ in range(2):
        response = await provider.chat_completion(
            [AIMessage(role=MessageRole.SYSTEM, content=system), AIMessage(role=MessageRole.USER, content=prompt)],
            **kwargs,
        )
        last = strip_think_content(response.content or "")
        payload = extract_json_payload(last)
        if payload:
            return payload
    raise ScriptError(f"模型未返回有效 JSON：{last[:200]}")


def normalize_script(payload: Dict[str, Any], fallback_title: str) -> Dict[str, Any]:
    segments = []
    for raw in payload.get("segments") or []:
        if not isinstance(raw, dict):
            continue
        text = str(raw.get("text") or "").strip()
        if not text:
            continue
        bullets = [str(b).strip() for b in (raw.get("bullets") or []) if str(b).strip()][:4]
        segments.append({"text": text, "visual": str(raw.get("visual") or "").strip(), "bullets": bullets})
    if not segments:
        raise ScriptError("讲解稿为空")
    return {"title": str(payload.get("title") or fallback_title or "讲解视频").strip(), "segments": segments}


async def write_script(user_id: int, source: SourceContent, *, duration: str, extra: str) -> Dict[str, Any]:
    seconds, segments = DURATIONS.get(duration, DURATIONS["1m"])
    prompt = (
        f"标题：{source.title}\n资料内容：{source.text[:6000]}\n"
        f"已有讲稿：{source.speech[:3000] or '无'}\n补充讲解方向：{extra or '无'}"
    )
    payload = await _complete_json(
        user_id, "speech_script", SCRIPT_SYSTEM.format(seconds=seconds, segments=segments), prompt
    )
    return normalize_script(payload, source.title)


async def plan_scenes(user_id: int, script: Dict[str, Any], source: SourceContent) -> List[Dict[str, Any]]:
    segments = [
        {"index": i, "text": seg["text"], "visual": seg.get("visual", ""), "bullets": seg.get("bullets", [])}
        for i, seg in enumerate(script["segments"])
    ]
    prompt = (
        f"视频标题：{script['title']}\n分段：{json.dumps(segments, ensure_ascii=False)}\n"
        f"原始资料（供参考）：{source.text[:2500]}"
    )
    payload = await _complete_json(user_id, "slide_generation", SCENE_SYSTEM, prompt)
    scenes = [scene for scene in (payload.get("scenes") or []) if isinstance(scene, dict)]
    # Missing scenes fall back to the segment's own bullets.
    for index, seg in enumerate(script["segments"]):
        if index >= len(scenes):
            scenes.append({"template": "bullets", "heading": script["title"], "items": seg.get("bullets", [])})
        elif scenes[index].get("template") not in TEMPLATES:
            scenes[index]["template"] = "bullets"
    return scenes[: len(script["segments"])]
