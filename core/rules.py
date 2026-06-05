from datetime import date

DEFAULT_DAILY_REVIEW_NEGATIVE_CAP = -50
DEFAULT_NEGATIVE_CAP = -60
DEFAULT_GLOBAL_NEGATIVE_CAP = -60
DEFAULT_MEMORY_RECALL_CAP = 5
DEFAULT_PROFILE_GROWTH_CAP = 12


def message_key(message_id: str, event_type: str) -> str:
    return f"message:{message_id}:{event_type}"


def daily_key(user_id: str, event_date: date, event_type: str) -> str:
    return f"daily:{user_id}:{event_date.isoformat()}:{event_type}"


def migration_key(user_id: str, migration_version: str) -> str:
    return f"migration:{user_id}:{migration_version}"


def apply_daily_cap(current_delta: float, requested_delta: float, cap: float) -> float:
    if requested_delta == 0:
        return 0

    if requested_delta > 0:
        remaining = cap - current_delta
        if remaining <= 0:
            return 0
        return min(requested_delta, remaining)

    if requested_delta < 0:
        remaining = cap - current_delta
        if remaining >= 0:
            return 0
        return max(requested_delta, remaining)

    return 0


def apply_global_negative_cap(
    user_id: str,
    event_date: date,
    requested_delta: float,
    db_manager,
    cap: float = DEFAULT_GLOBAL_NEGATIVE_CAP,
) -> float:
    if requested_delta >= 0:
        return requested_delta

    counter = db_manager.get_or_create_daily_counter(user_id, event_date)
    current_negative = float(counter.negative_delta or 0) + float(
        counter.review_negative_delta or 0
    )
    remaining = float(cap) - current_negative
    if remaining >= 0:
        return 0
    return max(requested_delta, remaining)
