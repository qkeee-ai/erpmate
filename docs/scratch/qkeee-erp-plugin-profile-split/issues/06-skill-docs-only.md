# Split the knowledge: tool usage rules in the Plugin, ERP domain know-how in the Profile

Status: done
Type: task
Blocked by: 04, 05

## Change

- **Plugin:** move the rules for calling the tools safely into the `erp_*` tool descriptions and one load-on-demand skill, `qkeee-erp:usage` (`register_skill`). That covers: pass `prompt_summary`; pass `latest_prompt` verbatim; show `gaps[].prompt` exactly, once per session; never retry a guard block another way; report a permission error as its own failure mode; never grant the bot a role. Write no Profile-specific wording; another agent must be able to use it.
- **Profile skill** (`SKILL.md`, `references/**`): keep ERP domain know-how. Every `python ${HERMES_SKILL_DIR}/scripts/…` becomes the matching `erp_*` tool call, or `hermes qkeee-erp …` marked Operator-only. Point at `qkeee-erp:usage` for the calling rules instead of repeating them. Remove "read `qkeee_erp.active_env`": the agent learns the Instance from `erp_discover health` (issue 11).
- README: "ERPNext credentials" and "Safety & governance" point at the Plugin; host install section points at issue 12's runbook.
- `CHANGELOG.md`: update "Lives in" for moved code.

## Done when

- `grep -rn "scripts/\|client.py\|init_bot.py\|execute_write.py\|active_env" skills/` finds nothing outside CHANGELOG history rows.
- `skill_view("qkeee-erp:usage")` returns the usage skill.

## Comments

2026-10-09 (erpnext-hermes `3eb5fee`, `75f9a83`):
- Plugin skill `qkeee-erp:usage` (`plugins/qkeee-erp/skills/usage/SKILL.md`, `register_skill`); `skill_view("qkeee-erp:usage")` verified under real Hermes. Tool descriptions carry the key rules and point at it.
- Instance Notes promotion: new `erp_discover` action `promotion_plan` (no network, no Requester); `memory_promote` CLI removed.
- SKILL.md + references: tool calls or `hermes qkeee-erp …` marked Operator-only; `cli-cookbook.md` → `tool-cookbook.md`; "prefer a harness HTTP tool" rule replaced by "erp_* tools only"; README + CHANGELOG updated.
- Done-when grep run on `skills/qkeee-erp` (clean outside CHANGELOG history). Over all of `skills/` it also hits productivity/research skills that legitimately ship their own `scripts/`; out of scope.
