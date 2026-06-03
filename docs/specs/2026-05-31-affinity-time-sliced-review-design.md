# 基于时间切片的好感度内容判定设计

## 1. 背景

当前每日回顾主要依赖硬规则生成好感事件：

- 有效互动轮数映射为闲聊/寒暄分。
- 新增画像按新增项加分。
- 长期记忆命中加分。
- 摘要中出现心事/秘密时加分。

这套规则稳定、可控，但无法真正判断当天聊天内容对关系的正向或负向影响。早期“让 LLM 在回复时带出加多少分”的方案也不理想：它可能暴露内部参数，且单次回复缺少完整上下文，容易误判。

因此，新方案将好感判定放到每日回顾阶段：先按时间划分聊天记录，再交给 LLM 提取关系事件，最后由本地规则表计算分数。

## 2. 设计目标

1. 不在普通聊天回复中暴露好感分、加分规则或内部字段。
2. 不让 LLM 直接决定最终分数，只让 LLM 提取结构化关系信号。
3. 使用当天较完整的聊天上下文，提高正负向判断稳定性。
4. 支持正向、负向、中性事件，并保留证据摘要用于审计。
5. 继续兼容现有每日回顾的 dry-run、幂等写入和 force rebuild。
6. 让计分规则可配置、可测试、可调整，而不是写死在 prompt 中。
7. 用关系阶段而不是固定日上限来约束“正向进度”：真实好感分自由累积，但对外可见的关系阶段每天最多推进一格，并且只在次日固定刷新时间推进。
8. 超出当前阶段上限的好感分不丢弃，存入“好感银行”，后续按天兑现为阶段推进。

## 3. 总体流程

```text
每日回顾触发
    ↓
从记忆插件读取目标用户当天聊天记录
    ↓
按时间间隔切分为多个会话片段
    ↓
过滤命令、系统消息、低价值噪音
    ↓
逐片段调用 LLM 提取关系事件
    ↓
校验 JSON、过滤低置信度事件
    ↓
合并去重同类事件
    ↓
本地规则表计算分数
    ↓
应用正负向上限与特殊规则
    ↓
写入 affinity_events
```

## 4. 数据来源

好感插件仍不直接读写记忆插件内部数据库，而是通过记忆插件 provider 获取只读数据。

建议新增或扩展 provider 接口：

```python
async def get_conversation_messages(user_id, event_date) -> list[dict]:
    ...
```

每条消息建议包含：

```json
{
  "message_id": "string",
  "user_id": "string",
  "role": "user|assistant|system",
  "content": "string",
  "created_at": "2026-05-31T20:10:00",
  "chat_type": "private|group",
  "group_id": "optional string"
}
```

每日回顾仍可继续读取现有数据：

- `get_profile_delta()`：画像新增。
- `get_memory_refs()`：长期记忆命中或形成。
- `get_daily_summary()`：当天摘要与 tags。
- `get_interaction_stats()`：作为兜底统计，不再作为主要好感依据。

## 5. 时间切片规则

按“自然会话片段”切分，而不是机械按整点切。

默认规则：

- 同一用户同一天内处理。
- 两条可用消息间隔超过 `45 分钟`，切成新片段。
- 单片段最多 `80 条消息` 或指定 token 上限，超过后继续切分。
- 片段切分时保留少量 overlap，例如前后各 `3 条`，减少上下文断裂。
- 过滤 `/查询好感`、`/好感回顾` 等命令消息。
- 过滤系统消息、空消息、纯转发占位、明显无语义内容。
- 群聊中只保留与目标用户、Bot 直接相关的上下文，避免无关群聊污染判断。

每个片段生成一个稳定 `session_id`：

```text
<user_id>:<date>:<start_time>:<end_time>:<index>
```

## 6. LLM 分析职责

LLM 只负责提取“关系事件”，不输出最终分数。

禁止让 LLM 返回：

```json
{"score_delta": 8}
```

应该返回：

```json
{
  "session_id": "u1:2026-05-31:20:00:20:50:0",
  "events": [
    {
      "type": "self_disclosure",
      "direction": "positive",
      "intensity": 2,
      "confidence": 0.84,
      "evidence": "用户分享了近期压力和真实情绪",
      "message_refs": ["m1001", "m1005"]
    }
  ]
}
```

字段约束：

