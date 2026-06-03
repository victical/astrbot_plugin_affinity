import asyncio
from dataclasses import dataclass
from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register
from astrbot.core.star.filter.command import GreedyStr

from .core.injection import (
    build_relationship_prompt,
    inject_relationship_prompt,
    normalize_stage_prompts,
    should_use_max_stage_prompt,
)
from .core.memory_provider import (
    diagnose_affinity_memory_provider,
    resolve_affinity_memory_provider,
)
from .db_manager import AffinityDatabaseManager
from .handlers.commands import AffinityCommandHandler
from .services.affinity_service import AffinityService
from .services.affinity_signal_analyzer import AffinitySignalAnalyzer
from .services.daily_review_service import DailyReviewService
from .services.migration_service import MigrationService
from .services.scheduler import DailyReviewScheduler


def _config_get(config, key: str, default=None):
    if config is None:
        return default
    getter = getattr(config, "get", None)
    if callable(getter):
        return getter(key, default)
    return getattr(config, key, default)


def _event_user_id(event: AstrMessageEvent) -> str:
    try:
        return str(event.get_sender_id())
    except Exception:
        return ""


def _event_message_id(event: AstrMessageEvent) -> str:
    message_obj = getattr(event, "message_obj", None)
    for attr in ("message_id", "message_id_str", "id"):
        value = getattr(message_obj, attr, None)
        if value:
            return str(value)
    return f"{_event_user_id(event)}:{id(event)}"


@dataclass(frozen=True, slots=True)
class QueryTarget:
    user_id: str
    display_name: str


def _event_sender_name(event: AstrMessageEvent) -> str:
    get_sender_name = getattr(event, "get_sender_name", None)
    if callable(get_sender_name):
        return str(get_sender_name() or "").strip()
    return ""


def _query_target_user(event: AstrMessageEvent, user_id: str = "") -> QueryTarget:
    explicit_user_id = str(user_id or "").strip()
    if explicit_user_id and not explicit_user_id.startswith("@"):
        return QueryTarget(user_id=explicit_user_id, display_name=explicit_user_id)

    self_id = ""
    get_self_id = getattr(event, "get_self_id", None)
    if callable(get_self_id):
        self_id = str(get_self_id() or "").strip()

    get_messages = getattr(event, "get_messages", None)
    messages = get_messages() if callable(get_messages) else getattr(
        getattr(event, "message_obj", None),
        "message",
        [],
    )
    for component in messages or []:
        target = str(getattr(component, "qq", "") or "").strip()
        if not target or target == "all" or target == self_id:
            continue
        display_name = str(getattr(component, "name", "") or "").strip() or target
        return QueryTarget(user_id=target, display_name=display_name)

    sender_id = _event_user_id(event)
    return QueryTarget(
        user_id=sender_id,
        display_name=_event_sender_name(event) or sender_id,
    )


def _migration_args(user_id: str = "", mode: str = "") -> tuple[str | None, bool]:
    first = str(user_id or "").strip()
    second = str(mode or "").strip()
    force_words = {"force", "强制"}
    if first.lower() in force_words and not second:
        return None, True
    return first or None, second.lower() in force_words


MAX_STAGE_PROMPT_NOTICE = (
    "你现在可以设置自己的最高阶段自定义提示词。\n"
    "使用：/设置提示词 <提示词>\n"
    "清除：/清除提示词"
)


