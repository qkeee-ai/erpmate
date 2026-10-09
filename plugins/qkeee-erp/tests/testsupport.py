"""Shared unit-test scaffolding for the operation pipeline (not a test module).

- `offline_schema()`: patchers that make schema-first mapping pass payloads
  through unchanged and report live meta as unavailable — unit tests have
  no live ERPNext schema (schema_mapping.py has its own tests).
- `generic_token()`: the operation token the pipeline expects for a generic
  resource write, without going through prepare().
- `render()`: operations.prepare_only() with a test context — the same
  render path the CLI uses, so tests exercise render -> execute for real.
"""

import unittest.mock

from qkeee_erp_plugin.qkeee_erp.core import operations
from qkeee_erp_plugin.qkeee_erp import schema_mapping
from qkeee_erp_plugin.qkeee_erp.core.confirm_token import confirmation_code

REQ = "admin@example.com"


def passthrough_map(tag, doctype, payload, **kwargs):
    return {"payload": dict(payload or {}), "status": "ok", "detail": None,
            "suggested_mappings": [], "unmatched": [], "high_risk": []}


def no_live_schema(tag, doctype, **kwargs):
    return None, "no live schema in unit tests"


def offline_schema():
    return [unittest.mock.patch.object(schema_mapping, "map_payload_for_write", new=passthrough_map),
            unittest.mock.patch.object(schema_mapping, "get_doctype_schema", new=no_live_schema)]


class OfflineSchemaMixin:
    """unittest mixin: offline_schema() active for every test."""

    def setUp(self):
        super().setUp()
        for p in offline_schema():
            p.start()
            self.addCleanup(p.stop)


def generic_token(op_key, action, doctype, name, payload, requested_by, issued_at,
                  expected_modified=None):
    if action in ("create", "update"):
        req = operations.PreparedRequest(transport="resource", doctype=doctype, action=action,
                                         name=name, body=payload or {})
    else:
        req = operations.PreparedRequest(transport="resource", doctype=doctype, action=action,
                                         name=name, bound={"expected_modified": expected_modified})
    return operations.operation_token(op_key, req, requested_by, issued_at)


def ctx(tag="test", **kw):
    kw.setdefault("mode", "read-write")
    kw.setdefault("requested_by", REQ)
    return operations.WriteContext(tag=tag, **kw)


def render(op_key, args, **ctx_kw):
    """Render, then return (full_args, execute_ctx) ready for run_operation."""
    out = operations.prepare_only(op_key, args, ctx(**ctx_kw))
    exec_kw = dict(ctx_kw)
    if "confirmation_token" in out:
        exec_kw.update(confirmation_token=out["confirmation_token"], issued_at=out["issued_at"],
                       user_confirmation_text=f"yes {out.get('confirmation_code', '')}")
    return out["args"], ctx(**exec_kw), out


__all__ = ["REQ", "offline_schema", "OfflineSchemaMixin", "generic_token", "ctx", "render",
           "confirmation_code"]
