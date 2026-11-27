# Sandbox runtime that can reach only Plugin wrapper modules

Status: needs-triage
Type: research
Blocked by: none

## Problem

Code execution for ERPNext tool calls needs a sandbox: an environment where agent-written code runs, that can call `servers/qkeee-erp/*` wrapper functions (issue 02) but has no other network egress and no filesystem path to the credentials file or to `plugin-data/qkeee-erp/`. This is distinct from Isolation (ADR 0006), which protects the gateway's secrets from the agent's commands in general — this sandbox protects the ERPNext call surface from the agent's own generated code, and both are needed together.

Hermes today runs agent commands through a terminal backend (`local` or `ssh`). Neither backend as described in ADR 0002/0006 is a code sandbox with per-process network/filesystem restriction; they isolate the gateway's secrets from a terminal session, not a single script's egress.

## Change

Research what sandboxing the gateway can use for this without a new infra dependency per Deployment: a subprocess with restricted environment and no network namespace, a WASM/QuickJS-style embedded runtime, or a dedicated container with an egress allowlist of exactly the wrapper's own process. Anthropic's own sandboxing writeup (linked from the code-execution article, not yet read) is the first thing to check.

## Done when

A recommendation exists: which mechanism, what it costs to run per Deployment (Docker and host), and what it cannot guarantee (e.g. a subprocess sandbox that only restricts network still shares the host filesystem unless also chrooted).

## Comments
