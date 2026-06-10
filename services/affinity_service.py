from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from ..core.confirmation import decide_confirmation_transition
from ..core.models import AffinityEventType, ConfirmationStatus, RelationshipStage
from ..core.rules import (
    DEFAULT_MEMORY_RECALL_CAP,
    apply_global_negative_cap,
    message_key,
)
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
    def __init__(
        self,
        db: AffinityDatabaseManager,
        options: dict[str, Any] | None = None,
    ):
        self.db = db
        self.options = options or {}

    def _option_float(self, key: str, default: float) -> float:
        try:
            return float(self.options.get(key, default))
        except (TypeError, ValueError):
            return float(default)

    def _option_int(self, key: str, default: int) -> int:
        try:
            return int(self.options.get(key, default))
        except (TypeError, ValueError):
            return int(default)

    @property
    def realtime_chat_score(self) -> float:
        return self._option_float("realtime_chat_score", 3)

    @property
    def realtime_chat_score_mid(self) -> float:
        return self._option_float("realtime_chat_score_mid", 2)

    @property
    def realtime_chat_score_late(self) -> float:
        return self._option_float("realtime_chat_score_late", 1.5)

    @property
    def memory_recall_score(self) -> float:
        return max(0.01, self._option_float("memory_recall_score", 5))

    @property
    def memory_recall_daily_cap(self) -> int:
        return self._option_int("memory_recall_daily_cap", DEFAULT_MEMORY_RECALL_CAP)

    @property
    def global_negative_cap(self) -> float:
        return self._option_float("negative_cap", -60)

    @property
    def negative_decay_days(self) -> int:
        return self._option_int("negative_decay_days", 3)

    @property
    def negative_decay_multiplier(self) -> float:
        return self._option_float("negative_decay_multiplier", 0.5)

    @property
    def repair_window_hours(self) -> float:
        return self._option_float("repair_window_hours", 24)

    @property
    def repair_bonus_multiplier(self) -> float:
        return self._option_float("repair_bonus_multiplier", 1.5)

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

    def _apply_mood_multiplier(self, user_id: str, base_delta: float) -> float:
        if not bool(self.options.get("mood_system_enabled", True)):
            return float(base_delta)
        state = self.db.get_or_create_user_state(user_id)
        multipliers = {
            "吃醋": 0.8,
            "开心": 1.2,
            "低落": 0.9,
            "冷战": 0.5,
            "平静": 1.0,
        }
        configured = self.options.get("mood_multipliers")
        if isinstance(configured, dict):
            aliases = {
                "jealous": "吃醋",
                "happy": "开心",
                "low": "低落",
                "cold_war": "冷战",
                "calm": "平静",
            }
            for key, value in configured.items():
                if not str(key).strip():
                    continue
                try:
                    multipliers[aliases.get(str(key), str(key))] = float(value)
                except (TypeError, ValueError):
                    continue
        return float(base_delta) * multipliers.get(str(state.current_mood or "").strip(), 1.0)

    def _realtime_chat_score_for_turn(self, turn_number: int) -> float:
        if turn_number <= 10:
            return self.realtime_chat_score
        if turn_number <= 20:
            return self.realtime_chat_score_mid
        return self.realtime_chat_score_late

    def _check_repair_window(self, user_id: str) -> bool:
        state = self.db.get_or_create_user_state(user_id)
        demote_at = state.last_demote_at
        if not demote_at:
            return False
        if isinstance(demote_at, str):
            try:
                demote_at = datetime.fromisoformat(demote_at)
            except ValueError:
                return False
        return (datetime.now() - demote_at).total_seconds() <= self.repair_window_hours * 3600

    def _update_interaction_streak(self, user_id: str, event_day: date) -> None:
        state = self.db.get_or_create_user_state(user_id)
        last_day = state.last_interaction_date
        if isinstance(last_day, str):
            try:
                last_day = date.fromisoformat(last_day)
            except ValueError:
                last_day = None
        if last_day == event_day - timedelta(days=1):
            state.consecutive_interaction_days = (
                int(state.consecutive_interaction_days or 0) + 1
            )
        elif last_day != event_day:
            state.consecutive_interaction_days = 1
        state.last_interaction_date = event_day
        state.save()

    def _latest_negative_event_date_before(
        self,
        user_id: str,
        event_day: date,
    ) -> date | None:
        query = (
            self.db.AffinityDailyCounter.select()
            .where(
                (self.db.AffinityDailyCounter.user_id == user_id)
                & (self.db.AffinityDailyCounter.last_negative_event_date.is_null(False))
                & (self.db.AffinityDailyCounter.event_date < event_day)
            )
            .order_by(self.db.AffinityDailyCounter.event_date.desc())
        )
        counter = query.first()
        if counter is None:
            return None
        last_day = counter.last_negative_event_date
        if isinstance(last_day, str):
            try:
                return date.fromisoformat(last_day)
            except ValueError:
                return None
        return last_day

    async def record_daily_chat(
        self,
        user_id: str,
        message_id: str,
        event_date: date | str | None = None,
    ):
        event_day = parse_event_date(event_date)
        counter = self.db.get_or_create_daily_counter(user_id, event_day)
        turn_number = int(float(counter.daily_chat_turns or 0)) + 1
        base_score = self._realtime_chat_score_for_turn(turn_number)
        delta = self._apply_mood_multiplier(user_id, base_score)
        if self._check_repair_window(user_id) and delta > 0:
            delta *= self.repair_bonus_multiplier
            reason = f"有效日常互动 (修复期×{self.repair_bonus_multiplier:g})"
        else:
            reason = "有效日常互动"
        delta = round(delta, 2)
        result = self.db.apply_score_event(
            user_id=user_id,
            event_type=AffinityEventType.DAILY_CHAT.value,
            score_delta=delta,
            reason=reason,
            source="message",
            source_ref=message_id,
            message_id=message_id,
            event_date=event_day,
            idempotency_key=message_key(message_id, AffinityEventType.DAILY_CHAT.value),
        )
        if result.applied:
            applied_delta = float(result.event.score_delta or 0) if result.event else 0
            self.db.increment_daily_counter(
                user_id,
                event_day,
                realtime_positive_delta=applied_delta,
                daily_chat_turns=1,
            )
            self._update_interaction_streak(user_id, event_day)
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
        recall_score = self.memory_recall_score
        memory_events = int(float(counter.memory_recall_count or 0) // recall_score)
        if memory_events >= self.memory_recall_daily_cap:
            delta = 0
        else:
            delta = recall_score
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
            applied_delta = float(result.event.score_delta or 0) if result.event else 0
            self.db.increment_daily_counter(
                user_id,
                event_day,
                memory_recall_count=applied_delta,
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
            self.db.increment_daily_counter(
                user_id,
                event_day,
                profile_growth_count=delta,
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
        requested_delta = min(0, delta)
        latest_negative_day = counter.last_negative_event_date
        if isinstance(latest_negative_day, str):
            try:
                latest_negative_day = date.fromisoformat(latest_negative_day)
            except ValueError:
                latest_negative_day = None
        latest_negative_day = latest_negative_day or self._latest_negative_event_date_before(
            user_id,
            event_day,
        )
        if latest_negative_day is not None:
            days_since = (event_day - latest_negative_day).days
            if 0 < days_since <= self.negative_decay_days:
                requested_delta *= self.negative_decay_multiplier
                reason = f"{reason} (衰减期×{self.negative_decay_multiplier:g})"

        capped_delta = apply_global_negative_cap(
            user_id=user_id,
            event_date=event_day,
            requested_delta=requested_delta,
            db_manager=self.db,
            cap=self.global_negative_cap,
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
            self.db.increment_daily_counter(
                user_id,
                event_day,
                negative_delta=capped_delta,
            )
            if capped_delta < 0:
                self.db.update_daily_counter(
                    user_id,
                    event_day,
                    last_negative_event_date=event_day,
                )
                # 记录降阶时间用于修复期判定
                state = result.user_state
                if state is not None:
                    state.last_demote_at = datetime.now()
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
