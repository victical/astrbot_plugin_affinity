from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any


@dataclass(frozen=True, slots=True)
class ConversationMessage:
    message_id: str
    user_id: str
    role: str
    content: str
    created_at: datetime
    chat_type: str = "private"
    group_id: str | None = None


@dataclass(frozen=True, slots=True)
class ConversationSession:
    session_id: str
    user_id: str
    event_date: date
    messages: list[ConversationMessage]


COMMAND_PREFIXES = (
    "/查询好感",
    "/好感查询",
    "/好感回顾",
    "查询好感",
    "好感查询",
    "好感回顾",
)


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def normalize_message(raw: dict[str, Any]) -> ConversationMessage | None:
    created_at = _parse_datetime(raw.get("created_at") or raw.get("timestamp"))
    if created_at is None:
        return None
    message_id = str(raw.get("message_id") or raw.get("uuid") or "").strip()
    if not message_id:
        message_id = f"{raw.get('user_id', '')}:{created_at.isoformat()}"
    return ConversationMessage(
        message_id=message_id,
        user_id=str(raw.get("user_id") or raw.get("member_id") or "").strip(),
        role=str(raw.get("role") or "user").strip().lower(),
        content=str(raw.get("content") or "").strip(),
        created_at=created_at,
        chat_type=str(raw.get("chat_type") or ("group" if raw.get("group_id") else "private")),
        group_id=str(raw.get("group_id")) if raw.get("group_id") is not None else None,
    )


def is_low_value_message(message: ConversationMessage | None) -> bool:
    if message is None:
        return True
    if message.role == "system":
        return True
    content = message.content.strip()
    if not content:
        return True
    if any(content.startswith(prefix) for prefix in COMMAND_PREFIXES):
        return True
    if content in {"[转发消息]", "[图片]", "[表情]", "[语音]"}:
        return True
    return False


def _session_id(
    user_id: str,
    event_day: date,
    messages: list[ConversationMessage],
    index: int,
) -> str:
    start = messages[0].created_at.strftime("%H:%M:%S")
    end = messages[-1].created_at.strftime("%H:%M:%S")
    return f"{user_id}:{event_day.isoformat()}:{start}:{end}:{index}"


def _flush_session(
    sessions: list[ConversationSession],
    user_id: str,
    event_day: date,
    messages: list[ConversationMessage],
) -> None:
    if not messages:
        return
    sessions.append(
        ConversationSession(
            session_id=_session_id(user_id, event_day, messages, len(sessions)),
            user_id=user_id,
            event_date=event_day,
            messages=list(messages),
        )
    )


def slice_conversation(
    user_id: str,
    event_date: date,
    raw_messages: list[dict[str, Any]],
    *,
    gap_minutes: int = 45,
    max_messages: int = 80,
    overlap_messages: int = 3,
) -> list[ConversationSession]:
    gap = timedelta(minutes=max(1, int(gap_minutes)))
    max_messages = max(1, int(max_messages))
    overlap_messages = max(0, min(int(overlap_messages), max_messages - 1))
    messages = sorted(
        (
            message
            for message in (normalize_message(raw) for raw in raw_messages)
            if not is_low_value_message(message)
        ),
        key=lambda item: (item.created_at, item.message_id),
    )

    sessions: list[ConversationSession] = []
    current: list[ConversationMessage] = []
    for message in messages:
        if current and message.created_at - current[-1].created_at > gap:
            _flush_session(sessions, user_id, event_date, current)
            current = []

        if len(current) >= max_messages:
            _flush_session(sessions, user_id, event_date, current)
            current = current[-overlap_messages:] if overlap_messages else []

        current.append(message)

    _flush_session(sessions, user_id, event_date, current)
    return sessions
