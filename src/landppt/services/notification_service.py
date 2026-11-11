"""
Notification service for 立铠 PPT.

Delivers a single event through two channels:
  * in-app  - a row in the `notifications` table, surfaced by the nav bell
  * email   - an HTML mail through the configured provider (SMTP or Resend)

Every entry point is best-effort: a delivery failure is logged and reported in the
return value but never propagates, because notifications are always the last step
of a longer job and must not fail it.
"""

from __future__ import annotations

import html
import logging
import time
import uuid
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Levels accepted by the in-app UI. Anything else is coerced to "info".
NOTIFICATION_LEVELS = ("info", "success", "warning", "error")

_LEVEL_ACCENTS = {
    "info": "#111111",
    "success": "#16a34a",
    "warning": "#b45309",
    "error": "#dc2626",
}


def _normalize_level(level: Optional[str]) -> str:
    value = (level or "info").strip().lower()
    return value if value in NOTIFICATION_LEVELS else "info"


def notification_to_dict(notification) -> Dict[str, Any]:
    """Serialize a Notification ORM row for the JSON API."""
    return {
        "id": notification.id,
        "project_id": notification.project_id,
        "type": notification.notification_type,
        "level": notification.level,
        "title": notification.title,
        "body": notification.body,
        "link_url": notification.link_url,
        "payload": notification.payload or {},
        "is_read": bool(notification.is_read),
        "read_at": notification.read_at,
        "created_at": notification.created_at,
    }


async def create_in_app_notification(
    *,
    user_id: int,
    title: str,
    body: Optional[str] = None,
    notification_type: str = "general",
    level: str = "info",
    project_id: Optional[str] = None,
    link_url: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Persist an in-app notification. Returns the notification id, or None on failure."""
    from ..database.database import AsyncSessionLocal
    from ..database.repositories import NotificationRepository

    try:
        async with AsyncSessionLocal() as session:
            notification = await NotificationRepository(session).create(
                {
                    "id": str(uuid.uuid4()),
                    "user_id": int(user_id),
                    "project_id": project_id,
                    "notification_type": notification_type,
                    "level": _normalize_level(level),
                    "title": title[:255],
                    "body": body,
                    "link_url": link_url,
                    "payload": payload or {},
                    "is_read": False,
                    "created_at": time.time(),
                }
            )
            return notification.id
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to create in-app notification for user %s: %s", user_id, exc)
        return None


async def get_user_email(user_id: int) -> Optional[str]:
    """Resolve a user's email address. Returns None when unset (common for OAuth users)."""
    from ..database.database import AsyncSessionLocal
    from ..database.repositories import UserRepository

    try:
        async with AsyncSessionLocal() as session:
            user = await UserRepository(session).get_by_id(int(user_id))
        email = (getattr(user, "email", None) or "").strip() if user else ""
        return email or None
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to resolve email for user %s: %s", user_id, exc)
        return None


def render_email_html(
    *,
    title: str,
    body: Optional[str] = None,
    level: str = "info",
    detail_rows: Optional[List[tuple]] = None,
    action_url: Optional[str] = None,
    action_text: str = "打开项目",
) -> str:
    """Render the standard 立铠 PPT notification email.

    detail_rows is a list of (label, value) pairs rendered as a definition table.
    All caller-supplied text is HTML-escaped.
    """
    accent = _LEVEL_ACCENTS.get(_normalize_level(level), _LEVEL_ACCENTS["info"])

    rows_html = ""
    for label, value in detail_rows or []:
        rows_html += (
            '<tr>'
            f'<td style="padding:8px 0;color:#6b7280;font-size:13px;width:96px;vertical-align:top;">{html.escape(str(label))}</td>'
            f'<td style="padding:8px 0;color:#111111;font-size:13px;">{html.escape(str(value))}</td>'
            '</tr>'
        )
    detail_html = (
        f'<table style="width:100%;border-collapse:collapse;margin-top:8px;">{rows_html}</table>'
        if rows_html else ""
    )

    action_html = ""
    if action_url:
        action_html = (
            f'<a href="{html.escape(action_url, quote=True)}" '
            'style="display:inline-block;margin-top:24px;padding:12px 24px;background:#111111;color:#ffffff;'
            'text-decoration:none;border-radius:10px;font-weight:600;font-size:14px;">'
            f'{html.escape(action_text)}</a>'
        )

    body_html = ""
    if body:
        body_html = (
            '<p style="color:#4b5563;font-size:14px;line-height:1.7;margin:0 0 4px;white-space:pre-line;">'
            f'{html.escape(body)}</p>'
        )

    return f"""
    <!DOCTYPE html>
    <html>
    <head><meta charset="UTF-8"></head>
    <body style="margin:0;padding:24px;background:#f5f5f5;font-family:'Inter',-apple-system,'Segoe UI',Arial,sans-serif;">
        <div style="max-width:560px;margin:0 auto;background:#ffffff;border-radius:16px;padding:40px;box-shadow:0 4px 20px rgba(0,0,0,0.08);">
            <div style="font-size:24px;font-weight:700;color:#111111;margin-bottom:24px;">立铠 PPT</div>
            <div style="border-left:3px solid {accent};padding-left:16px;margin-bottom:20px;">
                <div style="font-size:18px;font-weight:600;color:#111111;">{html.escape(title)}</div>
            </div>
            {body_html}
            {detail_html}
            {action_html}
            <div style="color:#9ca3af;font-size:12px;margin-top:32px;padding-top:20px;border-top:1px solid #eeeeee;">
                此邮件由 立铠 PPT 无人值守任务自动发送。如需关闭，请在「系统配置 → 生成参数 → 无人值守」中取消邮件通知。
            </div>
        </div>
    </body>
    </html>
    """


async def notify_user(
    *,
    user_id: int,
    title: str,
    body: Optional[str] = None,
    notification_type: str = "general",
    level: str = "info",
    project_id: Optional[str] = None,
    link_url: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
    send_in_app: bool = True,
    send_email_notification: bool = False,
    email_subject: Optional[str] = None,
    email_detail_rows: Optional[List[tuple]] = None,
    email_action_url: Optional[str] = None,
    email_action_text: str = "打开项目",
) -> Dict[str, Any]:
    """Deliver one event over the requested channels.

    Returns {"in_app": bool, "email": bool, "email_message": str|None,
             "notification_id": str|None}. Never raises.
    """
    outcome: Dict[str, Any] = {
        "in_app": False,
        "email": False,
        "email_message": None,
        "notification_id": None,
    }

    if send_in_app:
        notification_id = await create_in_app_notification(
            user_id=user_id,
            title=title,
            body=body,
            notification_type=notification_type,
            level=level,
            project_id=project_id,
            link_url=link_url,
            payload=payload,
        )
        outcome["notification_id"] = notification_id
        outcome["in_app"] = notification_id is not None

    if send_email_notification:
        try:
            email = await get_user_email(user_id)
            if not email:
                outcome["email_message"] = "用户未绑定邮箱"
            else:
                from .email_service import send_email

                success, message = await send_email(
                    email,
                    email_subject or f"立铠 PPT · {title}",
                    render_email_html(
                        title=title,
                        body=body,
                        level=level,
                        detail_rows=email_detail_rows,
                        action_url=email_action_url,
                        action_text=email_action_text,
                    ),
                )
                outcome["email"] = bool(success)
                outcome["email_message"] = message
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to send notification email to user %s: %s", user_id, exc)
            outcome["email_message"] = str(exc)

    return outcome
