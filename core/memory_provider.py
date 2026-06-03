from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol, runtime_checkable


@runtime_checkable
class AffinityMemoryProviderProtocol(Protocol):
    async def get_user_profile(self, user_id: str) -> dict: ...

    async def get_profile_delta(self, user_id: str, date) -> dict: ...

    async def get_interaction_stats(self, user_id: str, date) -> dict: ...

    async def get_daily_summary(self, user_id: str, date) -> dict: ...

    async def get_memory_refs(self, user_id: str, date, limit: int = 5) -> list[dict]: ...

    async def get_memory_count(self, user_id: str) -> int: ...

    async def get_legacy_bond_snapshot(self, user_id: str) -> dict | None: ...

    async def get_all_known_user_ids(self) -> list[str]: ...


REQUIRED_PROVIDER_METHODS = (
    "get_user_profile",
    "get_profile_delta",
    "get_interaction_stats",
    "get_daily_summary",
    "get_memory_refs",
    "get_memory_count",
    "get_legacy_bond_snapshot",
    "get_all_known_user_ids",
)


def provider_missing_methods(provider: object) -> list[str]:
    return [
        method_name
        for method_name in REQUIRED_PROVIDER_METHODS
        if not callable(getattr(provider, method_name, None))
    ]


def _iter_star_candidates(context: object) -> Iterable[object]:
    get_all_stars = getattr(context, "get_all_stars", None)
    if callable(get_all_stars):
        try:
            for item in get_all_stars() or []:
                star_cls = getattr(item, "star_cls", None)
                if star_cls is not None:
                    yield star_cls
                else:
                    yield item
        except Exception:
            pass

    star_manager = getattr(context, "star_manager", None)
    stars = getattr(star_manager, "stars", None)
    if isinstance(stars, dict):
        for item in stars.values():
            star_cls = getattr(item, "star_cls", None)
            yield star_cls if star_cls is not None else item
    elif stars is not None:
        for item in stars:
            star_cls = getattr(item, "star_cls", None)
            yield star_cls if star_cls is not None else item

    private_stars = getattr(context, "_stars", None)
    if isinstance(private_stars, dict):
        for item in private_stars.values():
            star_cls = getattr(item, "star_cls", None)
            yield star_cls if star_cls is not None else item
    elif private_stars is not None:
        for item in private_stars:
            star_cls = getattr(item, "star_cls", None)
            yield star_cls if star_cls is not None else item

    private_manager = getattr(context, "_star_manager", None)
    star_insts = getattr(private_manager, "star_insts", None)
    if isinstance(star_insts, dict):
        for item in star_insts.values():
            star_cls = getattr(item, "star_cls", None)
            yield star_cls if star_cls is not None else item
    elif star_insts is not None:
        for item in star_insts:
            star_cls = getattr(item, "star_cls", None)
            yield star_cls if star_cls is not None else item


def resolve_affinity_memory_provider(context: object) -> AffinityMemoryProviderProtocol | None:
    for star in _iter_star_candidates(context):
        provider = getattr(star, "affinity_memory_provider", None)
        if provider is None:
            continue
        if provider_missing_methods(provider):
            continue
        return provider
    return None


def diagnose_affinity_memory_provider(context: object) -> str:
    """枚举所有插件，说明为何没有解析出可用的 affinity_memory_provider。"""
    sentinel = object()
    seen: set[int] = set()
    scanned = 0
    complete: list[str] = []
    incomplete: list[str] = []
    none_valued: list[str] = []
    for star in _iter_star_candidates(context):
        if id(star) in seen:
            continue
        seen.add(id(star))
        scanned += 1
        provider = getattr(star, "affinity_memory_provider", sentinel)
        if provider is sentinel:
            continue
        name = getattr(star, "name", None) or type(star).__name__
        if provider is None:
            none_valued.append(name)
            continue
        missing = provider_missing_methods(provider)
        if missing:
            incomplete.append(f"{name} 缺少方法 {missing}")
        else:
            complete.append(name)
    parts = [f"已扫描 {scanned} 个插件实例"]
    if complete:
        parts.append(f"完整可用：{complete}")
    if incomplete:
        parts.append("；".join(incomplete))
    if none_valued:
        parts.append(
            f"属性存在但为 None（通常是 Engram 的 enable_profile_affinity 被关闭）：{none_valued}"
        )
    if not (complete or incomplete or none_valued):
        parts.append(
            "没有任何插件暴露 affinity_memory_provider 属性"
            "（Engram 未安装/未启用，或运行的是未实现该接口的旧版本）"
        )
    return "；".join(parts)