- `type`：事件类型，必须来自白名单。
- `direction`：`positive`、`negative` 或 `neutral`。
- `intensity`：`1` 到 `3`，表示强度，不表示分数。
- `confidence`：`0` 到 `1`。
- `evidence`：简短证据摘要，不能包含大段原文。
- `message_refs`：触发判断的消息 ID 列表，用于去重和审计。

如果模型输出非法 JSON、未知类型、缺少 evidence、confidence 过低，该事件应被丢弃。

### 6.1 LLM 调用适配器形态

`AffinitySignalAnalyzer` 复用 AstrBot 的 LLM provider 接口，与 engram 现有用法（`memory_manager` / `profile_manager` / `intent_classifier`）保持一致：

- **注入 context 而非具体 client**：analyzer 构造时接收 AstrBot `context`，内部按需取 provider，便于测试注入假 context。
- **取 provider**：优先用配置的独立分析模型，取不到再退默认 provider。

  ```python
  provider = context.get_provider_by_id(model_id) or context.get_using_provider()
  if not provider:
      # 无可用 provider → 回退（见第 12 节），不阻断每日回顾
      return SignalAnalysisResult(signals=[], raw_event_count=0, discarded_count=0, errors=["no_provider"])
  ```

- **发起调用**：`resp = await provider.text_chat(prompt=prompt)`；结果取 `resp.completion_text`，**必须按 `None` 兜底**（`(resp.completion_text or "").strip()`）。
- **容错范式**：整个调用包在 `try/except` 中，provider 缺失或调用异常时返回空信号并记录 `errors`，由 `DailyReviewService` 回退到硬规则。
- **可配置模型**：建议新增配置 `affinity_review_llm_model`（留空则用当前默认 provider），对应 engram 的 `intent_llm_model` / `summarize_model` 模式。
- **测试**：注入假 context，其 `get_using_provider()` 返回带 `async text_chat(prompt)` 的 fake，`completion_text` 直接给预设 JSON 字符串，从而不触网（与 engram 测试一致）。

> 这意味着计划中“无稳定 LLM API 则保持禁用”的顾虑已消除：API 稳定为 `provider.text_chat`。`affinity_llm_review_enabled` 默认仍为 `false`，仅作为灰度开关，而非因 API 不确定而禁用。

## 7. 事件类型

### 7.1 正向事件

- `casual_warmth`：自然友好闲聊，有一定陪伴感。
- `self_disclosure`：用户分享个人经历、真实想法、压力或情绪。
- `trust_signal`：用户表达信任、依赖、主动透露隐私。
- `relationship_repair`：道歉、解释误会、主动缓和冲突。
- `appreciation`：用户表达感谢、认可、喜欢陪伴。
- `preference_discovered`：产生新的偏好、厌恶、昵称等画像项。

### 7.2 负向事件

- `coldness`：明显冷淡、敷衍、疏离。
- `boundary_violation`：无视边界、强迫关系设定、强迫亲密。
- `hostility`：辱骂、攻击、恶意嘲讽。
- `rejection_signal`：明确拒绝亲近、拒绝关系推进。
- `trust_break`：欺骗、操控、恶意试探。
- `conflict_escalation`：争吵升级、持续敌意表达。

### 7.3 中性事件

- `neutral_chat`：普通问答、工具使用、无关系变化的交流。
- `low_value_noise`：重复消息、纯表情、无上下文短句。

中性事件默认不写入好感事件，除非 dry-run 需要展示调试信息。

## 8. 分数映射

分数由本地规则表决定，LLM 不知道最终分数。

建议初始规则：

```json
{
  "casual_warmth": {"base": 1, "per_intensity": 1, "max": 3},
  "self_disclosure": {"base": 3, "per_intensity": 2, "max": 8},
  "trust_signal": {"base": 5, "per_intensity": 3, "max": 12},
  "relationship_repair": {"base": 4, "per_intensity": 2, "max": 10},
  "appreciation": {"base": 2, "per_intensity": 2, "max": 6},
  "preference_discovered": {"per_item": 10},
  "coldness": {"base": -1, "per_intensity": -2, "min": -5},
  "boundary_violation": {"base": -5, "per_intensity": -4, "min": -15},
  "hostility": {"base": -8, "per_intensity": -5, "min": -20},
  "rejection_signal": {"base": -5, "per_intensity": -4, "min": -15},
  "trust_break": {"base": -10, "per_intensity": -8, "min": -50},
  "conflict_escalation": {"base": -5, "per_intensity": -5, "min": -20}
}
```

