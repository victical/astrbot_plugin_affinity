from __future__ import annotations

import json
import math
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime
from pathlib import Path

from ..core.models import AffinityEventType
from ..core.rules import migration_key
from ..db_manager import AffinityDatabaseManager


@dataclass(slots=True)
class MigrationResult:
    user_id: str
    migrated: bool
    score: float
    reason: str
    backup_path: Path | None = None


def calculate_initial_affinity(
    *,
    memory_count: int,
    old_days_score: float,
    profile_depth_pct: float,
    likes_count: int,
    dislikes_count: int,
    shared_secret: bool,
    old_level: int,
) -> int:
    memory_part = min(
        120,
        120 * math.log(1 + max(0, memory_count) / 150) / math.log(1 + 3000 / 150),
    )
    days_part = min(100, max(0, old_days_score) / 25 * 100)
    profile_part = min(100, max(0, profile_depth_pct))
    preference_part = min(60, max(0, likes_count + dislikes_count) * 6)
    special_part = 20 if shared_secret else 0
    formula_score = (
        memory_part + days_part + profile_part + preference_part + special_part
    ) * (439 / 400)
    level = max(1, min(7, int(old_level or 1)))
    level_multiplier = 1.0 + (level - 1) * 0.05
    final_score = min(439, formula_score * level_multiplier)
    stable_score = Decimal(str(round(final_score, 8)))
    return int(stable_score.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


class MigrationService:
    def __init__(
        self,
        db: AffinityDatabaseManager,
        provider,
        backup_dir: str | Path | None = None,
    ):
        self.db = db
        self.provider = provider
        self.backup_dir = Path(backup_dir or "affinity_migration_backups")

    def backup_current_state(self, user_id: str) -> Path:
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        path = self.backup_dir / (
            f"migration-{user_id}-{datetime.now().strftime('%Y%m%d%H%M%S%f')}.json"
        )
        snapshot = self.db.snapshot_user(user_id)
        path.write_text(json.dumps(snapshot, ensure_ascii=False, default=str, indent=2), encoding="utf-8")
        return path

    def _is_reset_default_state(self, user_id: str) -> bool:
        state = self.db.get_user_state(user_id)
        if state is None:
            return False
        has_events = (
            self.db.AffinityEvent.select()
            .where(self.db.AffinityEvent.user_id == user_id)
            .exists()
        )
        return (
            not has_events
            and float(state.affinity_score or 0) == 0
            and state.stage == "初识"
            and state.effective_stage == "初识"
            and state.unlocked_stage == "初识"
            and state.confirmed_stage is None
            and not bool(state.lover_locked)
        )

    async def migrate_user(self, user_id: str, force: bool = False) -> MigrationResult:
        existing = self.db.get_user_state(user_id)
        backup_path = None
        if existing is not None and not force and not self._is_reset_default_state(user_id):
            return MigrationResult(
                user_id=user_id,
                migrated=False,
                score=float(existing.affinity_score or 0),
                reason="existing_user",
            )
        if existing is not None and force:
            backup_path = self.backup_current_state(user_id)

        snapshot = await self.provider.get_legacy_bond_snapshot(user_id)
        if not snapshot:
            return MigrationResult(
                user_id=user_id,
                migrated=False,
                score=0,
                reason="no_legacy_snapshot",
                backup_path=backup_path,
            )

        score = calculate_initial_affinity(
            memory_count=int(snapshot.get("memory_count") or 0),
            old_days_score=float(snapshot.get("old_days_score") or 0),
            profile_depth_pct=float(snapshot.get("profile_depth_pct") or 0),
            likes_count=int(snapshot.get("likes_count") or 0),
            dislikes_count=int(snapshot.get("dislikes_count") or 0),
            shared_secret=bool(snapshot.get("shared_secret")),
            old_level=int(snapshot.get("old_level") or 1),
        )

        if force and existing is not None:
            self.db.reset_user(user_id)

        result = self.db.apply_score_event(
            user_id=user_id,
            event_type=AffinityEventType.DAILY_REVIEW.value,
            score_delta=score,
            reason="旧羁绊系统迁移初始分",
            source="migration",
            source_ref="legacy_bond",
            idempotency_key=migration_key(user_id, "v1"),
            metadata_json=snapshot,
        )
        state = self.db.get_or_create_user_state(user_id)
        state.confirmed_stage = None
        state.lover_locked = False
        state.save()
        self.db.recompute_user_state_from_events(user_id)
        return MigrationResult(
            user_id=user_id,
            migrated=bool(result.applied),
            score=score,
            reason="migrated" if result.applied else "duplicate",
            backup_path=backup_path,
        )

    async def migrate_all(self, force: bool = False) -> list[MigrationResult]:
        user_ids = await self.provider.get_all_known_user_ids()
        results = []
        for user_id in user_ids:
            results.append(await self.migrate_user(user_id, force=force))
        return results
