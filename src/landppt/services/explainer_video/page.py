"""The video page: an ordinary slide whose HTML carries data-explainer-video-id.

The HTML attribute is the only reliable marker (editor saves may rewrite metadata);
``slide_metadata`` mirrors it for convenience only.
"""

from __future__ import annotations

import re
from typing import Optional

VIDEO_PAGE_KIND = "explainer_video"

_MARKER = re.compile(r"""data-explainer-video-id\s*=\s*["']([^"']+)["']""", re.IGNORECASE)


def parse_explainer_video_id(html: str) -> Optional[str]:
    """Return the explainer video id embedded in a slide's HTML, if any."""
    match = _MARKER.search(html or "")
    if not match:
        return None
    return match.group(1).strip() or None


def video_url(video_id: str) -> str:
    return f"/api/explainer-videos/{video_id}/video.mp4"


def cover_url(video_id: str) -> str:
    return f"/api/explainer-videos/{video_id}/cover.png"


def build_video_page_html(video_id: str) -> str:
    return (
        f'<div class="explainer-video-page" data-explainer-video-id="{video_id}" '
        'style="width:1280px;height:720px;position:relative;background:#000;overflow:hidden">'
        f'<video src="{video_url(video_id)}" poster="{cover_url(video_id)}" '
        'controls playsinline preload="metadata" style="width:100%;height:100%;object-fit:contain"></video>'
        "</div>"
    )


def build_video_page_metadata(video_id: str) -> dict:
    return {"kind": VIDEO_PAGE_KIND, "explainer_video_id": video_id}
