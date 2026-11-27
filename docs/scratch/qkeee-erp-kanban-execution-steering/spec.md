# Kanban-based steering and execution for large ERPNext jobs

Status: needs-triage

## Context

2026-10-23 demo-procurement kanban run (`erpnext-hermes/logs/demo-procurement-data.log`) tried to run a multi-doctype ERPNext data-setup job as a "kanban series of tasks," per the user's own request. The run surfaced blockers to kanban-orchestrated ERPNext execution that are independent of each other and independent of the already-tracked confirmation-code friction ([[qkeee-erp-demo-procurement-domain-less-decision]]).

This directory tracks those blockers. ADR 0008 (`docs/adr/0008-email-approval-for-kanban-jobs.md`) addresses the confirmation-channel half of the problem (headless kanban workers can't receive a chat-paste code) but explicitly does not fix either issue below.

## Issues

- `issues/01-kanban-auto-decomposer-undispatchable-subcards.md` — board architecture, skill-level workaround shipped, upstream ask open
- `issues/02-session-restart-replays-stale-compaction.md` — needs-triage, blast-radius check not yet done
- `issues/03-job-level-approval-token-and-storage.md` — ADR 0008 implementation, part 1: `job_token()` + durable cross-task storage (open design decision: which store)
- `issues/04-notify-job-approval-email-operation.md` — ADR 0008 implementation, part 2: the plan email (`notify.job_approval_request`); blocked by 03
- `issues/05-inbound-reply-binding-and-sender-check.md` — ADR 0008 implementation, part 3: binding the reply (code + sender match); blocked by 03
- `issues/06-kanban-child-task-job-token-check.md` — ADR 0008 implementation, part 4: child tasks consume the approval; blocked by 03, 05, 07
- `issues/07-job-render-entry-point-missing.md` — found live 2026-10-24: `prepare_job()` called nowhere outside tests. Coded + committed (`erp_tools.py` `batches` arg); live round trip open
- `issues/08-spec-file-write-outside-safe-root.md` — found live 2026-10-24, pre-existing host quirk unrelated to ADR 0008; doc fix shipped (`00-conventions.md` Task spec row)
- `issues/09-confirm-job-entry-point-missing.md` — found 2026-10-24 during reanalysis of 07: `verify_job_reply()` also called nowhere outside tests. Coded + committed (`phase="confirm_job"`); live round trip open
- `issues/10-report-back-silent-kanban-substitution.md` — live test 2026-10-24 confirmed 03-09 work end to end (job stored, email sent). Found: agent didn't explain the kanban→job-email substitution, and the write_file bug (issue 08) recurred (likely stale session, not a doc gap). Both promoted to standalone paragraphs in `00-conventions.md`
- `issues/11-kanban-worker-plugin-never-registers.md` — **fixed, 2026-10-27 (`erpnext-hermes@4db8a55`), live-confirmed on dev-erp.** Supersedes issue 01's original framing. Real root cause (the 2026-10-26 "PluginManager cache-key mismatch" theory was cross-validated against the code and found wrong): `setup_steps.py` P1 wrote `known_plugin_toolsets.cli` but never `platform_toolsets.cli` itself, so `hermes_cli.tools_config._get_platform_tools()` permanently excluded `qkeee_erp` from the `cli` platform — the Kanban dispatcher's and every `chat -q` worker's own platform — regardless of `register()` always succeeding. Fixed by extending P1 to also write `platform_toolsets.cli`, mirroring its existing `google_chat` handling. **Existing profiles must re-run `qkeee-erp setup --apply-profile-fixes` once** to pick this up.

2026-10-29/30: a second live round trip (`erpnext-hermes/logs/sales-demo-29-Oct/prompt.log`) confirmed issues 07/09's job-email path works end to end but surfaced the respawn-token-orphan bug recurring (not one-off), a confirmed-broken cross-batch placeholder, a triage-stage dead end with no agent-side exit, and comment-only block notification. ADR 0010 (`docs/adr/0010-job-template-approval-draft-park-dual-channel-monitor.md`, proposed, pending sign-off) decides the fix shape: job approval covers the plan template (not frozen literal values), an unapprovable step parks as Draft instead of blocking the card, every park/block fires on email + Google Chat together, and the originating session's channel is bound to the job as its monitor of record for a scheduled poller to address later updates to. Broken into parallel-sized tickets:

- `issues/12-triage-dead-end-no-agent-side-exit.md` — ready-for-human (upstream board capability), independent
- `issues/13-step4-approval-double-gate.md` — ready-for-agent, independent
- `issues/14-sales-order-item-price-side-effect-doc.md` — ready-for-agent, independent
- `issues/15-anchor-and-per-task-spec-pattern.md` — ready-for-agent, independent
- `issues/16-job-approval-doctype-schema-extension.md` — ready-for-agent, blocked by ADR 0010 sign-off only; foundation for 17, 18, 19, 21, 22
- `issues/17-job-resume-idempotent-token.md` — ready-for-agent, blocked by 16
- `issues/18-cross-batch-placeholder-resolution.md` — ready-for-agent, blocked by 16; blocks 19
- `issues/19-draft-park-write-path.md` — ready-for-agent, blocked by 16, 18
- `issues/20-headless-worker-batch-op-guard.md` — ready-for-agent, independent
- `issues/21-notify-job-status-update-dual-channel.md` — ready-for-agent, blocked by 22
- `issues/22-job-monitor-channel-binding.md` — ready-for-agent, blocked by 16; blocks 21, 23
- `issues/23-cron-job-status-poller.md` — ready-for-agent, blocked by 21, 22
- `issues/24-tool-cookbook-worked-examples-refresh.md` — done 2026-10-30 (erpnext-hermes, wave 5/final — 17-23 all shipped)

## Decisions so far

- Confirmation delivery for kanban jobs: move to one job-level email approval (full plan in the email, Requester approves, ERP admin cc'd) — see ADR 0008 (accepted 2026-10-23, all 3 design questions resolved). This is a separate decision from issues 01/02/11; implementing it (issues 03-09) is now actually reachable from a real kanban dispatch, issue 11 having been fixed — still needs its own live round trip (SMTP + a real reply bind) to confirm end to end.
- 2026-10-26: confirmed the original premise (kanban-for-ERPNext-writes was always the intended end state, per `kanban_origin.py`/RIB issues 09-10) is correct.
- 2026-10-27: issue 11 fixed — a single, manually-`specify`d (non-decomposed) kanban task now reaches `erp_*` tools correctly on a profile that has re-run setup. The `00-conventions.md` "never use `kanban_create` for ERPNext execution" rule now narrows to what issue 01 alone still requires: don't let the board's auto-decomposer fan a job out into sub-cards (still undispatchable, still no `cancelled`/`wont-do` terminal state). It is no longer a blanket "don't use kanban for ERPNext" rule.
