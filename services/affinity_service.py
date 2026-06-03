from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from ..core.confirmation import decide_confirmation_transition
from ..core.models import AffinityEventType, ConfirmationStatus, RelationshipStage
from ..core.rules import (
    DEFAULT_NEGATIVE_CAP,
    apply_daily_cap,
    message_key,
)
from ..core.stage_progression import demote_stage
from ..core.stage import effective_stage, score_stage
from ..db_manager import AffinityDatabaseManager


def parse_event_date(value: date | str | None) -> date:
    if value is None:
        return date.today()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _event_key(prefix: str, *parts: object) -> str:
    return ":".join([prefix, *[str(part) for part in parts]])


@dataclass(slots=True)
class UserSnapshot:
    user_id: str
    affinity_score: float
    stage: str
    effective_stage: str
    confirmed_stage: str | None
    custom_nickname: str | None
    max_stage_custom_prompt: str | None
    max_stage_prompt_notice_sent_at: datetime | None
    lover_locked: bool
    current_mood: str
    impression_tags: list[str]


class AffinityService:
    def __init__(self, db: AffinityDatabaseManager):
        self.db = db

    def _update_counter_delta(
        self,
        *,
        user_id: str,
        event_date: date,
        field: str,
        delta: float,
    ) -> None:
        counter = self.db.get_or_create_daily_counter(user_id, event_date)
        current = float(getattr(counter, field) or 0)
        self.db.update_daily_counter(user_id, event_date, **{field: current + delta})

    async def record_daily_chat(
        self,
        user_id: str,
        message_id: str,
        event_date: date | str | None = None,
    ):
        event_day = parse_event_date(event_date)
        counter = self.db.get_or_create_daily_counter(user_id, event_day)
        delta = 6
        result = self.db.apply_score_event(
            user_id=user_id,
            event_type=AffinityEventType.DAILY_CHAT.value,
            score_delta=delta,
            reason="有效日常互动",
            source="message",
            source_ref=message_id,
            message_id=message_id,
            event_date=event_day,
            idempotency_key=message_key(message_id, AffinityEventType.DAILY_CHAT.value),
        )
        if result.applied:
            self.db.update_daily_counter(
                user_id,
                event_day,
                realtime_positive_delta=float(counter.realtime_positive_delta or 0) + delta,
                daily_chat_turns=float(counter.daily_chat_turns or 0) + 1,
            )
        return result

    async def record_memory_recall(
        self,
        user_id: str,
        message_id: str,
        memory_id: str,
        event_date: date | str | None = None,
    ):
        event_day = parse_event_date(event_date)
        counter = self.db.get_or_create_daily_counter(user_id, event_day)
        delta = 2
        result = self.db.apply_score_event(
            user_id=user_id,
            event_type=AffinityEventType.MEMORY_RECALL.value,
            score_delta=delta,
            reason="命中长期记忆",
            source="memory",
            source_ref=memory_id,
            message_id=message_id,
            event_date=event_day,
            idempotency_key=message_key(message_id, f"memory_recall:{memory_id}"),
        )
        if result.applied:
            self.db.update_daily_counter(
                user_id,
                event_day,
                memory_recall_count=float(counter.memory_recall_count or 0) + delta,
            )
        return result

    async def record_profile_growth(
        self,
        user_id: str,
        source_ref: str,
        count: int,
        event_date: date | str | None = None,
    ):
        event_day = parse_event_date(event_date)
        counter = self.db.get_or_create_daily_counter(user_id, event_day)
        delta = max(0, count) * 10
        result = self.db.apply_score_event(
            user_id=user_id,
            event_type=AffinityEventType.PROFILE_GROWTH.value,
            score_delta=delta,
            reason="画像新增信息",
            source="profile",
            source_ref=source_ref,
            event_date=event_day,
            idempotency_key=_event_key("profile", user_id, event_day.isoformat(), source_ref),
        )
        if result.applied:
            self.db.update_daily_counter(
                user_id,
                event_day,
                profile_growth_count=float(counter.profile_growth_count or 0) + delta,
            )
        return result

    async def record_negative_event(
        self,
        user_id: str,
        event_type: str,
        reason: str,
        source_ref: str,
        delta: float,
        event_date: date | str | None = None,
    ):
        event_day = parse_event_date(event_date)
        counter = self.db.get_or_create_daily_counter(user_id, event_day)
        capped_delta = apply_daily_cap(
            current_delta=float(counter.negative_delta or 0),
            requested_delta=min(0, delta),
            cap=DEFAULT_NEGATIVE_CAP,
        )
        result = self.db.apply_score_event(
            user_id=user_id,
            event_type=event_type,
            score_delta=capped_delta,
            reason=reason,
            source="negative",
            source_ref=source_ref,
            event_date=event_day,
            idempotency_key=_event_key("negative", user_id, event_day.isoformat(), event_type, source_ref),
        )
        if result.applied:
            self.db.update_daily_counter(
                user_id,
                event_day,
                negative_delta=float(counter.negative_delta or 0) + capped_delta,
            )
            state = result.user_state
            if state is not None and capped_delta < 0:
                target = score_stage(float(state.affinity_score or 0)).value
                current = getattr(state, "unlocked_stage", state.effective_stage)
                next_stage = demote_stage(current, target)
                if next_stage != current:
                    self.db.insert_event(
                        user_id=user_id,
                        event_type=AffinityEventType.STAGE_UNLOCK.value,
                        score_delta=0,
                        reason="负向事件即时下修关系阶段",
                        source="stage_progression",
                        source_ref=source_ref,
                        event_date=event_day,
                        idempotency_key=_event_key(
                            "stage-demote",
                            user_id,
                            event_day.isoformat(),
                            event_type,
                            source_ref,
                        ),
                        metadata_json={
                            "direction": "down",
                            "from_stage": current,
                            "to_stage": next_stage,
                            "score": float(state.affinity_score or 0),
                        },
                    )
                    state.unlocked_stage = next_stage
                    state.effective_stage = effective_stage(
                        float(state.affinity_score or 0),
                        state.confirmed_stage,
                        bool(state.lover_locked),
                        state.unlocked_stage,
                    ).value
                    state.save()
        return result

    def get_user_snapshot(self, user_id: str) -> UserSnapshot:
        state = self.db.get_or_create_user_state(user_id)
        try:
            tags = json.loads(state.impression_tags_json or "[]")
        except json.JSONDecodeError:
            tags = []
        return UserSnapshot(
            user_id=state.user_id,
            affinity_score=float(state.affinity_score or 0),
            stage=state.stage,
            effective_stage=state.effective_stage,
            confirmed_stage=state.confirmed_stage,
            custom_nickname=state.custom_nickname,
            max_stage_custom_prompt=state.max_stage_custom_prompt,
            max_stage_prompt_notice_sent_at=state.max_stage_prompt_notice_sent_at,
            lover_locked=bool(state.lover_locked),
            current_mood=state.current_mood,
            impression_tags=tags if isinstance(tags, list) else [],
        )

    def set_score(self, user_id: str, score: float):
        return self.db.set_user_score(user_id, score)

    def set_relationship(self, user_id: str, stage: str):
        return self.db.set_relationship(user_id, stage)

    def set_max_stage_custom_prompt(self, user_id: str, prompt: str):
        return self.db.set_max_stage_custom_prompt(user_id, prompt)

    def mark_max_stage_prompt_notice_sent(self, user_id: str):
        return self.db.mark_max_stage_prompt_notice_sent(user_id)

    def reset_user(self, user_id: str):
        return self.db.reset_user(user_id)

    def reset_all_users(self) -> int:
        return self.db.reset_all_users()

    def get_recent_events(self, user_id: str, limit: int = 10):
        return self.db.get_recent_events(user_id, limit)

    async def request_confirmation(
        self,
        user_id: str,
        initiator: str,
        scene: str,
        now: datetime | None = None,
    ):
        now = now or datetime.now()
        runtime = self.db.get_or_create_runtime_state(user_id)
        state = self.db.get_or_create_user_state(user_id)
        cooldown_active = bool(
            runtime.proactive_cooldown_until and runtime.proactive_cooldown_until > now
        )
        trigger = "bot_proactive" if initiator == "bot" else "user_query"
        transition = decide_confirmation_transition(
            status=runtime.confirmation_status,
            trigger=trigger,
            score=float(state.affinity_score or 0),
            effective_stage=state.effective_stage,
            scene_allows_proactive=True,
            rejected_cooldown_active=cooldown_active,
        )
        if not transition.changed:
            return transition

        self.db.update_runtime_state(
            user_id,
            confirmation_status=transition.next_status.value,
            confirmation_initiator=transition.initiator or initiator,
            confirmation_started_at=now,
            confirmation_expires_at=now + timedelta(hours=24),
            last_confirmation_attempt_at=now,
            cooldown_reason=None,
        )
        self.db.insert_event(
            user_id=user_id,
            event_type=AffinityEventType.RELATIONSHIP_CONFIRM_REQUESTED.value,
            score_delta=0,
            reason=f"{scene}关系确认请求",
            source="confirmation",
            source_ref=initiator,
            event_date=now.date(),
            idempotency_key=_event_key("confirm-request", user_id, now.isoformat()),
        )
        return transition

    async def accept_confirmation(self, user_id: str, now: datetime | None = None):
        now = now or datetime.now()
        runtime = self.db.get_or_create_runtime_state(user_id)
        state = self.db.get_or_create_user_state(user_id)
        transition = decide_confirmation_transition(
            status=runtime.confirmation_status,
            trigger="user_accept",
            score=float(state.affinity_score or 0),
            effective_stage=state.effective_stage,
            scene_allows_proactive=True,
            rejected_cooldown_active=False,
        )
        if transition.next_status != ConfirmationStatus.CONFIRMED:
            return transition

        self.db.set_relationship(
            user_id,
            RelationshipStage.LOVER.value,
            lover_locked=True,
            confirmed_stage=RelationshipStage.LOVER.value,
        )
        self.db.update_runtime_state(
            user_id,
            confirmation_status=ConfirmationStatus.CONFIRMED.value,
            confirmation_initiator=runtime.confirmation_initiator,
            confirmation_expires_at=None,
            proactive_cooldown_until=None,
            cooldown_reason=None,
        )
        self.db.insert_event(
            user_id=user_id,
            event_type=AffinityEventType.RELATIONSHIP_CONFIRMED.value,
            score_delta=0,
            reason="用户明确同意关系确认",
            source="confirmation",
            source_ref="user_accept",
            event_date=now.date(),
            idempotency_key=_event_key("confirm-accept", user_id, now.isoformat()),
        )
        return transition

    async def reject_confirmation(
        self,
        user_id: str,
        reason: str,
        now: datetime | None = None,
    ):
        now = now or datetime.now()
        runtime = self.db.get_or_create_runtime_state(user_id)
        state = self.db.get_or_create_user_state(user_id)
        transition = decide_confirmation_transition(
            status=runtime.confirmation_status,
            trigger="user_reject",
            score=float(state.affinity_score or 0),
            effective_stage=state.effective_stage,
            scene_allows_proactive=True,
            rejected_cooldown_active=False,
        )
        if transition.next_status != ConfirmationStatus.REJECTED:
            return transition
        self.db.update_runtime_state(
            user_id,
            confirmation_status=ConfirmationStatus.REJECTED.value,
            confirmation_expires_at=None,
            proactive_cooldown_until=now + timedelta(days=7),
            last_rejected_at=now,
            cooldown_reason=reason,
        )
        self.db.apply_score_event(
            user_id=user_id,
            event_type=AffinityEventType.REJECTION.value,
            score_delta=-8,
            reason=reason,
            source="confirmation",
            source_ref="user_reject",
            event_date=now.date(),
            idempotency_key=_event_key("confirm-reject", user_id, now.isoformat()),
        )
        return transition

    async def expire_confirmation(self, user_id: str, now: datetime | None = None):
        now = now or datetime.now()
        runtime = self.db.get_or_create_runtime_state(user_id)
        state = self.db.get_or_create_user_state(user_id)
        transition = decide_confirmation_transition(
            status=runtime.confirmation_status,
            trigger="expired",
            score=float(state.affinity_score or 0),
            effective_stage=state.effective_stage,
            scene_allows_proactive=True,
            rejected_cooldown_active=False,
        )
        if transition.changed:
            self.db.update_runtime_state(
                user_id,
                confirmation_status=ConfirmationStatus.NONE.value,
                confirmation_initiator=None,
                confirmation_started_at=None,
                confirmation_expires_at=None,
            )
        return transition
