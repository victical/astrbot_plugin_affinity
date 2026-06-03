from __future__ import annotations

import json
from dataclasses import dataclass, field

from ..core.review_signals import (
    NEGATIVE_SIGNAL_TYPES,
    NEUTRAL_SIGNAL_TYPES,
    POSITIVE_SIGNAL_TYPES,
    ReviewSignal,
    validate_signal_payload,
)
from .conversation_slicer import ConversationSession


@dataclass(slots=True)
class SignalAnalysisResult:
    signals: list[ReviewSignal] = field(default_factory=list)
    raw_event_count: int = 0
    discarded_count: int = 0
    errors: list[str] = field(default_factory=list)


class AffinitySignalAnalyzer:
    def __init__(
        self,
        *,
        context,
        model_id: str = "",
        min_confidence: float = 0.65,
    ):
        self.context = context
        self.model_id = str(model_id or "").strip()
        self.min_confidence = float(min_confidence)

    def _resolve_provider(self):
        provider = None
        if self.model_id:
            get_provider_by_id = getattr(self.context, "get_provider_by_id", None)
            if callable(get_provider_by_id):
                provider = get_provider_by_id(self.model_id)
        if provider is None:
            get_using_provider = getattr(self.context, "get_using_provider", None)
            if callable(get_using_provider):
                provider = get_using_provider()
        return provider

    def _build_prompt(self, session: ConversationSession) -> str:
        messages = "\n".join(
            f"- id={message.message_id} role={message.role}: {message.content[:300]}"
            for message in session.messages
        )
        return (
            "你是关系事件抽取器，只输出 JSON，不要输出分数或 score_delta。\n"
            "从会话中提取 relationship events，字段为 type, direction, intensity, "
            "confidence, evidence, message_refs。\n"
            f"positive types: {', '.join(sorted(POSITIVE_SIGNAL_TYPES))}\n"
            f"negative types: {', '.join(sorted(NEGATIVE_SIGNAL_TYPES))}\n"
            f"neutral types: {', '.join(sorted(NEUTRAL_SIGNAL_TYPES))}\n"
            "direction 必须匹配 type；intensity 为 1 到 3；confidence 为 0 到 1；"
            "evidence 用简短中文摘要，不要复制大段原文。\n"
            "返回格式: {\"events\": [{...}]}\n\n"
            f"session_id: {session.session_id}\n"
            f"date: {session.event_date.isoformat()}\n"
            "messages:\n"
            f"{messages}"
        )

    async def analyze_session(self, session: ConversationSession) -> SignalAnalysisResult:
        provider = self._resolve_provider()
        if provider is None:
            return SignalAnalysisResult(errors=["no_provider"])
        try:
            response = await provider.text_chat(prompt=self._build_prompt(session))
            text = (getattr(response, "completion_text", None) or "").strip()
            if not text:
                return SignalAnalysisResult(errors=["empty_completion"])
            payload = json.loads(text)
        except Exception as exc:
            return SignalAnalysisResult(errors=[f"provider_error:{type(exc).__name__}"])

        events = payload.get("events") if isinstance(payload, dict) else None
        if not isinstance(events, list):
            return SignalAnalysisResult(errors=["invalid_payload"])

        signals: list[ReviewSignal] = []
        discarded = 0
        for event in events:
            signal = validate_signal_payload(
                event,
                min_confidence=self.min_confidence,
                session_id=session.session_id,
            )
            if signal is None:
                discarded += 1
                continue
            signals.append(signal)

        return SignalAnalysisResult(
            signals=signals,
            raw_event_count=len(events),
            discarded_count=discarded,
        )
