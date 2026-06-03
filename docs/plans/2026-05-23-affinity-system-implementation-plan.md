# 好感度系统实现计划

> **给 agentic workers：** 必须使用子技能 `superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans`，按任务逐项实现本计划。所有步骤使用 checkbox（`- [ ]`）语法跟踪进度。

**目标：** 将 `astrbot_plugin_affinity` 实现为一个 AstrBot 插件，用于维护全局按用户保存的好感度、关系阶段、确认/锁档状态、事件审计日志、LLM 关系风格注入、每日回顾结算，以及从 `astrbot_plugin_engram` 旧羁绊系统迁移初始数据。

**架构：** `main.py` 保持为轻量 AstrBot 路由层。确定性的领域逻辑放在纯服务模块中，SQLite 持久化放在 repository 风格的 DB 模块中，命令输出放在 handlers 中，记忆插件集成通过 `AffinityMemoryProvider` 适配器隔离。先实现可以单元测试的纯 Python 模块，再接入 AstrBot 装饰器和运行时钩子。

**技术栈：** Python 3、AstrBot 插件 API、Peewee + SQLite、pytest、现有 `astrbot_plugin_engram` 记忆/画像 API。

---

## Goal 拆分执行索引

本计划已拆分为 4 个可独立执行和验收的 Goal。后续执行时优先打开对应 Goal 文档，不建议直接从本总计划一次性全量实现。

| 顺序 | Goal 文档 | 覆盖任务 | 验收重点 |
|---:|---|---|---|
| 1 | [Goal 1：插件骨架与纯规则核心](2026-05-23-affinity-goal-1-core-foundation.md) | 任务 1-3 | 插件可导入；阶段派生、锁档派生、幂等键和日上限纯函数测试通过 |
| 2 | [Goal 2：SQLite 持久化闭环](2026-05-23-affinity-goal-2-persistence.md) | 任务 4 | `affinity.db` 表结构、事件幂等写入、状态更新测试通过 |
| 3 | [Goal 3：Engram 接入、实时事件、关系确认与命令](2026-05-23-affinity-goal-3-engram-commands-confirmation.md) | 任务 5-9 | `engram` 只读 Provider、启动依赖、实时事件、关系确认状态机和命令测试通过 |
| 4 | [Goal 4：LLM 注入、每日回顾、迁移与回归](2026-05-23-affinity-goal-4-injection-review-migration.md) | 任务 10-13 | LLM 注入不泄露字段；每日回顾幂等；旧羁绊迁移保守；最终回归通过 |

推荐执行策略：

1. 每次只执行一个 Goal。
2. 每个 Goal 完成后运行该 Goal 文档中的汇总验证命令。
3. 只有当前 Goal 验证通过后，才进入下一个 Goal。
4. 若实现中发现设计文档需要调整，先更新 `docs/specs/2026-05-23-affinity-system-design.md`，再同步更新受影响的 Goal 文档。

---

## 来源规格

- 设计文档：`astrbot_plugin_affinity/docs/specs/2026-05-23-affinity-system-design.md`
- 相关现有插件：`astrbot_plugin_engram`
- 需要参考的现有模式：
  - `astrbot_plugin_engram/main.py`：使用 `@register`、`@filter.command`、`@filter.on_llm_request`、`@filter.after_message_sent` 的轻量路由层。
  - `astrbot_plugin_engram/db_manager.py`：绑定到插件专属 SQLite DB 的 Peewee models。
  - `astrbot_plugin_engram/services/bond_calculator.py`：旧羁绊公式，迁移时使用。
  - `astrbot_plugin_engram/core/memory_facade.py`：记忆和画像门面方法。

---

## 文件结构

在 `astrbot_plugin_affinity/` 中创建：

- `__init__.py`：包标记。
- `metadata.yaml`：AstrBot 插件元数据。
- `_conf_schema.json`：插件配置 schema。
- `requirements.txt`：运行/测试依赖列表，初始为 `peewee>=3.16`。
- `main.py`：AstrBot 插件入口、命令装饰器、LLM 钩子、调度器生命周期。
- `db_manager.py`：Peewee models 和 repository 风格数据库访问。
- `core/__init__.py`：core 模块导出。
- `core/models.py`：用户状态、事件、运行时状态、每日计数器的枚举与 dataclass。
- `core/stage.py`：分数 clamp、`stage` / `effective_stage` 派生。
- `core/rules.py`：事件分值、日上限、幂等键辅助函数。
- `core/confirmation.py`：关系确认状态机和 Bot 主动确认决策。
- `core/injection.py`：自然语言关系风格提示词构建器。
- `core/memory_provider.py`：`AffinityMemoryProviderProtocol`、空/假 Provider、Provider 解析器。
- `services/__init__.py`：services 模块导出。
- `services/affinity_service.py`：事件、状态更新、实时互动编排。
- `services/daily_review_service.py`：每日回顾 dry-run、正式写入、强制重算。
- `services/migration_service.py`：旧羁绊迁移公式、备份、回滚边界。
- `services/scheduler.py`：每日回顾后台任务包装。
- `handlers/__init__.py`：handlers 模块导出。
- `handlers/commands.py`：管理命令格式化和参数解析。
- `tests/conftest.py`：临时 DB 和假 Provider fixtures。
- `tests/test_stage.py`
- `tests/test_rules.py`
- `tests/test_db_manager.py`
- `tests/test_confirmation.py`
- `tests/test_injection.py`
- `tests/test_daily_review.py`
- `tests/test_migration.py`
- `tests/test_commands.py`
- `tests/test_plugin_startup.py`

