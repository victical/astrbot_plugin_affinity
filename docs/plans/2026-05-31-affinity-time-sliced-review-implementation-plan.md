# Time-Sliced Affinity Review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a time-sliced daily review pipeline that reads conversation records, asks an LLM to extract relationship events, and lets local rules calculate affinity score changes.

**Architecture:** Keep `DailyReviewService` as the orchestration entrypoint, but split time slicing, LLM signal analysis, event scoring, and aggregation into focused modules. The LLM never returns final score deltas; it only returns structured relationship signals that are validated, filtered, deduplicated, and scored by local code before writing `affinity_events`.

**Tech Stack:** Python 3, AstrBot plugin API, pytest, Peewee, existing `astrbot_plugin_engram` provider interface, existing affinity SQLite models.

---

## Source Spec

- `docs/specs/2026-05-31-affinity-time-sliced-review-design.md`

Implementation should follow the latest spec values:

- No positive daily cap. Positive affinity accumulates freely into the real score (the "affinity bank"), bounded only by the global clamp `[-100, 520]`.
- Positive progress is gated by **stage advancement**: the visible `effective_stage` advances at most one step per day, and only at the daily refresh time (§17 of the spec).
- Offline days are NOT back-filled: each refresh advances at most one step regardless of how long the bot was down (§17.3.1).
- Negative daily cap stays `-50`. Negative events apply immediately and demote the stage immediately (no banking).
- The score table is the single source of per-event strength. The old "single event ±5" clamp is removed, so `trust_signal` can yield `+12` and `hostility` can yield `-13`.
- Profile growth: `+10` per newly discovered profile item. `preference_discovered` LLM signals are NOT scored (avoid double-count); the only source of `+10` is the hard-rule profile delta.
- LLM analyzer calls `provider.text_chat` via injected `context` (spec §6.1); the API is stable, so `affinity_llm_review_enabled` is a gradual-rollout switch, not an "API unknown" guard.

---

## File Map

Create:

- `core/review_signals.py`
  Defines relationship signal event types, directions, validated dataclasses, the score table (single source of per-event strength), scoring helpers, aggregation/dedup, the negative-only daily cap, and a deterministic per-day index for idempotency keys.

- `services/conversation_slicer.py`
  Normalizes raw provider messages, filters low-value records, and splits messages into time-based conversation sessions.

- `services/affinity_signal_analyzer.py`
  Builds the LLM prompt, takes an injected AstrBot `context`, resolves a provider via `get_provider_by_id(model_id) or get_using_provider()`, calls `await provider.text_chat(prompt=...)`, parses strict JSON (with `completion_text` None-guard), validates signal events, and returns sanitized signals. Wrapped in try/except for fallback.

- `core/stage_progression.py`
  Pure helpers for stage advancement: `advance_stage(unlocked_stage, target_stage, max_steps)`, `demote_stage(unlocked_stage, target_stage)`, stage index helpers, and bank computation. No DB access, fully unit-testable.

- `tests/test_conversation_slicer.py`
  Tests session splitting, filtering, message limits, and overlap behavior.

- `tests/test_review_signals.py`
  Tests JSON validation, score mapping (table values pass through, no ±5 clamp), negative cap, confidence filtering, and deterministic event keys.

- `tests/test_affinity_signal_analyzer.py`
  Tests analyzer parsing and rejection of unsafe or malformed model output using a fake context whose provider returns canned `completion_text`, without making network calls.

- `tests/test_stage_progression.py`
  Tests one-step-per-day advancement, no offline back-fill, immediate demotion, and bank accounting.

Modify:

- `core/rules.py`
  Remove positive daily caps from the positive path (`DEFAULT_REALTIME_POSITIVE_CAP`, `DEFAULT_DAILY_REVIEW_POSITIVE_CAP`, `DEFAULT_DAILY_POSITIVE_CAP`). Keep negative caps. Add `affinity_daily_refresh_hour` / `max_steps_per_day` defaults if not living in config.

- `core/models.py`
  Add `AffinityEventType.STAGE_UNLOCK` for stage advancement/demotion audit events (`score_delta = 0`). Store detailed LLM signal type in `metadata_json`; reuse existing types where possible.

- `core/stage.py`
  `effective_stage` reads from `unlocked_stage` instead of recomputing from score directly (keep `lover_locked` override precedence).

- `core/memory_provider.py`
  Add a thin read-only `get_conversation_messages(user_id, event_date)` on the Engram-side provider that wraps `db.get_all_raw_messages(user_id, start, end)` and maps `RawMemory` fields (`uuid`→message_id, `timestamp`→created_at, `group_id`/`role`/`content`/`user_id`). Treat the method as optional in validation so existing deployments without it still pass.

- `db_manager.py`
  Add `unlocked_stage` and `last_stage_advance_date` columns to `AffinityUserState`. Extend `recompute_user_state_from_events` to replay `STAGE_UNLOCK` events in time order to rebuild `unlocked_stage` / `last_stage_advance_date` (these cannot be derived from score sums).

- `services/daily_review_service.py`
  Integrate conversation slicing, signal analysis, scoring, dry-run diagnostics, write path, force rebuild cleanup, fallback behavior, and a post-scoring stage-advancement step.

