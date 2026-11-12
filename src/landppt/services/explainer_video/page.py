"""Explainer video page helpers.

Temporary export-branch version; the generation branch provides the canonical one.
"""

from __future__ import annotations

import re
from typing import Optional

_VIDEO_ID_RE = re.compile(r"""data-explainer-video-id\s*=\s*["']([^"']+)["']""", re.IGNORECASE)


def parse_explainer_video_id(html: str) -> Optional[str]:
    """Return the explainer video id when ``html`` is a video page."""
    match = _VIDEO_ID_RE.search(html or "")
    return match.group(1).strip() if match else None