在 `astrbot_plugin_engram/` 中修改：

- `core/affinity_provider.py`：暴露只读记忆/画像/统计适配器。
- `core/__init__.py`：导出 `AffinityMemoryProvider`。
- `main.py`：把 provider 实例挂到公开属性，例如 `self.affinity_memory_provider`。
- `tests/test_affinity_provider.py`：Provider 契约测试。

不要修改 `engram` 的存储布局，也不要把好感数据写入 `engram_memories.db`、`engram_personas/` 或 ChromaDB。

---

## 任务 1：搭建插件元数据和配置骨架

**文件：**
- 创建：`astrbot_plugin_affinity/__init__.py`
- 创建：`astrbot_plugin_affinity/metadata.yaml`
- 创建：`astrbot_plugin_affinity/requirements.txt`
- 创建：`astrbot_plugin_affinity/_conf_schema.json`
- 创建：`astrbot_plugin_affinity/main.py`
- 测试：`astrbot_plugin_affinity/tests/test_plugin_startup.py`

- [ ] **步骤 1：编写启动导入失败测试**

```python
def test_plugin_module_imports():
    import astrbot_plugin_affinity.main as main

    assert hasattr(main, "AffinityPlugin")
```

- [ ] **步骤 2：运行测试并确认失败**

运行：`python -m pytest astrbot_plugin_affinity/tests/test_plugin_startup.py -q`

预期：失败，因为 `astrbot_plugin_affinity.main` 或 `AffinityPlugin` 尚不存在。

- [ ] **步骤 3：创建最小插件文件**

`metadata.yaml` 内容：

```yaml
name: astrbot_plugin_affinity
desc: 好感度与关系系统插件
help: 使用 /好感查询 查看关系状态
version: 0.1.0
author: victical
repo:
```

`requirements.txt`：

```text
peewee>=3.16
```

`main.py` 最小形态：

```python
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register


@register("astrbot_plugin_affinity", "victical", "好感度与关系系统插件", "0.1.0")
class AffinityPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        self.context = context
        self.config = config if config is not None else {}
        self.enabled = True

    async def terminate(self):
        self.enabled = False
```

`_conf_schema.json` 需要包含设计文档中的默认值：

```json
{
  "enable_affinity": {
    "description": "启用好感度系统",
    "type": "bool",
    "default": true
  },
  "affinity_group_full_expression": {
    "description": "群聊是否完整表现当前关系阶段",
    "type": "bool",
    "default": true
  },
  "affinity_group_soften_romance": {
    "description": "群聊是否弱化暧昧/恋人表达",
    "type": "bool",
    "default": false
  },
  "affinity_group_allow_proactive_confirm": {
    "description": "群聊是否允许 Bot 主动推进确认关系",
    "type": "bool",
    "default": false
  },
  "affinity_private_allow_proactive_confirm": {
    "description": "私聊是否允许 Bot 主动推进确认关系",
    "type": "bool",
    "default": true
  },
  "daily_review_hour": {
    "description": "每日回顾执行小时，0-23",
    "type": "int",
    "default": 4
  }
}
```

- [ ] **步骤 4：运行启动测试**

运行：`python -m pytest astrbot_plugin_affinity/tests/test_plugin_startup.py -q`

预期：通过。

- [ ] **步骤 5：提交**

```bash
git -C astrbot_plugin_affinity add __init__.py metadata.yaml requirements.txt _conf_schema.json main.py tests/test_plugin_startup.py
git -C astrbot_plugin_affinity commit -m "feat: scaffold affinity plugin"
```

说明：如果 `astrbot_plugin_affinity` 不是 Git 仓库，跳过提交，并在进度记录中说明。

---

## 任务 2：实现阶段规则和领域模型

**文件：**
- 创建：`astrbot_plugin_affinity/core/__init__.py`
- 创建：`astrbot_plugin_affinity/core/models.py`
- 创建：`astrbot_plugin_affinity/core/stage.py`
- 测试：`astrbot_plugin_affinity/tests/test_stage.py`

- [ ] **步骤 1：编写阶段规则失败测试**