- `services/scheduler.py`
  After daily review scoring, invoke the stage-advancement pass (one step per user per refresh).

- `handlers/commands.py`
  Expand `好感回顾 ... dry_run` output to show session counts, candidate counts, valid signal counts, discarded counts, score totals, current stage, and bank balance.

- `_conf_schema.json`
  Add LLM review config switches, slicing thresholds, `affinity_review_llm_model`, `affinity_review_negative_cap`, `affinity_stage_advance_*`, `affinity_daily_refresh_hour`. Do NOT add positive-cap / single-event-cap keys.

- `tests/conftest.py`
  Extend fake provider with `get_conversation_messages()`.

- `tests/test_daily_review.py`
  Add integration tests for LLM review events, dry-run, idempotency, force rebuild, fallback, and stage advancement / banking.

Do not modify:

- `main.py` unless configuration injection is required for constructing new services.

---

## Task 1: Remove Positive Caps And Add Configuration

**Files:**
- Modify: `core/rules.py`
- Modify: `_conf_schema.json`
- Test: `tests/test_rules.py`, `tests/test_daily_review.py`

> Goal: positive affinity no longer has a daily cap. Positive deltas accumulate freely into the real score (the bank); positive *progress* is throttled by stage advancement (Task 13), not by a score cap. Negative caps remain.

- [ ] **Step 1: Write failing tests for cap removal**

Assert that the positive-cap constants are gone (or no longer applied on the positive path), and the negative cap remains:

```python
import astrbot_plugin_affinity.core.rules as rules


def test_positive_daily_caps_removed():
    # Positive daily caps must no longer exist as active constraints.
    assert not hasattr(rules, "DEFAULT_DAILY_REVIEW_POSITIVE_CAP")
    assert not hasattr(rules, "DEFAULT_REALTIME_POSITIVE_CAP")
    assert not hasattr(rules, "DEFAULT_DAILY_POSITIVE_CAP")


def test_negative_cap_retained():
    assert rules.DEFAULT_DAILY_REVIEW_NEGATIVE_CAP == -50
```

Also add a positive-path test in `tests/test_daily_review.py` proving several large positive events sum without being clipped to any positive daily total (only global `[-100, 520]` clamp applies).

- [ ] **Step 2: Run the failing test**

Run from `E:\AI\shouban` (bash shell; use forward slashes):

```bash
python -m pytest astrbot_plugin_affinity/tests/test_rules.py -q
```

Expected: FAIL while the positive-cap constants still exist.

- [ ] **Step 3: Remove positive caps in `core/rules.py`**

Delete `DEFAULT_REALTIME_POSITIVE_CAP`, `DEFAULT_DAILY_REVIEW_POSITIVE_CAP`, `DEFAULT_DAILY_POSITIVE_CAP`. Keep `DEFAULT_DAILY_REVIEW_NEGATIVE_CAP = -50`, `DEFAULT_NEGATIVE_CAP`, and other negative/memory caps. `apply_daily_cap` stays (used by the negative path only).

Grep for the removed constants and remove their usages on the positive path in `services/affinity_service.py` and `services/daily_review_service.py` (these are touched in detail in Task 4/7; here just ensure rules.py compiles and negative path is untouched).

- [ ] **Step 4: Add config schema defaults**

In `_conf_schema.json`, add (note: NO positive-cap or single-event-cap keys):

```json
"affinity_llm_review_enabled": {
  "description": "启用基于聊天时间片的 LLM 好感回顾",
  "type": "bool",
  "default": false
},
"affinity_llm_review_min_confidence": {
  "description": "LLM 好感事件最低置信度",
  "type": "float",
  "default": 0.65
},
"affinity_review_llm_model": {
  "description": "好感回顾分析使用的模型 id，留空使用当前默认 provider",
  "type": "string",
  "default": ""
},
"affinity_review_session_gap_minutes": {
  "description": "每日回顾会话切片间隔分钟数",
  "type": "int",
  "default": 45
},
"affinity_review_max_messages_per_session": {
  "description": "每日回顾单个会话片段最多消息数",
  "type": "int",
  "default": 80
},
"affinity_review_negative_cap": {
  "description": "每日负向好感下限",
  "type": "int",
  "default": -50
},
"affinity_stage_advance_enabled": {
  "description": "启用关系阶段每日推进",
  "type": "bool",
  "default": true
},
"affinity_stage_advance_max_steps_per_day": {
  "description": "每天最多推进的关系阶段格数",
  "type": "int",
  "default": 1
},
"affinity_daily_refresh_hour": {
  "description": "每日刷新与阶段推进时间（小时）",
  "type": "int",
  "default": 4
}
```

Use `false` for `affinity_llm_review_enabled` initially so existing deployments keep deterministic hard-rule behavior until the analyzer is wired (the LLM API itself is stable, this is only a gradual-rollout switch).

- [ ] **Step 5: Run targeted tests**

```bash
python -m pytest astrbot_plugin_affinity/tests/test_rules.py astrbot_plugin_affinity/tests/test_daily_review.py -q
```

Expected: PASS.

