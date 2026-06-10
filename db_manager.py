from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from peewee import (
    AutoField,
    BooleanField,
    DateField,
    DateTimeField,
    FloatField,
    IntegrityError,
    IntegerField,
    Model,
    SqliteDatabase,
    TextField,
)

from .core.models import ConfirmationStatus, Mood, RelationshipStage
from .core.stage import clamp_score, effective_stage, score_stage


def _utcnow() -> datetime:
    return datetime.now()


def _enum_value(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "value"):
        return value.value
    return value


def _json_text(value: Any, default: str) -> str:
    if value is None:
        return default
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class _AffinityBaseModel(Model):
    class Meta:
        abstract = True


class AffinityUserState(_AffinityBaseModel):
    user_id = TextField(primary_key=True)
    affinity_score = FloatField(default=0)
    stage = TextField(default=RelationshipStage.STRANGER.value)
    effective_stage = TextField(default=RelationshipStage.STRANGER.value)
    unlocked_stage = TextField(default=RelationshipStage.STRANGER.value)
    last_stage_advance_date = DateField(null=True)
    confirmed_stage = TextField(null=True)
    custom_nickname = TextField(null=True)
    max_stage_custom_prompt = TextField(null=True)
    max_stage_prompt_notice_sent_at = DateTimeField(null=True)
    lover_locked = BooleanField(default=False)
    impression_tags_json = TextField(default="[]")
    current_mood = TextField(default=Mood.CALM.value)
    last_interaction_at = DateTimeField(null=True)
    last_interaction_date = DateField(null=True)
    last_negative_at = DateTimeField(null=True)
    last_demote_at = DateTimeField(null=True)
    last_proactive_prompt_at = DateTimeField(null=True)
    daily_review_at = DateTimeField(null=True)
    consecutive_interaction_days = IntegerField(default=0)
    created_at = DateTimeField(default=_utcnow)
    updated_at = DateTimeField(default=_utcnow)

    class Meta:
        table_name = "affinity_user_state"


class AffinityEvent(_AffinityBaseModel):
    id = AutoField()
    user_id = TextField(index=True)
    event_type = TextField(index=True)
    score_delta = FloatField(default=0)
    reason = TextField(null=True)
    source = TextField(null=True)
    source_ref = TextField(null=True)
    message_id = TextField(null=True, index=True)
    event_date = DateField(null=True, index=True)
    idempotency_key = TextField(unique=True)
    metadata_json = TextField(default="{}")
    created_at = DateTimeField(default=_utcnow)
    updated_at = DateTimeField(default=_utcnow)

    class Meta:
        table_name = "affinity_events"


class AffinityRuntimeState(_AffinityBaseModel):
    user_id = TextField(primary_key=True)
    proactive_cooldown_until = DateTimeField(null=True)
    confirmation_status = TextField(default=ConfirmationStatus.NONE.value)
    confirmation_initiator = TextField(null=True)
    confirmation_started_at = DateTimeField(null=True)
    confirmation_expires_at = DateTimeField(null=True)
    last_confirmation_attempt_at = DateTimeField(null=True)
    last_rejected_at = DateTimeField(null=True)
    stage_locked = BooleanField(default=False)
    cooldown_reason = TextField(null=True)
    created_at = DateTimeField(default=_utcnow)
    updated_at = DateTimeField(default=_utcnow)

    class Meta:
        table_name = "affinity_runtime_state"


class AffinityDailyCounter(_AffinityBaseModel):
    id = AutoField()
    user_id = TextField()
    event_date = DateField()
    realtime_positive_delta = FloatField(default=0)
    review_positive_delta = FloatField(default=0)
    negative_delta = FloatField(default=0)
    review_negative_delta = FloatField(default=0)
    last_negative_event_date = DateField(null=True)
    daily_chat_turns = FloatField(default=0)
    profile_growth_count = FloatField(default=0)
    memory_recall_count = FloatField(default=0)
    counters_json = TextField(default="{}")
    created_at = DateTimeField(default=_utcnow)
    updated_at = DateTimeField(default=_utcnow)

    class Meta:
        table_name = "affinity_daily_counters"
        indexes = ((("user_id", "event_date"), True),)


@dataclass(slots=True)
class ScoreEventResult:
    applied: bool
    event: AffinityEvent | None = None
    user_state: AffinityUserState | None = None