```python
from astrbot_plugin_affinity.core.models import RelationshipStage
from astrbot_plugin_affinity.core.stage import clamp_score, score_stage, effective_stage


def test_clamp_score_bounds():
    assert clamp_score(-999) == -100
    assert clamp_score(999) == 520


def test_score_stage_boundaries():
    assert score_stage(-100) == RelationshipStage.COLD_WAR
    assert score_stage(-31) == RelationshipStage.DISLIKE
    assert score_stage(-1) == RelationshipStage.DISTANT
    assert score_stage(0) == RelationshipStage.STRANGER
    assert score_stage(50) == RelationshipStage.FRIEND
    assert score_stage(150) == RelationshipStage.CLOSE_FRIEND
    assert score_stage(300) == RelationshipStage.AMBIGUOUS
    assert score_stage(440) == RelationshipStage.LOVER_CANDIDATE
    assert score_stage(520) == RelationshipStage.LOVER_CANDIDATE


def test_effective_stage_lover_lock():
    assert effective_stage(120, confirmed_stage="恋人", lover_locked=True) == RelationshipStage.LOVER
    assert effective_stage(120, confirmed_stage=None, lover_locked=False) == RelationshipStage.FRIEND
```

- [ ] **步骤 2：运行测试并确认失败**

运行：`python -m pytest astrbot_plugin_affinity/tests/test_stage.py -q`

预期：失败，因为模块尚不存在。

- [ ] **步骤 3：实现枚举和阶段函数**

在 `core/models.py` 中创建 `RelationshipStage`、`ConfirmationStatus`、`ConfirmationInitiator`、`AffinityEventType`、`Mood` 枚举。用户可见阶段使用设计文档中的中文值，内部事件/状态使用稳定英文值。

`core/stage.py` 必须实现：

```python
def clamp_score(score: float) -> float: ...
def score_stage(score: float) -> RelationshipStage: ...
def effective_stage(score: float, confirmed_stage: str | None, lover_locked: bool) -> RelationshipStage: ...
```

关键规则：`520` 仍然只是 `LOVER_CANDIDATE`；只有 `lover_locked` 且 `confirmed_stage == "恋人"` 时才是 `LOVER`。

- [ ] **步骤 4：运行阶段测试**

运行：`python -m pytest astrbot_plugin_affinity/tests/test_stage.py -q`

预期：通过。

- [ ] **步骤 5：提交**

```bash
git -C astrbot_plugin_affinity add core tests/test_stage.py
git -C astrbot_plugin_affinity commit -m "feat: add affinity stage model"
```

---

## 任务 3：实现规则引擎和幂等辅助函数

**文件：**
- 创建：`astrbot_plugin_affinity/core/rules.py`
- 测试：`astrbot_plugin_affinity/tests/test_rules.py`

- [ ] **步骤 1：编写规则失败测试**

```python
from datetime import date

from astrbot_plugin_affinity.core.rules import (
    daily_key,
    message_key,
    migration_key,
    apply_daily_cap,
)


def test_idempotency_key_shapes():
    assert message_key("m1", "daily_chat") == "message:m1:daily_chat"
    assert daily_key("u1", date(2026, 5, 23), "daily_review") == "daily:u1:2026-05-23:daily_review"
    assert migration_key("u1", "v1") == "migration:u1:v1"


def test_daily_positive_cap():
    assert apply_daily_cap(current_delta=18, requested_delta=10, cap=20) == 2
```

- [ ] **步骤 2：运行测试并确认失败**

运行：`python -m pytest astrbot_plugin_affinity/tests/test_rules.py -q`

预期：失败，因为 `core.rules` 尚不存在。

- [ ] **步骤 3：实现最小规则辅助函数**

实现幂等键构建器和日上限：

- 实时正向默认上限：`20`
- 每日回顾正向默认上限：`25`
- 单日正向总上限：`35`
- 负向默认上限：`-60`
- 记忆命中默认上限：`5`
- 画像新增默认上限：`12`

这些规则保持为常量和纯函数。此任务不连接数据库。

- [ ] **步骤 4：运行规则测试**

运行：`python -m pytest astrbot_plugin_affinity/tests/test_rules.py -q`

预期：通过。

- [ ] **步骤 5：提交**

```bash
git -C astrbot_plugin_affinity add core/rules.py tests/test_rules.py
git -C astrbot_plugin_affinity commit -m "feat: add affinity rule helpers"
```

---

## 任务 4：实现 SQLite 持久化

**文件：**
- 创建：`astrbot_plugin_affinity/db_manager.py`
- 修改：`astrbot_plugin_affinity/core/models.py`
- 测试：`astrbot_plugin_affinity/tests/conftest.py`
- 测试：`astrbot_plugin_affinity/tests/test_db_manager.py`

- [ ] **步骤 1：编写数据库失败测试**

```python
def test_db_creates_default_user_state(temp_affinity_db):
    state = temp_affinity_db.get_or_create_user_state("u1")

    assert state.user_id == "u1"
    assert state.affinity_score == 0
    assert state.stage == "初识"
    assert state.effective_stage == "初识"


def test_idempotent_event_applies_once(temp_affinity_db):
    first = temp_affinity_db.apply_score_event(
        user_id="u1",
        event_type="daily_chat",
        score_delta=6,
        reason="first chat",
        source="message",
        idempotency_key="message:m1:daily_chat",
    )
    second = temp_affinity_db.apply_score_event(
        user_id="u1",
        event_type="daily_chat",
        score_delta=6,
        reason="duplicate",
        source="message",
        idempotency_key="message:m1:daily_chat",
    )

    assert first.applied is True
    assert second.applied is False
    assert temp_affinity_db.get_user_state("u1").affinity_score == 6
```