---

## Task 2: Add Conversation Message Provider Contract

**Files:**
- Modify: `core/memory_provider.py`
- Modify (Engram side): `astrbot_plugin_engram/core/affinity_provider.py`
- Modify: `tests/conftest.py`
- Test: `tests/test_plugin_startup.py`

> Confirmed: Engram already stores raw messages and can query by user + time range via `db.get_all_raw_messages(user_id, start, end, limit)`. `RawMemory` has `uuid` / `user_id` / `role` / `content` / `timestamp` / `group_id` / `session_id` / `member_id`. So this task is a thin mapping layer, not a new storage feature.

- [ ] **Step 1: Inspect current provider validation**

Read `core/memory_provider.py` (the `REQUIRED_PROVIDER_METHODS` tuple and `provider_missing_methods`). Note `get_conversation_messages` must stay OUT of the required tuple so existing Engram builds still validate.

- [ ] **Step 2: Write failing tests for optional conversation method**

Add tests proving:

- Existing fake providers WITHOUT `get_conversation_messages` still pass `resolve_affinity_memory_provider`.
- A provider WITH `get_conversation_messages` is accepted and callable.
- Daily review detects absence and falls back (full fallback test lives in Task 12).

```python
assert resolve_affinity_memory_provider(FakeContext([EngramStar()])) is EngramStar.affinity_memory_provider
```

- [ ] **Step 3: Implement the Engram-side adapter method**

In `astrbot_plugin_engram/core/affinity_provider.py`, add a read-only method that reuses the existing `_to_date_range` helper and `db.get_all_raw_messages`:

```python
async def get_conversation_messages(self, user_id, event_date) -> list[dict]:
    start, end = _to_date_range(event_date)
    rows = _safe_call(self.db, "get_all_raw_messages", user_id, start, end, None, default=[]) or []
    result = []
    for row in rows:
        result.append({
            "message_id": str(getattr(row, "uuid", "") or ""),
            "user_id": str(getattr(row, "user_id", "") or ""),
            "role": str(getattr(row, "role", "") or ""),
            "content": str(getattr(row, "content", "") or ""),
            "created_at": str(getattr(row, "timestamp", "") or ""),
            "chat_type": "group" if getattr(row, "group_id", None) else "private",
            "group_id": getattr(row, "group_id", None),
            "member_id": getattr(row, "member_id", None),
            "session_id": str(getattr(row, "session_id", "") or ""),
        })
    return result
```

Keep it defensive (`_safe_call`, `getattr` defaults) to match the existing provider style.

- [ ] **Step 4: Extend fake provider**

In `tests/conftest.py`, add a `get_conversation_messages` returning a small deterministic conversation (used by Task 7). Default fake can return `[]`:

```python
async def get_conversation_messages(self, user_id, event_date):
    return []
```

- [ ] **Step 5: Run provider tests**

```bash
python -m pytest astrbot_plugin_affinity/tests/test_plugin_startup.py -q
```

Expected: PASS after optional-method handling.

---

## Task 3: Implement Conversation Slicing

**Files:**
- Create: `services/conversation_slicer.py`
- Create: `tests/test_conversation_slicer.py`

- [ ] **Step 1: Write failing tests for message normalization**

Test that raw dict messages normalize into a stable internal structure:

```python
def test_normalizes_provider_message():
    message = normalize_message({
        "message_id": "m1",
        "user_id": "u1",
        "role": "user",
        "content": "hello",
        "created_at": "2026-05-31T20:10:00",
        "chat_type": "private",
    })

    assert message.message_id == "m1"
    assert message.created_at.isoformat().startswith("2026-05-31T20:10:00")
```

- [ ] **Step 2: Write failing tests for filtering**

Cover:

- empty content removed
- command messages beginning with `/查询好感`, `/好感回顾`, `查询好感`, `好感回顾` removed
- system role removed
- normal user/assistant messages retained

- [ ] **Step 3: Write failing tests for gap-based slicing**

Use messages at `20:00`, `20:10`, and `21:05`; with `gap_minutes=45`, expect two sessions.

- [ ] **Step 4: Write failing tests for max messages**

With `max_messages=3`, five messages should produce two sessions.

- [ ] **Step 5: Implement dataclasses and slicer**

Create:

```python
@dataclass(frozen=True, slots=True)
class ConversationMessage:
    message_id: str
    user_id: str
    role: str
    content: str
    created_at: datetime
    chat_type: str = "private"
    group_id: str | None = None


@dataclass(frozen=True, slots=True)
class ConversationSession:
    session_id: str
    user_id: str
    event_date: date
    messages: list[ConversationMessage]
```

Functions:

- `normalize_message(raw: dict) -> ConversationMessage | None`
- `is_low_value_message(message: ConversationMessage) -> bool`
- `slice_conversation(user_id, event_date, raw_messages, gap_minutes=45, max_messages=80, overlap_messages=3) -> list[ConversationSession]`

- [ ] **Step 6: Run slicer tests**

```bash
python -m pytest astrbot_plugin_affinity/tests/test_conversation_slicer.py -q
```

Expected: PASS.

---

## Task 4: Define Relationship Signals And Scoring

