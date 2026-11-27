# qkeee-erp: code execution for ERPNext tool calls

Status: needs-triage (boundary decision recorded in ADR; sandbox design and benchmarks still open)
Raised: 2026-11-23, after reading Anthropic's "Code execution with MCP" research
Raised by: nikhil.sharma@qkeee.ai
Repos: `erpnext-hermes` (plugin, wrapper generation, sandbox), `agents` (ADR, this spec)
Decisions: `docs/adr/0013-erp-code-execution-only-through-plugin-wrappers.md`
Terms: `CONTEXT.md` (Plugin, Requester Gate, Isolation, Instance, Operator)

## Goal

Cut the context and latency cost of ERPNext tool calls by letting the agent write code against typed wrapper functions instead of calling `erp_*` tools directly, without reopening the hole closed by dropping `code_execution` from chat toolsets (Requester identity binding, issue 02). The sandbox that runs agent-written code must reach ERPNext only through Plugin-generated wrappers — never raw network, never the credentials file.

## Decisions

| # | Question | Decision |
|---|---|---|
| D1 | Where does code execution run | Sandbox reachable only from the `cli` toolset (batch/Kanban runs). Chat platforms keep no `code_execution`, per existing issue 02. |
| D2 | How ERPNext is reached from the sandbox | Only through `servers/qkeee-erp/*` wrapper functions the Plugin generates from the `erp_*` tool registry. No other network egress, no path to the credentials file. |
| D3 | Does code execution change permission enforcement | No. Every wrapper call still passes the Requester Gate and writes the audit log, same as a direct tool call today. |
| D4 | What is still open | Sandbox technology, wrapper-generation mechanism, PII tokenization at the boundary, and whether the claimed 98.7% token saving holds for our own runs. Tracked as issues below. |

## Facts this design rests on

- Issue 02 (`qkeee-erp-requester-identity-binding/issues/02`) removed `code_execution` from `platform_toolsets.google_chat` in `erpnext-hermes/config.yaml` because an `execute_code` script could read `$HERMES_HOME/qkeee-erp.env` and call ERPNext over raw HTTP, bypassing the Plugin. `code_execution` stays on the `cli` toolset.
- ADR 0006 requires proven Isolation (nothing the agent runs can read the gateway's secrets) before the Plugin registers any `erp_*` tool. Isolation is a property of the Deployment's terminal backend; it does not by itself constrain what a sandboxed script is allowed to call. The wrapper-only boundary in ADR 0013 is a narrower, additional restriction on top of Isolation, scoped to the ERPNext call surface specifically.
- Item run and HRMS demo run reviews both show large row sets (Items, GL entries, employee records) passing through the agent loop in full today.
- Kanban execution steering (ADR 0010) polls job/wave status tool-call by tool-call; each poll round-trips through the model.
- The demo procurement domain-less decision confirmation-gates every unscoped Item/Item Group/Purchase Receipt create; the gate currently needs the full record in context to evaluate.

## Risks

- A sandbox that is supposed to be wrapper-only but leaks a raw HTTP path (via an unvetted library, a `fetch` left reachable, or a misconfigured network namespace) silently recreates the issue-02 hole, this time without the chat-toolset restriction to fall back on.
- Filtering/aggregating inside the sandbox before the model sees anything makes the agent's own filter logic a new place a bug can hide: e.g. a filter that silently drops rows a Requester did have permission to see, with no tool-call-level trace in the transcript the way a direct call leaves one. The audit log must capture what the wrapper returned, not just what the model saw.
- PII tokenization at the wrapper boundary (issue 06) must detokenize only inside another wrapper call that itself re-checks the Requester Gate — not inside the sandbox, and not via a lookup the sandbox script controls.
