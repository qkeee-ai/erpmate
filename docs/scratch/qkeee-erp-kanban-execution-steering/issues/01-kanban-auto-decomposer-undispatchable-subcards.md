# Kanban board auto-decomposer makes ERPNext execution cards undispatchable

Status: **open, narrowed back to its original scope (2026-10-27).** Issue 11 (a `setup_steps.py` config gap, fixed `erpnext-hermes@4db8a55`) turned out to be a *second, independent* blocker that was masking this one — with 11 fixed, a kanban worker now genuinely gets `erp_*` tools, which means THIS issue's auto-decomposer/undispatchable-sub-card/no-terminal-state problem is the real remaining blocker for `kanban_create` fan-out specifically (not for a single manually-`specify`d task, which now works). Track 1 (skill-level workaround) stays shipped and correct; Track 2 (upstream ask) is still open and now the only thing standing between this repo and real `kanban_create`-based ERPNext job fan-out.

## Problem

Evidence: `erpnext-hermes/logs/demo-procurement-data.log` lines 64-233, repeated near-verbatim at lines 1160-1261.

The orchestrating session called `kanban_create` for 5 index cards describing the procurement data-setup work. The board's auto-decomposer immediately split them into 9+ sub-cards. Each sub-card dispatched a worker that blocked on arrival, because workers on this deployment have no `erp_*` tools at all (`/opt/data/.hermes/plugins/` is empty for kanban-dispatched sessions — already known, see agent memory on kanban ERP cards being index-only).

Worse than the missing-tools problem by itself: 2 of the 5 root cards became "decomposition roots." Every time their children complete, the root re-wakes and re-dispatches, and rejects `kanban_complete` on the root with "stale/already terminal." There is no way to close a decomposition-root card from the orchestrating chat session once its children are done — it just keeps re-waking. The orchestrator burned roughly 1,500 words of reasoning (log lines 90, 1182-1183) agonizing over whether to force-close the card (dishonest — the work wasn't done the way the card describes) or keep letting it loop, because the kanban protocol has only a `done` terminal state, no `cancelled`/`wont-do`.

## Impact

Any future "run this as a kanban series" request for ERPNext work hits this immediately, independent of which doctypes or confirmation mechanism are involved. Systemic, not a one-off — the auto-decomposer behavior is a board-level default, not something tied to this particular task's wording.

## Proposed fix

Two tracks:

1. **Immediate (skill-level workaround). Done 2026-10-23.** `00-conventions.md` in `qkeee-erp-associate` now has a hard rule: don't use `kanban_create` for ERPNext execution work on the default board; run it as a spec in the session that already has `erp_*` tools instead.
2. **Upstream (hermes-agent). Still open.** File against hermes-agent: (a) a real `kanban_cancel`/`wont-do` terminal state so an orchestrator isn't forced to choose between a dishonest `done` and an infinite re-wake loop; (b) a way to either disable auto-decomposition per-card/per-board, or give kanban-dispatched workers the same tool access as the dispatching session, so a decomposed sub-card isn't automatically undispatchable for ERPNext work.

## Comments

2026-10-23: Track 1 shipped — see `erpnext-hermes/skills/qkeee-erp/qkeee-erp-associate/references/00-conventions.md` ("Don't use `kanban_create` to fan out ERPNext execution work...") and `CHANGELOG.md` row `DP-K 2026-10-23`. Track 2 is a hermes-agent (upstream) ask, not something this repo can fix directly — left `ready-for-human` to file it there. ADR 0008 (job-level email approval) is a related but separate decision; it does not fix this issue.

2026-10-26: Live, methodical re-investigation (prompted by a direct challenge to the "avoid kanban entirely" conclusion — the kanban_origin.py/RIB-09/10 architecture was explicitly built so kanban COULD run ERPNext writes, so "it's fundamentally incompatible" needed re-checking, not just asserting). Found the auto-decomposer framing in this issue was not (at the time) the blocker being hit: even with `auto_decompose: false`, correct `--assignee dev-erp`, clean `setup status` (P3 included), and a full container restart, a kanban-dispatched worker **still had zero `erp_*` tools**. Filed as issue 11, believed at the time to be an upstream hermes-agent bug.

2026-10-27: Issue 11 turned out to be our own `setup_steps.py` config gap, not a hermes-agent bug — fixed and live-confirmed (`erpnext-hermes@4db8a55`). With it fixed, a manually-`specify`d atomic kanban task now gets `erp_*` tools correctly. That makes THIS issue real again on its own terms: `kanban_create`'s auto-decomposition into undispatchable sub-cards, and the no-`cancelled`/`wont-do`-terminal-state re-wake loop, are unrelated to issue 11 and still unfixed. Track 2 is still the open ask here.