**Files:**
- Create: `core/review_signals.py`
- Create: `tests/test_review_signals.py`

- [ ] **Step 1: Write failing tests for valid signal parsing**

Test input:

```python
payload = {
    "type": "self_disclosure",
    "direction": "positive",
    "intensity": 2,
    "confidence": 0.84,
    "evidence": "用户分享了近期压力",
    "message_refs": ["m1", "m2"],
}
```

Expected: accepted as a `ReviewSignal`.

- [ ] **Step 2: Write failing tests for invalid signal rejection**

Reject:

- unknown `type`
- missing `evidence`
- `intensity` outside `1..3`
- `confidence < min_confidence`
- `direction` inconsistent with the event type

- [ ] **Step 3: Write failing tests for scoring**

Examples (score table values pass through directly — NO ±5 clamp):

```python
assert score_signal(signal("casual_warmth", intensity=1)) == 2      # base 1 + per_intensity 1
assert score_signal(signal("self_disclosure", intensity=2)) == 7     # base 3 + per_intensity 2*2
assert score_signal(signal("trust_signal", intensity=3)) == 12       # base 5 + 3*3 -> capped at max 12
assert score_signal(signal("hostility", intensity=1)) == -13         # base -8 + per_intensity -5
```

Use the spec score table (§8). Each value is bounded only by that event's own `max` / `min`. There is no global single-event ±5 clamp. Document the exact formula in the test name where ambiguous.

- [ ] **Step 4: Write failing tests for `preference_discovered` NOT scored**

`preference_discovered` LLM signals must NOT contribute score, to avoid double-counting with the hard-rule profile delta (`+10` per item). Assert `score_signal(signal("preference_discovered"))` is excluded from the scored set (returns 0 / is filtered before scoring). The only `+10` source is the profile delta path.

- [ ] **Step 5: Write failing tests for deterministic idempotency suffix**

The suffix must be stable and reproducible WITHOUT depending on LLM free text (`evidence`) or order. Per spec §10, the per-event key is `daily-llm:<user>:<date>:<event_type>:<index>`, where `<index>` is a deterministic ordinal assigned during aggregation (by session order then in-session order). Assert two aggregation runs over the same session inputs yield the same `(event_type, index)` ordering.

- [ ] **Step 6: Implement review signal module**

Include:

- `POSITIVE_SIGNAL_TYPES`
- `NEGATIVE_SIGNAL_TYPES`
- `NEUTRAL_SIGNAL_TYPES`
- `SCORED_SIGNAL_TYPES` (positive + negative minus `preference_discovered`)
- `ReviewSignal`
- `validate_signal_payload(payload, min_confidence=0.65) -> ReviewSignal | None`
- `score_signal(signal: ReviewSignal) -> float` (table-driven, bounded by per-type max/min only)
- `assign_event_indices(signals) -> list[tuple[ReviewSignal, int]]` (deterministic ordinal for idempotency keys)

- [ ] **Step 7: Run signal tests**

```bash
python -m pytest astrbot_plugin_affinity/tests/test_review_signals.py -q
```

Expected: PASS.

---

## Task 5: Implement LLM Signal Analyzer Shell

**Files:**
- Create: `services/affinity_signal_analyzer.py`
- Create: `tests/test_affinity_signal_analyzer.py`

- [ ] **Step 1: Write failing tests using a fake context**

The analyzer takes an injected AstrBot `context` (NOT a bespoke client), matching spec §6.1 and Engram's existing usage. Tests inject a fake context whose provider returns canned `completion_text`, so no network/provider is hit:

```python
class FakeProvider:
    def __init__(self, text):
        self._text = text
        self.prompts = []
    async def text_chat(self, prompt):
        self.prompts.append(prompt)
        return SimpleNamespace(completion_text=self._text)

class FakeContext:
    def __init__(self, text):
        self._p = FakeProvider(text)
    def get_using_provider(self):
        return self._p
    def get_provider_by_id(self, model_id):
        return None

ctx = FakeContext('{"events":[{"type":"self_disclosure","direction":"positive","intensity":2,"confidence":0.9,"evidence":"用户分享压力","message_refs":["m1"]}]}')
```

- [ ] **Step 2: Write failing tests for malformed / empty output**

- Malformed JSON → returns no signals, one discard reason, does not raise.
- `completion_text is None` → returns no signals (None-guard), does not raise.
- No provider (`get_using_provider()` returns None) → returns empty result with `errors=["no_provider"]`.

- [ ] **Step 3: Write failing tests for forbidden score output**

If model output contains only `score_delta`, ignore it. If a valid event also includes an extra `score_delta`, drop that field; local scoring is authoritative.

- [ ] **Step 4: Implement analyzer**

```python
class AffinitySignalAnalyzer:
    def __init__(self, context=None, model_id: str = "", min_confidence: float = 0.65):
        self._context = context
        self._model_id = model_id
        self._min_confidence = min_confidence

    async def analyze_session(self, session: ConversationSession) -> SignalAnalysisResult:
        # resolve provider: get_provider_by_id(model_id) or get_using_provider()
        # if no provider -> SignalAnalysisResult(errors=["no_provider"])
        # resp = await provider.text_chat(prompt=...)
        # text = (resp.completion_text or "").strip()  # None-guard
        # parse strict JSON, validate via validate_signal_payload, drop score_delta
        ...
```

