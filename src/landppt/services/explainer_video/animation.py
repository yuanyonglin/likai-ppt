"""Render the timeline into a self-contained 1920x1080 animation page.

The model only chooses a scene template per segment and fills its text; layout,
timing, captions and the ``window.__start`` contract are owned here, so the page
never references external resources and cannot drift from the audio.
"""

from __future__ import annotations

import json
import re
from html import escape
from typing import Any, Dict, List, Sequence

WIDTH, HEIGHT = 1920, 1080
TEMPLATES = ("title", "bullets", "cards", "steps", "compare", "highlight")

THEMES = {
    "business_blue": {
        "bg": "#F4F7FC", "surface": "#FFFFFF", "text": "#16202E", "muted": "#5B6675",
        "primary": "#1F4E9E", "accent": "#2F80ED", "line": "#DCE4F0",
    },
    "dark_tech": {
        "bg": "#0B1220", "surface": "#121C2E", "text": "#E6EDF7", "muted": "#8FA3BF",
        "primary": "#22D3EE", "accent": "#6366F1", "line": "#1F2C44",
    },
}


def _luminance(color: str) -> float:
    r, g, b = (int(color[i:i + 2], 16) / 255 for i in (1, 3, 5))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def resolve_theme(style: str, slide_colors: Sequence[str] = ()) -> Dict[str, str]:
    if style in THEMES:
        return dict(THEMES[style])
    theme = dict(THEMES["business_blue"])
    # follow_ppt: brand colors are the saturated ones, not page whites/blacks.
    brand = [c for c in slide_colors if re.fullmatch(r"#[0-9A-Fa-f]{6}", c) and 0.12 < _luminance(c) < 0.8]
    if brand:
        theme["primary"] = brand[0]
        theme["accent"] = brand[1] if len(brand) > 1 else brand[0]
    return theme


def _text(value: Any, limit: int = 80) -> str:
    return escape(str(value or "").strip()[:limit])


def _items(scene: Dict[str, Any], limit: int) -> List[Dict[str, str]]:
    out = []
    for item in scene.get("items") or []:
        if isinstance(item, dict):
            title, text = item.get("title") or "", item.get("text") or ""
        else:
            title, text = str(item), ""
        if str(title).strip() or str(text).strip():
            out.append({"title": _text(title, 40), "text": _text(text, 90)})
    return out[:limit]


def _scene_body(scene: Dict[str, Any], index: int) -> tuple[str, int]:
    """Return (inner html, number of timed items)."""
    template = scene.get("template") if scene.get("template") in TEMPLATES else "bullets"
    heading = _text(scene.get("heading"), 40)
    sub = _text(scene.get("subheading"), 60)
    head = f'<h2 class="h">{heading}</h2>' + (f'<p class="sub">{sub}</p>' if sub else "")

    if template == "title":
        return (
            f'<div class="title-wrap"><div class="bar item"></div><h1 class="big">{heading}</h1>'
            + (f'<p class="lead item">{sub}</p>' if sub else "")
            + "</div>",
            1 + bool(sub),
        )

    if template == "compare":
        sides = []
        for key, cls in (("left", "before"), ("right", "after")):
            side = scene.get(key) if isinstance(scene.get(key), dict) else {}
            rows = "".join(f'<li>{item["title"]}</li>' for item in _items(side, 5))
            sides.append(f'<div class="col {cls} item"><h3>{_text(side.get("title"), 20)}</h3><ul>{rows}</ul></div>')
        return head + f'<div class="compare">{sides[0]}<div class="arrow item">→</div>{sides[1]}</div>', 3

    if template == "highlight":
        items = _items(scene, 4)
        tags = "".join(f'<span class="tag item">{item["title"]}</span>' for item in items)
        return (
            f'<div class="hl"><div class="quote item">{heading}</div>'
            + (f'<p class="lead item">{sub}</p>' if sub else "")
            + f'<div class="tags">{tags}</div></div>',
            1 + bool(sub) + len(items),
        )

    if template == "cards":
        items = _items(scene, 4)
        cards = "".join(
            f'<div class="card item"><div class="num">{i + 1:02d}</div><h3>{item["title"]}</h3><p>{item["text"]}</p></div>'
            for i, item in enumerate(items)
        )
        return head + f'<div class="cards n{len(items)}">{cards}</div>', len(items)

    if template == "steps":
        items = _items(scene, 5)
        steps = "".join(
            f'<div class="step item"><div class="dot">{i + 1}</div><h3>{item["title"]}</h3><p>{item["text"]}</p></div>'
            for i, item in enumerate(items)
        )
        return head + f'<div class="steps"><div class="rail"></div>{steps}</div>', len(items)

    items = _items(scene, 5)
    rows = "".join(
        f'<li class="item"><span class="badge">{i + 1}</span><div><b>{item["title"]}</b>'
        + (f'<p>{item["text"]}</p>' if item["text"] else "")
        + "</div></li>"
        for i, item in enumerate(items)
    )
    art = (
        '<svg class="art item" viewBox="0 0 400 400" aria-hidden="true">'
        '<circle cx="200" cy="200" r="150" fill="none" stroke="var(--line)" stroke-width="24"/>'
        '<circle class="spin" cx="200" cy="200" r="150" fill="none" stroke="var(--primary)" stroke-width="24" '
        'stroke-dasharray="240 700" stroke-linecap="round"/>'
        f'<text x="200" y="228" text-anchor="middle" font-size="96" font-weight="700" fill="var(--primary)">{index + 1:02d}</text>'
        "</svg>"
    )
    return head + f'<div class="bullets"><ul>{rows}</ul>{art}</div>', len(items) + 1