class AffinityDatabaseManager:
    @staticmethod
    def _bind_model(model_cls: type[Model], database: SqliteDatabase) -> type[Model]:
        base_meta = getattr(model_cls, "Meta", object)
        meta = type(
            "Meta",
            (base_meta,),
            {
                "database": database,
                "table_name": model_cls._meta.table_name,
                "indexes": model_cls._meta.indexes,
            },
        )
        return type(
            f"{model_cls.__name__}Bound_{id(database)}",
            (model_cls,),
            {"Meta": meta},
        )

    def __init__(self, database: SqliteDatabase | str | Path | None = None):
        if database is None:
            database = Path.cwd() / "affinity.db"

        if isinstance(database, SqliteDatabase):
            self.db = database
        else:
            self.db = SqliteDatabase(
                str(database),
                pragmas={
                    "journal_mode": "wal",
                    "foreign_keys": 1,
                },
            )

        self.AffinityUserState = self._bind_model(AffinityUserState, self.db)
        self.AffinityEvent = self._bind_model(AffinityEvent, self.db)
        self.AffinityRuntimeState = self._bind_model(AffinityRuntimeState, self.db)
        self.AffinityDailyCounter = self._bind_model(AffinityDailyCounter, self.db)

        self.db.connect(reuse_if_open=True)
        self.db.create_tables(
            [
                self.AffinityUserState,
                self.AffinityEvent,
                self.AffinityRuntimeState,
                self.AffinityDailyCounter,
            ],
            safe=True,
        )
        self._ensure_user_state_columns()
        self._ensure_daily_counter_columns()

    def _ensure_user_state_columns(self) -> None:
        existing = {
            column.name
            for column in self.db.get_columns(self.AffinityUserState._meta.table_name)
        }
        migrator_sql = []
        if "unlocked_stage" not in existing:
            migrator_sql.append(
                "ALTER TABLE affinity_user_state "
                "ADD COLUMN unlocked_stage TEXT NOT NULL DEFAULT '初识'"
            )
        if "last_stage_advance_date" not in existing:
            migrator_sql.append(
                "ALTER TABLE affinity_user_state ADD COLUMN last_stage_advance_date DATE"
            )
        if "max_stage_custom_prompt" not in existing:
            migrator_sql.append(
                "ALTER TABLE affinity_user_state ADD COLUMN max_stage_custom_prompt TEXT"
            )
        if "max_stage_prompt_notice_sent_at" not in existing:
            migrator_sql.append(
                "ALTER TABLE affinity_user_state ADD COLUMN max_stage_prompt_notice_sent_at DATETIME"
            )
        if "last_demote_at" not in existing:
            migrator_sql.append(
                "ALTER TABLE affinity_user_state ADD COLUMN last_demote_at DATETIME"
            )
        if "consecutive_interaction_days" not in existing:
            migrator_sql.append(
                "ALTER TABLE affinity_user_state "
                "ADD COLUMN consecutive_interaction_days INTEGER NOT NULL DEFAULT 0"
            )
        if "last_interaction_date" not in existing:
            migrator_sql.append(
                "ALTER TABLE affinity_user_state ADD COLUMN last_interaction_date DATE"
            )
        for statement in migrator_sql:
            self.db.execute_sql(statement)

    def _ensure_daily_counter_columns(self) -> None:
        existing = {
            column.name
            for column in self.db.get_columns(self.AffinityDailyCounter._meta.table_name)
        }
        migrator_sql = []
        if "review_negative_delta" not in existing:
            migrator_sql.append(
                "ALTER TABLE affinity_daily_counters "
                "ADD COLUMN review_negative_delta REAL NOT NULL DEFAULT 0"
            )
        if "last_negative_event_date" not in existing:
            migrator_sql.append(
                "ALTER TABLE affinity_daily_counters ADD COLUMN last_negative_event_date DATE"
            )
        for statement in migrator_sql:
            self.db.execute_sql(statement)

    def close(self) -> None:
        if not self.db.is_closed():
            self.db.close()

    def _default_user_state_fields(self) -> dict[str, Any]:
        base_stage = score_stage(0).value
        now = _utcnow()
        return {
            "affinity_score": 0,
            "stage": base_stage,
            "effective_stage": base_stage,
            "unlocked_stage": base_stage,
            "last_stage_advance_date": None,
            "confirmed_stage": None,
            "custom_nickname": None,
            "max_stage_custom_prompt": None,
            "max_stage_prompt_notice_sent_at": None,
            "lover_locked": False,
            "impression_tags_json": "[]",
            "current_mood": Mood.CALM.value,
            "last_interaction_at": None,
            "last_interaction_date": None,
            "last_negative_at": None,
            "last_demote_at": None,
            "last_proactive_prompt_at": None,
            "daily_review_at": None,
            "consecutive_interaction_days": 0,
            "created_at": now,
            "updated_at": now,
        }

    def get_or_create_user_state(self, user_id: str) -> AffinityUserState:
        state, created = self.AffinityUserState.get_or_create(
            user_id=user_id,
            defaults=self._default_user_state_fields(),
        )
        if created:
            return state

        return state

    def get_user_state(self, user_id: str) -> AffinityUserState | None:
        return self.AffinityUserState.get_or_none(self.AffinityUserState.user_id == user_id)

    def list_user_ids(self) -> list[str]:
        return [
            row.user_id
            for row in self.AffinityUserState.select(self.AffinityUserState.user_id)
            .order_by(self.AffinityUserState.user_id.asc())
        ]

    def set_user_score(self, user_id: str, score: float) -> AffinityUserState:
        state = self.get_or_create_user_state(user_id)
        new_score = clamp_score(float(score))
        state.affinity_score = new_score
        state.stage = score_stage(new_score).value
        if not getattr(state, "unlocked_stage", None):
            state.unlocked_stage = state.stage
        state.effective_stage = effective_stage(
            new_score,
            state.confirmed_stage,
            bool(state.lover_locked),
            state.unlocked_stage,
        ).value
        state.updated_at = _utcnow()
        state.save()
        return state

    def set_relationship(
        self,
        user_id: str,
        stage: str,
        *,
        lover_locked: bool | None = None,
        confirmed_stage: str | None = None,
    ) -> AffinityUserState:
        state = self.get_or_create_user_state(user_id)
        stage_value = _enum_value(stage)
        if stage_value == RelationshipStage.LOVER.value:
            state.confirmed_stage = confirmed_stage or RelationshipStage.LOVER.value
            state.lover_locked = True if lover_locked is None else bool(lover_locked)
        else:
            state.confirmed_stage = confirmed_stage
            if lover_locked is not None:
                state.lover_locked = bool(lover_locked)
            elif confirmed_stage is None:
                state.lover_locked = False

        state.stage = score_stage(float(state.affinity_score or 0)).value
        if stage_value != RelationshipStage.LOVER.value:
            state.unlocked_stage = stage_value
        state.effective_stage = effective_stage(
            float(state.affinity_score or 0),
            state.confirmed_stage,
            bool(state.lover_locked),
            state.unlocked_stage,
        ).value
        if stage_value != RelationshipStage.LOVER.value and confirmed_stage is None:
            state.effective_stage = stage_value
        state.updated_at = _utcnow()
        state.save()
        return state

    def set_max_stage_custom_prompt(
        self,
        user_id: str,
        prompt: str | None,
    ) -> AffinityUserState:
        state = self.get_or_create_user_state(user_id)
        cleaned = str(prompt or "").strip()
        state.max_stage_custom_prompt = cleaned or None
        state.updated_at = _utcnow()
        state.save()
        return state

    def mark_max_stage_prompt_notice_sent(self, user_id: str) -> AffinityUserState:
        state = self.get_or_create_user_state(user_id)
        now = _utcnow()
        state.max_stage_prompt_notice_sent_at = now
        state.updated_at = now
        state.save()
        return state

    def insert_event(
        self,
        *,
        user_id: str,
        event_type: str,
        score_delta: float,
        reason: str | None,
        source: str | None,
        idempotency_key: str,
        source_ref: str | None = None,
        message_id: str | None = None,
        event_date: date | None = None,
        metadata_json: Any = None,
    ) -> AffinityEvent:
        payload = {
            "user_id": user_id,
            "event_type": _enum_value(event_type),
            "score_delta": score_delta,
            "reason": reason,
            "source": source,
            "source_ref": source_ref,
            "message_id": message_id,
            "event_date": event_date or date.today(),
            "idempotency_key": idempotency_key,
            "metadata_json": _json_text(metadata_json, "{}"),
            "created_at": _utcnow(),
            "updated_at": _utcnow(),
        }
        return self.AffinityEvent.create(**payload)

    def apply_score_event(
        self,
        *,
        user_id: str,
        event_type: str,
        score_delta: float,
        reason: str | None,
        source: str | None,
        idempotency_key: str,
        source_ref: str | None = None,
        message_id: str | None = None,
        event_date: date | None = None,
        metadata_json: Any = None,
    ) -> ScoreEventResult:
        event_date = event_date or date.today()

        with self.db.atomic():
            existing = self.AffinityEvent.get_or_none(
                self.AffinityEvent.idempotency_key == idempotency_key
            )
            current_state = self.get_or_create_user_state(user_id)
            if existing is not None:
                return ScoreEventResult(
                    applied=False,
                    event=existing,
                    user_state=current_state,
                )

            current_score = float(current_state.affinity_score or 0)
            requested_score = current_score + score_delta
            new_score = clamp_score(requested_score)
            applied_delta = new_score - current_score
            now = _utcnow()

            event = self.insert_event(
                user_id=user_id,
                event_type=event_type,
                score_delta=applied_delta,
                reason=reason,
                source=source,
                source_ref=source_ref,
                message_id=message_id,
                event_date=event_date,
                idempotency_key=idempotency_key,
                metadata_json=metadata_json,
            )

            current_state.affinity_score = new_score
            current_state.stage = score_stage(new_score).value
            if not getattr(current_state, "unlocked_stage", None):
                current_state.unlocked_stage = current_state.stage
            current_state.effective_stage = effective_stage(
                new_score,
                current_state.confirmed_stage,
                bool(current_state.lover_locked),
                current_state.unlocked_stage,
            ).value
            current_state.last_interaction_at = now
            if applied_delta < 0:
                current_state.last_negative_at = now
            current_state.updated_at = now
            current_state.save()

            return ScoreEventResult(
                applied=True,
                event=event,
                user_state=current_state,
            )

    def get_recent_events(self, user_id: str, limit: int = 10) -> list[AffinityEvent]:
        query = (
            self.AffinityEvent.select()
            .where(self.AffinityEvent.user_id == user_id)
            .order_by(self.AffinityEvent.created_at.desc(), self.AffinityEvent.id.desc())
            .limit(limit)
        )
        return list(query)

    def get_events_for_day(
        self,
        user_id: str,
        event_date: date,
        event_type: str | None = None,
        source: str | None = None,
    ) -> list[AffinityEvent]:
        query = self.AffinityEvent.select().where(
            (self.AffinityEvent.user_id == user_id)
            & (self.AffinityEvent.event_date == event_date)
        )
        if event_type is not None:
            query = query.where(self.AffinityEvent.event_type == _enum_value(event_type))
        if source is not None:
            query = query.where(self.AffinityEvent.source == source)
        return list(query.order_by(self.AffinityEvent.id.asc()))

    def snapshot_user(self, user_id: str) -> dict[str, Any]:
        state = self.get_or_create_user_state(user_id)
        runtime = self.get_or_create_runtime_state(user_id)
        return {
            "user_state": dict(state.__data__),
            "runtime_state": dict(runtime.__data__),
            "recent_events": [
                dict(event.__data__) for event in self.get_recent_events(user_id, limit=50)
            ],
        }

    def recompute_user_state_from_events(self, user_id: str) -> AffinityUserState:
        with self.db.atomic():
            state = self.get_or_create_user_state(user_id)
            total = 0.0
            unlocked_stage = RelationshipStage.STRANGER.value
            last_stage_advance_date = None
            for event in (
                self.AffinityEvent.select()
                .where(self.AffinityEvent.user_id == user_id)
                .order_by(self.AffinityEvent.id.asc())
            ):
                total = clamp_score(total + float(event.score_delta or 0))
                if event.event_type == "stage_unlock":
                    try:
                        metadata = json.loads(event.metadata_json or "{}")
                    except json.JSONDecodeError:
                        metadata = {}
                    to_stage = metadata.get("to_stage")
                    if to_stage:
                        unlocked_stage = str(to_stage)
                        last_stage_advance_date = event.event_date
            state.affinity_score = total
            state.stage = score_stage(total).value
            state.unlocked_stage = unlocked_stage
            state.last_stage_advance_date = last_stage_advance_date
            state.effective_stage = effective_stage(
                total,
                state.confirmed_stage,
                bool(state.lover_locked),
                state.unlocked_stage,
            ).value
            state.updated_at = _utcnow()
            state.save()
            return state

    def delete_events(self, events: list[AffinityEvent]) -> int:
        ids = [event.id for event in events]
        if not ids:
            return 0
        return self.AffinityEvent.delete().where(self.AffinityEvent.id << ids).execute()

    def get_or_create_runtime_state(self, user_id: str) -> AffinityRuntimeState:
        state, _created = self.AffinityRuntimeState.get_or_create(
            user_id=user_id,
            defaults={
                "confirmation_status": ConfirmationStatus.NONE.value,
                "confirmation_initiator": None,
                "proactive_cooldown_until": None,
                "confirmation_started_at": None,
                "confirmation_expires_at": None,
                "last_confirmation_attempt_at": None,
                "last_rejected_at": None,
                "stage_locked": False,
                "cooldown_reason": None,
                "created_at": _utcnow(),
                "updated_at": _utcnow(),
            },
        )
        return state

    def update_runtime_state(self, user_id: str, **fields: Any) -> AffinityRuntimeState:
        state = self.get_or_create_runtime_state(user_id)
        for key, value in fields.items():
            setattr(state, key, value)
        state.updated_at = _utcnow()
        state.save()
        return state

    def get_or_create_daily_counter(self, user_id: str, event_date: date) -> AffinityDailyCounter:
        counter, _created = self.AffinityDailyCounter.get_or_create(
            user_id=user_id,
            event_date=event_date,
            defaults={
                "realtime_positive_delta": 0,
                "review_positive_delta": 0,
                "negative_delta": 0,
                "review_negative_delta": 0,
                "last_negative_event_date": None,
                "daily_chat_turns": 0,
                "profile_growth_count": 0,
                "memory_recall_count": 0,
                "counters_json": "{}",
                "created_at": _utcnow(),
                "updated_at": _utcnow(),
            },
        )
        return counter

    def update_daily_counter(
        self,
        user_id: str,
        event_date: date,
        **fields: Any,
    ) -> AffinityDailyCounter:
        self.get_or_create_daily_counter(user_id, event_date)
        self.AffinityDailyCounter.update(updated_at=_utcnow(), **fields).where(
            (self.AffinityDailyCounter.user_id == user_id)
            & (self.AffinityDailyCounter.event_date == event_date)
        ).execute()
        return self.get_or_create_daily_counter(user_id, event_date)

    def increment_daily_counter(
        self,
        user_id: str,
        event_date: date,
        **increments: float,
    ) -> AffinityDailyCounter:
        self.get_or_create_daily_counter(user_id, event_date)
        update_fields = {"updated_at": _utcnow()}
        for field, delta in increments.items():
            column = getattr(self.AffinityDailyCounter, field)
            update_fields[field] = column + delta
        self.AffinityDailyCounter.update(**update_fields).where(
            (self.AffinityDailyCounter.user_id == user_id)
            & (self.AffinityDailyCounter.event_date == event_date)
        ).execute()
        return self.get_or_create_daily_counter(user_id, event_date)

    def cleanup_old_events(self, days_to_keep: int = 90) -> int:
        from datetime import timedelta
        cutoff = date.today() - timedelta(days=days_to_keep)
        deleted = self.AffinityEvent.delete().where(
            self.AffinityEvent.event_date < cutoff
        ).execute()
        return deleted

    def cleanup_old_counters(self, days_to_keep: int = 90) -> int:
        from datetime import timedelta
        cutoff = date.today() - timedelta(days=days_to_keep)
        deleted = self.AffinityDailyCounter.delete().where(
            self.AffinityDailyCounter.event_date < cutoff
        ).execute()
        return deleted

    def maintenance(self) -> None:
        self.db.execute_sql("PRAGMA wal_checkpoint(TRUNCATE)")
        self.db.execute_sql("VACUUM")

    def reset_user(self, user_id: str) -> AffinityUserState:
        with self.db.atomic():
            self.AffinityEvent.delete().where(self.AffinityEvent.user_id == user_id).execute()
            self.AffinityDailyCounter.delete().where(
                self.AffinityDailyCounter.user_id == user_id
            ).execute()
            self.AffinityRuntimeState.delete().where(
                self.AffinityRuntimeState.user_id == user_id
            ).execute()

            state = self.get_or_create_user_state(user_id)
            default_fields = self._default_user_state_fields()
            for key, value in default_fields.items():
                setattr(state, key, value)
            state.user_id = user_id
            state.updated_at = _utcnow()
            state.save()
            return state

    def reset_all_users(self) -> int:
        with self.db.atomic():
            user_ids = self.list_user_ids()
            self.AffinityEvent.delete().execute()
            self.AffinityDailyCounter.delete().execute()
            self.AffinityRuntimeState.delete().execute()

            for user_id in user_ids:
                state = self.get_or_create_user_state(user_id)
                default_fields = self._default_user_state_fields()
                for key, value in default_fields.items():
                    setattr(state, key, value)
                state.user_id = user_id
                state.updated_at = _utcnow()
                state.save()
            return len(user_ids)