`SignalAnalysisResult` includes: `signals`, `raw_event_count`, `discarded_count`, `errors`.

Provider resolution and the whole call are wrapped in try/except; on exception return an empty result with the error recorded (DailyReviewService handles fallback in Task 12). Keep prompt text in this module; the prompt describes the event schema and definitions, NOT score values.

- [ ] **Step 5: Run analyzer tests**

```bash
python -m pytest astrbot_plugin_affinity/tests/test_affinity_signal_analyzer.py -q
```

Expected: PASS.

---

## Task 6: Aggregate And Apply Negative Cap

**Files:**
- Modify: `core/review_signals.py`
- Test: `tests/test_review_signals.py`

> Positive signals have NO cap here — they sum freely and flow into the real score (banking is handled by stage advancement in Task 13). Only the negative daily cap (`-50`) applies.

- [ ] **Step 1: Write failing tests for deduplication**

Same `type`, overlapping `message_refs`, similar evidence → keep only the higher-confidence signal.

- [ ] **Step 2: Write failing tests for negative-only cap**

- Several negative signals summing below `-50` → capped total `-50`.
- Several positive signals summing above `+50` → NOT capped; total passes through (e.g. `+72` stays `+72`).

- [ ] **Step 3: Write failing test that positive and negative do not cancel**

`+40` positive and `-30` negative remain two separate groups; the negative cap is computed on the negative group alone (so `-30` is under `-50`, kept as-is), and the net written delta is `+10`. Positives never consume negative cap budget and vice versa.

- [ ] **Step 4: Implement aggregation helper**

```python
def aggregate_signals(signals: list[ReviewSignal]) -> list[ReviewSignal]:
    # dedup by (type, overlapping refs, similar evidence), keep highest confidence
    ...

def score_signals_with_negative_cap(signals, negative_cap=-50):
    # score each via score_signal (excluding preference_discovered)
    # positives: pass through, no cap
    # negatives: accumulate, clamp group total to negative_cap
    # return list of (signal, delta) preserving deterministic index order
    ...
```

Return `(signal, delta)` pairs (with the deterministic index from Task 4 Step 5) so `DailyReviewService` can write them.

- [ ] **Step 5: Run signal tests**

```bash
python -m pytest astrbot_plugin_affinity/tests/test_review_signals.py -q
```

Expected: PASS.

---

## Task 7: Integrate LLM Review Into DailyReviewService Dry Run

**Files:**
- Modify: `services/daily_review_service.py`
- Modify: `tests/conftest.py`
- Modify: `tests/test_daily_review.py`

- [ ] **Step 1: Add fake provider conversation messages**

In `tests/conftest.py`, return a small conversation with stable message IDs and timestamps.

- [ ] **Step 2: Write failing dry-run integration test**

Instantiate `DailyReviewService` with a fake analyzer that returns one positive and one negative signal.

Expected:

- `dry_run=True` writes no DB rows
- result includes signal-derived events
- total delta equals local rule output
- metadata contains signal type, direction, intensity, confidence, evidence, message refs

- [ ] **Step 3: Extend DailyReviewService constructor**

Add optional dependencies:

```python
def __init__(self, db, provider, backup_dir=None, signal_analyzer=None, llm_review_enabled=False, review_options=None):
    ...
```

Default must preserve existing behavior when `llm_review_enabled=False`.

- [ ] **Step 4: Add private LLM review candidate builder**

Create:

```python
async def _build_llm_review_candidates(self, user_id: str, event_day: date) -> DailyReviewDiagnostics:
    ...
```

It should:

- detect missing `get_conversation_messages`
- slice messages
- analyze sessions
- aggregate signals
- return candidates plus diagnostics

- [ ] **Step 5: Run targeted daily review tests**

```bash
python -m pytest astrbot_plugin_affinity/tests/test_daily_review.py -q
```

Expected: PASS.

---

## Task 8: Write LLM Review Events And Preserve Idempotency

**Files:**
- Modify: `services/daily_review_service.py`
- Modify: `tests/test_daily_review.py`

> Idempotency is coarse-grained per day (spec §10). Do NOT hash LLM free text (`evidence` / `message_refs`) into the key — non-deterministic output would change the key and re-write on a second run.

- [ ] **Step 1: Write failing write-path test**

Run `run_for_user()` with LLM review enabled and a fake analyzer.

Expected DB event:

- `source == "daily_review_llm"`
- `idempotency_key == f"daily-llm:{user_id}:{event_day}:{event_type}:{index}"` (stable index from aggregation, no hash)
- `score_delta` from local scoring (table value, not ±5-clamped)
- `metadata_json` contains type, direction, intensity, confidence, evidence, message refs

- [ ] **Step 2: Write failing day-level idempotency test**

Run the same user/date twice (non-force):

- First run writes events.
- Second run detects an existing `source = "daily_review_llm"` event for the day and **skips entirely** — does not call the analyzer, writes nothing, score unchanged.