CSS = """
*{box-sizing:border-box;margin:0;padding:0}
html,body{width:1920px;height:1080px;overflow:hidden;background:var(--bg);color:var(--text);
font-family:"PingFang SC","Microsoft YaHei","Noto Sans CJK SC",sans-serif}
.top{position:absolute;left:120px;right:120px;top:56px;display:flex;align-items:center;gap:20px;color:var(--muted);font-size:26px}
.top i{width:14px;height:14px;border-radius:50%;background:var(--primary);display:block}
.prog{position:absolute;left:0;top:0;height:8px;width:0;background:linear-gradient(90deg,var(--primary),var(--accent))}
.scene{position:absolute;left:120px;right:120px;top:150px;bottom:170px;opacity:0;transform:translateY(30px);
transition:opacity .7s ease,transform .7s ease}
.scene.on{opacity:1;transform:none}
.item{opacity:0;transform:translateY(22px);transition:opacity .6s ease,transform .6s ease}
.item.on{opacity:1;transform:none}
.h{font-size:64px;font-weight:700;line-height:1.2;letter-spacing:1px}
.sub{font-size:32px;color:var(--muted);margin-top:16px}
.title-wrap{height:100%;display:flex;flex-direction:column;justify-content:center}
.bar{width:140px;height:12px;border-radius:6px;background:var(--primary);margin-bottom:44px}
.big{font-size:96px;font-weight:800;line-height:1.18;max-width:1500px}
.lead{font-size:40px;color:var(--muted);margin-top:34px;max-width:1400px;line-height:1.5}
.bullets{display:flex;align-items:center;justify-content:space-between;margin-top:56px;gap:80px}
.bullets ul{list-style:none;flex:1;display:flex;flex-direction:column;gap:34px}
.bullets li{display:flex;gap:28px;align-items:flex-start;font-size:40px;line-height:1.4}
.bullets li p{font-size:30px;color:var(--muted);margin-top:6px}
.badge{flex:none;width:64px;height:64px;border-radius:18px;background:var(--primary);color:#fff;
display:flex;align-items:center;justify-content:center;font-size:32px;font-weight:700}
.art{width:420px;height:420px;flex:none}
.spin{transform-origin:200px 200px;animation:spin 9s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
.cards{display:grid;gap:36px;margin-top:64px;grid-template-columns:repeat(var(--cols,3),1fr)}
.cards.n1{--cols:1}.cards.n2{--cols:2}.cards.n3{--cols:3}.cards.n4{--cols:4}
.card{background:var(--surface);border:2px solid var(--line);border-radius:28px;padding:44px 40px;min-height:380px;
box-shadow:0 18px 50px rgba(15,30,60,.08);border-top:10px solid var(--primary)}
.card .num{font-size:56px;font-weight:800;color:var(--accent);opacity:.9}
.card h3{font-size:40px;margin:22px 0 16px;line-height:1.3}
.card p{font-size:29px;line-height:1.55;color:var(--muted)}
.steps{position:relative;display:flex;justify-content:space-between;gap:30px;margin-top:110px}
.rail{position:absolute;left:60px;right:60px;top:52px;height:6px;background:var(--line)}
.step{position:relative;flex:1;text-align:center}
.dot{width:110px;height:110px;margin:0 auto 34px;border-radius:50%;background:var(--primary);color:#fff;
font-size:48px;font-weight:800;display:flex;align-items:center;justify-content:center;box-shadow:0 0 0 14px var(--bg)}
.step h3{font-size:38px;line-height:1.3}
.step p{font-size:28px;color:var(--muted);margin-top:14px;line-height:1.5;padding:0 10px}
.compare{display:flex;align-items:stretch;gap:40px;margin-top:64px}
.col{flex:1;border-radius:28px;padding:44px;background:var(--surface);border:2px solid var(--line)}
.col h3{font-size:42px;margin-bottom:28px}
.col ul{list-style:none;display:flex;flex-direction:column;gap:22px}
.col li{font-size:32px;line-height:1.45;padding-left:34px;position:relative}
.col li:before{content:"";position:absolute;left:0;top:18px;width:14px;height:14px;border-radius:50%;background:var(--muted)}
.after{border-color:var(--primary)}.after h3{color:var(--primary)}.after li:before{background:var(--primary)}
.arrow{align-self:center;font-size:96px;color:var(--accent);font-weight:800}
.hl{height:100%;display:flex;flex-direction:column;justify-content:center;border-left:16px solid var(--primary);padding-left:70px}
.quote{font-size:84px;font-weight:800;line-height:1.3;max-width:1500px}
.tags{display:flex;gap:24px;margin-top:50px;flex-wrap:wrap}
.tag{font-size:32px;padding:16px 32px;border-radius:999px;background:var(--surface);border:2px solid var(--primary);color:var(--primary)}
#cap{position:absolute;left:50%;bottom:44px;transform:translateX(-50%);max-width:1560px;padding:14px 32px;border-radius:12px;
background:rgba(0,0,0,.66);color:#fff;font-size:38px;line-height:1.45;text-align:center;opacity:0;transition:opacity .2s}
"""

