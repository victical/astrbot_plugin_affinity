from __future__ import annotations

from dataclasses import dataclass

from .models import ConfirmationStatus, RelationshipStage


@dataclass(frozen=True, slots=True)
class ConfirmationTransition:
    next_status: ConfirmationStatus
    confirmed_stage: str | None = None
    lover_locked: bool = False
    initiator: str | None = None
    cooldown: bool = False
    changed: bool = False


def _status(value: ConfirmationStatus | str) -> ConfirmationStatus:
    if isinstance(value, ConfirmationStatus):
        return value
    return ConfirmationStatus(value)


def _eligible_for_confirmation(
    *,
    score: float,
    effective_stage: str,
    scene_allows_proactive: bool,
    rejected_cooldown_active: bool,
) -> bool:
    if rejected_cooldown_active:
        return False
    if not scene_allows_proactive:
        return False
    return score >= 440 or effective_stage in {
        RelationshipStage.LOVER_CANDIDATE.value,
        RelationshipStage.LOVER.value,
    }


def decide_confirmation_transition(
    *,
    status: ConfirmationStatus | str,
    trigger: str,
    score: float,
    effective_stage: str,
    scene_allows_proactive: bool,
    rejected_cooldown_active: bool,
) -> ConfirmationTransition:
    current = _status(status)

    if current == ConfirmationStatus.CONFIRMED:
        return ConfirmationTransition(
            next_status=ConfirmationStatus.CONFIRMED,
            confirmed_stage=RelationshipStage.LOVER.value,
            lover_locked=True,
        )

    if current == ConfirmationStatus.NONE and trigger in {"user_query", "bot_proactive"}:
        if not _eligible_for_confirmation(
            score=score,
            effective_stage=effective_stage,
            scene_allows_proactive=scene_allows_proactive,
            rejected_cooldown_active=rejected_cooldown_active,
        ):
            return ConfirmationTransition(next_status=current)
        initiator = "bot" if trigger == "bot_proactive" else "user"
        return ConfirmationTransition(
            next_status=ConfirmationStatus.PENDING,
            confirmed_stage=None,
            lover_locked=False,
            initiator=initiator,
            changed=True,
        )

    if current == ConfirmationStatus.PENDING and trigger == "user_accept":
        return ConfirmationTransition(
            next_status=ConfirmationStatus.CONFIRMED,
            confirmed_stage=RelationshipStage.LOVER.value,
            lover_locked=True,
            changed=True,
        )

    if current == ConfirmationStatus.PENDING and trigger == "user_reject":
        return ConfirmationTransition(
            next_status=ConfirmationStatus.REJECTED,
            confirmed_stage=None,
            lover_locked=False,
            cooldown=True,
            changed=True,
        )

    if current == ConfirmationStatus.PENDING and trigger == "expired":
        return ConfirmationTransition(
            next_status=ConfirmationStatus.NONE,
            confirmed_stage=None,
            lover_locked=False,
            changed=True,
        )

    if current == ConfirmationStatus.REJECTED and trigger == "cooldown_end":
        return ConfirmationTransition(
            next_status=ConfirmationStatus.NONE,
            confirmed_stage=None,
            lover_locked=False,
            changed=True,
        )

    return ConfirmationTransition(next_status=current)