@register("astrbot_plugin_affinity", "victical", "好感度与关系系统插件", "0.1.0")
class AffinityPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        self.context = context
        self.config = config if config is not None else {}
        self.enabled = bool(_config_get(self.config, "enable_affinity", True))
        self.memory_provider = None
        self.db = None
        self.service = None
        self.daily_review_service = None
        self.migration_service = None
        self.command_handler = None
        self.scheduler = None
        self._initialized = False

    def _lazy_init(self) -> bool:
        if self._initialized:
            return self.enabled
        self._initialized = True

        if not self.enabled:
            return False

        self.memory_provider = resolve_affinity_memory_provider(self.context)
        if self.memory_provider is None:
            self.enabled = False
            logger.warning(
                "Affinity：未找到可用的 affinity_memory_provider，插件已禁用。"
                f"诊断：{diagnose_affinity_memory_provider(self.context)}"
            )
            return False

        data_dir = self._get_data_dir()
        self.db = AffinityDatabaseManager(data_dir / "affinity.db")
        self.service = AffinityService(self.db)
        review_options = {
            "session_gap_minutes": int(
                _config_get(self.config, "affinity_review_session_gap_minutes", 45)
            ),
            "max_messages_per_session": int(
                _config_get(self.config, "affinity_review_max_messages_per_session", 80)
            ),
            "negative_cap": float(_config_get(self.config, "affinity_review_negative_cap", -50)),
            "stage_advance_enabled": bool(
                _config_get(self.config, "affinity_stage_advance_enabled", True)
            ),
            "stage_advance_max_steps_per_day": int(
                _config_get(self.config, "affinity_stage_advance_max_steps_per_day", 1)
            ),
        }
        signal_analyzer = AffinitySignalAnalyzer(
            context=self.context,
            model_id=str(_config_get(self.config, "affinity_review_llm_model", "")),
            min_confidence=float(
                _config_get(self.config, "affinity_llm_review_min_confidence", 0.65)
            ),
        )
        self.daily_review_service = DailyReviewService(
            self.db,
            self.memory_provider,
            backup_dir=data_dir / "affinity_logs",
            signal_analyzer=signal_analyzer,
            llm_review_enabled=bool(
                _config_get(self.config, "affinity_llm_review_enabled", False)
            ),
            review_options=review_options,
        )
        self.migration_service = MigrationService(
            self.db,
            self.memory_provider,
            backup_dir=data_dir / "affinity_migration_backups",
        )
        self.command_handler = AffinityCommandHandler(
            service=self.service,
            daily_review_service=self.daily_review_service,
            migration_service=self.migration_service,
        )
        self.scheduler = DailyReviewScheduler(
            review_service=self.daily_review_service,
            provider=self.memory_provider,
            hour=int(
                _config_get(
                    self.config,
                    "affinity_daily_refresh_hour",
                    _config_get(self.config, "daily_review_hour", 4),
                )
            ),
        )
        try:
            asyncio.get_running_loop()
            self.scheduler.start()
        except RuntimeError:
            pass
        logger.info("Affinity：好感度系统初始化完成")
        return True

    def _get_data_dir(self) -> Path:
        try:
            from astrbot.api.star import StarTools

            data_dir = Path(StarTools.get_data_dir())
        except Exception:
            data_dir = Path(__file__).resolve().parent / "data" / "plugin_data" / "astrbot_plugin_affinity"
        try:
            data_dir.mkdir(parents=True, exist_ok=True)
            return data_dir
        except OSError:
            fallback = Path(__file__).resolve().parent / "data" / "plugin_data" / "astrbot_plugin_affinity"
            fallback.mkdir(parents=True, exist_ok=True)
            return fallback

    def _plain_result(self, event: AstrMessageEvent, text: str):
        result = getattr(event, "plain_result", None)
        if callable(result):
            return result(text)
        return text

    async def _run_command(self, event: AstrMessageEvent, command_name: str, call):
        self._lazy_init()
        if not self.enabled or self.command_handler is None:
            yield self._plain_result(event, "好感系统未启用。")
            return
        try:
            yield self._plain_result(event, await call())
        except Exception as exc:
            logger.error("Affinity command %s failed: %s", command_name, exc, exc_info=True)
            yield self._plain_result(event, "命令执行失败，请稍后重试。")

    @filter.command("查询好感", alias={"好感查询", "好感度"})
    async def affinity_query(self, event: AstrMessageEvent, user_id: str = ""):
        target = _query_target_user(event, user_id)
        async for result in self._run_command(
            event,
            "好感查询",
            lambda: self.command_handler.handle_query(
                target.user_id,
                display_name=target.display_name,
            ),
        ):
            yield result

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("设置好感", alias={"好感设置"})
    async def affinity_set_score(self, event: AstrMessageEvent, user_id: str, score: str):
        async for result in self._run_command(
            event,
            "好感设置",
            lambda: self.command_handler.handle_set_score(user_id, float(score)),
        ):
            yield result

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("设置关系", alias={"关系设置", "设置阶段", "阶段设置"})
    async def affinity_set_relationship(self, event: AstrMessageEvent, user_id: str, stage: str):
        async for result in self._run_command(
            event,
            "关系设置",
            lambda: self.command_handler.handle_set_relationship(user_id, stage),
        ):
            yield result

    @filter.command("设置提示词", alias={"设置专属提示词", "最高阶段提示词"})
    async def affinity_set_max_stage_custom_prompt(
        self,
        event: AstrMessageEvent,
        prompt: GreedyStr,
    ):
        user_id = _event_user_id(event)
        async for result in self._run_command(
            event,
            "设置提示词",
            lambda: self.command_handler.handle_set_max_stage_custom_prompt(user_id, prompt),
        ):
            yield result

    @filter.command("清除提示词", alias={"删除最高阶段提示词", "重置最高阶段提示词"})
    async def affinity_clear_max_stage_custom_prompt(self, event: AstrMessageEvent):
        user_id = _event_user_id(event)
        async for result in self._run_command(
            event,
            "清除提示词",
            lambda: self.command_handler.handle_clear_max_stage_custom_prompt(user_id),
        ):
            yield result

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("重置好感", alias={"好感重置"})
    async def affinity_reset(self, event: AstrMessageEvent, user_id: str):
        async for result in self._run_command(
            event,
            "好感重置",
            lambda: self.command_handler.handle_reset(user_id),
        ):
            yield result

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("重置所有人好感", alias={"重置所有好感", "好感全部重置", "重置全部好感"})
    async def affinity_reset_all(self, event: AstrMessageEvent, confirmation: str = ""):
        async for result in self._run_command(
            event,
            "重置所有人好感",
            lambda: self.command_handler.handle_reset_all(confirmation),
        ):
            yield result

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("好感事件", alias={"查询好感事件"})
    async def affinity_events(self, event: AstrMessageEvent, user_id: str, limit: str = "10"):
        async for result in self._run_command(
            event,
            "好感事件",
            lambda: self.command_handler.handle_events(user_id, int(limit or 10)),
        ):
            yield result

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("好感回顾")
    async def affinity_review(
        self,
        event: AstrMessageEvent,
        user_id: str,
        event_date: str = "",
        mode: str = "",
    ):
        async for result in self._run_command(
            event,
            "好感回顾",
            lambda: self.command_handler.handle_daily_review(
                user_id,
                event_date or None,
                dry_run=mode == "dry_run",
                force_rebuild=mode == "force_rebuild",
            ),
        ):
            yield result

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("好感迁移", alias={"迁移好感"})
    async def affinity_migrate(self, event: AstrMessageEvent, user_id: str = "", mode: str = ""):
        target_user_id, force = _migration_args(user_id, mode)
        async for result in self._run_command(
            event,
            "好感迁移",
            lambda: self.command_handler.handle_migration(target_user_id, force=force),
        ):
            yield result

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent):
        self._lazy_init()
        if not self.enabled or self.service is None:
            return
        user_id = _event_user_id(event)
        if not user_id:
            return

        snapshot = self.service.get_user_snapshot(user_id)
        is_group = bool(event.get_group_id()) if hasattr(event, "get_group_id") else False
        should_prompt = should_use_max_stage_prompt(
            stage=snapshot.effective_stage,
            enabled=bool(
                _config_get(self.config, "affinity_max_stage_custom_prompt_enabled", True)
            ),
            scope=str(
                _config_get(
                    self.config,
                    "affinity_max_stage_custom_prompt_scope",
                    "private_only",
                )
            ),
            is_group=is_group,
            prompt="notice",
        )
        if (
            should_prompt
            and not str(getattr(snapshot, "max_stage_custom_prompt", "") or "").strip()
            and not getattr(snapshot, "max_stage_prompt_notice_sent_at", None)
        ):
            self.service.mark_max_stage_prompt_notice_sent(user_id)
            yield self._plain_result(event, MAX_STAGE_PROMPT_NOTICE)

    @filter.on_llm_request(priority=-1)
    async def on_llm_request(self, event: AstrMessageEvent, req):
        self._lazy_init()
        if not self.enabled or self.service is None:
            return
        user_id = _event_user_id(event)
        if not user_id:
            return
        snapshot = self.service.get_user_snapshot(user_id)
        is_group = bool(event.get_group_id()) if hasattr(event, "get_group_id") else False
        max_stage_custom_prompt = str(
            getattr(snapshot, "max_stage_custom_prompt", "") or ""
        )
        use_max_stage_custom_prompt = should_use_max_stage_prompt(
            stage=snapshot.effective_stage,
            enabled=bool(
                _config_get(self.config, "affinity_max_stage_custom_prompt_enabled", True)
            ),
            scope=str(
                _config_get(
                    self.config,
                    "affinity_max_stage_custom_prompt_scope",
                    "private_only",
                )
            ),
            is_group=is_group,
            prompt=max_stage_custom_prompt,
        )
        prompt = build_relationship_prompt(
            effective_stage=snapshot.effective_stage,
            affinity_score=float(getattr(snapshot, "affinity_score", 0) or 0),
            current_mood=snapshot.current_mood,
            is_group=is_group,
            soften_romance=bool(_config_get(self.config, "affinity_group_soften_romance", False)),
            stage_prompts=normalize_stage_prompts(
                _config_get(self.config, "affinity_stage_prompts", {})
            ),
            max_stage_custom_prompt=max_stage_custom_prompt,
            use_max_stage_custom_prompt=use_max_stage_custom_prompt,
        )
        target = str(_config_get(self.config, "affinity_prompt_injection_target", "system"))
        inject_relationship_prompt(
            req,
            prompt,
            target=target,
        )
        if bool(_config_get(self.config, "affinity_debug_injection", False)):
            logger.info(
                f"Affinity：注入关系提示 target={target} user_id={user_id} "
                f"stage={snapshot.effective_stage} is_group={is_group} "
                f"max_stage_custom={use_max_stage_custom_prompt} prompt={prompt}"
            )

    async def terminate(self):
        self.enabled = False
        if self.scheduler is not None:
            await self.scheduler.cancel()
        if self.db is not None:
            self.db.close()
