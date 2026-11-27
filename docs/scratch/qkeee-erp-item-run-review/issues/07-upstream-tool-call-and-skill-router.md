# Upstream: `tool_call` wrapper friction and Jev skill-router misfire

Status: ready-for-human
Type: task
Blocked by: none
Findings: F9, F10
Decision: D4 (spec.md)

## Problem

1. **`tool_call` wrapper (hermes-agent lazy tools).** Three failures before the first ERPNext call:
   - `calls` sent as a JSON string → `tool_call 'calls' must be a non-empty array of {name, arguments}` (twice).
   - Two local tools in one call → `Local tools require one entry per tool_call; mixed and multi-local batches are not supported.`
   - A `same_tool_failure_warning` loop warning followed.
   The model guessed the shape from the tool description. The error messages are clear; the description is not.
2. **Jev skill suggestion.** "check the connectivity now" matched `jev-setup` at 0.6. The agent loaded it (6.8k chars) although the turn was about ERPNext connectivity.

## Options (for the owner of each repo)

1. hermes-agent: accept a JSON-string `calls` and parse it; put "one local tool per call" in the `tool_call` description. Or: for a plugin toolset that is always used (`qkeee_erp` on google_chat), register the tools directly instead of behind `tool_search`/`tool_call`.
2. Jev: raise the suggestion threshold, or skip a `setup`/credentials skill when the active toolset already names the subject (here `erp_discover health`).

## Acceptance

- An issue filed with each upstream owner, linked here. No change in erpnext-hermes.
