# ERPmate: project context

Snapshot written 2026-11-27 from `develop` at `c523cbd`. Update it when the picture changes.

## What it is

ERPmate is a Hermes Agent profile distribution (`distribution.yaml`). It acts as an ERPNext functional consultant and operator for business owners. ERPNext stays the system of record. The agent works through the `qkeee-erp` plugin's `erp_*` tools only. Every read and write goes through one connector with an RBAC pre-check and an audit-log row.

Install path: `hermes profile install github.com/qkeee-ai/erpmate`. A Docker image is also provided.

## Where things are

| Path | Holds |
|---|---|
| `README.md` | Overview, install (host and Docker), updating, usage, design |
| `profile.md`, `SOUL.md` | Scope boundaries; identity and voice |
| `plugins/qkeee-erp/` | The ERPNext connector plugin (`qkeee_erp/core/client.py` is the single access point) |
| `skills/` | `qkeee-erp` (the one associate skill), `adminops`, `email`, `productivity`, `research` |
| `scripts/` | LCM corruption check, kanban job-status poller, weekly request report |
| `tests/` | Python tests for distribution, host runbook and the scripts above |
| `docs/ERPmate — Architecture Document.md` | Product vision and architecture |
| `docs/qkeee-erpnext-feature-inventory.md` | ERPNext feature inventory |
| `docs/scratch/` | Imported planning history: 14 feature folders (spec plus numbered issues). A one-time snapshot from the `agents` repo. See its README. |
| `NOTES.md` | Owner's TODO list and eval scenarios |

## Where the project stands

- **Built and shipped:** the plugin and profile split, requester identity binding, write-path hardening, schema convergence, discovery and write-readiness gates, kanban-first intake and execution steering, manufacturing domain, Request Registry (tracks one ask across email and chat), weekly request report, files.attach write op, bot self-verification read, Google Meet plugin install with patches.
- **Latest work (newest first):** the Google Meet plugin patches (join race, caption toggle, duplicate captions); the planning-history import into `docs/scratch/`; `CLAUDE.md` moved to the repo root.
- **Open items:** many issues in `docs/scratch/` are `ready-for-human`. Their code is done, but the live round trip on DEMO_ERP is still open. Some are `needs-triage`, such as the upstream asks to hermes-agent. The kanban auto-decomposer fan-out problem is still open (`qkeee-erp-kanban-execution-steering`). The Request Registry email Message-ID index is inert until hermes-agent changes.
- **Owner TODO (`NOTES.md`):** quiet working messages, an eval suite, multi-user and multi-channel tests, an end-of-day summary, a weekly report email to the mentor, Google Drive as evidence area, MCP-as-code.

## Conventions

- Issue tracker: local markdown under `.scratch/<feature>/` (history in `docs/scratch/`). Triage labels: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`.
- `CLAUDE.md` points to `docs/agents/*.md` and to `docs/adr/`. Those files do not exist in this repo yet. The ADRs (for example 0006 to 0010) live in the `agents` repo.
- No application-level test runner at the root. Run `python -m pytest tests/` for the Python tests.
