from __future__ import annotations

import math
from typing import Any

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

STAGE_NEXT_THRESHOLDS = {
    RelationshipStage.COLD_WAR.value: -70,
    RelationshipStage.DISLIKE.value: -30,
    RelationshipStage.DISTANT.value: 0,
    RelationshipStage.STRANGER.value: 50,
    RelationshipStage.FRIEND.value: 150,
    RelationshipStage.CLOSE_FRIEND.value: 250,
    RelationshipStage.AMBIGUOUS.value: 400,
}


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
    """已废弃: 阶段推进已改为实时更新，不再需要此函数"""
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
    """已废弃: 阶段推进已改为实时更新，不再需要此函数"""
    current_index = stage_index(unlocked_stage)
    target_index = stage_index(target_stage)
    if target_index >= current_index:
        return STAGE_ORDER[current_index]
    return STAGE_ORDER[target_index]


def bank_balance(score: float, unlocked_stage: str | RelationshipStage | None) -> int:
    """已废弃: 好感银行机制已移除"""
    return max(0, stage_index(score_stage(score)) - stage_index(unlocked_stage))


def calculate_stage_progress(
    current_score: float,
    unlocked_stage: str | RelationshipStage | None = None,  # 已废弃，保留仅为兼容性
) -> dict[str, Any]:
    # 基于当前分数计算阶段进度
    current = score_stage(current_score)
    current_index = stage_index(current)
    if current_index >= len(STAGE_ORDER) - 1:
        return {
            "next_stage": None,
            "score_needed": 0,
            "days_estimate": 0,
        }

    next_stage = STAGE_ORDER[current_index + 1]
    next_threshold = STAGE_NEXT_THRESHOLDS[current.value]
    score_needed = max(0, int(math.ceil(next_threshold - float(current_score or 0))))
    days_estimate = math.ceil(score_needed / 15) if score_needed > 0 else 0
    return {
        "next_stage": next_stage,
        "score_needed": score_needed,
        "days_estimate": days_estimate,
    }


def advance_stage_dynamic(
    unlocked_stage: str | RelationshipStage | None,
    target_stage: str | RelationshipStage | None,
    bank_balance_value: int,
    *,
    dynamic_threshold: int = 3,
    base_max_steps: int = 1,
    boosted_max_steps: int = 2,
) -> str:
    """已废弃: 动态推进机制已移除，阶段改为实时更新"""
    max_steps = (
        int(boosted_max_steps)
        if int(bank_balance_value or 0) >= int(dynamic_threshold)
        else int(base_max_steps)
    )
    return advance_stage(unlocked_stage, target_stage, max_steps=max_steps)
