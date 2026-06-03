# Goal 3：Engram 接入、实时事件、关系确认与命令实现计划

> **给 agentic workers：** 使用 `superpowers:subagent-driven-development` 或 `superpowers:executing-plans` 按 checkbox 执行。本文是主计划的 Goal 3 拆分版，对应主计划任务 5-9。

**目标：** 打通 `astrbot_plugin_affinity` 与 `astrbot_plugin_engram` 的只读依赖，完成插件启动依赖自检、实时互动事件、关系确认状态机和基础管理命令。

**架构：** `engram` 只暴露 `AffinityMemoryProvider`，不依赖 `affinity`。`affinity` 启动时解析 Provider，缺失则禁用。实时事件和关系确认都通过 `AffinityService` 写事件和更新状态；命令层只做参数解析与文本格式化。

**技术栈：** Python 3、AstrBot 插件 API、pytest、Peewee、现有 `astrbot_plugin_engram` API。

---

## 前置条件

必须先完成：

- [Goal 1：插件骨架与纯规则核心](2026-05-23-affinity-goal-1-core-foundation.md)
- [Goal 2：SQLite 持久化闭环](2026-05-23-affinity-goal-2-persistence.md)

---

## 范围

包含主计划：

- 任务 5：在 Engram 中暴露只读 Affinity Provider。
- 任务 6：在 Affinity 插件启动时解析 Provider。
- 任务 7：实现实时事件 Affinity Service。
- 任务 8：实现关系确认状态机。
- 任务 9：实现命令 Handler。

不包含：

- LLM 关系风格注入。
- 每日回顾调度。
- 旧羁绊迁移执行。
- 完整 AstrBot 手动冒烟。

---

## 文件

在 `astrbot_plugin_engram/` 中创建或修改：

- `core/affinity_provider.py`
- `core/__init__.py`
- `main.py`
- `tests/test_affinity_provider.py`

在 `astrbot_plugin_affinity/` 中创建或修改：

- `core/memory_provider.py`
- `core/confirmation.py`
- `services/__init__.py`
- `services/affinity_service.py`
- `handlers/__init__.py`
- `handlers/commands.py`
- `main.py`
- `tests/test_plugin_startup.py`
- `tests/test_confirmation.py`
- `tests/test_commands.py`
- `tests/test_rules.py`

---

## 执行清单

- [x] **步骤 1：执行主计划任务 5**

在 `astrbot_plugin_engram` 中实现 `AffinityMemoryProvider`，暴露设计文档中的只读接口。

验证：

```bash
python -m pytest astrbot_plugin_engram/tests/test_affinity_provider.py -q
```

预期：通过。

- [x] **步骤 2：执行主计划任务 6**

在 `astrbot_plugin_affinity` 中实现 Provider Protocol 和 resolver。启动时 Provider 缺失则禁用插件并输出明确日志。

验证：

```bash
python -m pytest astrbot_plugin_affinity/tests/test_plugin_startup.py -q
```

预期：通过。

- [x] **步骤 3：执行主计划任务 7**

实现 `AffinityService` 的实时事件方法：

- `record_daily_chat()`
- `record_memory_recall()`
- `record_profile_growth()`
- `record_negative_event()`
- `get_user_snapshot()`

验证：

```bash
python -m pytest astrbot_plugin_affinity/tests/test_rules.py astrbot_plugin_affinity/tests/test_db_manager.py -q
```

预期：通过。

- [x] **步骤 4：执行主计划任务 8**

实现关系确认状态机。关键要求：

- Bot 主动确认只创建 `pending`。
- 用户明确同意后才写入 `confirmed_stage = 恋人`。
- 用户明确同意后才设置 `lover_locked = true`。
- 拒绝和回避进入冷却。

验证：

```bash
python -m pytest astrbot_plugin_affinity/tests/test_confirmation.py astrbot_plugin_affinity/tests/test_db_manager.py -q
```

预期：通过。

- [x] **步骤 5：执行主计划任务 9**

实现命令 handler 并接入 AstrBot 命令：

- `/好感查询 [user_id]`
- `/好感设置 <user_id> <score>`
- `/关系设置 <user_id> <stage>`
- `/好感重置 <user_id>`
- `/好感事件 <user_id>`
- `/好感回顾 <user_id> [date]`
- `/好感迁移 [user_id]`

验证：

```bash
python -m pytest astrbot_plugin_affinity/tests/test_commands.py -q
```

预期：通过。

- [x] **步骤 6：Goal 3 汇总验证**

运行：

```bash
python -m pytest astrbot_plugin_engram/tests/test_affinity_provider.py -q
python -m pytest astrbot_plugin_affinity/tests/test_plugin_startup.py astrbot_plugin_affinity/tests/test_rules.py astrbot_plugin_affinity/tests/test_db_manager.py astrbot_plugin_affinity/tests/test_confirmation.py astrbot_plugin_affinity/tests/test_commands.py -q
```

预期：全部通过。

---

## 完成标准

- `engram` 只读 Provider 可用，且 `engram` 不依赖 `affinity`。
- `affinity` 缺失 Provider 时明确禁用。
- 实时事件通过 DB 幂等写入。
- Bot 主动确认不会直接锁档。
- 用户同意后才确认恋人并锁档。
- 管理命令 handler 可被测试验证。