Add a test asserting the analyzer is NOT invoked on the second run (e.g. fake analyzer call counter stays at 1).

- [ ] **Step 3: Implement write path + day-level skip**

```python
# skip guard (mirrors the existing hard-rule existing-check at daily_review_service.py)
existing_llm = self.db.get_events_for_day(user_id, event_day, source="daily_review_llm")
if existing_llm and not force_rebuild:
    return DailyReviewResult(..., written=False, skipped=True)

# write
source="daily_review_llm"
source_ref=session_id
idempotency_key=f"daily-llm:{user_id}:{event_day}:{event_type}:{index}"
```

`<index>` comes from `assign_event_indices` (Task 4 Step 5) — deterministic per (event_type) per day.

- [ ] **Step 4: Run daily review tests**

```bash
python -m pytest astrbot_plugin_affinity/tests/test_daily_review.py -q
```

Expected: PASS.

---

## Task 9: Update Force Rebuild Backup And Cleanup

**Files:**
- Modify: `services/daily_review_service.py`
- Modify: `tests/test_daily_review.py`

- [ ] **Step 1: Write failing force rebuild test**

Create `source="daily_review"`, `source="daily_review_llm"`, AND `STAGE_UNLOCK` events for the same day, then run force rebuild.

Expected:

- backup JSON contains all three (both review sources + stage_unlock)
- old events are deleted
- user state is recomputed (score + `unlocked_stage` + `last_stage_advance_date` via replay — see Task 14)
- new events are written once

- [ ] **Step 2: Modify backup query**

Backup should include all same-day review + stage events:

```python
source in ("daily_review", "daily_review_llm")
# plus same-day STAGE_UNLOCK events (event_type == AffinityEventType.STAGE_UNLOCK)
```

If the Peewee helper does not support `IN`, add a small DB manager helper or query directly inside the service.

- [ ] **Step 3: Modify cleanup query**

`_remove_existing_review_events()` should remove both review sources. Note: stage-unlock cleanup interacts with replay — only delete same-day stage_unlock events, then let recompute+re-advance regenerate them, so historical stage progression from prior days is preserved.

- [ ] **Step 4: Run tests**

```bash
python -m pytest astrbot_plugin_affinity/tests/test_daily_review.py -q
```

Expected: PASS.

---

## Task 10: Improve Command Dry-Run Output

**Files:**
- Modify: `services/daily_review_service.py`
- Modify: `handlers/commands.py`
- Modify: `tests/test_commands.py`
- Modify: `tests/test_daily_review.py`

- [ ] **Step 1: Extend result dataclass**

Add diagnostics fields to `DailyReviewResult`:

```python
session_count: int = 0
raw_signal_count: int = 0
valid_signal_count: int = 0
discarded_signal_count: int = 0
fallback_used: bool = False
current_stage: str = ""
bank_balance: int = 0          # stage_index(score_stage(score)) - stage_index(unlocked_stage)
next_advance_stage: str = ""   # stage the next refresh would unlock, or current if none
```

- [ ] **Step 2: Write failing command formatting test**

For `dry_run`, expected output includes:

- session count
- candidate count
- valid signal count
- discarded signal count
- real-score total delta
- current stage and whether the next refresh advances
- bank balance

- [ ] **Step 3: Update `handle_daily_review()`**

Keep concise output for normal mode; include diagnostics + stage/bank lines when available (mirror spec §11 dry-run example).

- [ ] **Step 4: Run command and daily review tests**

```bash
python -m pytest astrbot_plugin_affinity/tests/test_commands.py astrbot_plugin_affinity/tests/test_daily_review.py -q
```

Expected: PASS.

---

## Task 11: Wire Configuration In Plugin Startup

**Files:**
- Modify: `main.py`
- Modify: `tests/test_plugin_startup.py`

- [ ] **Step 1: Write failing startup test**

Create plugin with config:

```python
{
    "enable_affinity": True,
    "affinity_llm_review_enabled": True,
    "affinity_llm_review_min_confidence": 0.7,
    "affinity_review_llm_model": "",
    "affinity_review_session_gap_minutes": 30,
    "affinity_review_max_messages_per_session": 40,
    "affinity_stage_advance_enabled": True,
    "affinity_stage_advance_max_steps_per_day": 1,
    "affinity_daily_refresh_hour": 4,
}
```

Expected `DailyReviewService` receives these options, and the scheduler is constructed with `affinity_daily_refresh_hour`.

- [ ] **Step 2: Add options extraction in `main.py`**

Use existing `_config_get()`. Pull all keys above into a `review_options` dict / scheduler hour.

- [ ] **Step 3: Construct the analyzer with `context`**

The LLM API is stable (`provider.text_chat`, spec §6.1), so construct a real analyzer:

```python
analyzer = AffinitySignalAnalyzer(
    context=self.context,
    model_id=_config_get("affinity_review_llm_model", ""),
    min_confidence=_config_get("affinity_llm_review_min_confidence", 0.65),
)
DailyReviewService(..., signal_analyzer=analyzer,
                   llm_review_enabled=_config_get("affinity_llm_review_enabled", False),
                   review_options=review_options)
```

