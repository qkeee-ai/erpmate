"""ERPNext calls as gateway tools (requester identity binding, issue 04).

The tools run in the Hermes gateway process, not in the agent's terminal.
They call the plugin's ERP library (qkeee_erp/: core/client.py,
core/operations.py, discover.py).

- The requester is never a tool argument. It is the gateway session's
  sender email (client.resolve_requested_by, through the session reader
  the plugin installs) or, in a Kanban worker, the sender recorded for the
  task's origin. With neither, every tool refuses before any ERPNext call.
- `mode` (read-only / read-write) is never a tool argument either. It comes
  from the profile's skill config `skills.config.qkeee_erp.mode`.
- The ERPNext credentials are read by client.py in this process, from
  `<HERMES_HOME>/plugin-data/qkeee-erp/qkeee-erp.env`.

Five tools: erp_query, erp_get, erp_report, erp_discover (health, whoami,
roles and live metadata) and erp_execute_write (list_ops, render,
execute). Writes are only in erp_execute_write, so a platform toolset or a
hook can target them by name.
"""

import json
import os

from .qkeee_erp import discover
from .qkeee_erp import execute_write  # imports every domain module: registers allowlists + operations
from .qkeee_erp.core import client, operations

TOOLSET = "qkeee_erp"
DOMAIN_CODE = "qkeee-erp-associate"

_TAG = {"type": "string", "description": "Environment tag. Omit to use skills.config.qkeee_erp.active_env."}
_AUDIT = {
    "prompt_summary": {"type": "string", "description": "One-line summary of the user request (audit row)."},
    "latest_prompt": {"type": "string", "description": "The user's most recent message, verbatim (audit "
                                                       "row). Mandatory for a write that needs confirmation."},
}
_NO_REQUESTER_NOTE = ("The requester is taken from the gateway session; there is no requester argument.")


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
         "filters": {"type": "array", "description": 'Frappe filters, e.g. [["status","=","Open"]].'},
         "fields": {"type": "array", "items": {"type": "string"}},
         "limit": {"type": "integer", "default": 20}},
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
        "ERPNext connectivity and live metadata. health: connectivity, auth and role gaps. whoami: the "
        "requester this turn is bound to (no network). roles: a user's roles. apps / modules: installed "
        "apps. meta / resolve: one DocType's live schema / module and app. preflight: write-readiness "
        "check for one create (needs doctype and payload).",
        {"action": {"type": "string", "enum": ["health", "whoami", "roles", "apps", "modules", "meta",
                                                "resolve", "preflight"]},
         "doctype": {"type": "string"}, "payload": {"type": "object"},
         "user": {"type": "string", "description": "roles: the user to look up; omit for the bot account."}},
        required=("action",)),
    "erp_execute_write": _schema(
        "erp_execute_write",
        "The only ERPNext write path. list_ops: every registered operation and its arguments. render: "
        "prepare the exact request; show its request and confirmation_code to the user. execute: run "
        "the operation with the rendered args unchanged, plus confirmation_token, issued_at and the "
        "user's own reply as user_confirmation_text. batch: several steps, stops at the first failure.",
        {"phase": {"type": "string", "enum": ["list_ops", "render", "execute"]},
         "op": {"type": "string", "description": "Operation key from list_ops, e.g. sales.generic."},
         "args": {"type": "object", "description": "The operation's arguments."},
         "batch": {"type": "array", "items": {"type": "object"},
                   "description": 'execute only: [{"op", "args", "confirmation_token"?, "issued_at"?, '
                                  '"user_confirmation_text"?}] instead of op/args.'},
         "confirmation_token": {"type": "string"}, "issued_at": {"type": "integer"},
         "user_confirmation_text": {"type": "string"},
         "user_approved": {"type": "boolean"}, "approval_note": {"type": "string"}},
        required=("phase",)),
}

_STOP_AND_ASK = ("Stop. Show the user the missing or invalid fields, ask for the values, update the "
                 "spec and re-confirm. Never fill a value the user did not give.")


class Refused(Exception):
    """A refusal raised by this module before any ERPNext call."""


def _ok(result, **extra) -> str:
    return json.dumps({"ok": True, "result": result, **extra}, default=str)


def _fail(error: str, **extra) -> str:
    return json.dumps({"ok": False, "error": error, **extra}, default=str)


