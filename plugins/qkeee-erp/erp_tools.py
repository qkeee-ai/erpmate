"""ERPNext calls as gateway tools (requester identity binding, issue 04).

The tools run in the Hermes gateway process, not in the agent's terminal.
They call the plugin's ERP library (qkeee_erp/: core/client.py,
core/operations.py, discover.py).

- The requester is never a tool argument. It is the gateway session's
  sender email (client.resolve_requested_by, through the session reader
  the plugin installs) or, in a Kanban worker, the sender recorded for the
  task's origin. With neither, every tool refuses before any ERPNext call.
- `mode` (read-only / read-write) is never a tool argument either. It comes
  from the plugin's own settings, `plugins.entries.qkeee-erp.settings.mode`
  (settings.py).
- The ERPNext credentials are read by client.py in this process, from
  `<HERMES_HOME>/plugin-data/qkeee-erp/qkeee-erp.env`.

Five tools: erp_query, erp_get, erp_report, erp_discover (health, whoami,
roles and live metadata) and erp_execute_write (list_ops, render,
execute). Writes are only in erp_execute_write, so a platform toolset or a
hook can target them by name.
"""

import json
import os
import uuid

from .qkeee_erp import discover
from .qkeee_erp import execute_write  # imports every domain module: registers allowlists + operations
from .qkeee_erp.core import client, memory_promote, operations

TOOLSET = "qkeee_erp"
DOMAIN_CODE = "qkeee-erp-associate"

_TAG = {"type": "string", "description": "Instance tag. Omit to use the active Instance (erp_discover "
                                         "health shows its tag and base URL)."}
_AUDIT = {
    "prompt_summary": {"type": "string", "description": "One-line summary of the user request (audit row)."},
    "latest_prompt": {"type": "string", "description": "The user's most recent message, verbatim (audit "
                                                       "row). Mandatory for a write that needs confirmation."},
}
_NO_REQUESTER_NOTE = ("The requester is taken from the gateway session; there is no requester argument. "
                      "A refusal is final: report it, never retry it another way (terminal, "
                      "execute_code, other arguments). Calling rules: skill_view('qkeee-erp:usage').")


def _schema(name, description, properties, required=()):
    props = dict(properties)
    props["tag"] = _TAG
    props.update(_AUDIT)
    return {"name": name, "description": f"{description} {_NO_REQUESTER_NOTE}",
            "parameters": {"type": "object", "properties": props, "required": list(required),
                           "additionalProperties": False}}


