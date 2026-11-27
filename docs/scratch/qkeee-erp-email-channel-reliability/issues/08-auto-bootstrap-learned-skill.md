# Auto-bootstrap qkeee-erp-learned/<env-tag> on first miss

Status: ready-for-human — implemented, tested, committed in hermes-agent (3d1a21b34e, 53c9ee4c4b); live check open
Type: task
Blocked by: none
Findings: F5

## Problem

Memory pointed at a skill `qkeee-erp-learned/default` for instance pitfalls. `skill_view` returned "Skill 'qkeee-erp-learned' not found" (L1190) — confirmed via filesystem too, the skill genuinely doesn't exist in this profile (distribution.yaml's own comment describes it as self-written at runtime, never shipped). The agent worked around the miss by stuffing a long procedural pitfall paragraph directly into flat memory (L1201), explicitly reasoning out loud that this violates its own system-prompt rule ("procedures belong in skills, not memory") — and doing it anyway under time pressure, because there was no bootstrap path to do the right thing instead.

## Change

On a `skill_view` miss for `qkeee-erp-learned/<env-tag>`, auto-create the skill (empty/templated "Instance Notes" skill for that env tag) instead of leaving the agent to choose between a dangling memory pointer and a rule violation. Then let the agent write the pitfall into the newly-created skill as originally intended.

## Acceptance

- A fresh env tag with no `qkeee-erp-learned/<tag>` skill yet: the first pitfall-recording attempt creates the skill rather than falling back to flat memory.
- Existing flat-memory pitfall text (written this session, L1201) gets migrated into the skill once it exists, so it isn't lost or duplicated.

## Comments

2026-11-19: Implemented in `hermes-agent` (`tools/skills_tool.py`). `skill_view`'s existing
not-found branch (`locate_skill` miss) is generalized: any lookup name matching
`<category>-learned/<tag>` (e.g. `qkeee-erp-learned/prod`) now auto-creates an empty templated
"Instance Notes" SKILL.md via `skill_manager_tool._create_skill` (bypassing the model-authored-content
approval gate — the template is fixed, not model-chosen) before retrying the lookup once. A
`bootstrap_note` field on the success result tells the agent to migrate any pre-existing
MEMORY.md/USER.md pitfall text for that environment into the new skill and remove it from flat
memory, closing acceptance criterion 2 (no generic flat-memory → skill migration is attempted
blind — the agent does it, now that it has somewhere to put it).

Tests added to `tests/tools/test_skills_tool.py::TestLearnedSkillBootstrap` (4 new, all passing):
first-miss creates the skill; second view has no bootstrap note; a non-`*-learned` miss is
untouched; an existing skill under a `*-learned` category is served normally. Full
`tests/tools/test_skills_tool.py` + `tests/tools/test_skill_manager_tool.py` suites pass except one
pre-existing, unrelated Windows path-separator failure
(`TestSameRootDuplicationResolves::test_nested_copy_inside_same_root_does_not_block_bare_name`).

Committed in hermes-agent `main` 3d1a21b34e (not pushed).

**2026-11-19, follow-up (`/code-review` Standards+Spec pass):** the initial commit generalized the
match to any `<category>-learned/<tag>`, bypassing skill_manage's approval gate for categories the
ticket never named. Per user decision, narrowed back to the literal `qkeee-erp-learned` category
only. Also fixed two tools/AGENTS.md HARDLINE violations the review caught: tests now dispatch
through the registry (not bare `skill_view()` calls), and a new E2E test runs against a real
`HERMES_HOME` with `skills.write_approval: true` set — this caught a real bug: `skill_view` reused
stale `roots` on its post-bootstrap retry, so a brand-new profile's very first skill bootstrap
couldn't find what it had just created (the skills dir didn't exist when `roots` was first
resolved). Fixed. Per user decision, acceptance criterion 2 (scripted flat-memory migration) is
left as the `bootstrap_note` hint, not implemented as code — judged safer left to the agent's own
read of memory than a blind scripted move. Committed `53c9ee4c4b`.

Live check (acceptance criterion 1: a fresh env tag's first pitfall-recording attempt creates the
skill, in a real session) still open.