- [ ] **步骤 2：运行测试并确认失败**

运行：`python -m pytest astrbot_plugin_affinity/tests/test_db_manager.py -q`

预期：失败，因为 DB manager 尚不存在。

- [ ] **步骤 3：实现 Peewee models**

创建表：

- `AffinityUserState`
- `AffinityEvent`
- `AffinityRuntimeState`
- `AffinityDailyCounter`

创建 `AffinityDatabaseManager` 类，并参考 `astrbot_plugin_engram/db_manager.py` 为每个实例绑定 Peewee models。

必需方法：

```python
get_or_create_user_state(user_id: str)
get_user_state(user_id: str)
apply_score_event(...)
insert_event(...)
get_recent_events(user_id: str, limit: int = 10)
get_or_create_runtime_state(user_id: str)
update_runtime_state(user_id: str, **fields)
get_or_create_daily_counter(user_id: str, event_date)
update_daily_counter(user_id: str, event_date, **fields)
reset_user(user_id: str)
```

`apply_score_event` 必须在事务中执行，强制唯一 `idempotency_key`，clamp 分数，重新计算 `stage` 与 `effective_stage`，然后更新用户主状态。

- [ ] **步骤 4：运行数据库测试**

运行：`python -m pytest astrbot_plugin_affinity/tests/test_db_manager.py -q`

预期：通过。

- [ ] **步骤 5：提交**

```bash
git -C astrbot_plugin_affinity add db_manager.py core/models.py tests/conftest.py tests/test_db_manager.py
git -C astrbot_plugin_affinity commit -m "feat: add affinity sqlite persistence"
```

---

## 任务 5：在 Engram 中暴露只读 Affinity Provider

**文件：**
- 创建：`astrbot_plugin_engram/core/affinity_provider.py`
- 修改：`astrbot_plugin_engram/core/__init__.py`
- 修改：`astrbot_plugin_engram/main.py`
- 测试：`astrbot_plugin_engram/tests/test_affinity_provider.py`

- [ ] **步骤 1：编写 Provider 契约失败测试**

使用假的 `MemoryFacade` / DB 对象，不依赖真实 ChromaDB。

```python
import datetime as dt

from astrbot_plugin_engram.core.affinity_provider import AffinityMemoryProvider


class FakeLogic:
    async def get_user_profile(self, user_id):
        return {"user_id": user_id, "preferences": {"likes": ["tea"]}}


class FakeDB:
    def get_message_stats(self, user_id):
        return {"total_messages": 10}

    def get_summaries_by_type(self, user_id, source_type, days=7):
        return []

    def get_memory_list(self, user_id, limit=5):
        return []

    def get_all_user_ids(self):
        return ["u1"]


async def test_provider_get_user_profile():
    provider = AffinityMemoryProvider(FakeLogic(), FakeDB(), bond_calculator=None)

    assert await provider.get_user_profile("u1") == {"user_id": "u1", "preferences": {"likes": ["tea"]}}
```

- [ ] **步骤 2：运行测试并确认失败**

运行：`python -m pytest astrbot_plugin_engram/tests/test_affinity_provider.py -q`

预期：失败，因为 Provider 尚不存在。

- [ ] **步骤 3：实现 Provider**

实现设计文档中的 async 方法：

```python
get_user_profile(user_id)
get_profile_delta(user_id, date)
get_interaction_stats(user_id, date)
get_daily_summary(user_id, date)
get_memory_refs(user_id, date, limit)
get_memory_count(user_id)
get_legacy_bond_snapshot(user_id)
get_all_known_user_ids()
```

对于 `engram` 暂时没有精确数据源的方法，返回保守且可序列化的结果：

- 缺失画像 delta：返回带 `available=False` 的空 dict。
- 每日总结：优先使用 `get_summaries_by_type()`；不可用时返回空 summary。
- 记忆数量：只有在 `get_memory_list(user_id, limit=large)` 可靠时才推导；否则返回 `0` 并在 metadata 中标明来源。

- [ ] **步骤 4：在 `EngramPlugin.__init__` 中挂载 Provider**

在 `self.logic` 初始化后：

```python
from .core import AffinityMemoryProvider
from .services import BondCalculator

self.affinity_memory_provider = AffinityMemoryProvider(
    logic=self.logic,
    db=self.logic.db,
    bond_calculator=BondCalculator(),
)
```

不要让 `engram` 依赖 `astrbot_plugin_affinity`。

- [ ] **步骤 5：运行 Provider 测试**

运行：`python -m pytest astrbot_plugin_engram/tests/test_affinity_provider.py -q`

预期：通过。

- [ ] **步骤 6：提交**

```bash
git -C astrbot_plugin_engram add core/affinity_provider.py core/__init__.py main.py tests/test_affinity_provider.py
git -C astrbot_plugin_engram commit -m "feat: expose affinity memory provider"
```

---

## 任务 6：在 Affinity 插件启动时解析 Provider

