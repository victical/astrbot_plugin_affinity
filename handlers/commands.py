from __future__ import annotations

from datetime import date

from ..core.stage_progression import calculate_stage_progress
from ..services.affinity_service import AffinityService


class AffinityCommandHandler:
    def __init__(
        self,
        service: AffinityService,
        daily_review_service=None,
        migration_service=None,
    ):
        self.service = service
        self.daily_review_service = daily_review_service
        self.migration_service = migration_service

    def format_query(self, state, display_name: str | None = None) -> str:
        display_user = str(display_name or "").strip() or state.user_id
        today_delta = self._get_today_delta(state.user_id)
        score_text = f"{float(state.affinity_score):.0f}"
        if today_delta != 0:
            score_text += f" (今日{today_delta:+.0f})"
        lines = [
            f"用户: {display_user}",
            f"好感度: {score_text}",
            f"关系阶段: {state.effective_stage}",
        ]
        progress = calculate_stage_progress(
            float(state.affinity_score or 0),
            getattr(state, "unlocked_stage", None) or state.effective_stage,
        )
        if progress["next_stage"]:
            lines.append(
                f"距离 {progress['next_stage']}: "
                f"{progress['score_needed']}分 "
                f"(预计{progress['days_estimate']}天)"
            )
        return "\n".join(lines)

    def _get_today_delta(self, user_id: str) -> float:
        from datetime import date
        counter = self.service.db.get_or_create_daily_counter(user_id, date.today())
        return (
            float(counter.realtime_positive_delta or 0)
            + float(counter.negative_delta or 0)
            + float(counter.memory_recall_count or 0)
            + float(counter.profile_growth_count or 0)
        )

    async def handle_query(self, user_id: str, display_name: str | None = None) -> str:
        state = self.service.db.get_or_create_user_state(user_id)
        return self.format_query(state, display_name=display_name)

    async def handle_set_score(self, user_id: str, score: float) -> str:
        state = self.service.set_score(user_id, float(score))
        return f"已设置 {user_id} 好感为 {float(state.affinity_score):.0f}。"

    async def handle_set_relationship(self, user_id: str, stage: str) -> str:
        state = self.service.set_relationship(user_id, stage)
        return (
            f"已设置 {user_id} 关系阶段为 {state.effective_stage}；"
            f"confirmed_stage={state.confirmed_stage or '未确认'}；"
            f"lover_locked={bool(state.lover_locked)}。"
        )

    async def handle_set_max_stage_custom_prompt(self, user_id: str, prompt: str) -> str:
        cleaned = str(prompt or "").strip()
        if not cleaned:
            return "请输入要设置的提示词。"
        self.service.set_max_stage_custom_prompt(user_id, cleaned)
        return "已设置你的最高阶段自定义提示词。"

    async def handle_clear_max_stage_custom_prompt(self, user_id: str) -> str:
        self.service.set_max_stage_custom_prompt(user_id, "")
        return "已清除你的最高阶段自定义提示词。"

    async def handle_reset(self, user_id: str) -> str:
        self.service.reset_user(user_id)
        return f"已重置 {user_id} 的好感数据。"

    async def handle_reset_all(self, confirmation: str = "") -> str:
        if str(confirmation or "").strip().lower() not in {"confirm", "确认"}:
            return "此操作会重置所有人的好感数据。请使用 /重置所有人好感 confirm 或 /重置所有人好感 确认 继续。"
        count = self.service.reset_all_users()
        return f"已重置所有人的好感数据，共 {count} 个用户。"

    async def handle_events(self, user_id: str, limit: int = 10) -> str:
        events = self.service.get_recent_events(user_id, limit)
        if not events:
            return "暂无好感事件。"
        lines = ["最近好感事件:"]
        for event in events:
            ref = event.source_ref or event.message_id or "-"
            lines.append(
                f"- #{event.id} {event.event_type} {float(event.score_delta):+g} {ref} {event.reason or ''}"
            )
        return "\n".join(lines)

    async def handle_daily_review(
        self,
        user_id: str,
        event_date: date | str | None = None,
        dry_run: bool = False,
        force_rebuild: bool = False,
    ) -> str:
        if self.daily_review_service is None:
            return "每日回顾服务未初始化。"
        result = await self.daily_review_service.run_for_user(
            user_id,
            event_date,
            dry_run=dry_run,
            force_rebuild=force_rebuild,
        )
        mode = "预览" if dry_run else "已写入" if result.written else "已跳过"
        if dry_run:
            lines = [
                f"每日回顾 {mode}:",
                f"会话片段: {result.session_count}",
                f"候选事件: {len(result.events)}",
                f"原始信号: {result.raw_signal_count}",
                f"有效信号: {result.valid_signal_count}",
                f"丢弃信号: {result.discarded_signal_count}",
                f"真实分变化: {result.total_delta:+g}",
            ]
            if result.current_stage:
                lines.append(f"当前阶段: {result.current_stage}")
            if result.next_advance_stage:
                lines.append(f"次日推进: {result.next_advance_stage}")
            if result.fallback_used:
                lines.append("回退: 已使用硬规则")
            return "\n".join(lines)
        return f"每日回顾 {mode}: {len(result.events)} 个候选事件，预计 {result.total_delta:+g}。"

    async def handle_migration(self, user_id: str | None = None, force: bool = False) -> str:
        if self.migration_service is None:
            return "迁移服务未初始化。"
        if user_id:
            result = await self.migration_service.migrate_user(user_id, force=force)
            status = "已迁移" if result.migrated else "已跳过"
            return f"{status} {user_id}: {result.score:g}。"
        results = await self.migration_service.migrate_all(force=force)
        migrated = sum(1 for item in results if item.migrated)
        return f"迁移完成：{migrated}/{len(results)} 个用户已迁移。"
