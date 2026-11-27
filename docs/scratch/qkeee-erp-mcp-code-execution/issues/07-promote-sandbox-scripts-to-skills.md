# Promote proven sandbox scripts to Skills

Status: needs-triage
Type: task
Blocked by: 02

## Problem

Once a generated-code flow against the wrappers proves itself (e.g. a stable Item-run creation sequence), it is currently thrown away after the run instead of reused. The code-execution pattern suggests saving such a script as a named function plus a `SKILL.md`, so later runs import it instead of regenerating the same code.

## Change

- Define where a promoted script lives (`skills/qkeee-erp-learned/<name>/` fits the existing Instance Notes convention) and what review gate, if any, it passes before being reusable (a script with a bug reused forever is worse than one regenerated each time).
- Confirm promoted scripts stay subject to the same wrapper-only boundary (ADR 0013) as freshly generated code — a Skill is not an escape hatch to raw network access.

## Done when

A design note exists for where promoted scripts live and what review they need; this is a convention decision, not yet implementation.

## Comments