`affinity_llm_review_enabled` defaults `False` (gradual rollout). When disabled, the analyzer is constructed but not invoked; hard-rule behavior is preserved. The analyzer's own `no_provider` / exception handling (Task 5) covers environments where no provider is configured.

- [ ] **Step 4: Run startup tests**

```bash
python -m pytest astrbot_plugin_affinity/tests/test_plugin_startup.py -q
```

Expected: PASS.

---

## Task 12: Fallback Behavior

**Files:**
- Modify: `services/daily_review_service.py`
- Modify: `tests/test_daily_review.py`

- [ ] **Step 1: Write failing test for missing conversation provider method**

When `llm_review_enabled=True` but provider lacks `get_conversation_messages`, daily review should still use existing hard-rule candidates.

- [ ] **Step 2: Write failing test for analyzer failure**

If analyzer raises an exception, daily review should:

- not crash
- set `fallback_used=True`
- use existing hard-rule candidates
- write only hard-rule events

- [ ] **Step 3: Implement guarded fallback**

Wrap LLM path narrowly. Log errors, but do not swallow DB write errors unrelated to analyzer failure.

- [ ] **Step 4: Run tests**

```bash
python -m pytest astrbot_plugin_affinity/tests/test_daily_review.py -q
```

Expected: PASS.

---

## Task 13: Stage Advancement And Affinity Bank

**Files:**
- Create: `core/stage_progression.py`
- Create: `tests/test_stage_progression.py`
- Modify: `core/models.py` (add `AffinityEventType.STAGE_UNLOCK`)
- Modify: `core/stage.py` (`effective_stage` reads `unlocked_stage`)
- Modify: `db_manager.py` (new columns + replay in recompute)
- Modify: `services/daily_review_service.py` (advance after scoring; demote on negative)
- Modify: `services/scheduler.py` (invoke advance pass)
- Modify: `tests/test_daily_review.py`, `tests/test_stage.py`

> Implements spec §17. Real score = accumulator/bank. Visible `unlocked_stage` advances ≤1 step per day, only at refresh, never back-filled. Negative demotes immediately.

- [ ] **Step 1: Write failing pure-logic tests (`tests/test_stage_progression.py`)**

```python
STAGES = ["初识", "朋友", "挚友", "暧昧", "恋人候选"]

def test_advance_one_step_even_if_bank_large():
    # unlocked=初识, target(score)=暧昧, max_steps=1 -> 朋友
    assert advance_stage("初识", "暧昧", max_steps=1) == "朋友"

def test_no_advance_when_target_not_higher():
    assert advance_stage("暧昧", "暧昧", max_steps=1) == "暧昧"

def test_offline_not_backfilled():
    # advancing once per call; multiple missed days do NOT compound in a single call
    assert advance_stage("朋友", "恋人候选", max_steps=1) == "挚友"

def test_demote_is_immediate_multi_step():
    # negative drop: unlocked=暧昧, target=朋友 -> 朋友 (no one-step limit on the way down)
    assert demote_stage("暧昧", "朋友") == "朋友"

def test_bank_balance():
    assert bank_balance(score_for("暧昧"), "朋友") == 2  # 暧昧 idx - 朋友 idx
```

- [ ] **Step 2: Implement `core/stage_progression.py`**

Pure functions over the `score_stage` ladder (exclude 恋人, which stays confirmation-gated):

- `STAGE_ORDER` / `stage_index(stage)`
- `advance_stage(unlocked, target, max_steps=1)` → move up toward target by at most `max_steps`
- `demote_stage(unlocked, target)` → if target lower, return target (immediate, unbounded down)
- `bank_balance(score, unlocked)` → `stage_index(score_stage(score)) - stage_index(unlocked)`

No DB access.

- [ ] **Step 3: Add `STAGE_UNLOCK` event type + DB columns**

- `core/models.py`: add `STAGE_UNLOCK = "stage_unlock"`.
- `db_manager.py`: add `unlocked_stage = TextField(default=RelationshipStage.STRANGER.value)` and `last_stage_advance_date = DateField(null=True)` to `AffinityUserState`; ensure `create_tables(safe=True)` adds them (add a lightweight column-add migration if the table already exists).
- `core/stage.py`: `effective_stage(...)` returns `unlocked_stage` (keep `lover_locked + confirmed_stage == LOVER` override on top).

- [ ] **Step 4: Write failing replay test**

Assert `recompute_user_state_from_events` rebuilds `unlocked_stage` and `last_stage_advance_date` by replaying `STAGE_UNLOCK` events in time order (score sum alone cannot rebuild stage progress). `STAGE_UNLOCK.score_delta == 0` so it does not affect the score total.

- [ ] **Step 5: Implement `recompute` replay**

In `db_manager.recompute_user_state_from_events`: after summing `score_delta` for the score, iterate events in `id` order; for each `STAGE_UNLOCK`, set `unlocked_stage = metadata.to_stage` and `last_stage_advance_date = event_date`. Then set `effective_stage` from `unlocked_stage` (respecting `lover_locked`).

- [ ] **Step 6: Write failing daily-advance integration test (`tests/test_daily_review.py`)**