**文件：**
- 创建：`astrbot_plugin_affinity/core/memory_provider.py`
- 修改：`astrbot_plugin_affinity/main.py`
- 测试：`astrbot_plugin_affinity/tests/test_plugin_startup.py`

- [ ] **步骤 1：编写 Provider 解析失败测试**

```python
from astrbot_plugin_affinity.core.memory_provider import resolve_affinity_memory_provider


class EngramStar:
    affinity_memory_provider = object()


class FakeContext:
    def get_all_stars(self):
        return [EngramStar()]


def test_resolve_provider_from_loaded_star():
    provider = resolve_affinity_memory_provider(FakeContext())

    assert provider is EngramStar.affinity_memory_provider
```

- [ ] **步骤 2：运行测试并确认失败**

运行：`python -m pytest astrbot_plugin_affinity/tests/test_plugin_startup.py -q`

预期：失败，因为 resolver 尚不存在。

- [ ] **步骤 3：实现 Protocol 和 resolver**

定义 `AffinityMemoryProviderProtocol`，包含必需方法。resolver 按顺序尝试：

1. `context.get_all_stars()`，如果存在。
2. `context.star_manager.stars`，如果存在。
3. `context._stars`，如果存在。
4. `context._star_manager.star_insts`，如果存在。

返回第一个带 `affinity_memory_provider` 的对象。如果缺失必需方法，则返回 `None` 和原因。

- [ ] **步骤 4：接入启动行为**

在 `AffinityPlugin.__init__` 中：

- 解析 Provider。
- 如果 Provider 缺失且 `enable_affinity` 为 true，则设置 `self.enabled = False` 并输出明确 warning。
- 只有 Provider 存在时才初始化 DB。

- [ ] **步骤 5：运行启动测试**

运行：`python -m pytest astrbot_plugin_affinity/tests/test_plugin_startup.py -q`

预期：通过，包含“缺失 Provider 时插件禁用”的测试。

- [ ] **步骤 6：提交**

```bash
git -C astrbot_plugin_affinity add core/memory_provider.py main.py tests/test_plugin_startup.py
git -C astrbot_plugin_affinity commit -m "feat: require engram affinity provider"
```

---

## 任务 7：实现实时事件 Affinity Service

**文件：**
- 创建：`astrbot_plugin_affinity/services/__init__.py`
- 创建：`astrbot_plugin_affinity/services/affinity_service.py`
- 修改：`astrbot_plugin_affinity/main.py`
- 测试：`astrbot_plugin_affinity/tests/test_rules.py`

- [ ] **步骤 1：编写 service 失败测试**

```python
async def test_first_daily_interaction_adds_once(temp_affinity_db):
    service = AffinityService(temp_affinity_db)

    await service.record_daily_chat(user_id="u1", message_id="m1", event_date="2026-05-23")
    await service.record_daily_chat(user_id="u1", message_id="m1", event_date="2026-05-23")

    assert temp_affinity_db.get_user_state("u1").affinity_score == 6
```

- [ ] **步骤 2：运行测试并确认失败**

运行：`python -m pytest astrbot_plugin_affinity/tests/test_rules.py -q`

预期：失败，因为 `AffinityService` 尚不存在。

- [ ] **步骤 3：实现 service 方法**

方法：

```python
record_daily_chat(user_id, message_id, event_date)
record_memory_recall(user_id, message_id, memory_id, event_date)
record_profile_growth(user_id, source_ref, count, event_date)
record_negative_event(user_id, event_type, reason, source_ref, delta, event_date)
get_user_snapshot(user_id)
```

所有方法必须通过 `AffinityDatabaseManager.apply_score_event()` 写入。

- [ ] **步骤 4：接入被动消息钩子**

在 `main.py` 中添加私聊/群聊消息处理器，为有效用户消息记录 daily chat。保持最小实现；此阶段不做 LLM 情感检测。

- [ ] **步骤 5：运行 service 测试**

运行：`python -m pytest astrbot_plugin_affinity/tests/test_rules.py astrbot_plugin_affinity/tests/test_db_manager.py -q`

预期：通过。

- [ ] **步骤 6：提交**

```bash
git -C astrbot_plugin_affinity add services main.py tests/test_rules.py
git -C astrbot_plugin_affinity commit -m "feat: add realtime affinity events"
```

---

## 任务 8：实现关系确认状态机

**文件：**
- 创建：`astrbot_plugin_affinity/core/confirmation.py`
- 修改：`astrbot_plugin_affinity/services/affinity_service.py`
- 测试：`astrbot_plugin_affinity/tests/test_confirmation.py`

- [ ] **步骤 1：编写关系确认失败测试**