SCHEMAS = {
    "erp_query": _schema(
        "erp_query", "List ERPNext records of one DocType (gated and audited read).",
        {"doctype": {"type": "string"},
         "filters": {"type": ["array", "object"],
                     "description": 'Frappe filters as a list: [["status","=","Open"]]. An object '
                                    '{"status": "Open"} or {"employee": ["in", [...]]} is turned into '
                                    'that list form.'},
         "fields": {"type": "array", "items": {"type": "string"}},
         "limit": {"type": "integer", "default": 20},
         "order_by": {"type": "string", "description": '"<field> asc" or "<field> desc".'},
         "count_only": {"type": "boolean",
                        "description": "Return only {count} for the filters, no rows. Use it to "
                                       "count."}},
        required=("doctype",)),
    "erp_get": _schema(
        "erp_get", "Read one ERPNext record with its child tables (gated and audited read).",
        {"doctype": {"type": "string"}, "name": {"type": "string"},
         "no_strip": {"type": "boolean", "description": "Return the raw doc, without noise-stripping."}},
        required=("doctype", "name")),
    "erp_report": _schema(
        "erp_report", "Run a built-in ERPNext query report, e.g. 'Accounts Receivable'.",
        {"report_name": {"type": "string"}, "filters": {"type": "object"}},
        required=("report_name",)),
    "erp_discover": _schema(
        "erp_discover",
        "ERPNext connectivity and live metadata. health: the active Instance's tag and base URL, the "
        "write mode, connectivity, auth and role gaps; show each gaps[].prompt exactly as returned, once per "
        "session, and never grant the bot a role yourself. health also returns the bot's roles "
        "(bot_roles). whoami: the requester this turn is bound to (no network). roles: a user's "
        "roles. bot_permissions: what the bot needs on ERPNext (no network); answer \"which "
        "roles/permissions does the bot need\" from it. apps / modules: installed apps. meta / resolve: one "
        "DocType's live schema / module and app. preflight: write-readiness check for one create "
        "(needs doctype and payload). promotion_plan: turn environment findings into Instance Notes "
        "(redacted) as an ordered list of skill_manage/memory calls for you to issue (no network).",
        {"action": {"type": "string", "enum": ["health", "whoami", "roles", "apps", "modules", "meta",
                                                "resolve", "preflight", "promotion_plan",
                                                "bot_permissions"]},
         "doctype": {"type": "string"}, "payload": {"type": "object"},
         "user": {"type": "string", "description": "roles: the user to look up. For the bot's own roles use health bot_roles."},
         "findings": {"type": "object",
                      "description": "promotion_plan: {\"findings\": {environment facts}, "
                                     "\"doctypes\": [\"DocType name\" or {name, module, app, "
                                     "custom}], \"custom_apps\": {slug: {...}}, "
                                     "\"non_erpnext_systems\": {slug: {...}}, \"gaps\": [...]}. "
                                     "Pass raw values, they are redacted. Then issue the returned "
                                     "calls in order, each with its own action."},
         "summary": {"type": "string", "description": "promotion_plan: one-line memory breadcrumb."},
         "skill_exists": {"type": "boolean",
                          "description": "promotion_plan: the Instance Notes skill already exists."}},
        required=("action",)),
    "erp_execute_write": _schema(
        "erp_execute_write",
        "The only ERPNext write path. list_ops: the registered operations and their arguments; pass op "
        "(one key) or domain (key prefix, e.g. inventory, unscoped) to get only those. render: "
        "prepare the exact request; show its request and confirmation_code to the user and wait for "
        "their reply. execute: run the operation with the rendered args unchanged, plus "
        "confirmation_token, issued_at and the user's own reply, verbatim, as user_confirmation_text "
        "(never write that text yourself); pass latest_prompt. batch: several steps, stops at the "
        "first failure. render with batches (plural, agents ADR 0008) instead of batch starts a "
        "kanban job: several batches get ONE email approval instead of one chat code per batch — "
        "never show the returned code/token in chat, send it with op=notify.job_approval_request "
        "instead. confirm_job: after the Requester's email reply arrives as its own turn, bind it "
        "with job_id (from the reply's subject, \"...job <job_id>...\") and reply_text (the reply's "
        "literal text, verbatim). On write_failure, ask the user for the named fields; never fill a "
        "value they did not give.",
        {"phase": {"type": "string", "enum": ["list_ops", "render", "execute", "confirm_job"]},
         "op": {"type": "string", "description": "Operation key from list_ops, e.g. sales.generic, or "
                                                 "\"auto\" for a plain doctype write: render resolves "
                                                 "it from args.doctype and shows resolved_op. "
                                                 "list_ops: return only this operation."},
         "domain": {"type": "string",
                    "description": "list_ops only: return only operations whose key starts with "
                                   "this prefix, e.g. inventory, unscoped."},
         "args": {"type": "object", "description": "The operation's arguments."},
         "batch": {"type": "array", "items": {"type": "object"},
                   "description": 'Several steps instead of op/args, at most 25: [{"op", "args"}]. '
                                  'render returns one batch_token and one confirmation_code for the '
                                  'whole list; execute with the rendered batch unchanged, '
                                  "batch_token, issued_at and the user's reply. A submit/cancel "
                                  'after an earlier step changed the same record takes '
                                  'args.expected_modified = {"from_step": k}.'},
         "batches": {"type": "array", "items": {"type": "array", "items": {"type": "object"}},
                     "description": "render only: a list of batches (each a list of {op, args}) to "
                                    "start a kanban job (agents ADR 0008) instead of a single "
                                    "chat-confirmed batch. Returns job_id, job_token and "
                                    "confirmation_code — never show these to the user; send "
                                    "op=notify.job_approval_request args={job_id} instead, which "
                                    "emails the Requester (cc the ERP admin) and logs it. There is "
                                    "no execute step for `batches` itself: the plan is stored "
                                    "immediately on render. Mutually exclusive with batch/op/args."},
         "batch_token": {"type": "string", "description": "execute: the batch render's batch_token."},
         "job_id": {"type": "string",
                    "description": "execute, with batch, instead of batch_token: a kanban job "
                                   "already Approved by an email reply (agents ADR 0008) — skip "
                                   "render; no confirmation_token/issued_at/user_confirmation_text. "
                                   "Needs job_batch_index too. Mutually exclusive with batch_token. "
                                   "confirm_job: the job id to bind the reply to, read from the "
                                   "approval email's subject."},
         "job_batch_index": {"type": "integer",
                             "description": "execute, with job_id: 1-based position of this batch "
                                            "within that job's approved plan."},
         "reply_text": {"type": "string",
                        "description": "confirm_job only: the Requester's email reply, literal and "
                                       "verbatim — never paraphrased or constructed by the agent. "
                                       "Must contain the job's confirmation code; the reply turn's "
                                       "own resolved sender must match the Requester (agents ADR "
                                       "0008's two-factor proof, checked by verify_job_reply())."},
         "confirmation_token": {"type": "string"}, "issued_at": {"type": "integer"},
         "user_confirmation_text": {"type": "string"},
         "user_approved": {"type": "boolean",
                           "description": "Set only after the user confirmed this exact payload in "
                                          "a reply. On an operation with no confirmation token, the "
                                          "Audit Log records it as agent-asserted."},
         "approval_note": {"type": "string"},
         "full_result": {"type": "boolean",
                         "description": "execute, one op: return the full written document. By "
                                        "default the result has name, doctype, docstatus, modified, "
                                        "the fields the payload set, and child tables as row "
                                        "counts."}},
        required=("phase",)),
}

