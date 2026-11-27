# `~` expands to the wrong home dir on first read_file call

Status: ready-for-human — implemented, tested, committed in hermes-agent (dcc02f0f62); live check open
Type: task
Blocked by: none
Findings: F4

## Problem

First `read_file` attempt failed with "File not found" (L74/79) because `~` expanded to a different home directory than the one the real doc-cache path lives under. The agent then burned 5 calls trying `pip3`/`sudo`/`apt-get` (none available, L184-259) before trying the absolute path directly, which worked cleanly (L269-291) — the tool it needed was already there, the path resolution was just wrong.

Exact file/location in `hermes-agent` not yet identified — needs tracing which process resolves `~` for the `read_file` tool (shell env `$HOME` vs. a hardcoded/different-user home) and why it diverges from the doc-cache path's real location.

## Change

Fix the `~`-expansion so it resolves to the same home directory the doc-cache actually writes under.

## Acceptance

- A fresh run's first `read_file` against a `~`-relative doc-cache path succeeds without a fallback to the absolute path.

## Comments

2026-11-19: Four research passes traced the actual root cause, which was not a simple env-var
mismatch. `to_agent_visible_cache_path()` (`tools/credential_files.py`) hands the agent a literal
`~/.hermes/cache/documents/...` string for home-relative terminal backends (ssh, daytona,
vercel_sandbox) — explicitly meant for the remote shell, and the system prompt's on-disk-reference
text explicitly tells the agent to use `read_file` on that exact string. SSH already resolved `~`
correctly (`_resolve_ssh_path` uses that session's own detected remote home); daytona/vercel_sandbox
had no equivalent — `_host_text()` fell through to `_expand_tilde()`, a Hermes-host-only concept
with zero sandbox awareness, silently producing a host-shaped path `_anchor()` then treated as
already-absolute.

Fixed in `hermes-agent` by mirroring the SSH pattern: a `~`-prefixed container path now resolves
against that plugin sandbox's own detected home (`plugin_sandbox_home()`, already used for cwd
resolution) instead of `_expand_tilde`. Without a live/detectable sandbox home, the path now fails
honestly instead of silently landing on the wrong machine. 5 new tests in
`tests/tools/test_resolve_path.py`. Committed `dcc02f0f62`.

**`/code-review` honesty note:** traced `to_agent_visible_cache_path` for the DEFAULT/local backend
(no container, no SSH — most users): it returns the absolute host path unchanged, never a `~`
shorthand. The common case was never broken by this bug. This fix closes a real but narrower gap
than the ticket's "a fresh run" framing suggests: **daytona and vercel_sandbox specifically**
(ssh was already fine). No raw incident log was available to confirm which backend the original
report actually ran on — if it was local/docker/modal, this commit doesn't touch that case, because
per the trace, no such case exists to fix (the doc-cache path reaching the agent is already
absolute there). Recording this rather than claiming full acceptance-criterion closure.

Live check still open: a `read_file` against a `~`-relative doc-cache path on an actual
daytona/vercel_sandbox-backed session, confirming it now succeeds on the first attempt.
