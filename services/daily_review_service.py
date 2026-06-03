from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from ..core.models import AffinityEventType
from ..core.review_signals import (
    aggregate_signals,
    score_signals_with_negative_cap,
)
from ..core.rules import (
    DEFAULT_DAILY_REVIEW_NEGATIVE_CAP,
    apply_daily_cap,
    daily_key,
)
from ..core.stage import effective_stage, score_stage
from ..core.stage_progression import advance_stage, bank_balance
from ..db_manager import AffinityDatabaseManager
from .affinity_service import parse_event_date
from .conversation_slicer import slice_conversation


@dataclass(slots=True)
class DailyReviewCandidate:
    event_type: str
    score_delta: float
    reason: str
    source_ref: str
    metadata: dict[str, Any]


@dataclass(slots=True)
class DailyReviewResult:
    user_id: str
    event_date: date
    events: list[DailyReviewCandidate]
    total_delta: float
    written: bool
    skipped: bool = False
    backup_path: Path | None = None
    session_count: int = 0
    raw_signal_count: int = 0
    valid_signal_count: int = 0
    discarded_signal_count: int = 0
    fallback_used: bool = False
    current_stage: str = ""
    bank_balance: int = 0
    next_advance_stage: str = ""


@dataclass(slots=True)
class DailyReviewDiagnostics:
    candidates: list[DailyReviewCandidate]
    session_count: int = 0
    raw_signal_count: int = 0
    valid_signal_count: int = 0
    discarded_signal_count: int = 0
    fallback_used: bool = False


