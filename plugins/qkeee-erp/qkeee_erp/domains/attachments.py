#!/usr/bin/env python3
"""
qkeee-erp-associate — attach a file to an existing record.

| Operation      | What it does                                                    |
|----------------|------------------------------------------------------------------|
| files.attach   | attach_file(doctype, docname, filename, filedata): uploads via Frappe's generic frappe.client.attach_file RPC and links it onto the target record |

Not a business domain (agents issue 06): "files" is not one of the fixed
domain-slug enum in `00-conventions.md` — same non-enum, bespoke-RPC shape
as `notify` (that file's own docstring: "notify is not a business domain").
A file attaches to a record in ANY domain, so there is no single
ALLOWED_WRITE_DOCTYPES this could register; RBAC and audit are gated on the
TARGET record's own doctype instead (set dynamically in `_attach_prepare`,
the same pattern `system_admin.permission_update` uses for its target
DocType), never on a synthetic "File" allowlist entry.

Before this operation existed, the agent had no write path to attach a file
and burned several `list_ops` calls across domains hunting for one that was
never going to exist (F3, qkeee-erp-email-channel-reliability issue 06).
Reads were never the gap: any doctype's `File` rows (`attached_to_doctype`/
`attached_to_name`) are already visible through the ordinary query path.

Uses `frappe.client.attach_file` (plain JSON RPC, base64 `filedata`), not
the REST multipart `/api/method/upload_file` endpoint: `core/client.py`'s
`_request` only ever sends `application/json` bodies, and adding multipart
support for this one op would be a materially bigger change than the ticket
asked for. Body keys match `frappe.client.attach_file`'s real signature,
read from frappe/frappe `develop` branch `client.py` (MIT licensed) 2026-11-19:
``attach_file(filename, filedata, doctype, docname, folder, decode_base64,
is_private, docfield)`` — NOT the internal File-doc field names (`file_name`,
`attached_to_doctype`, `content`, `decode`, ...) that function builds
internally; sending those instead of the RPC's own parameter names would
silently fail at the wrong layer. The function itself checks "write" on
`doctype`/`docname` before attaching (`frappe.get_lazy_doc(..., check_permission="write")`),
confirming `rbac_ptype="write"` below. Verify this RPC is still whitelisted
on the target ERPNext version before relying on this live (noted as an open
live check, same as every other write op here) — the parameter names
themselves are confirmed against upstream source, not guessed.
"""

from ..core import operations
from ..core.operations import PreparedRequest, require_args
from ..core import client as core_client

DOMAIN_NAME = "files"

_ATTACH_FILE = "/api/method/frappe.client.attach_file"

# Base64 grows ~4/3 over the source; this caps the body at roughly 11 MB raw
# (a generous ceiling for the triggering case — attaching a source PDF — well
# under typical web-server upload limits, with no existing precedent in this
# codebase to match instead).
_MAX_FILEDATA_CHARS = 15_000_000


def _attach_prepare(args, ctx):
    require_args(args, ["doctype", "docname", "filename", "filedata"], ["is_private", "folder"])
    if len(args["filedata"]) > _MAX_FILEDATA_CHARS:
        raise core_client.ConnectorError(
            f"filedata: at most {_MAX_FILEDATA_CHARS} base64 characters (~11 MB raw); this file is "
            f"too large to attach through this operation.")
    body = {"filename": args["filename"], "filedata": args["filedata"], "doctype": args["doctype"],
            "docname": args["docname"], "decode_base64": 1,
            "is_private": int(bool(args.get("is_private", 1)))}
    if args.get("folder"):
        body["folder"] = args["folder"]
    return PreparedRequest(
        # doctype is the TARGET record's own doctype, not "File": RBAC
        # (rbac_doctype defaults to doctype) and the audit trail land on the
        # record actually being touched, same as system_admin.permission_update.
        transport="rpc", doctype=args["doctype"], action="attach", rpc_path=_ATTACH_FILE, body=body,
        rbac_ptype="write", audit_action="Create", audit_doctype=args["doctype"],
        audit_reference=args["docname"], bound={"filename": args["filename"]})


operations.register_operation(operations.Operation(
    key="files.attach", domain=DOMAIN_NAME,
    summary="attach a file to an existing record (frappe.client.attach_file)",
    prepare=_attach_prepare, allowlist_domain=None,
    example_args={"doctype": "Purchase Invoice", "docname": "ACC-PINV-2026-00001",
                  "filename": "invoice-source.pdf", "filedata": "dGVzdCBjb250ZW50"},
    args_help={"doctype": "DocType of the record to attach to",
               "docname": "name of the record to attach to",
               "filename": "file name, with extension",
               "filedata": "base64-encoded file content",
               "is_private": "0/1, default 1 (private — only users with access to the record can see it)",
               "folder": "optional Frappe File folder (default Home)"},
))