分数表的取值直接生效，不再被旧的“单条 ±5”逻辑二次截断。即 `trust_signal` 可以一次产生 +12，`hostility` 可以一次产生 -13。每个事件最终落到事件流的 `score_delta` 就是按上表算出的值（受各事件自身 `max` / `min` 约束）。

> 设计取舍：旧实现里普通每日回顾事件会被无差别 clamp 到 `±5`，这会让上表中超过 5 的部分全部失效。本方案废弃“单条 ±5”这一层，让分数表成为唯一的单条强度来源。负向事件仍受“每日负向上限”约束（见 8.2）。

### 8.1 正向分不设日上限，改用阶段闸门

去掉旧的“实时 +20/天”“回顾正向 +50/天”等正向日上限。正向好感分按事件直接累加进 `affinity_score`，只受全局 `[-100, 520]` clamp 约束。

正向进度改由“关系阶段每日推进”约束（见第 17 节）：真实分可以一天涨很多并存入好感银行，但对外可见的 `effective_stage` 每天最多推进一格，且只在次日固定刷新时间推进。

### 8.2 负向分立即生效，不进银行

负向事件与正向相反：

- 立即扣减 `affinity_score`，不缓冲、不延迟。
- 阶段随之即时下修（见 17.4）。
- 仍保留“每日负向上限 `-50`”，防止单日被负向事件击穿。

非对称设计的理由：正向关系需要时间沉淀（慢涨、按天兑现），负向行为应当即时反映，不应被“银行”缓冲。

### 8.3 画像新增

- 新增画像每项 `+10`。
- 画像分由 engram 侧 `ProfileGuardian` 守门（置信度晋升、冲突挂起、强证据保护），低质量/重复申报不会形成画像项，因此不会触发加分。
- 画像分同样直接累加进真实分、存入好感银行，不单独设上限。其“正向进度”同样受第 17 节阶段闸门约束。
- LLM 信号中的 `preference_discovered` **不**单独计分，避免与硬规则画像 delta 重复加分；画像 `+10` 的唯一来源是画像 delta。


## 9. 聚合与去重

LLM 逐片段输出后，需要做日级聚合。

去重规则：

- 同一 `type`、相近 `message_refs`、相似 evidence 的事件合并。
- 同一画像项只计算一次。
- overlap 区域重复识别的事件只保留置信度最高的一条。
- 同一片段内多个低强度 `casual_warmth` 合并为一个事件。

置信度规则：

- `confidence < 0.65`：丢弃。
- `0.65 <= confidence < 0.8`：只允许低强度事件。
- `confidence >= 0.8`：允许按 intensity 正常结算。

冲突规则：

- 同一片段同时存在正向与负向事件时，不互相抵消，分别记录。
- 如果同一证据被判成互斥事件，优先保留置信度更高者。
- 强负向事件不应被大量闲聊正向事件完全掩盖，负向上限独立计算。

## 10. 写入事件

最终仍写入 `affinity_events`。

建议事件字段：

- `event_type`：现有类型或新增类型。
- `score_delta`：本地规则计算结果。
- `reason`：事件类型对应中文说明。
- `source`：`daily_review_llm`。
- `source_ref`：`session_id` 或聚合事件 ID。
- `event_date`：回顾日期。
- `idempotency_key`：稳定键，防重复写入。
- `metadata_json`：保存事件类型、方向、强度、置信度、证据摘要、消息引用。

幂等策略：

LLM 输出非确定性，`evidence` 文字和 `message_refs` 组合每次运行都可能不同，因此**不能用 LLM 输出内容算 hash 作为幂等键**，否则二次运行会因 key 变化而重复写入。

采用粗粒度日级幂等：

- 非 force-rebuild：若当天已存在 `source = "daily_review_llm"` 的事件，则整体跳过，不再分析、不再写入。
- force-rebuild：先清理当天旧事件再重算（见下）。

单条事件的幂等键用稳定可复现的构成，不含 LLM 自由文本：

```text
daily-llm:<user_id>:<date>:<event_type>:<index>
```

其中 `<index>` 是该用户该日聚合后同类型事件的稳定序号（按 session 顺序 + 片段内顺序生成）。

force rebuild 时：

1. 备份当天旧的 `source in ("daily_review", "daily_review_llm")` 事件。
2. 删除旧事件。
3. 重新计算用户状态（含好感银行与已解锁阶段，见第 17 节）。
4. 重新生成并写入事件。

## 11. dry-run 输出

`好感回顾 <user_id> <date> dry_run` 应显示：