class DailyReviewService:
    def __init__(
        self,
        db: AffinityDatabaseManager,
        provider,
        backup_dir: str | Path | None = None,
        signal_analyzer=None,
        llm_review_enabled: bool = False,
        review_options: dict[str, Any] | None = None,
    ):
        self.db = db
        self.provider = provider
        self.backup_dir = Path(backup_dir or "affinity_logs")
        self.signal_analyzer = signal_analyzer
        self.llm_review_enabled = bool(llm_review_enabled)
        self.review_options = review_options or {}

    @property
    def stage_advance_enabled(self) -> bool:
        return bool(self.review_options.get("stage_advance_enabled", True))

    @property
    def max_stage_steps_per_day(self) -> int:
        return int(self.review_options.get("stage_advance_max_steps_per_day", 1))

    def _casual_chat_score(self, turns: int) -> int:
        if turns <= 0:
            return 0
        if turns <= 2:
            return 1
        if turns <= 5:
            return 2
        return 3

    async def _build_candidates(
        self,
        user_id: str,
        event_day: date,
    ) -> list[DailyReviewCandidate]:
        profile_delta = await self.provider.get_profile_delta(user_id, event_day)
        stats = await self.provider.get_interaction_stats(user_id, event_day)
        summary = await self.provider.get_daily_summary(user_id, event_day)
        memory_refs = await self.provider.get_memory_refs(user_id, event_day, limit=5)

        candidates: list[DailyReviewCandidate] = []

        turns = int(stats.get("valid_turns") or stats.get("valid_messages") or 0)
        if turns > 0:
            candidates.append(
                DailyReviewCandidate(
                    event_type=AffinityEventType.DAILY_REVIEW.value,
                    score_delta=self._casual_chat_score(turns),
                    reason="每日互动回顾",
                    source_ref="interaction",
                    metadata={"turns": turns},
                )
            )

        added_count = 0
        if isinstance(profile_delta, dict):
            for key in ("likes_added", "dislikes_added", "nicknames_added"):
                value = profile_delta.get(key) or []
                if isinstance(value, (list, tuple, set)):
                    added_count += len(value)
        if added_count:
            candidates.append(
                DailyReviewCandidate(
                    event_type=AffinityEventType.PROFILE_GROWTH.value,
                    score_delta=added_count * 10,
                    reason="画像在今日产生新增信息",
                    source_ref="profile_delta",
                    metadata={"added_count": added_count},
                )
            )

        if memory_refs:
            candidates.append(
                DailyReviewCandidate(
                    event_type=AffinityEventType.MEMORY_RECALL.value,
                    score_delta=min(5, len(memory_refs) * 2),
                    reason="今日形成或命中长期记忆",
                    source_ref="memory_refs",
                    metadata={"memory_refs": memory_refs[:5]},
                )
            )

        summary_text = str((summary or {}).get("summary") or "")
        tags = summary.get("tags") if isinstance(summary, dict) else []
        if "心事" in summary_text or "秘密" in summary_text or "心事" in (tags or []):
            candidates.append(
                DailyReviewCandidate(
                    event_type=AffinityEventType.SHARED_WORRY.value,
                    score_delta=4,
                    reason="用户分享了心事或重要情绪",
                    source_ref="daily_summary",
                    metadata={"summary": summary_text[:120]},
                )
            )

        capped: list[DailyReviewCandidate] = []
        current_negative = 0.0
        for candidate in candidates:
            if candidate.event_type == AffinityEventType.PROFILE_GROWTH.value:
                capped.append(candidate)
                continue

            requested_delta = candidate.score_delta
            if requested_delta > 0:
                capped.append(candidate)
                continue
            elif requested_delta < 0:
                current_delta = current_negative
                cap = DEFAULT_DAILY_REVIEW_NEGATIVE_CAP
            else:
                continue

            delta = apply_daily_cap(
                current_delta=current_delta,
                requested_delta=requested_delta,
                cap=cap,
            )
            if delta == 0:
                continue
            if delta < 0:
                current_negative += delta
            capped.append(
                DailyReviewCandidate(
                    event_type=candidate.event_type,
                    score_delta=delta,
                    reason=candidate.reason,
                    source_ref=candidate.source_ref,
                    metadata=candidate.metadata,
                )
            )
        return capped

    async def _build_llm_review_candidates(
        self,
        user_id: str,
        event_day: date,
    ) -> DailyReviewDiagnostics:
        get_messages = getattr(self.provider, "get_conversation_messages", None)
        if not callable(get_messages) or self.signal_analyzer is None:
            return DailyReviewDiagnostics(candidates=[], fallback_used=True)

        try:
            raw_messages = await get_messages(user_id, event_day)
            sessions = slice_conversation(
                user_id,
                event_day,
                raw_messages or [],
                gap_minutes=int(self.review_options.get("session_gap_minutes", 45)),
                max_messages=int(self.review_options.get("max_messages_per_session", 80)),
                overlap_messages=int(self.review_options.get("overlap_messages", 3)),
            )
            raw_count = 0
            discarded_count = 0
            signals = []
            for session in sessions:
                analysis = await self.signal_analyzer.analyze_session(session)
                if getattr(analysis, "errors", None):
                    return DailyReviewDiagnostics(candidates=[], fallback_used=True)
                raw_count += int(getattr(analysis, "raw_event_count", 0) or 0)
                discarded_count += int(getattr(analysis, "discarded_count", 0) or 0)
                signals.extend(getattr(analysis, "signals", []) or [])

            aggregated = aggregate_signals(signals)
            negative_cap = float(
                self.review_options.get(
                    "negative_cap",
                    DEFAULT_DAILY_REVIEW_NEGATIVE_CAP,
                )
            )
            candidates = []
            for signal, delta, index in score_signals_with_negative_cap(
                aggregated,
                negative_cap=negative_cap,
            ):
                candidates.append(
                    DailyReviewCandidate(
                        event_type=AffinityEventType.DAILY_REVIEW.value,
                        score_delta=delta,
                        reason=f"LLM关系信号: {signal.type}",
                        source_ref=signal.session_id,
                        metadata={
                            "signal_type": signal.type,
                            "direction": signal.direction,
                            "intensity": signal.intensity,
                            "confidence": signal.confidence,
                            "evidence": signal.evidence,
                            "message_refs": signal.message_refs,
                            "event_index": index,
                        },
                    )
                )

            return DailyReviewDiagnostics(
                candidates=candidates,
                session_count=len(sessions),
                raw_signal_count=raw_count,
                valid_signal_count=len(aggregated),
                discarded_signal_count=discarded_count,
            )
        except Exception:
            return DailyReviewDiagnostics(candidates=[], fallback_used=True)

    def _backup_user_day(self, user_id: str, event_day: date) -> Path:
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        path = self.backup_dir / (
            f"daily-review-{user_id}-{event_day.isoformat()}-"
            f"{datetime.now().strftime('%Y%m%d%H%M%S%f')}.json"
        )
        snapshot = self.db.snapshot_user(user_id)
        snapshot["daily_review_events"] = [
            dict(event.__data__)
            for event in self._review_rebuild_events(user_id, event_day)
        ]
        path.write_text(json.dumps(snapshot, ensure_ascii=False, default=str, indent=2), encoding="utf-8")
        return path

    def _review_rebuild_events(self, user_id: str, event_day: date):
        events = self.db.get_events_for_day(user_id, event_day)
        return [
            event
            for event in events
            if event.source in {"daily_review", "daily_review_llm"}
            or event.event_type == AffinityEventType.STAGE_UNLOCK.value
        ]

    def _remove_existing_review_events(self, user_id: str, event_day: date) -> None:
        events = self._review_rebuild_events(user_id, event_day)
        self.db.delete_events(events)
        self.db.recompute_user_state_from_events(user_id)
        self.db.update_daily_counter(user_id, event_day, review_positive_delta=0)

    async def run_for_user(
        self,
        user_id: str,
        event_date: date | str | None = None,
        *,
        dry_run: bool = False,
        force_rebuild: bool = False,
    ) -> DailyReviewResult:
        event_day = parse_event_date(event_date)
        self.db.get_or_create_user_state(user_id)
        diagnostics = DailyReviewDiagnostics(candidates=[])
        source = "daily_review"
        if self.llm_review_enabled:
            existing_llm = self.db.get_events_for_day(
                user_id,
                event_day,
                source="daily_review_llm",
            )
            if existing_llm and not force_rebuild and not dry_run:
                return DailyReviewResult(
                    user_id=user_id,
                    event_date=event_day,
                    events=[],
                    total_delta=0,
                    written=False,
                    skipped=True,
                )
            diagnostics = await self._build_llm_review_candidates(user_id, event_day)
            if diagnostics.fallback_used:
                candidates = await self._build_candidates(user_id, event_day)
            else:
                candidates = diagnostics.candidates
                source = "daily_review_llm"
        else:
            candidates = await self._build_candidates(user_id, event_day)
        total_delta = sum(candidate.score_delta for candidate in candidates)

        if dry_run:
            return DailyReviewResult(
                user_id=user_id,
                event_date=event_day,
                events=candidates,
                total_delta=total_delta,
                written=False,
                session_count=diagnostics.session_count,
                raw_signal_count=diagnostics.raw_signal_count,
                valid_signal_count=diagnostics.valid_signal_count,
                discarded_signal_count=diagnostics.discarded_signal_count,
                fallback_used=diagnostics.fallback_used,
                **self._stage_diagnostics(user_id, projected_delta=total_delta),
            )

        backup_path = None
        existing = self.db.get_events_for_day(user_id, event_day, source=source)
        if existing and not force_rebuild:
            return DailyReviewResult(
                user_id=user_id,
                event_date=event_day,
                events=candidates,
                total_delta=0,
                written=False,
                skipped=True,
                session_count=diagnostics.session_count,
                raw_signal_count=diagnostics.raw_signal_count,
                valid_signal_count=diagnostics.valid_signal_count,
                discarded_signal_count=diagnostics.discarded_signal_count,
                fallback_used=diagnostics.fallback_used,
            )

        if force_rebuild:
            backup_path = self._backup_user_day(user_id, event_day)
            self._remove_existing_review_events(user_id, event_day)

        written_delta = 0.0
        for candidate in candidates:
            signal_type = candidate.metadata.get("signal_type")
            event_index = candidate.metadata.get("event_index")
            if source == "daily_review_llm" and signal_type is not None:
                idempotency_key = (
                    f"daily-llm:{user_id}:{event_day.isoformat()}:{signal_type}:{event_index}"
                )
            else:
                idempotency_key = daily_key(
                    user_id,
                    event_day,
                    f"{candidate.event_type}:{candidate.source_ref}",
                )
            result = self.db.apply_score_event(
                user_id=user_id,
                event_type=candidate.event_type,
                score_delta=candidate.score_delta,
                reason=candidate.reason,
                source=source,
                source_ref=candidate.source_ref,
                event_date=event_day,
                idempotency_key=idempotency_key,
                metadata_json=candidate.metadata,
            )
            if result.applied:
                written_delta += float(result.event.score_delta or 0)

        state = self.db.get_or_create_user_state(user_id)
        state.daily_review_at = datetime.now()
        state.save()
        if self.stage_advance_enabled:
            self.advance_stage_for_day(user_id, event_day)
        self.db.update_daily_counter(
            user_id,
            event_day,
            review_positive_delta=written_delta,
        )
        return DailyReviewResult(
            user_id=user_id,
            event_date=event_day,
            events=candidates,
            total_delta=written_delta,
            written=True,
            backup_path=backup_path,
            session_count=diagnostics.session_count,
            raw_signal_count=diagnostics.raw_signal_count,
            valid_signal_count=diagnostics.valid_signal_count,
            discarded_signal_count=diagnostics.discarded_signal_count,
            fallback_used=diagnostics.fallback_used,
            **self._stage_diagnostics(user_id),
        )

    def _stage_diagnostics(self, user_id: str, projected_delta: float = 0) -> dict[str, Any]:
        state = self.db.get_or_create_user_state(user_id)
        current = getattr(state, "unlocked_stage", None) or state.effective_stage
        projected_score = float(state.affinity_score or 0) + float(projected_delta or 0)
        target = score_stage(projected_score).value
        return {
            "current_stage": current,
            "bank_balance": bank_balance(projected_score, current),
            "next_advance_stage": advance_stage(
                current,
                target,
                max_steps=self.max_stage_steps_per_day,
            ),
        }

    def advance_stage_for_day(self, user_id: str, event_day: date) -> None:
        state = self.db.get_or_create_user_state(user_id)
        if state.last_stage_advance_date == event_day:
            return
        current = getattr(state, "unlocked_stage", None) or state.effective_stage
        target = score_stage(float(state.affinity_score or 0)).value
        next_stage = advance_stage(
            current,
            target,
            max_steps=self.max_stage_steps_per_day,
        )
        if next_stage != current:
            self.db.insert_event(
                user_id=user_id,
                event_type=AffinityEventType.STAGE_UNLOCK.value,
                score_delta=0,
                reason="每日刷新推进关系阶段",
                source="stage_progression",
                source_ref=event_day.isoformat(),
                event_date=event_day,
                idempotency_key=f"stage-unlock:{user_id}:{event_day.isoformat()}:{current}:{next_stage}",
                metadata_json={
                    "direction": "up",
                    "from_stage": current,
                    "to_stage": next_stage,
                    "score": float(state.affinity_score or 0),
                },
            )
            state.unlocked_stage = next_stage
        state.last_stage_advance_date = event_day
        state.effective_stage = effective_stage(
            float(state.affinity_score or 0),
            state.confirmed_stage,
            bool(state.lover_locked),
            state.unlocked_stage,
        ).value
        state.save()
