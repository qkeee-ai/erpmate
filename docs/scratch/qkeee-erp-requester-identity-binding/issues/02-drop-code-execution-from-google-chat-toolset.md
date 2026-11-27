# Drop code_execution from the google_chat toolset

Status: implemented (erpnext-hermes `feat/discovery-write-readiness` ab5ef65, 2026-10-08); dev-erp retest pending
Type: task
Blocked by: none

## Problem

`erpnext-hermes/config.yaml` enables `code_execution` for `google_chat`. Issue 01 makes the connector refuse inside `execute_code`. But an `execute_code` script can still read `$HERMES_HOME/qkeee-erp.env` and call ERPNext over HTTP without the connector.

## Change

Remove `code_execution` from `platform_toolsets.google_chat` in `erpnext-hermes/config.yaml`. Keep it for `cli` if needed.

## Decision needed

Does any skill on this profile need `execute_code` on Google Chat? If yes, keep it, and rely on issue 04 to move the credentials out of reach.

## Comments

**Decided 2026-10-08** (grill-with-docs). No ERP skill needs `execute_code`; `qkeee-erp-associate/SKILL.md:121` forbids it. Other mentions (ocr-and-documents, llm-wiki, research-paper-writing) are optional or cli-only and fall back to terminal `python`. It was added on 2026-10-07 only as a copy of the cli list (`config.yaml:344-348`). Remove `code_execution` from `platform_toolsets.google_chat`; keep it on `cli`.