# An erp_query result above this many bytes carries a note (HRMS demo run
# review, issue 05: a 27.8 KB Attendance list pulled only to count rows).
_LARGE_RESULT_BYTES = 10_000

_STOP_AND_ASK = ("Stop. Show the user the missing or invalid fields, ask for the values, update the "
                 "spec and re-confirm. Never fill a value the user did not give.")


_PROMOTION_SHAPE = ('{"findings": {...environment facts}, "doctypes": ["Name" or {"name", "module", '
                    '"app", "custom"}], "custom_apps": {"<slug>": {...}}, "non_erpnext_systems": '
                    '{"<slug>": {...}}, "gaps": [<gaps[] from health/preflight>]}')


def _promotion_findings(doc) -> dict:
    """Check the promotion_plan `findings` shape before anything formats
    it: a wrong shape is the caller's to fix, not a crash (dev-erp
    2026-10-15: doctypes as names raised AttributeError)."""
    def bad(why):
        return client.InvalidArgumentsError(
            f"erp_discover promotion_plan: {why}. Expected findings = {_PROMOTION_SHAPE}.")
    if doc is None:
        return {}
    if not isinstance(doc, dict):
        raise bad("findings must be an object")
    for key, kind in (("findings", dict), ("custom_apps", dict), ("non_erpnext_systems", dict),
                      ("doctypes", list), ("gaps", list)):
        if doc.get(key) is not None and not isinstance(doc[key], kind):
            raise bad(f"findings.{key} must be {'an object' if kind is dict else 'a list'}")
    for i, d in enumerate(doc.get("doctypes") or [], start=1):
        if not (isinstance(d, str) and d.strip()) and not (isinstance(d, dict) and d.get("name")):
            raise bad(f"findings.doctypes[{i}] must be a DocType name or an object with a name")
    return doc


