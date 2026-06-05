from __future__ import annotations

from dataclasses import dataclass
from typing import Any


POSITIVE_SIGNAL_TYPES = frozenset(
    {
        "casual_warmth",
        "self_disclosure",
        "trust_signal",
        "relationship_repair",
        "appreciation",
        "preference_discovered",
    }
)
NEGATIVE_SIGNAL_TYPES = frozenset(
    {
        "coldness",
        "boundary_violation",
        "hostility",
        "rejection_signal",
        "trust_break",
        "conflict_escalation",
    }
)
NEUTRAL_SIGNAL_TYPES = frozenset({"neutral_chat", "low_value_noise"})
SCORED_SIGNAL_TYPES = (POSITIVE_SIGNAL_TYPES | NEGATIVE_SIGNAL_TYPES) - {
    "preference_discovered"
}


SCORE_TABLE: dict[str, dict[str, float]] = {
    "casual_warmth": {"base": 1, "per_intensity": 1, "max": 3},
    "self_disclosure": {"base": 3, "per_intensity": 2, "max": 8},
    "trust_signal": {"base": 5, "per_intensity": 3, "max": 12},
    "relationship_repair": {"base": 4, "per_intensity": 2, "max": 10},
    "appreciation": {"base": 2, "per_intensity": 2, "max": 6},
    "coldness": {"base": -1, "per_intensity": -2, "min": -5},
    "boundary_violation": {"base": -5, "per_intensity": -4, "min": -15},
    "hostility": {"base": -8, "per_intensity": -5, "min": -20},
    "rejection_signal": {"base": -5, "per_intensity": -4, "min": -15},
    "trust_break": {"base": -10, "per_intensity": -8, "min": -50},
    "conflict_escalation": {"base": -5, "per_intensity": -5, "min": -20},
}


@dataclass(frozen=True, slots=True)
class ReviewSignal:
    type: str
    direction: str
    intensity: int
    confidence: float
    evidence: str
    message_refs: list[str]
    session_id: str = ""


def _expected_direction(event_type: str) -> str | None:
    if event_type in POSITIVE_SIGNAL_TYPES:
        return "positive"
    if event_type in NEGATIVE_SIGNAL_TYPES:
        return "negative"
    if event_type in NEUTRAL_SIGNAL_TYPES:
        return "neutral"
    return None


def validate_signal_payload(
    payload: dict[str, Any],
    min_confidence: float = 0.65,
    *,
    session_id: str = "",
) -> ReviewSignal | None:
    if not isinstance(payload, dict):
        return None
    if "score_delta" in payload:
        return None

    event_type = str(payload.get("type") or "").strip()
    expected_direction = _expected_direction(event_type)
    if expected_direction is None:
        return None

    direction = str(payload.get("direction") or "").strip()
    if direction != expected_direction:
        return None

    try:
        intensity = int(payload.get("intensity"))
        confidence = float(payload.get("confidence"))
    except (TypeError, ValueError):
        return None
    if intensity < 1 or intensity > 3:
        return None
    if direction == "negative":
        min_confidence = max(min_confidence, 0.75)
    elif direction == "positive":
        min_confidence = min(min_confidence, 0.60)

    if confidence < min_confidence or confidence > 1:
        return None

    evidence = str(payload.get("evidence") or "").strip()
    if not evidence:
        return None

    refs = payload.get("message_refs") or []
    if not isinstance(refs, list):
        return None
    message_refs = [str(ref).strip() for ref in refs if str(ref or "").strip()]

    return ReviewSignal(
        type=event_type,
        direction=direction,
        intensity=intensity,
        confidence=confidence,
        evidence=evidence[:160],
        message_refs=message_refs,
        session_id=session_id,
    )


def score_signal(signal: ReviewSignal) -> float:
    if signal.type not in SCORED_SIGNAL_TYPES:
        return 0
    spec = SCORE_TABLE.get(signal.type)
    if not spec:
        return 0
    value = spec["base"] + spec["per_intensity"] * signal.intensity
    if signal.direction == "positive":
        return min(value, spec["max"])
    return max(value, spec["min"])


def _sort_key(signal: ReviewSignal) -> tuple[str, str, str]:
    first_ref = min(signal.message_refs) if signal.message_refs else ""
    return (signal.session_id, first_ref, signal.type)


def assign_event_indices(signals: list[ReviewSignal]) -> list[tuple[ReviewSignal, int]]:
    counts: dict[str, int] = {}
    indexed: list[tuple[ReviewSignal, int]] = []
    for signal in sorted(signals, key=_sort_key):
        index = counts.get(signal.type, 0)
        counts[signal.type] = index + 1
        indexed.append((signal, index))
    return indexed


def _has_overlapping_refs(left: ReviewSignal, right: ReviewSignal) -> bool:
    return bool(set(left.message_refs) & set(right.message_refs))


def _similar_evidence(left: str, right: str) -> bool:
    left_tokens = set(left.strip())
    right_tokens = set(right.strip())
    if not left_tokens or not right_tokens:
        return False
    return len(left_tokens & right_tokens) / max(len(left_tokens | right_tokens), 1) >= 0.8


def aggregate_signals(signals: list[ReviewSignal]) -> list[ReviewSignal]:
    kept: list[ReviewSignal] = []
    for signal in sorted(signals, key=_sort_key):
        duplicate_index = None
        for index, existing in enumerate(kept):
            if existing.type != signal.type:
                continue
            if existing.intensity != signal.intensity:
                continue
            if _has_overlapping_refs(existing, signal) or _similar_evidence(
                existing.evidence,
                signal.evidence,
            ):
                duplicate_index = index
                break
        if duplicate_index is None:
            kept.append(signal)
            continue
        if signal.confidence > kept[duplicate_index].confidence:
            kept[duplicate_index] = signal
    return sorted(kept, key=_sort_key)


def score_signals_with_negative_cap(
    signals: list[ReviewSignal],
    negative_cap: float = -50,
) -> list[tuple[ReviewSignal, float, int]]:
    result: list[tuple[ReviewSignal, float, int]] = []
    current_negative = 0.0
    for signal, index in assign_event_indices(signals):
        delta = score_signal(signal)
        if delta == 0:
            continue
        if delta < 0:
            remaining = negative_cap - current_negative
            if remaining >= 0:
                continue
            delta = max(delta, remaining)
            current_negative += delta
        result.append((signal, delta, index))
    return result