```python
from astrbot_plugin_affinity.core.confirmation import decide_confirmation_transition
from astrbot_plugin_affinity.core.models import ConfirmationStatus


def test_bot_confirm_request_creates_pending_only():
    result = decide_confirmation_transition(
        status=ConfirmationStatus.NONE,
        trigger="bot_proactive",
        score=460,
        effective_stage="恋人候选",
        scene_allows_proactive=True,
        rejected_cooldown_active=False,
    )

    assert result.next_status == ConfirmationStatus.PENDING
    assert result.confirmed_stage is None
    assert result.lover_locked is False
    assert result.initiator == "bot"


def test_user_accept_locks_lover():
    result = decide_confirmation_transition(
        status=ConfirmationStatus.PENDING,
        trigger="user_accept",
        score=460,
        effective_stage="恋人候选",
        scene_allows_proactive=True,
        rejected_cooldown_active=False,
    )

    assert result.next_status == ConfirmationStatus.CONFIRMED
    assert result.confirmed_stage == "恋人"
    assert result.lover_locked is True
```

- [ ] **步骤 2：运行测试并确认失败**

运行：`python -m pytest astrbot_plugin_affinity/tests/test_confirmation.py -q`

预期：失败，因为状态机尚不存在。

- [ ] **步骤 3：实现状态机**

实现设计文档中的流转规则：

- `none + user_query + conditions` -> `pending`，initiator 为 `user`
- `none + bot_proactive + conditions` -> `pending`，initiator 为 `bot`
- `pending + user_accept` -> `confirmed`，锁定恋人
- `pending + user_reject` -> `rejected`
- `pending + expired` -> `none`
- `rejected + cooldown_end` -> `none`
- `confirmed + any normal interaction` -> `confirmed`

Bot 主动确认不得设置 `confirmed_stage`。

- [ ] **步骤 4：添加 service 方法**

添加：

```python
request_confirmation(user_id, initiator, scene, now)
accept_confirmation(user_id, now)
reject_confirmation(user_id, reason, now)
expire_confirmation(user_id, now)
```

写入 `relationship_confirm_requested`、`relationship_confirmed` 和 `rejection` 事件作为审计记录。

- [ ] **步骤 5：运行确认测试**

运行：`python -m pytest astrbot_plugin_affinity/tests/test_confirmation.py astrbot_plugin_affinity/tests/test_db_manager.py -q`

预期：通过。

- [ ] **步骤 6：提交**

```bash
git -C astrbot_plugin_affinity add core/confirmation.py services/affinity_service.py tests/test_confirmation.py
git -C astrbot_plugin_affinity commit -m "feat: add relationship confirmation state machine"
```

---

## 任务 9：实现命令 Handler

**文件：**
- 创建：`astrbot_plugin_affinity/handlers/__init__.py`
- 创建：`astrbot_plugin_affinity/handlers/commands.py`
- 修改：`astrbot_plugin_affinity/main.py`
- 测试：`astrbot_plugin_affinity/tests/test_commands.py`

- [ ] **步骤 1：编写命令格式化失败测试**

```python
def test_query_command_includes_admin_fields(fake_state):
    handler = AffinityCommandHandler(service=FakeService(fake_state))

    text = handler.format_query(fake_state)

    assert "好感" in text
    assert "effective_stage" in text
    assert "lover_locked" in text
```

- [ ] **步骤 2：运行测试并确认失败**

运行：`python -m pytest astrbot_plugin_affinity/tests/test_commands.py -q`

预期：失败，因为 handler 尚不存在。

- [ ] **步骤 3：实现 handler 方法**

方法：

```python
handle_query(user_id)
handle_set_score(user_id, score)
handle_set_relationship(user_id, stage)
handle_reset(user_id)
handle_events(user_id, limit=10)
handle_daily_review(user_id, date=None, dry_run=False, force_rebuild=False)
handle_migration(user_id=None, force=False)
```

管理命令输出可以包含内部字段。普通聊天输出不得包含内部字段。

- [ ] **步骤 4：接入 AstrBot 命令**

在 `main.py` 中添加：

- `/好感查询 [user_id]`
- `/好感设置 <user_id> <score>` 管理员
- `/关系设置 <user_id> <stage>` 管理员
- `/好感重置 <user_id>` 管理员
- `/好感事件 <user_id>` 管理员
- `/好感回顾 <user_id> [date]` 管理员
- `/好感迁移 [user_id]` 管理员

对变更类命令使用 `@filter.permission_type(filter.PermissionType.ADMIN)`。

- [ ] **步骤 5：运行命令测试**

运行：`python -m pytest astrbot_plugin_affinity/tests/test_commands.py -q`

预期：通过。

- [ ] **步骤 6：提交**

```bash
git -C astrbot_plugin_affinity add handlers main.py tests/test_commands.py
git -C astrbot_plugin_affinity commit -m "feat: add affinity management commands"
```

---

## 任务 10：实现 LLM 关系风格注入

**文件：**
- 创建：`astrbot_plugin_affinity/core/injection.py`
- 修改：`astrbot_plugin_affinity/main.py`
- 测试：`astrbot_plugin_affinity/tests/test_injection.py`

- [ ] **步骤 1：编写注入失败测试**

```python
from astrbot_plugin_affinity.core.injection import build_relationship_prompt


def test_prompt_does_not_leak_fields():
    prompt = build_relationship_prompt(
        effective_stage="暧昧",
        current_mood="平静",
        custom_nickname=None,
        is_group=False,
        soften_romance=False,
    )

    forbidden = ["好感度:", "关系:", "stage", "affinity_score"]
    assert not any(item in prompt for item in forbidden)
    assert "内部数值" in prompt
```

