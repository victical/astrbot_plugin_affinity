# astrbot_plugin_affinity

好感度与关系系统插件。插件依赖 `astrbot_plugin_engram` 提供只读画像、记忆、互动统计和旧羁绊快照；好感数据单独写入本插件数据库，不写入 Engram 的记忆库。

## 功能

- 按用户维护全局好感分、阶段、确认状态和锁档状态。
- 记录实时互动、记忆命中、画像增长、负向事件和每日回顾事件。
- 在 LLM 请求中注入自然语言关系风格提示，不暴露内部字段或分数。
- 支持每日回顾 dry-run、幂等写入和 force rebuild。
- 支持从 Engram 旧羁绊系统保守迁移初始分，最高迁移到 `439`，不会自动确认恋人或锁档。

## 命令

- `/好感查询 [user_id]`
- `/设置提示词 <提示词>` 设置当前用户自己的最高阶段提示词
- `/清除提示词` 清除当前用户自己的最高阶段提示词
- `/好感设置 <user_id> <score>` 管理员
- `/设置阶段 <user_id> <stage>` 管理员
- `/关系设置 <user_id> <stage>` 管理员
- `/好感重置 <user_id>` 管理员
- `/重置所有人好感 confirm` 管理员
- `/好感事件 <user_id> [limit]` 管理员
- `/好感回顾 <user_id> [date] [dry_run|force_rebuild]` 管理员
- `/好感迁移 [user_id] [force]` 管理员

管理命令会显示 `effective_stage`、`confirmed_stage`、`lover_locked` 等内部字段。普通聊天不会展示好感分、阶段字段、事件键或计算过程；LLM 注入会提供好感度、关系、情绪和聊天场景作为语气上下文。

## 数据文件

- `affinity.db`：好感主状态、事件日志、运行时状态、每日计数器。
- `affinity_logs/`：每日回顾 force rebuild 备份。
- `affinity_migration_backups/`：旧羁绊 force 迁移备份。

默认运行在 AstrBot 插件数据目录下；若该目录不可写，测试和本地运行会回退到插件目录内的 `data/plugin_data/astrbot_plugin_affinity/`。

## 默认配置

- `enable_affinity`: `true`
- `affinity_group_full_expression`: `true`
- `affinity_group_soften_romance`: `false`
- `affinity_max_stage_custom_prompt_enabled`: `true`
- `affinity_max_stage_custom_prompt_scope`: `private_only`
- `affinity_group_allow_proactive_confirm`: `false`
- `affinity_private_allow_proactive_confirm`: `true`
- `affinity_llm_review_enabled`: `false`
- `affinity_daily_refresh_hour`: `4`

群聊默认不主动推进关系确认；私聊默认允许在达到条件时发起确认。启用 `affinity_group_soften_romance` 后，群聊中的暧昧和恋人阶段会弱化为更克制的表达。

当用户达到 `恋人候选` 或 `恋人` 且尚未设置提示词时，插件会在下次互动时提示一次可用指令。

阶段提示词和用户自己的最高阶段提示词支持变量：`{好感度}`/`{affinity}`、`{关系}`/`{stage}`、`{情绪}`/`{mood}`、`{私聊/群聊}`/`{聊天场景}`/`{chat_type}`。注入给 LLM 的固定上下文格式为好感度、关系、情绪、私聊/群聊。

## 每日回顾与阶段推进

LLM 回顾是可选灰度功能，默认关闭。启用 `affinity_llm_review_enabled` 后，每日回顾会从 Engram 读取当天聊天记录，按时间切片后让 LLM 只抽取关系信号；LLM 不返回分数，所有好感变化都由本地规则表计算并写入事件流。

正向好感不再设置每日上限，会直接累积到真实分数里，形成“好感银行”。可见关系阶段由 `unlocked_stage` 控制，每天刷新时最多推进一格，离线天数不补算；银行分不会丢失，只会在后续实际运行日继续逐日解锁。

负向事件保留每日 `-50` 下限，扣分立即生效，并会按真实分数即时下修已解锁阶段。画像增长仍只由 Engram 的画像 delta 触发，每个新增项 `+10`；LLM 的 `preference_discovered` 信号不单独加分，避免重复计算。

`/好感回顾 <user_id> <date> dry_run` 会预览候选事件、LLM 信号计数、预计真实分变化、当前阶段、次日可推进阶段和好感银行余额，不写入数据库。