- 分析了多少个会话片段。
- LLM 识别了多少个原始事件。
- 丢弃了多少低置信度或非法事件。
- 最终会写入多少事件。
- 每类事件预计分数（按第 8 节分数表）。
- 当天预计真实分总变化。
- 当天结束后真实分、好感银行存量、当前阶段、次日可推进到的阶段。

示例（数字与第 8 节分数表一致）：

```text
每日回顾 dry-run:
会话片段: 4
候选事件: 7
有效事件: 3
- casual_warmth +2 自然友好闲聊
- self_disclosure +7 用户分享真实压力
- hostility -13 出现攻击性表达
真实分变化: -4
当前阶段: 朋友 (次日不推进)
好感银行存量: 0
```

## 12. 安全边界

1. prompt 中不包含真实分数表，只描述事件类型和输出 schema。
2. LLM 输出不直接进入用户回复。
3. evidence 只保存摘要，不保存大段原文。
4. 所有分数由代码计算。
5. 所有事件经过 JSON schema 校验。
6. 低置信度事件不写入。
7. 失败时回退到现有硬规则，不阻断每日回顾。

## 13. 配置建议

建议新增配置项：

```json
{
  "affinity_llm_review_enabled": true,
  "affinity_llm_review_min_confidence": 0.65,
  "affinity_review_llm_model": "",
  "affinity_review_session_gap_minutes": 45,
  "affinity_review_max_messages_per_session": 80,
  "affinity_review_negative_cap": -50,
  "affinity_stage_advance_enabled": true,
  "affinity_stage_advance_max_steps_per_day": 1,
  "affinity_daily_refresh_hour": 4
}
```

说明：

- 已移除 `affinity_review_positive_cap` 与 `affinity_review_single_event_cap`：正向不再设日上限，单条强度由分数表决定（见第 8 节）。
- 保留 `affinity_review_negative_cap`：负向仍受每日负向上限约束。
- `affinity_stage_advance_max_steps_per_day` 默认 `1`，即每天最多推进一格。
- `affinity_daily_refresh_hour` 为次日固定刷新与阶段推进的时间，复用每日回顾调度时间。

事件分数表可以先写在代码常量中，稳定后再暴露为高级配置。

## 14. 落地阶段

### 阶段一：基础设施

- provider 增加按日期读取聊天记录接口。
- 新增会话切片模块。
- 增加 JSON schema 校验。
- 增加 dry-run 调试输出。

### 阶段二：LLM 事件提取

- 新增 `AffinitySignalAnalyzer`。
- 实现严格 JSON 输出 prompt。
- 支持正向、负向、中性事件白名单。
- 增加低置信度过滤。

### 阶段三：本地计分与写入

- 新增事件分数映射表（分数表为单条强度唯一来源，废弃单条 ±5 截断）。
- 实现聚合、去重、负向日上限。
- 正向分累加进真实分与好感银行，不设日上限。
- 写入 `daily_review_llm` 来源事件。
- 保持 force rebuild 可恢复。

### 阶段四：替换旧规则

- 将 `valid_turns` 只作为兜底。
- 优先使用 LLM 关系事件结算。
- 保留画像新增每项 `+10` 的特殊规则。
- 保留记忆命中、心事等硬规则作为辅助信号。

### 阶段五：阶段化每日推进与好感银行（见第 17 节）

- 去掉正向日上限，真实分自由累积。
- 新增“已解锁阶段”状态与每日推进逻辑，仅在次日固定刷新时间推进。
- 推进落成 `stage_unlock` 事件，保证 force-rebuild 可重放。
- 负向事件即时扣分并即时下修阶段。

## 15. 测试计划

需要覆盖：

- 时间间隔超过阈值时正确切片。
- 长片段按消息数上限切分。
- 命令消息和系统消息被过滤。
- LLM 非法 JSON 被丢弃。
- 低置信度事件被丢弃。
- 同一 overlap 事件只计一次。
- 分数表取值直接生效（`trust_signal` 可达 +12、`hostility` 可达 -13），不被 ±5 截断。
- 正向分无日上限，超出当前阶段上限的部分进入好感银行（真实分照常累积）。
- 负向事件受 `-50` 日上限限制，且即时生效不进银行。
- 新增画像每项 `+10`，`preference_discovered` 信号不重复计分。
- 阶段每天最多推进一格；银行有大量存量时也只推进一格。
- 阶段推进只在次日固定刷新时间发生，刷新前阶段不变。
- 负向事件使真实分下降时，已解锁阶段即时下修。
- `stage_unlock` 事件可被 force-rebuild 重放，重算后阶段进度一致。
- dry-run 不写数据库。
- force rebuild 会备份、删除旧事件、重算状态（含银行与已解锁阶段）。

