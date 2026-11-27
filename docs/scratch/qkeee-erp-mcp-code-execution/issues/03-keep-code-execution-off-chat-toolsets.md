# Keep code_execution off chat toolsets; sandbox lives on cli only

Status: ready-for-human
Type: task
Blocked by: none

## Problem

ADR 0013 scopes the new sandbox to the `cli` toolset only, consistent with issue 02 of the Requester identity binding spec (which removed `code_execution` from `google_chat`). Nothing yet guards against a future change re-adding `code_execution` to a chat toolset once the wrapper modules exist and look safe to expose everywhere.

## Change

- Add a repo test (alongside the Distribution test from the plugin/profile split work) that fails if any chat platform's `platform_toolsets` entry in `config.yaml` includes `code_execution`.
- Note in the Plugin's `qkeee-erp:usage` skill doc that the wrapper modules are reachable only from `cli`, so a skill author doesn't assume otherwise.

## Done when

The test exists and fails on a deliberate `config.yaml` edit that adds `code_execution` to `google_chat`; passes on the current config.

## Comments