class Refused(Exception):
    """A refusal raised by this module before any ERPNext call."""


def _ok(result, **extra) -> str:
    return json.dumps({"ok": True, "result": result, **extra}, default=str)


def _fail(error: str, **extra) -> str:
    return json.dumps({"ok": False, "error": error, **extra}, default=str)


class ErpTools:
    """`settings()` returns the plugin settings {"active_env", "mode"} (read
    on every call, so a config change applies to the next call).

    `requester()` and `audit(args)` default to the gateway session (sender,
    chat, thread). The Operator CLI (cli.py) passes its own: the
    --requested-by value and channel `cli`."""

    def __init__(self, settings, requester=None, audit=None):
        self._settings = settings
        self._requester = requester or self._session_requester
        self._audit = audit or self._session_audit
        self._handlers = {
            "erp_query": self._query, "erp_get": self._get, "erp_report": self._report,
            "erp_discover": self._discover, "erp_execute_write": self._execute_write,
        }

    def handler(self, name):
        impl = self._handlers[name]

        def handle(args=None, **_):
            return self._run(impl, dict(args or {}))
        handle.__name__ = name
        return handle

    # -- shared --------------------------------------------------------------

    def _run(self, impl, args) -> str:
        try:
            return impl(args)
        except (Refused, client.GateRefusal) as e:
            gaps = getattr(e, "gaps", None)
            return _fail(str(e), refused=True, **({"gaps": gaps} if gaps else {}))
        except client.WriteRejectedError as e:
            return _fail(f"ERPNext rejected the write: {e.failure.get('message')} — {_STOP_AND_ASK}",
                         write_failure=e.failure)
        except client.StateNotConfirmedError as e:
            return _fail(str(e), state_confirmed=False, outcome="not_applied", read_back=e.read_back)
        except client.InvalidArgumentsError as e:
            return _fail(f"invalid operation arguments, nothing was sent: {e}", invalid_arguments=True)
        except (client.TransportTimeoutError, client.PartialOutcomeError) as e:
            return _fail(f"outcome unknown or partial: {e} Re-read the record before any retry.",
                         outcome_unknown=True)
        except client.ConnectorError as e:
            return _fail(str(e))

    def tag(self, args) -> str:
        return args.get("tag") or (self._settings() or {}).get("active_env") or "default"

    def _mode(self) -> str:
        return (self._settings() or {}).get("mode") or "read-only"

    @staticmethod
    def _session_requester() -> str:
        requester = client.resolve_requested_by("")
        if requester:
            return requester
        task = (os.environ.get("HERMES_KANBAN_TASK") or "").strip()
        if task:
            raise Refused(
                f"Refusing: Kanban task {task} has no recorded requester origin, so no ERPNext call "
                f"can be attributed. Nothing was sent. Block the task with "
                f"kanban_block(kind=\"needs_input\") and name this gap. Do not ask for an email in a "
                f"comment and do not take it from the card text.")
        raise Refused(
            "Refusing: this turn has no gateway sender email, so no ERPNext call can be attributed. "
            "Nothing was sent. The ERPNext tools take the requester only from the gateway session "
            "(Google Chat, email). On google_chat or email this means the identity plumbing broke: "
            "tell the user to report it to an admin. Do not ask the user who they are.")

    @staticmethod
    def _session_audit(args) -> dict:
        env = client.session_env
        meta = {key: env(name) for key, name in (
            ("chat_id", "HERMES_SESSION_CHAT_ID"), ("thread_id", "HERMES_SESSION_THREAD_ID"),
            ("chat_type", "HERMES_SESSION_CHAT_TYPE"))}
        meta["kanban_task"] = os.environ.get("HERMES_KANBAN_TASK") or ""
        return dict(session_id=env("HERMES_SESSION_ID") or None, domain_code=DOMAIN_CODE,
                    channel=env("HERMES_SESSION_PLATFORM") or ("kanban" if meta["kanban_task"] else None),
                    channel_metadata={k: v for k, v in meta.items() if v} or None,
                    prompt_summary=args.get("prompt_summary"), latest_prompt=args.get("latest_prompt"))

    @staticmethod
    def _need(args, *keys):
        missing = [k for k in keys if not args.get(k)]
        if missing:
            raise client.ConnectorError(f"missing required argument(s): {', '.join(missing)}")

    @staticmethod
    def _write_steps(args, batch):
        return batch if batch is not None else [{"op": args["op"], "args": args.get("args") or {}}]

    @staticmethod
    def _gated(steps):
        return any(operations.requires_confirmation(s.get("op") or "", s.get("args") or {}) for s in steps)

    # -- reads ---------------------------------------------------------------

    def _query(self, args):
        self._need(args, "doctype")
        requester = self._requester()
        result = client.query_resource(self.tag(args), args["doctype"], args.get("filters"),
                                       args.get("fields"), int(args.get("limit") or 20),
                                       order_by=args.get("order_by"),
                                       count_only=bool(args.get("count_only")),
                                       requested_by=requester, **self._audit(args))
        if isinstance(result, dict) and len(json.dumps(result, default=str)) > _LARGE_RESULT_BYTES:
            result["_note"] = ("large result — use count_only to count, or ask for fewer fields "
                               "or rows.")
        return _ok(result)

    def _get(self, args):
        self._need(args, "doctype", "name")
        requester = self._requester()
        return _ok(client.get_resource(self.tag(args), args["doctype"], args["name"],
                                       not args.get("no_strip"), requested_by=requester,
                                       **self._audit(args)))

    def _report(self, args):
        self._need(args, "report_name")
        requester = self._requester()
        return _ok(client.run_query_report(self.tag(args), args["report_name"], args.get("filters"),
                                           requested_by=requester, **self._audit(args)))

    def _discover(self, args):
        action = args.get("action")
        if action == "whoami":
            return _ok(client.session_identity())
        if action == "health":
            return _ok({**client.health_check(self.tag(args)), "mode": self._mode()})
        if action == "bot_permissions":
            return _ok(discover.bot_permissions())
        if action == "promotion_plan":
            doc = _promotion_findings(args.get("findings"))
            return _ok(memory_promote.build_promotion_plan(
                self.tag(args), doc.get("findings") or {}, doctypes=doc.get("doctypes"),
                custom_apps=doc.get("custom_apps"), non_erpnext_systems=doc.get("non_erpnext_systems"),
                one_line_summary=args.get("summary"), skill_already_exists=bool(args.get("skill_exists")),
                gaps=doc.get("gaps")))
        tag, requester = self.tag(args), None
        if action in ("roles", "apps", "modules", "meta", "resolve", "preflight"):
            requester = self._requester()
        kw = dict(requested_by=requester, **self._audit(args))
        if action == "roles":
            return _ok(client.get_user_roles(tag, args.get("user") or "", **kw))
        if action == "apps":
            return _ok(discover.list_installed_apps(tag, **kw))
        if action == "modules":
            return _ok(discover.list_modules(tag, **kw))
        if action in ("meta", "resolve", "preflight"):
            self._need(args, "doctype")
            if action == "meta":
                return _ok(discover.doctype_meta(tag, args["doctype"], **kw))
            if action == "resolve":
                return _ok(discover.resolve_doctype(tag, args["doctype"], **kw))
            return _ok(discover.preflight(tag, args["doctype"], payload=args.get("payload"), **kw))
        raise client.ConnectorError(f"unknown erp_discover action {action!r}")

    # -- writes --------------------------------------------------------------

    def _context(self, args, requester) -> "operations.WriteContext":
        return operations.WriteContext(
            tag=self.tag(args), mode=self._mode(), requested_by=requester,
            confirmation_token=args.get("confirmation_token"), issued_at=args.get("issued_at"),
            user_confirmation_text=args.get("user_confirmation_text"),
            user_approved=bool(args.get("user_approved")), approval_note=args.get("approval_note"),
            **self._audit(args))

    def _execute_write(self, args):
        phase = args.get("phase")
        if phase == "list_ops":
            return _ok(execute_write.list_ops(op=args.get("op"), domain=args.get("domain")))
        if phase == "confirm_job":
            self._need(args, "job_id", "reply_text")
            requester = self._requester()
            ctx = self._context(args, requester)
            return _ok(operations.verify_job_reply(args["job_id"], args["reply_text"], ctx))
        if phase not in ("render", "execute"):
            raise client.ConnectorError(f"unknown erp_execute_write phase {phase!r}")
        batch = args.get("batch")
        batches = args.get("batches")
        if batches is not None and (batch is not None or args.get("op") or args.get("args")):
            raise client.ConnectorError("batches replaces batch/op/args; pass one or the other.")
        if batches is not None and phase != "render":
            raise client.ConnectorError(
                "batches (starting a kanban job) only supports phase=render — prepare_job() stores "
                "the plan immediately, there is no separate execute step for it. Execute each batch "
                "afterwards with phase=execute, batch=<that batch>, job_id and job_batch_index.")
        if batch is not None and (args.get("op") or args.get("args")):
            raise client.ConnectorError("batch replaces op/args; pass one or the other.")
        if batch is None and batches is None:
            self._need(args, "op")
        if args.get("batch_token") and batch is None:
            raise client.ConnectorError("batch_token goes with batch.")
        if args.get("job_id") and batch is None:
            raise client.ConnectorError("job_id goes with batch.")
        if args.get("job_batch_index") is not None and not args.get("job_id"):
            raise client.ConnectorError("job_batch_index goes with job_id.")
        if args.get("job_id") and args.get("batch_token"):
            raise client.ConnectorError(
                "job_id and batch_token are mutually exclusive — a batch is approved by a fresh "
                "chat code or a job-level email approval, never both.")
        if (batches is None and not args.get("job_id")
                and client.session_env("HERMES_SESSION_SOURCE") == "kanban"
                and self._gated(self._write_steps(args, batch))):
            raise Refused(
                    "Refusing: a kanban-dispatched (headless) worker has no live chat session to "
                    "receive a confirmation code, so a plain batch/op render or execute for a "
                    "confirmation-gated step cannot be approved here. Nothing was sent. Use "
                    "erp_execute_write phase=render with `batches` instead (agents ADR 0008's "
                    "job-level email approval), then execute each batch with job_id and "
                    "job_batch_index.")
        requester = self._requester()
        ctx = self._context(args, requester)
        if phase == "render" and batches is not None:
            # A freshly generated id, discarded by prepare_job() itself
            # (issue 17) when an existing Pending/Approved row already
            # covers this exact plan — always read job_id back from `out`
            # below, never this local variable, so a resumed job's real
            # id is what every later instruction actually names.
            job_id = f"JOB-{uuid.uuid4().hex[:10].upper()}"
            out = operations.prepare_job(batches, ctx, job_id)
            job_id = out.get("job_id") or job_id
            if out.get("policy") == operations.POLICY_NONE:
                out["_note"] = ("No step in this job needs a confirmation. Execute each batch "
                                "directly with phase=execute, batch=<that batch>; job_id/"
                                "job_batch_index are not needed.")
            elif out.get("resumed") and out.get("status") == "Approved":
                out["_note"] = (
                    f"This exact plan already has an Approved job (job_id=\"{job_id}\") — do NOT "
                    f"send another approval email. Execute each batch directly with "
                    f"phase=execute, batch=<that batch>, job_id=\"{job_id}\", "
                    f"job_batch_index=<1-based position>.")
            else:
                # Covers both a genuinely new job AND a resumed one still
                # Pending (out.get("resumed") True, status "Pending"): the
                # Requester hasn't replied yet either way, so the same
                # "send the approval email" instruction applies — a
                # resend of the identical job_id/code to a Requester who
                # hasn't answered is a harmless nudge, unlike resending to
                # one who already has (the Approved branch above, which
                # this specifically avoids).
                out["_note"] = (
                    f"Do NOT show confirmation_code or job_token to the user in chat — this is a "
                    f"job-level approval (agents ADR 0008), approved by email only, never a chat "
                    f"code. Next: erp_execute_write phase=render then phase=execute with "
                    f"op=\"notify.job_approval_request\", args={{\"job_id\": \"{job_id}\"}} to send "
                    f"the approval email to the Requester (cc the ERP admin). Once the Requester "
                    f"replies, bind it with phase=confirm_job (job_id, reply_text), then execute "
                    f"each batch with phase=execute, batch=<that batch>, job_id=\"{job_id}\", "
                    f"job_batch_index=<1-based position>.")
            return _ok(out)
        if phase == "render" and batch is not None:
            out = operations.prepare_batch(batch, ctx)
            if out.get("policy") == operations.POLICY_NONE:
                out["_note"] = ("No step needs a confirmation. Execute with phase=execute and "
                                "`batch` as given.")
            else:
                out["_note"] = ("Show the user every `steps[].line` and the one `confirmation_code`, "
                                "and \"expires at HH:MM UTC\" from `expires_at`. Execute with "
                                "phase=execute, `batch` unchanged, batch_token and issued_at as "
                                "given, and the user's own reply as user_confirmation_text.")
            return _ok(out)
        if phase == "render":
            out = operations.prepare_only(args["op"], args.get("args") or {}, ctx)
            if out.get("policy") == operations.POLICY_NONE:
                out["_note"] = "This operation needs no confirmation token for these args."
            else:
                out["_note"] = ("Show `request` and `confirmation_code` to the user, and \"expires at "
                                "HH:MM UTC\" taken from `expires_at`; it is the only clock you need. "
                                "Execute with "
                                "phase=execute, the same op, these `args` unchanged, "
                                "confirmation_token and issued_at as given, and the user's own reply "
                                "as user_confirmation_text.")
            return _ok(out)
        steps = self._write_steps(args, batch)
        if not isinstance(steps, list) or not steps or not all(isinstance(s, dict) for s in steps):
            raise client.ConnectorError("batch must be a non-empty list of step objects.")
        job_id = args.get("job_id")
        gated = self._gated(steps)
        # A job-approved batch has no live chat session to cite latest_prompt
        # from (the point of ADR 0008) — the job's own email-approval trail
        # is the audit record instead, not a chat message.
        if gated and not args.get("latest_prompt") and job_id is None:
            raise client.ConnectorError("this write needs a confirmation, so latest_prompt (the "
                                        "user's most recent message, verbatim) is mandatory.")
        if batch is not None:
            report = operations.run_batch(batch, ctx, batch_token=args.get("batch_token"),
                                          job_id=job_id, job_batch_index=args.get("job_batch_index"))
            return json.dumps({"ok": report["stopped_at"] is None, "result": report}, default=str)
        result = operations.run_operation(args["op"], args.get("args") or {}, ctx)
        if (isinstance(result, dict) and isinstance(result.get("data"), dict)
                and not args.get("full_result")):
            result["data"] = operations.compact_record(
                result["data"], (args.get("args") or {}).get("payload"))
        warnings = list((result.get("_notes") or []) if isinstance(result, dict) else [])
        audit_status = result.get("_audit_log_status") if isinstance(result, dict) else None
        if audit_status not in ("ok", "exempt"):
            warnings.append(f"this write's Qkeee Bot Audit Log status is {audit_status!r}: the write "
                            f"succeeded but is NOT reliably in the audit trail. Tell the user; do not "
                            f"report a plain success.")
        return _ok(result, warnings=warnings) if warnings else _ok(result)