## 16. 未决问题

1. ~~记忆插件是否已经保存足够完整的原始聊天记录，以及是否能按用户和日期查询。~~ 已确认：engram `db_manager.get_all_raw_messages(user_id, start, end, limit)` 支持按用户+时间范围查询；`RawMemory` 含 `uuid`/`user_id`/`role`/`content`/`timestamp`/`group_id`/`session_id`/`member_id`，满足切片与去重所需字段。
2. 群聊中如何判定“与目标用户相关”的上下文范围（可借助 `member_id`/`session_id`/`group_id`）。
3. 是否需要为不同关系阶段设置不同评分敏感度。
4. 是否需要人工审核高强度负向事件。
5. 是否需要把事件分数表暴露到插件配置。
6. ~~AstrBot 侧可用的 LLM 调用适配器形态（可参考 engram 的 `llm_injector` / `intent_classifier`），决定 `AffinitySignalAnalyzer` 注入哪种 client。~~ 已定：注入 `context`，用 `context.get_using_provider()` / `get_provider_by_id(model_id)` 取 provider，`await provider.text_chat(prompt=...)` 拿 `completion_text`，失败回退（见 6.1）。
7. ~~离线多日（bot 未运行）后的补算策略：错过的每个自然日是否各补一格推进，还是只按实际运行日推进。~~ 已定：不补算，每次刷新最多推进一格，离线期间错过的日不累计（见 17.3.1）。

## 17. 阶段化每日推进与好感银行

本节是本设计在“计分”之上的关系节奏层，取代旧的“正向日上限”思路。

### 17.1 核心思想

- 真实好感分 `affinity_score` 是一个**累加器/银行**：正向分自由累积，只受全局 `[-100, 520]` clamp。
- 对外可见的关系阶段 `effective_stage` 是一个**按天放行的进度**：每天最多推进一格，且只在次日固定刷新时间推进。
- 真实分领先于已解锁阶段的部分，就是“好感银行存量”——不丢弃，后续按天兑现为阶段推进。

直观理解：分数管“你们实际有多近”，阶段管“关系对外承认到哪一步”。关系承认需要时间，所以阶段慢放；但好感不浪费，所以分数照常存。

### 17.2 状态模型

在 `AffinityUserState` 增加：

- `unlocked_stage`：当前已解锁（对外可见）的关系阶段。`effective_stage` 取 `unlocked_stage`。
- `last_stage_advance_date`：上次执行每日推进的自然日，保证一天只推进一次。

阶段边界沿用 `stage.py`：

| 阶段 | 区间 |
|---|---|
| 初识 | 0~49 |
| 朋友 | 50~149 |
| 挚友 | 150~299 |
| 暧昧 | 300~439 |
| 恋人候选 | 440~520 |

> “恋人”仍是显式确认 + `lover_locked` 锁定，不在自动推进范围内（保持现有 confirmation 流程）。

好感银行存量（仅用于展示/诊断）：

```text
bank = stage_index(score_stage(affinity_score)) - stage_index(unlocked_stage)
```

`bank > 0` 表示真实分已经够进更高阶段，但尚未按天兑现。

### 17.3 每日推进（仅次日固定刷新时间执行）

在每日固定刷新时间（`affinity_daily_refresh_hour`，复用每日回顾调度），对每个用户：

1. 若 `last_stage_advance_date == 今天`，跳过（防重复）。
2. 计算 `target = score_stage(affinity_score)`（真实分对应的“应得”阶段）。
3. 若 `target` 高于 `unlocked_stage`，则 `unlocked_stage` 向 `target` 方向推进**最多 `max_steps_per_day` 格**（默认 1）。
4. 推进发生时写入一条 `stage_unlock` 事件（`score_delta = 0`，metadata 记录 from/to 阶段、当时真实分），并刷新 `effective_stage`。
5. 记 `last_stage_advance_date = 今天`。

要点：

- 刷新前，即使真实分已经涨够，对外阶段也不变——这就是“要到下一阶段必须等第二天”。
- 银行存量大时一天也只推进一格（除非配置允许多格），银行余量留到后续日继续兑现。
- 每日推进与 LLM 回顾计分是两个步骤：先回顾计分（改真实分），再执行推进（按新的真实分放行阶段）。

### 17.3.1 离线多日不补算（按实际运行日推进）

