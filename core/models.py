from enum import Enum


class RelationshipStage(str, Enum):
    COLD_WAR = "冷战"
    DISLIKE = "反感"
    DISTANT = "疏离"
    STRANGER = "初识"
    FRIEND = "朋友"
    CLOSE_FRIEND = "挚友"
    AMBIGUOUS = "暧昧"
    LOVER_CANDIDATE = "恋人候选"
    LOVER = "恋人"


class ConfirmationStatus(str, Enum):
    NONE = "none"
    PENDING = "pending"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


class ConfirmationInitiator(str, Enum):
    USER = "user"
    BOT = "bot"
    ADMIN = "admin"


class AffinityEventType(str, Enum):
    DAILY_CHAT = "daily_chat"
    MEMORY_RECALL = "memory_recall"
    PROFILE_GROWTH = "profile_growth"
    DAILY_REVIEW = "daily_review"
    SHARE_SECRET = "share_secret"
    SHARED_WORRY = "shared_worry"
    JEALOUSY = "jealousy"
    REJECTION = "rejection"
    COLD_WAR = "cold_war"
    ARGUMENT = "argument"
    STAGE_UNLOCK = "stage_unlock"
    RELATIONSHIP_CONFIRM_REQUESTED = "relationship_confirm_requested"
    RELATIONSHIP_CONFIRMED = "relationship_confirmed"


class Mood(str, Enum):
    CALM = "平静"
    HAPPY = "开心"
    JEALOUS = "吃醋"
    LOW = "低落"
    COLD_WAR = "冷战"
