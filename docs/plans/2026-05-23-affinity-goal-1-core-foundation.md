# Goal 1：插件骨架与纯规则核心实现计划

> **给 agentic workers：** 使用 `superpowers:subagent-driven-development` 或 `superpowers:executing-plans` 按 checkbox 执行。本文是主计划的 Goal 1 拆分版，对应主计划任务 1-3。

**目标：** 实现 `astrbot_plugin_affinity` 的最小可测试核心：插件骨架、阶段派生、规则引擎和幂等键，不接入 SQLite、不接入 `engram`、不接入 AstrBot 复杂运行时。

**架构：** 先创建标准 AstrBot 插件外壳，再把所有确定性规则放进 `core/` 纯 Python 模块。Goal 1 完成后，后续任务可以在不启动 AstrBot 的情况下复用阶段判断、分数边界和幂等键规则。

**技术栈：** Python 3、AstrBot 插件 API、pytest。

---

## 范围

包含主计划：

- 任务 1：搭建插件元数据和配置骨架。
- 任务 2：实现阶段规则和领域模型。
- 任务 3：实现规则引擎和幂等辅助函数。

不包含：

- SQLite 表结构和事件写入。
- `astrbot_plugin_engram` Provider。
- AstrBot 命令、LLM 注入、每日回顾、迁移。

---

## 文件

创建：

- `astrbot_plugin_affinity/__init__.py`
- `astrbot_plugin_affinity/metadata.yaml`
- `astrbot_plugin_affinity/requirements.txt`
- `astrbot_plugin_affinity/_conf_schema.json`
- `astrbot_plugin_affinity/main.py`
- `astrbot_plugin_affinity/core/__init__.py`
- `astrbot_plugin_affinity/core/models.py`
- `astrbot_plugin_affinity/core/stage.py`
- `astrbot_plugin_affinity/core/rules.py`
- `astrbot_plugin_affinity/tests/test_plugin_startup.py`
- `astrbot_plugin_affinity/tests/test_stage.py`
- `astrbot_plugin_affinity/tests/test_rules.py`

---

## 执行清单

- [ ] **步骤 1：执行主计划任务 1**

按主计划“任务 1：搭建插件元数据和配置骨架”实现插件最小入口、元数据、配置 schema 和启动导入测试。

验证：

```bash
python -m pytest astrbot_plugin_affinity/tests/test_plugin_startup.py -q
```

预期：通过。

- [ ] **步骤 2：执行主计划任务 2**

按主计划“任务 2：实现阶段规则和领域模型”实现：

- `RelationshipStage`
- `ConfirmationStatus`
- `ConfirmationInitiator`
- `AffinityEventType`
- `Mood`
- `clamp_score()`
- `score_stage()`
- `effective_stage()`

关键验收：

- `520` 未确认时仍是恋人候选。
- `lover_locked=True` 且 `confirmed_stage="恋人"` 时，`effective_stage` 才是恋人。

验证：

```bash
python -m pytest astrbot_plugin_affinity/tests/test_stage.py -q
```

预期：通过。

- [ ] **步骤 3：执行主计划任务 3**

按主计划“任务 3：实现规则引擎和幂等辅助函数”实现：

- `message_key()`
- `daily_key()`
- `migration_key()`
- `apply_daily_cap()`
- 默认日上限常量。

验证：

```bash
python -m pytest astrbot_plugin_affinity/tests/test_rules.py -q
```

预期：通过。

- [ ] **步骤 4：Goal 1 汇总验证**

运行：

```bash
python -m pytest astrbot_plugin_affinity/tests/test_plugin_startup.py astrbot_plugin_affinity/tests/test_stage.py astrbot_plugin_affinity/tests/test_rules.py -q
```

预期：全部通过。

---

## 完成标准

- 插件模块可以被导入。
- 阶段边界与锁档派生规则通过测试。
- 幂等键格式稳定。
- 日上限纯函数通过测试。
- 没有引入数据库、Provider、命令或 LLM 运行时依赖。