推进代表的是“实际陪伴投入”，不是单纯的时间流逝。因此 bot 离线/关机期间错过的刷新日**不累计、不补偿**：

- 每次刷新触发时，无论距上次推进过了多少自然日，都**最多推进 `max_steps_per_day` 格**（默认 1）。
- `last_stage_advance_date` 只用于“同一天防重复”，不用于计算“补几格”。判断依据是 `today != last_stage_advance_date`，而非两者相差几天。
- 例：真实分早已够“暧昧”（银行存了 3 格），bot 关机 3 天后重开，重开当天首次刷新只推进 1 格（朋友→挚友），剩余 2 格留到后续运行日逐日兑现。

理由：阶段推进要求“那段时间 bot 真的在陪伴”。关机即无互动，不应自动跃迁；银行存量不丢失，重新运行后照常逐日放行，对用户无损失，只是节奏回到每天一格。

> 后续如需照顾“正常掉线”体验，可在配置中放开 `max_steps_per_day`，但默认保持 1，防止长期关机后重开瞬间跳级。

### 17.4 负向即时降级（不进银行）

负向事件不走“按天放行”，立即生效：

1. 立即扣减真实分。
2. 立即重算 `target = score_stage(affinity_score)`。
3. 若 `target` 低于 `unlocked_stage`，则**即时**把 `unlocked_stage` 下修到 `target`（不受“每天一格”限制，下行不延迟）。
4. 下修同样写入 `stage_unlock` 事件（记录为下行），保证可重放。

非对称：上行慢（按天放行、可缓存进银行），下行快（即时、即时下修阶段）。坏行为不被银行缓冲，也不能用旧银行存量在次日把阶段弹回——因为银行存量是由真实分推导的，真实分降了银行自然缩水。

### 17.5 与 event-sourcing 的一致性

`recompute_user_state_from_events` 能从事件 `score_delta` 之和重建真实分，但**“已解锁到第几阶”是按日历逐日放行的历史，无法靠求和重建**。因此：

- 每次每日推进 / 即时下修都必须落 `stage_unlock` 事件。
- force-rebuild 重算时，先按 `score_delta` 重建真实分，再**按时间顺序重放 `stage_unlock` 事件**还原 `unlocked_stage` 与 `last_stage_advance_date`。
- `stage_unlock` 事件 `score_delta = 0`，不影响真实分求和，只承载阶段进度。

### 17.6 示例时间线

设某用户初始 0 分、初识。

| 日 | 当日真实分变化 | 刷新后真实分 | score_stage(真实分) | unlocked_stage（推进后） | 银行 |
|---|---|---|---|---|---|
| D1 | +60 | 60 | 朋友 | 初识→朋友 | 0 |
| D2 | +120（深聊 + 画像 +10×N） | 180 | 挚友 | 朋友→挚友 | 0 |
| D3 | +200 | 380 | 暧昧 | 挚友→暧昧 | 0（一天一格，剩余留存） |
| D4 | 0（没聊） | 380 | 暧昧 | 暧昧（已是 target，不变） | 0 |
| D5 | hostility -13 等 | 320 | 暧昧 | 暧昧（仍在区间内，不下修） | 0 |

说明 D3：若某天真实分一次冲到“暧昧”，但 `unlocked_stage` 当时还在“朋友”，则当天最多推进到“挚友”，“暧昧”留到下一日继续放行（银行 > 0）。表中 D2/D3 为简化展示，实际每日只推进一格。

### 17.7 与现有实现的差异（落地提示）

- `rules.py`：移除/停用 `DEFAULT_REALTIME_POSITIVE_CAP`、`DEFAULT_DAILY_REVIEW_POSITIVE_CAP`、`DEFAULT_DAILY_POSITIVE_CAP` 等正向上限在正向路径上的约束；保留负向上限常量。
- `affinity_service.py`：`record_daily_chat` / `record_memory_recall` / `record_profile_growth` 去掉正向 `apply_daily_cap`，直接累加真实分；负向路径保留即时扣分 + 即时下修。
- `db_manager.py`：`AffinityUserState` 增 `unlocked_stage`、`last_stage_advance_date`；`recompute_user_state_from_events` 增加 `stage_unlock` 重放逻辑。
- `scheduler.py`：每日刷新时，回顾计分后追加一次阶段推进调用。
- `stage.py`：`effective_stage` 改为以 `unlocked_stage` 为准（保留 `lover_locked` 优先于一切的现有逻辑）。