JS = """
(function(){
var TL=__TIMELINE__,scenes=document.querySelectorAll('.scene'),cap=document.getElementById('cap'),
prog=document.querySelector('.prog'),t0=null;
function tick(){var t=(performance.now()-t0)/1000;
for(var i=0;i<scenes.length;i++){var sc=TL.scenes[i],on=t>=sc.start&&(i===scenes.length-1||t<TL.scenes[i+1].start);
scenes[i].classList.toggle('on',on);var items=scenes[i].querySelectorAll('.item');
for(var j=0;j<items.length;j++){items[j].classList.toggle('on',on&&t>=(sc.items[j]===undefined?sc.start:sc.items[j]));}}
var text='';if(TL.subtitles){for(var k=0;k<TL.cues.length;k++){if(t>=TL.cues[k][0]&&t<TL.cues[k][1]){text=TL.cues[k][2];break;}}}
cap.textContent=text;cap.style.opacity=text?1:0;prog.style.width=Math.min(100,t/TL.total*100)+'%';
if(t<TL.total+3)requestAnimationFrame(tick);}
window.__start=function(){if(t0===null){t0=performance.now();requestAnimationFrame(tick);}};
window.__seek=function(t){t0=performance.now()-t*1000;tick();};
window.__ready=true;
})();
"""


def item_times(start: float, duration: float, count: int) -> List[float]:
    """Reveal items one by one over the first ~70% of the segment."""
    if count <= 0:
        return []
    lead = min(0.5, duration * 0.1)
    span = max(0.0, duration * 0.7 - lead)
    step = span / count if count > 1 else 0
    return [round(start + lead + step * i, 2) for i in range(count)]


def build_animation_html(
    *,
    title: str,
    scenes: List[Dict[str, Any]],
    starts: Sequence[float],
    durations: Sequence[float],
    cues: Sequence[Sequence[Any]],
    total: float,
    theme: Dict[str, str],
    subtitles: bool = True,
) -> str:
    bodies, timeline = [], []
    for index, (start, duration) in enumerate(zip(starts, durations)):
        scene = scenes[index] if index < len(scenes) and isinstance(scenes[index], dict) else {}
        body, count = _scene_body(scene, index)
        bodies.append(f'<section class="scene">{body}</section>')
        timeline.append({"start": round(start, 2), "items": item_times(start, duration, count)})
    data = {"scenes": timeline, "cues": [list(c) for c in cues], "total": round(total, 2), "subtitles": bool(subtitles)}
    variables = ";".join(f"--{key}:{value}" for key, value in theme.items())
    # "</" inside JSON would close the script element.
    timeline_json = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return (
        "<!DOCTYPE html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
        f"<title>{_text(title, 60)}</title><style>:root{{{variables}}}{CSS}</style></head>"
        f'<body><div class="prog"></div><div class="top"><i></i><span>{_text(title, 60)}</span></div>'
        + "".join(bodies)
        + '<div id="cap"></div><script>'
        + JS.replace("__TIMELINE__", timeline_json)
        + "</script></body></html>"
    )


def build_cues(texts: Sequence[str], starts: Sequence[float], durations: Sequence[float]) -> List[List[Any]]:
    """Split each segment into sentence cues timed by character share."""
    cues: List[List[Any]] = []
    for text, start, duration in zip(texts, starts, durations):
        parts = [p.strip() for p in re.split(r"(?<=[。！？；!?;])", text or "") if p.strip()]
        # Long sentences become two lines' worth at most per cue.
        pieces: List[str] = []
        for part in parts:
            while len(part) > 40:
                cut = max(part.rfind(mark, 0, 40) for mark in "，,、：:") + 1 or 40
                pieces.append(part[:cut])
                part = part[cut:]
            if part:
                pieces.append(part)
        total_chars = sum(len(p) for p in pieces) or 1
        cursor = start
        for piece in pieces:
            length = duration * len(piece) / total_chars
            cues.append([round(cursor, 2), round(cursor + length, 2), piece.rstrip("，,、")])
            cursor += length
    return cues
