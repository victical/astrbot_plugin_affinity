# Goal 2：SQLite 持久化闭环实现计划

> **给 agentic workers：** 使用 `superpowers:subagent-driven-development` 或 `superpowers:executing-plans` 按 checkbox 执行。本文是主计划的 Goal 2 拆分版，对应主计划任务 4。

**目标：** 实现 `affinity.db` 的核心持久化闭环：用户主状态、事件日志、运行时状态、每日计数器，以及基于 `idempotency_key` 的一次性事件应用。

**架构：** 使用 Peewee + SQLite，参考 `astrbot_plugin_engram/db_manager.py` 的 per-instance model binding 模式。所有分数变化都通过 `apply_score_event()` 进入事务，先写事件，再更新用户状态。

**技术栈：** Python 3、Peewee、SQLite、pytest。

---

## 前置条件

必须先完成：

- [Goal 1：插件骨架与纯规则核心](2026-05-23-affinity-goal-1-core-foundation.md)

特别依赖：

- `core/models.py`
- `core/stage.py`
- `core/rules.py`

---

## 范围

包含主计划：

- 任务 4：实现 SQLite 持久化。

不包含：

- `engram` Provider。
- AstrBot 命令。
- LLM 注入。
- 每日回顾和迁移业务。

---

## 文件

创建或修改：

- `astrbot_plugin_affinity/db_manager.py`
- `astrbot_plugin_affinity/core/models.py`
- `astrbot_plugin_affinity/tests/conftest.py`
- `astrbot_plugin_affinity/tests/test_db_manager.py`

---

## 执行清单

- [ ] **步骤 1：编写数据库失败测试**

按主计划任务 4 写入：

- 默认用户状态创建测试。
- 重复 `idempotency_key` 不重复加分测试。

验证：

```bash
python -m pytest astrbot_plugin_affinity/tests/test_db_manager.py -q
```

预期：先失败，原因是 DB manager 尚不存在。

- [ ] **步骤 2：实现 Peewee 表模型**

创建：

- `AffinityUserState`
- `AffinityEvent`
- `AffinityRuntimeState`
- `AffinityDailyCounter`

最低要求：

- `AffinityEvent.idempotency_key` 唯一。
- `AffinityUserState.user_id` 唯一。
- 每个表包含 `created_at` / `updated_at` 或等价时间字段。

- [ ] **步骤 3：实现 `AffinityDatabaseManager`**

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

- [ ] **步骤 4：实现事务性事件应用**

`apply_score_event()` 必须：

1. 在事务内运行。
2. 检查或写入唯一 `idempotency_key`。
3. 如果是重复事件，返回 `applied=False`，不改分。
4. 如果是新事件，clamp 分数。
5. 重新计算 `stage` 和 `effective_stage`。
6. 更新 `AffinityUserState`。

- [ ] **步骤 5：运行持久化测试**

运行：

```bash
python -m pytest astrbot_plugin_affinity/tests/test_db_manager.py -q
```

预期：通过。

- [ ] **步骤 6：Goal 2 汇总验证**

运行：

```bash
python -m pytest astrbot_plugin_affinity/tests/test_stage.py astrbot_plugin_affinity/tests/test_rules.py astrbot_plugin_affinity/tests/test_db_manager.py -q
```

预期：全部通过。

---

## 完成标准

- 默认用户状态可创建。
- 事件写入可追溯。
- 重复幂等键不会重复加分。
- 分数变化后阶段快照自动更新。
- 恋人锁档时 `effective_stage` 不被低分覆盖。