- Score already enough for 暧昧 but `unlocked_stage=朋友`; run the advance pass once → unlocked becomes 挚友 (one step), a `STAGE_UNLOCK` event written (from=朋友,to=挚友, score_delta=0), `last_stage_advance_date=today`.
- Run advance again same day → no-op (guard on `last_stage_advance_date == today`).
- Simulate "offline 3 days" (score high, last_advance 3 days ago) → single call advances only one step.

- [ ] **Step 7: Implement advance pass + negative demotion**

In `services/daily_review_service.py`:

```python
def _advance_stage_for_day(self, user_id, event_day):
    state = self.db.get_or_create_user_state(user_id)
    if state.last_stage_advance_date == event_day:
        return
    target = score_stage(state.affinity_score)
    new_stage = advance_stage(state.unlocked_stage, target.value, self.max_steps_per_day)
    if new_stage != state.unlocked_stage:
        # write STAGE_UNLOCK (score_delta=0, metadata from/to, score snapshot)
        # update unlocked_stage + effective_stage
    state.last_stage_advance_date = event_day
    state.save()
```

Negative demotion: wherever negative deltas are applied (`affinity_service.record_negative_event` / negative LLM signals), after the score drops, immediately recompute `target = score_stage(score)`; if `target` is lower than `unlocked_stage`, set `unlocked_stage = demote_stage(...)` and write a `STAGE_UNLOCK` (down) event. No one-step limit on demotion.

Order in `run_for_user` (non-dry-run): score events first, then `_advance_stage_for_day`.

- [ ] **Step 8: Wire scheduler**

In `services/scheduler.py`, the daily loop already calls `run_for_user(user_id, yesterday)`. Ensure the advance pass runs as part of (or right after) that call for each user, using the configured refresh hour. Dry-run must NOT advance or write `STAGE_UNLOCK`.

- [ ] **Step 9: Run tests**

```bash
python -m pytest astrbot_plugin_affinity/tests/test_stage_progression.py astrbot_plugin_affinity/tests/test_stage.py astrbot_plugin_affinity/tests/test_daily_review.py -q
```

Expected: PASS.

---

## Task 14: Full Regression And Documentation Update

**Files:**
- Modify: `README.md`
- Optionally modify: `docs/specs/2026-05-31-affinity-time-sliced-review-design.md` only if implementation discovers design corrections

- [ ] **Step 1: Update README**

Add a short section explaining:

- LLM review is optional/config gated (`affinity_llm_review_enabled`, default off).
- LLM extracts relationship events, not score deltas; local rules score them.
- Positive affinity accumulates freely (the "bank"); the visible relationship stage advances at most one step per day, only at the daily refresh time.
- Offline days are not back-filled; banked affinity is never lost, it just unlocks one step per running day.
- Negative events apply immediately (cap `-50`) and can demote the stage immediately.
- Profile growth is `+10` per item (guarded by Engram), not double-counted via `preference_discovered`.
- dry-run can preview candidate events, stage, and bank balance.

- [ ] **Step 2: Run full test suite**

```bash
python -m pytest astrbot_plugin_affinity/tests -q
```

Expected: all tests pass.

- [ ] **Step 3: Inspect changed files**

This project dir is not a git repo by itself; check from the workspace that contains it (skip if not version-controlled):

```bash
git status --short 2>/dev/null || echo "not a git repo"
```

Expected: only files related to this implementation are modified.

- [ ] **Step 4: Manual smoke checklist**

Verify by reading test output and dry-run command formatting:

- no score values appear in the LLM prompt (only event schema/definitions)
- malformed / None LLM output is ignored, falls back to hard rules
- profile growth remains `+10` per item; `preference_discovered` does not add score
- score-table values pass through (e.g. `trust_signal +12`, `hostility -13`), no ±5 clamp
- NO positive daily cap: large positive days accumulate into the real score
- negative cap is `-50` and applies immediately
- stage advances at most one step per day, only at refresh time; offline days not back-filled
- banked affinity persists across runs (force-rebuild replays `stage_unlock`)
- dry-run does not write database rows
- force rebuild backs up and replaces both review sources + same-day stage_unlock

---

## Execution Notes

- Use TDD for every task: write the failing test, run it, implement, run again.
- Shell is bash on Windows: use forward slashes in paths, `/dev/null` not `NUL`.
- Keep LLM calls injectable via a fake `context` (provider with `text_chat`) and mocked in tests. Do not add network-dependent tests.
- DB schema DOES change here: `AffinityUserState` gains `unlocked_stage` + `last_stage_advance_date`; add a migration/`create_tables` safe-add path.
- Positive affinity has no daily cap; throttling lives entirely in stage advancement. Do not reintroduce positive caps.
- Negative path keeps immediate scoring + `-50` cap + immediate demotion.
- Preserve existing hard-rule daily review behavior when LLM review is disabled.
- Treat `get_conversation_messages()` as optional in provider validation, even though Engram now implements it.
- Do not reintroduce per-message immediate affinity scoring.
- `stage_unlock` events carry `score_delta = 0` and must be replayed in `recompute_user_state_from_events`; they are the only way stage progress survives a rebuild.