- [ ] **步骤 2：运行测试并确认失败**

运行：`python -m pytest astrbot_plugin_affinity/tests/test_injection.py -q`

预期：失败，因为 injection 模块尚不存在。

- [ ] **步骤 3：实现提示词构建器**

为以下阶段实现自然语言提示模板：

- 冷战
- 反感
- 疏离
- 初识
- 朋友
- 挚友
- 暧昧
- 恋人候选
- 恋人

硬性要求：最终提示词不得包含原始分数、数据库字段名、幂等键或结构化标签。

- [ ] **步骤 4：接入 `@filter.on_llm_request(priority=-1)`**

在 `main.py` 中：

1. 获取 `user_id = event.get_sender_id()`。
2. 从 service 获取状态快照。
3. 根据群聊/私聊配置构建提示词。
4. 追加到 `req.system_prompt`；如果已有和 `engram` 相同的注入目标模式，则复用该模式。

- [ ] **步骤 5：运行注入测试**

运行：`python -m pytest astrbot_plugin_affinity/tests/test_injection.py -q`

预期：通过。

- [ ] **步骤 6：提交**

```bash
git -C astrbot_plugin_affinity add core/injection.py main.py tests/test_injection.py
git -C astrbot_plugin_affinity commit -m "feat: inject relationship style prompt"
```

---

## 任务 11：实现每日回顾 Service

**文件：**
- 创建：`astrbot_plugin_affinity/services/daily_review_service.py`
- 创建：`astrbot_plugin_affinity/services/scheduler.py`
- 修改：`astrbot_plugin_affinity/main.py`
- 测试：`astrbot_plugin_affinity/tests/test_daily_review.py`

- [ ] **步骤 1：编写每日回顾失败测试**

```python
async def test_dry_run_does_not_write(temp_affinity_db, fake_provider):
    service = DailyReviewService(temp_affinity_db, fake_provider)

    result = await service.run_for_user("u1", "2026-05-23", dry_run=True)

    assert result.events
    assert temp_affinity_db.get_user_state("u1").affinity_score == 0


async def test_daily_review_is_idempotent(temp_affinity_db, fake_provider):
    service = DailyReviewService(temp_affinity_db, fake_provider)

    await service.run_for_user("u1", "2026-05-23")
    await service.run_for_user("u1", "2026-05-23")

    assert temp_affinity_db.get_user_state("u1").affinity_score <= 25
```

- [ ] **步骤 2：运行测试并确认失败**

运行：`python -m pytest astrbot_plugin_affinity/tests/test_daily_review.py -q`

预期：失败，因为 service 尚不存在。

- [ ] **步骤 3：实现每日回顾**

流程：

1. 读取画像变化。
2. 读取互动统计。
3. 读取每日总结。
4. 读取记忆引用。
5. 将确定性输入转换为候选事件。
6. 应用日上限和幂等键。
7. 除非 `dry_run`，否则写入事件并更新状态。

除非当前已有稳定的 provider 调用模式，否则先把 LLM 趋势分析延后为可选增强。第一版保持确定性、可测试。

- [ ] **步骤 4：实现强制重算**

`force_rebuild=True` 必须：

1. 将受影响的每日事件和用户状态备份到 `affinity_migration_backups/` 或 `affinity_logs/`。
2. 只反转该日期的每日回顾事件。
3. 重新计算。
4. 写入新事件。

- [ ] **步骤 5：接入调度器和手动命令**

添加调度任务，在配置的 `daily_review_hour` 执行。插件禁用或 Provider 缺失时跳过。`terminate()` 中需要干净取消任务。

- [ ] **步骤 6：运行每日回顾测试**

运行：`python -m pytest astrbot_plugin_affinity/tests/test_daily_review.py -q`

预期：通过。

- [ ] **步骤 7：提交**

```bash
git -C astrbot_plugin_affinity add services/daily_review_service.py services/scheduler.py main.py tests/test_daily_review.py
git -C astrbot_plugin_affinity commit -m "feat: add daily affinity review"
```

---

## 任务 12：实现旧羁绊迁移

**文件：**
- 创建：`astrbot_plugin_affinity/services/migration_service.py`
- 修改：`astrbot_plugin_affinity/handlers/commands.py`
- 测试：`astrbot_plugin_affinity/tests/test_migration.py`

- [ ] **步骤 1：编写迁移公式失败测试**

```python
from astrbot_plugin_affinity.services.migration_service import calculate_initial_affinity


def test_migration_never_exceeds_candidate_threshold():
    score = calculate_initial_affinity(
        memory_count=99999,
        old_days_score=25,
        profile_depth_pct=100,
        likes_count=20,
        dislikes_count=20,
        shared_secret=True,
        old_level=7,
    )

    assert score == 439


def test_level_floor_applies():
    score = calculate_initial_affinity(
        memory_count=0,
        old_days_score=0,
        profile_depth_pct=0,
        likes_count=0,
        dislikes_count=0,
        shared_secret=False,
        old_level=6,
    )

    assert score == 340
```

