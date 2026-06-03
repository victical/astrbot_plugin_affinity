# Goal 4：LLM 注入、每日回顾、迁移与回归实现计划

> **给 agentic workers：** 使用 `superpowers:subagent-driven-development` 或 `superpowers:executing-plans` 按 checkbox 执行。本文是主计划的 Goal 4 拆分版，对应主计划任务 10-13。

**目标：** 完成好感系统的运行时增强能力：LLM 自然语言关系风格注入、每日回顾结算、旧羁绊迁移、README 和最终回归验证。

**架构：** LLM 注入只消费 `effective_stage`、短期情绪和少量称呼信息，不暴露内部字段。每日回顾保持确定性优先，先用 Provider 数据生成候选事件，支持 `dry_run` 和 `force_rebuild`。迁移服务使用旧羁绊快照生成保守初始分，不自动确认恋人。

**技术栈：** Python 3、AstrBot 插件 API、pytest、Peewee、现有 `astrbot_plugin_engram` API。

---

## 前置条件

必须先完成：

- [Goal 1：插件骨架与纯规则核心](2026-05-23-affinity-goal-1-core-foundation.md)
- [Goal 2：SQLite 持久化闭环](2026-05-23-affinity-goal-2-persistence.md)
- [Goal 3：Engram 接入、实时事件、关系确认与命令](2026-05-23-affinity-goal-3-engram-commands-confirmation.md)

---

## 范围

包含主计划：

- 任务 10：实现 LLM 关系风格注入。
- 任务 11：实现每日回顾 Service。
- 任务 12：实现旧羁绊迁移。
- 任务 13：集成与回归检查。

不包含：

- 重构 Goal 1-3 已完成的核心规则、DB、Provider 和命令。
- 将 LLM 趋势分析作为每日回顾第一版的硬依赖。

---

## 文件

创建或修改：

- `astrbot_plugin_affinity/core/injection.py`
- `astrbot_plugin_affinity/services/daily_review_service.py`
- `astrbot_plugin_affinity/services/scheduler.py`
- `astrbot_plugin_affinity/services/migration_service.py`
- `astrbot_plugin_affinity/handlers/commands.py`
- `astrbot_plugin_affinity/main.py`
- `astrbot_plugin_affinity/README.md`
- `astrbot_plugin_affinity/tests/test_injection.py`
- `astrbot_plugin_affinity/tests/test_daily_review.py`
- `astrbot_plugin_affinity/tests/test_migration.py`

只有实现发现设计需要修正时，才修改：

- `astrbot_plugin_affinity/docs/specs/2026-05-23-affinity-system-design.md`

---

## 执行清单

- [x] **步骤 1：执行主计划任务 10**

实现 `build_relationship_prompt()` 和 `@filter.on_llm_request(priority=-1)` 接入。

关键要求：

- 不输出 `好感度:`。
- 不输出 `关系:`。
- 不输出 `stage`。
- 不输出 `affinity_score`。
- 群聊配置 `affinity_group_soften_romance` 生效。

验证：

```bash
python -m pytest astrbot_plugin_affinity/tests/test_injection.py -q
```

预期：通过。

- [x] **步骤 2：执行主计划任务 11**

实现每日回顾：

- `dry_run` 只生成候选事件，不写库。
- 正式运行写入幂等事件。
- 重复运行不重复加分。
- `force_rebuild` 先备份，再撤销当天每日回顾事件，再重算。
- 调度器在 `terminate()` 中可干净取消。

验证：

```bash
python -m pytest astrbot_plugin_affinity/tests/test_daily_review.py -q
```

预期：通过。

- [x] **步骤 3：执行主计划任务 12**

实现旧羁绊迁移：

- 迁移分数最高 `439`。
- 旧等级保底生效。
- 不写入 `confirmed_stage = 恋人`。
- 不设置 `lover_locked = true`。
- 重复迁移默认跳过。
- force 迁移前备份。

验证：

```bash
python -m pytest astrbot_plugin_affinity/tests/test_migration.py -q
```

预期：通过。

- [x] **步骤 4：执行主计划任务 13**

补充 README，执行最终回归。

验证：

```bash
python -m pytest astrbot_plugin_affinity/tests -q
python -m pytest astrbot_plugin_engram/tests/test_affinity_provider.py -q
```

预期：全部通过。

- [ ] **步骤 5：手动 AstrBot 冒烟检查**

本轮未启动真实 AstrBot 实例执行该人工流程；已完成自动化回归验证。

如果本地 AstrBot 可运行，按主计划任务 13 的手动冒烟清单执行：

1. 加载 `astrbot_plugin_engram`。
2. 加载 `astrbot_plugin_affinity`。
3. 发送私聊消息。
4. 执行 `/好感查询`。
5. 把分数设置到 `440`。
6. 在私聊中触发 Bot 主动确认关系。
7. 用户明确同意。
8. 确认 `confirmed_stage = 恋人` 且 `lover_locked = true`。
9. 普通回复不泄露内部字段。

---

## 完成标准

- LLM 注入不泄露内部字段。
- 每日回顾可 dry-run、幂等、force rebuild。
- 迁移最高不自动超过 `439`，不自动锁定恋人。
- README 说明依赖、命令、数据文件和默认配置。
- affinity 测试套件通过。
- engram Provider 测试通过。
