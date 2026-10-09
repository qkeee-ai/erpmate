"""
Create-payloads for the Qkeee Bot audit-trail doctype + the Qkeee Bot
role. A qkeee-erp-associate-specific design doc for these field shapes
doesn't exist yet — this module is the source of truth for now; if you're
changing field shapes, update it directly.

CODE-ONLY: this module has never been run against a live instance in this
form.

The doctype is created as a custom (custom=1) record attached to Frappe's
built-in "Custom" module — no app, no module folder, no Python
controller.
"""

ROLE_NAME = "Qkeee Bot"

ROLE_PAYLOAD = {
    "doctype": "Role",
    "role_name": ROLE_NAME,
    "desk_access": 1,
}


def _perm(role, **flags):
    base = {"role": role, "read": 0, "write": 0, "create": 0,
            "submit": 0, "cancel": 0, "delete": 0}
    base.update(flags)
    return base


AUDIT_LOG = {
    "doctype": "DocType",
    "name": "Qkeee Bot Audit Log",
    "module": "Custom",
    "custom": 1,
    "naming_rule": "Random",
    "autoname": "hash",
    "is_submittable": 1,
    "track_changes": 0,
    "fields": [
        {"fieldname": "session", "label": "Session (raw id)", "fieldtype": "Data",
         "reqd": 1, "in_list_view": 1,
         "description": "Raw session-id string, not a Link — a plain correlator the "
                         "caller passes as-is."},
        {"fieldname": "domain_code", "label": "Domain Code", "fieldtype": "Data",
         "description": "Denormalized string (no doctype join) naming the active "
                         "qkeee-erp-associate domain reference that made this call (e.g. "
                         "'qkeee-erp-associate/hr-payroll'). A live schema migration "
                         "(renaming an existing field on an already-provisioned "
                         "instance) is NOT performed by this code — reconciling an "
                         "already-provisioned instance's field name is a manual, "
                         "deliberate action for whoever operates that instance."},
        {"fieldname": "environment_tag", "label": "Environment Tag", "fieldtype": "Data"},
        {"fieldname": "channel", "label": "Channel", "fieldtype": "Select",
         "options": "\nWeb\nDiscord\nTelegram\nWhatsApp\nEmail\nSlack\nGoogle Chat\nCLI\nAPI\nOther",
         "in_list_view": 1,
         "description": "Denormalized from Session where one exists; settable directly "
                         "otherwise."},
        {"fieldname": "channel_metadata", "label": "Channel Metadata (JSON)", "fieldtype": "Long Text",
         "description": "Free-form per-channel tracing detail, e.g. chat_id, message_id, "
                         "thread id, email Message-Id header."},
        {"fieldname": "action", "label": "Action", "fieldtype": "Select",
         "options": "Read\nCreate\nUpdate\nSubmit\nCancel\nDelete", "reqd": 1,
         "in_list_view": 1},
        {"fieldname": "reference_doctype", "label": "Reference DocType", "fieldtype": "Link",
         "options": "DocType", "reqd": 1, "in_list_view": 1},
        # Data, NOT Dynamic Link: an audit trail must outlive the records it
        # describes. As a Dynamic Link, every audited record became
        # undeletable (Frappe's check_if_doc_is_dynamically_linked raises
        # LinkExistsError) — found live on DEMO_ERP, 2026-10-06. init_bot.py
        # migrates an existing instance (MIGRATABLE_FIELDS below).
        {"fieldname": "reference_name", "label": "Reference Name", "fieldtype": "Data",
         "in_list_view": 1},
        {"fieldname": "requested_by", "label": "Requested By", "fieldtype": "Link",
         "options": "User", "reqd": 1, "in_list_view": 1},
        {"fieldname": "timestamp", "label": "Timestamp", "fieldtype": "Datetime", "reqd": 1,
         "in_list_view": 1},
        {"fieldname": "status", "label": "Status", "fieldtype": "Select",
         "options": "Attempted\nSuccess\nFailure", "reqd": 1, "in_list_view": 1},
        {"fieldname": "error_detail", "label": "Error Detail", "fieldtype": "Small Text"},
        {"fieldname": "payload_before", "label": "Payload Before (JSON)", "fieldtype": "Long Text"},
        {"fieldname": "payload_after", "label": "Payload After (JSON)", "fieldtype": "Long Text"},
        {"fieldname": "field_diff", "label": "Field Diff (JSON)", "fieldtype": "Long Text"},
        {"fieldname": "response_payload", "label": "Response Payload (JSON)", "fieldtype": "Long Text",
         "description": "Raw response body returned to the caller for a Read action (query/get/"
                         "report) — empty for Create/Update/Submit/Cancel/Delete rows, which already "
                         "carry payload_before/payload_after. Truncated past "
                         "RESPONSE_PAYLOAD_MAX_LEN in core/client.py."},
        {"fieldname": "prompt_summary", "label": "Prompt Summary", "fieldtype": "Small Text",
         "description": "One-line summary of the user request that led to this action, as resolved "
                         "by the calling agent."},
        {"fieldname": "latest_user_prompt", "label": "Latest User Prompt", "fieldtype": "Long Text",
         "description": "Verbatim most-recent user prompt from the chat/session driving this call. "
                         "SSN/credit-card-shaped substrings are redacted before storage, same as "
                         "channel_metadata/approval_note — not a substitute for not pasting sensitive "
                         "values into it in the first place."},
        {"fieldname": "audit_comment_posted", "label": "Audit Comment Posted", "fieldtype": "Check"},
        {"fieldname": "user_approved", "label": "User Approved", "fieldtype": "Select",
         "options": "Not Required\nApproved\nNot Confirmed", "reqd": 1,
         "default": "Not Required", "in_list_view": 1,
         "description": "Set by the caller, not inferred. 'Not Required' is reserved for "
                         "Read rows (auto). Every Create/Update/Submit/Cancel row must be "
                         "explicitly 'Approved' (caller passed user_approved=True after a "
                         "real confirm) or 'Not Confirmed' (caller didn't) — this is a "
                         "detection field for scanning whether the confirm-before-Execute "
                         "discipline was actually followed, not a write-blocking gate; "
                         "the write still proceeds either way so the violation is visible "
                         "here instead of silently prevented."},
        {"fieldname": "approval_note", "label": "Approval Note", "fieldtype": "Small Text",
         "description": "Free text of what was confirmed, e.g. 'user confirmed JE draft "
                         "JE-0001, balanced, before submit' — populated by the calling "
                         "domain's confirm-stage renderer where one exists."},
    ],
    "permissions": [
        _perm(ROLE_NAME, read=1, write=1, create=1, submit=1),
        _perm("System Manager", read=1),
    ],
}

# Audit Log is the only doctype here, so there is no create-order question
# to document — kept as a single-element list for init_bot.py's existing
# iteration shape.
ALL_DOCTYPES = [AUDIT_LOG]


# Doctypes on which the ROLE_NAME role gets one permlevel-0 row with read
# ONLY — never write/create/delete. Module Def: Environment Metadata the bot
# reads itself (agents/docs/adr/0001). Workflow: `discover.py preflight`
# checks for an active workflow. Rows go on the dedicated role, never on a
# stock role (agents/docs/adr/0003). init_bot.py adds them through the Role
# Permission Manager's `add` RPC, which first copies the doctype's standard
# DocPerm rows into Custom DocPerm (Frappe add_permission() ->
# setup_custom_perms()), so System Manager keeps its access.
BOT_READ_GRANTS = ("Module Def", "Workflow")


# Fields whose type init_bot.py may change on an ALREADY-provisioned
# instance, to converge it on the definitions above. Only these — nothing
# else about a live doctype is ever modified by a re-run.
MIGRATABLE_FIELDS = {
    "Qkeee Bot Audit Log": ("reference_name",),
}