- [ ] **步骤 2：运行测试并确认失败**

运行：`python -m pytest astrbot_plugin_affinity/tests/test_migration.py -q`

预期：失败，因为 migration service 尚不存在。

- [ ] **步骤 3：实现迁移公式**

使用设计文档中的公式：

```text
memory_part = min(120, 120 * log(1 + memory_count / 150) / log(1 + 3000 / 150))
days_part = min(100, old_days_score / 25 * 100)
profile_part = min(100, profile_depth_pct)
preference_part = min(60, (likes_count + dislikes_count) * 6)
special_part = shared_secret ? 20 : 0
initial_affinity_score = min(439, max(formula_score, level_floor))
```

生成一条 `migration:{user_id}:v1` 事件。不得设置 `confirmed_stage` 或 `lover_locked`。

- [ ] **步骤 4：实现迁移 service**

方法：

```python
migrate_user(user_id, force=False)
migrate_all(force=False)
backup_current_state(user_id)
```

默认行为：如果 `affinity_user_state` 已存在，则跳过。

- [ ] **步骤 5：运行迁移测试**

运行：`python -m pytest astrbot_plugin_affinity/tests/test_migration.py -q`

预期：通过。

- [ ] **步骤 6：提交**

```bash
git -C astrbot_plugin_affinity add services/migration_service.py handlers/commands.py tests/test_migration.py
git -C astrbot_plugin_affinity commit -m "feat: add legacy bond migration"
```

---

## 任务 13：集成与回归检查

**文件：**
- 修改：`astrbot_plugin_affinity/README.md`
- 只有当实现发现设计需要修正时，才修改：`astrbot_plugin_affinity/docs/specs/2026-05-23-affinity-system-design.md`
- 测试：全部 `astrbot_plugin_affinity/tests/*.py`
- 测试：`astrbot_plugin_engram/tests/test_affinity_provider.py`

- [ ] **步骤 1：运行 affinity 单元测试套件**

运行：`python -m pytest astrbot_plugin_affinity/tests -q`

预期：通过。

- [ ] **步骤 2：运行 engram Provider 测试**

运行：`python -m pytest astrbot_plugin_engram/tests/test_affinity_provider.py -q`

预期：通过。

- [ ] **步骤 3：运行重点 engram 既有回归测试**

运行：`python -m pytest astrbot_plugin_engram/tests/test_plugin_lifecycle_regressions.py astrbot_plugin_engram/tests/test_profile_regressions.py astrbot_plugin_engram/tests/test_persistence_features.py -q`

预期：通过。

- [ ] **步骤 4：补充 README**

文档说明：

- 依赖 `astrbot_plugin_engram`。
- 可用命令。
- 数据文件：`affinity.db`、`affinity_logs/`、`affinity_migration_backups/`。
- 群聊/私聊 Bot 主动确认的默认配置。
- 普通聊天不会显示内部分数字段。

- [ ] **步骤 5：手动 AstrBot 冒烟检查清单**

如果本地有 AstrBot 实例，按以下流程检查：

1. 加载 `astrbot_plugin_engram`。
2. 加载 `astrbot_plugin_affinity`。
3. 发送一条私聊消息。
4. 执行 `/好感查询`。
5. 把分数设置为 `440`。
6. 在私聊中触发 Bot 主动确认关系。
7. 用户明确同意。
8. 确认查询结果显示 `confirmed_stage = 恋人`、`lover_locked = true`。
9. 随便问一个普通聊天问题，确认回复没有泄露 `好感度:` / `stage` / `affinity_score`。

- [ ] **步骤 6：最终验证**

运行：

```bash
python -m pytest astrbot_plugin_affinity/tests -q
python -m pytest astrbot_plugin_engram/tests/test_affinity_provider.py -q
```

预期：两者都通过。

- [ ] **步骤 7：提交**

```bash
git -C astrbot_plugin_affinity add README.md docs tests main.py core services handlers db_manager.py metadata.yaml _conf_schema.json requirements.txt
git -C astrbot_plugin_affinity commit -m "docs: add affinity usage guide"
```

如果 `astrbot_plugin_affinity` 不是 Git 仓库，跳过提交，并保留清晰的文件变更摘要。

---

## 复核关卡

实现开始前，按以下文件复核本计划：

- `astrbot_plugin_affinity/docs/specs/2026-05-23-affinity-system-design.md`
- `astrbot_plugin_engram/main.py`
- `astrbot_plugin_engram/db_manager.py`
- `astrbot_plugin_engram/core/memory_facade.py`
- `astrbot_plugin_engram/services/bond_calculator.py`

重点复核问题：

1. 设计文档中的每条需求是否至少映射到一个实现任务？
2. 所有会改变分数的路径是否都有事件 + 幂等测试覆盖？
3. Bot 主动确认是否只创建 `pending`，绝不直接锁档？
4. `engram` 是否只暴露只读数据，并且不依赖 `affinity`？
5. 前半段实现是否可以在不启动 AstrBot 的情况下测试？

在 reviewer 明确批准或本计划完成修正前，不应开始实现。