class ErpTools:
    """`settings()` returns the skill config {"active_env", "mode"} (read on
    every call, so a config change applies to the next call)."""

    def __init__(self, settings):
        self._settings = settings
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
            return _fail(str(e), refused=True)
        except client.WriteRejectedError as e:
            return _fail(f"ERPNext rejected the write: {e.failure.get('message')} — {_STOP_AND_ASK}",
                         write_failure=e.failure)
        except client.InvalidArgumentsError as e:
            return _fail(f"invalid operation arguments, nothing was sent: {e}")
        except (client.TransportTimeoutError, client.PartialOutcomeError) as e:
            return _fail(f"outcome unknown or partial: {e} Re-read the record before any retry.",
                         outcome_unknown=True)
        except client.ConnectorError as e:
            return _fail(str(e))

    def _tag(self, args) -> str:
        return args.get("tag") or (self._settings() or {}).get("active_env") or "default"

    def _mode(self) -> str:
        return (self._settings() or {}).get("mode") or "read-only"

    @staticmethod
    def _requester() -> str:
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
    def _audit(args) -> dict:
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

    # -- reads ---------------------------------------------------------------

    def _query(self, args):
        self._need(args, "doctype")
        requester = self._requester()
        return _ok(client.query_resource(self._tag(args), args["doctype"], args.get("filters"),
                                         args.get("fields"), int(args.get("limit") or 20),
                                         requested_by=requester, **self._audit(args)))

    def _get(self, args):
        self._need(args, "doctype", "name")
        requester = self._requester()
        return _ok(client.get_resource(self._tag(args), args["doctype"], args["name"],
                                       not args.get("no_strip"), requested_by=requester,
                                       **self._audit(args)))

    def _report(self, args):
        self._need(args, "report_name")
        requester = self._requester()
        return _ok(client.run_query_report(self._tag(args), args["report_name"], args.get("filters"),
                                           requested_by=requester, **self._audit(args)))

    def _discover(self, args):
        action = args.get("action")
        if action == "whoami":
            return _ok(client.session_identity())
        if action == "health":
            return _ok(client.health_check(self._tag(args)))
        tag, requester = self._tag(args), None
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
            tag=self._tag(args), mode=self._mode(), requested_by=requester,
            confirmation_token=args.get("confirmation_token"), issued_at=args.get("issued_at"),
            user_confirmation_text=args.get("user_confirmation_text"),
            user_approved=bool(args.get("user_approved")), approval_note=args.get("approval_note"),
            **self._audit(args))

    def _execute_write(self, args):
        phase = args.get("phase")
        if phase == "list_ops":
            return _ok(execute_write.list_ops())
        if phase not in ("render", "execute"):
            raise client.ConnectorError(f"unknown erp_execute_write phase {phase!r}")
        batch = args.get("batch")
        if batch is not None and (phase != "execute" or args.get("op")):
            raise client.ConnectorError("batch is for phase=execute only, without op/args.")
        if batch is None:
            self._need(args, "op")
        requester = self._requester()
        ctx = self._context(args, requester)
        if phase == "render":
            out = operations.prepare_only(args["op"], args.get("args") or {}, ctx)
            if out.get("policy") == operations.POLICY_NONE:
                out["_note"] = "This operation needs no confirmation token for these args."
            else:
                out["_note"] = ("Show `request` and `confirmation_code` to the user. Execute with "
                                "phase=execute, the same op, these `args` unchanged, "
                                "confirmation_token and issued_at as given, and the user's own reply "
                                "as user_confirmation_text.")
            return _ok(out)
        steps = batch if batch is not None else [{"op": args["op"], "args": args.get("args") or {}}]
        if not isinstance(steps, list) or not steps or not all(isinstance(s, dict) for s in steps):
            raise client.ConnectorError("batch must be a non-empty list of step objects.")
        gated = any(operations.requires_confirmation(s.get("op") or "", s.get("args") or {})
                    for s in steps)
        if gated and not args.get("latest_prompt"):
            raise client.ConnectorError("this write needs a confirmation, so latest_prompt (the "
                                        "user's most recent message, verbatim) is mandatory.")
        if batch is not None:
            report = operations.run_batch(batch, ctx)
            return json.dumps({"ok": report["stopped_at"] is None, "result": report}, default=str)
        result = operations.run_operation(args["op"], args.get("args") or {}, ctx)
        warnings = list((result.get("_notes") or []) if isinstance(result, dict) else [])
        audit_status = result.get("_audit_log_status") if isinstance(result, dict) else None
        if audit_status not in ("ok", "exempt"):
            warnings.append(f"this write's Qkeee Bot Audit Log status is {audit_status!r}: the write "
                            f"succeeded but is NOT reliably in the audit trail. Tell the user; do not "
                            f"report a plain success.")
        return _ok(result, warnings=warnings) if warnings else _ok(result)
