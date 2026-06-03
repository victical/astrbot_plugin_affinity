from .models import RelationshipStage

MIN_AFFINITY_SCORE = -100
MAX_AFFINITY_SCORE = 520


def clamp_score(score: float) -> float:
    return max(MIN_AFFINITY_SCORE, min(MAX_AFFINITY_SCORE, score))


def score_stage(score: float) -> RelationshipStage:
    score = clamp_score(score)

    if score <= -71:
        return RelationshipStage.COLD_WAR
    if score <= -31:
        return RelationshipStage.DISLIKE
    if score <= -1:
        return RelationshipStage.DISTANT
    if score <= 49:
        return RelationshipStage.STRANGER
    if score <= 149:
        return RelationshipStage.FRIEND
    if score <= 299:
        return RelationshipStage.CLOSE_FRIEND
    if score <= 439:
        return RelationshipStage.AMBIGUOUS
    return RelationshipStage.LOVER_CANDIDATE


def effective_stage(
    score: float,
    confirmed_stage: str | None,
    lover_locked: bool,
    unlocked_stage: str | None = None,
) -> RelationshipStage:
    if lover_locked and confirmed_stage == RelationshipStage.LOVER.value:
        return RelationshipStage.LOVER

    if unlocked_stage:
        try:
            return RelationshipStage(unlocked_stage)
        except ValueError:
            pass

    return score_stage(score)
