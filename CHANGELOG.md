# Changelog

## 1.3.0 - 2026-06-05

- Rebalanced realtime chat, daily review, and memory recall scoring.
- Added daily-turn decay for realtime chat scoring: 1-10 turns +3, 11-20 turns +2, and 21+ turns +1.5 before mood multipliers.
- Changed `/好感回顾` to default to a `预览` for the sender and current day, with public modes documented as `预览` / `写入` / `重建`.
- Added global daily negative cap, negative decay, repair-window bonus, mood multipliers, and consecutive interaction rewards.
- Replaced migration level floors with a formula-based level multiplier.
- Added transparent stage progress output and dynamic two-step stage advancement when the affinity bank is high.
- Tightened LLM relationship signal deduplication and confidence thresholds.
- Added automatic schema columns and a standalone optimization migration helper.
