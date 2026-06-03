from __future__ import annotations

import asyncio
from datetime import datetime, timedelta


class DailyReviewScheduler:
    def __init__(self, review_service, provider, hour: int = 4):
        self.review_service = review_service
        self.provider = provider
        self.hour = max(0, min(23, int(hour)))
        self.task: asyncio.Task | None = None
        self._stopped = False

    def start(self) -> asyncio.Task:
        if self.task is None or self.task.done():
            self._stopped = False
            self.task = asyncio.create_task(self._run_loop())
        return self.task

    async def cancel(self) -> None:
        self._stopped = True
        if self.task is not None and not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass

    async def _run_loop(self) -> None:
        while not self._stopped:
            await asyncio.sleep(self._seconds_until_next_run())
            if self._stopped:
                return
            user_ids = await self.provider.get_all_known_user_ids()
            yesterday = (datetime.now().date() - timedelta(days=1)).isoformat()
            for user_id in user_ids:
                await self.review_service.run_for_user(user_id, yesterday)

    def _seconds_until_next_run(self) -> float:
        now = datetime.now()
        target = now.replace(hour=self.hour, minute=0, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return max(1.0, (target - now).total_seconds())
