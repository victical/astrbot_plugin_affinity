from __future__ import annotations


ROMANTIC_STAGES = {"暧昧", "恋人候选", "恋人"}
MAX_STAGE_CUSTOM_PROMPT_STAGES = {"恋人候选", "恋人"}

STAGE_PROMPT_CONFIG_KEYS = {
    "cold_war": "冷战",
    "dislike": "反感",
    "distant": "疏离",
    "stranger": "初识",
    "friend": "朋友",
    "close_friend": "挚友",
    "ambiguous": "暧昧",
    "lover_candidate": "恋人候选",
    "lover": "恋人",
}

STAGE_GUIDANCE = {
    "冷战": "语气保持克制、低温和边界感，避免主动亲近，先处理误解和安全距离。",
    "反感": "回应要简短礼貌，不追问隐私，不使用亲昵称呼。",
    "疏离": "保持普通熟人距离，少量表达关心，但不显得过度热情。",
    "初识": "像刚认识的人一样自然交流，友好但不过分熟稔。",
    "朋友": "语气可以轻松一些，记得对方偏好，保持朋友式关心。",
    "挚友": "可以更熟悉、更可靠，允许温和调侃和稳定陪伴。",
    "暧昧": "表达可以更柔和亲近，但不要越过用户明确边界。",
    "恋人候选": "可以体现珍视和期待，但不要替用户确认亲密身份。",
    "恋人": "表达亲近、稳定和专属感，同时尊重用户当下情绪和边界。",
}

SOFTENED_ROMANCE_GUIDANCE = "公开表达保持克制，用亲近朋友般的关心替代明显暧昧或恋爱表达。"


def should_use_max_stage_prompt(
    *,
    stage: str,
    enabled: bool,
    scope: str,
    is_group: bool,
    prompt: str,
) -> bool:
    if not enabled:
        return False
    if str(stage or "") not in MAX_STAGE_CUSTOM_PROMPT_STAGES:
        return False
    if not str(prompt or "").strip():
        return False
    normalized_scope = str(scope or "private_only").strip().lower()
    if normalized_scope != "all" and is_group:
        return False
    return True


def normalize_stage_prompts(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}

    prompts: dict[str, str] = {}
    for raw_key, raw_prompt in value.items():
        stage = STAGE_PROMPT_CONFIG_KEYS.get(str(raw_key), str(raw_key))
        if stage not in STAGE_GUIDANCE:
            continue
        prompts[stage] = str(raw_prompt or "")
    return prompts


def _format_affinity_score(score: float) -> str:
    value = float(score or 0)
    return f"{value:.0f}" if value.is_integer() else f"{value:g}"


def _render_prompt_variables(template: str, variables: dict[str, str]) -> str:
    rendered = str(template or "")
    for key, value in variables.items():
        rendered = rendered.replace("{" + key + "}", value)
    return rendered


def build_relationship_prompt(
    *,
    effective_stage: str,
    current_mood: str,
    is_group: bool,
    soften_romance: bool,
    affinity_score: float = 0,
    stage_prompts: dict[str, str] | None = None,
    max_stage_custom_prompt: str = "",
    use_max_stage_custom_prompt: bool = False,
) -> str:
    stage = str(effective_stage or "初识")
    mood = str(current_mood or "平静")
    affinity = _format_affinity_score(affinity_score)
    chat_type = "群聊" if is_group else "私聊"
    variables = {
        "stage": stage,
        "relationship": stage,
        "relation": stage,
        "关系": stage,
        "mood": mood,
        "情绪": mood,
        "affinity": affinity,
        "score": affinity,
        "好感度": affinity,
        "chat_type": chat_type,
        "聊天场景": chat_type,
        "私聊/群聊": chat_type,
    }

    max_stage_guidance = str(max_stage_custom_prompt or "").strip()
    custom_guidance = max_stage_guidance if use_max_stage_custom_prompt else ""
    if not custom_guidance and isinstance(stage_prompts, dict):
        custom_guidance = str(stage_prompts.get(stage) or "").strip()
    guidance = custom_guidance or STAGE_GUIDANCE.get(stage, STAGE_GUIDANCE["初识"])
    if (
        not use_max_stage_custom_prompt
        and is_group
        and soften_romance
        and stage in ROMANTIC_STAGES
    ):
        guidance = SOFTENED_ROMANCE_GUIDANCE
    else:
        guidance = _render_prompt_variables(guidance, variables)

    lines = [
        "根据以下关系上下文调整语气：",
        f"好感度：{affinity}",
        f"关系：{stage}",
        f"情绪：{mood}",
        f"私聊/群聊：{chat_type}",
        guidance,
    ]
    return "\n".join(lines)


def inject_relationship_prompt(req, prompt: str, target: str = "system") -> None:
    target_name = str(target or "system").strip().lower()
    attr = "prompt" if target_name in {"user", "prompt", "user_prompt"} else "system_prompt"
    existing = getattr(req, attr, None) or ""
    setattr(req, attr, f"{existing}\n\n{prompt}" if existing else prompt)
