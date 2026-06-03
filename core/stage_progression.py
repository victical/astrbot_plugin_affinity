from __future__ import annotations

from .models import RelationshipStage
from .stage import score_stage


STAGE_ORDER = [
    RelationshipStage.COLD_WAR.value,
    RelationshipStage.DISLIKE.value,
    RelationshipStage.DISTANT.value,
    RelationshipStage.STRANGER.value,
    RelationshipStage.FRIEND.value,
    RelationshipStage.CLOSE_FRIEND.value,
    RelationshipStage.AMBIGUOUS.value,
    RelationshipStage.LOVER_CANDIDATE.value,
]


def _normalize_stage(stage: str | RelationshipStage | None) -> str:
    value = stage.value if isinstance(stage, RelationshipStage) else str(stage or "")
    if value == RelationshipStage.LOVER.value:
        return RelationshipStage.LOVER_CANDIDATE.value
    if value in STAGE_ORDER:
        return value
    return RelationshipStage.STRANGER.value


def stage_index(stage: str | RelationshipStage | None) -> int:
    return STAGE_ORDER.index(_normalize_stage(stage))


def advance_stage(
    unlocked_stage: str | RelationshipStage | None,
    target_stage: str | RelationshipStage | None,
    max_steps: int = 1,
) -> str:
    current_index = stage_index(unlocked_stage)
    target_index = stage_index(target_stage)
    if target_index <= current_index:
        return STAGE_ORDER[current_index]
    steps = max(0, int(max_steps))
    return STAGE_ORDER[min(target_index, current_index + steps)]


def demote_stage(
    unlocked_stage: str | RelationshipStage | None,
    target_stage: str | RelationshipStage | None,
) -> str:
    current_index = stage_index(unlocked_stage)
    target_index = stage_index(target_stage)
    if target_index >= current_index:
        return STAGE_ORDER[current_index]
    return STAGE_ORDER[target_index]


def bank_balance(score: float, unlocked_stage: str | RelationshipStage | None) -> int:
    return max(0, stage_index(score_stage(score)) - stage_index(unlocked_stage))
