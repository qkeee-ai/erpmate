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

# `init-bot --domains` (agents ADR 0003 amendment 2026-10-13): the rights
# init-bot may put on ROLE_NAME for business work. Never delete; never a
# privileged doctype (operations.admin_key_doctypes() / UNSCOPED_DENY).
# submit/cancel only on a submittable doctype.
PROVISION_PTYPES = ("read", "create", "write", "submit", "cancel")

# Master doctypes the agent writes through `unscoped.generic` (no domain
# owns them). Granted with every --domains run. Adding one is a code change.
UNSCOPED_PROVISION_DOCTYPES = ("Item", "Item Price", "Address", "Contact")

# Masters a domain's work creates on the way (a new Supplier Group before
# the Supplier). No domain owns them, so the agent writes them through
# `unscoped.generic` (every action token-gated). Granted read/create/write
# only when the domain is in `init-bot --domains` (dev-erp 2026-10-15: 403
# creating a Supplier Group after procurement was provisioned). Adding one
# is a code change; never a privileged or a domain-owned doctype.
DOMAIN_MASTER_DOCTYPES = {
    "procurement": ("Supplier Group", "Payment Terms Template"),
    "sales": ("Customer Group", "Territory"),
    "inventory": ("Item Group", "Brand", "UOM", "Warehouse"),
}

# Stock roles whose permlevel-0 rows init-bot copies onto ROLE_NAME with
# their domain (agents ADR 0003 amendment 2026-10-21). The bot never holds
# these roles; init-bot reads their live rows and grants the same ticks
# (cut to PROVISION_PTYPES, never a privileged doctype, never an if_owner
# row, read only on a Single) on ROLE_NAME. So the bot gets what a person
# in that job gets, and an app upgrade that adds a doctype is covered by
# the next init-bot run (dev-erp 2026-10-21: 403 on Salary Structure,
# Attendance, Leave Allocation, Job Opening: no code list named them). A
# role missing on the instance adds nothing. Adding a role is a code change.
DOMAIN_TEMPLATE_ROLES = {
    "accounts": ("Accounts Manager", "Accounts User"),
    "hr_payroll": ("HR Manager", "HR User"),
    "inventory": ("Stock Manager", "Stock User", "Item Manager"),
    "procurement": ("Purchase Manager", "Purchase User", "Purchase Master Manager"),
    "sales": ("Sales Manager", "Sales User", "Sales Master Manager"),
}


# Fields whose value must be set before ERPNext allows submit, where the
# field sits above permlevel 0 (agents ADR 0003 amendment 2026-10-22, HRMS
# demo run review D3: "Only Leave Applications with status 'Approved' and
# 'Rejected' can be submitted", and Leave Application `status` is
# permlevel 1). init-bot copies a template role's row at that field's
# permlevel, read and write only. Adding one is a code change.
SUBMIT_GATE_FIELDS = {"Leave Application": ("status",)}
# The ticks init-bot may set on a row above permlevel 0.
PERMLEVEL_PTYPES = ("read", "write")


def level_key(doctype: str, permlevel: int) -> str:
    """The plan key of a permission row: "<doctype>" at permlevel 0,
    "<doctype>@<level>" above it."""
    return f"{doctype}@{permlevel}" if permlevel else doctype


def split_level_key(key: str) -> tuple:
    """(doctype, permlevel) from level_key()."""
    doctype, sep, level = key.rpartition("@")
    return (doctype, int(level)) if sep and level.isdigit() else (key, 0)


def is_bot_read_link(field: dict) -> bool:
    """A Link field whose target the bot must be able to read: mandatory,
    or one a user can set (not hidden, not read-only, not fetched). The
    mandatory-only rule missed optional pickers such as Supplier.
    supplier_group, Item Default expense_account or default_price_list
    (dev-erp 2026-10-15: 403 on Supplier Group after provisioning)."""
    if field.get("fieldtype") != "Link" or not field.get("options"):
        return False
    if field.get("reqd"):
        return True
    return not (field.get("hidden") or field.get("read_only") or field.get("fetch_from"))


# Fields whose type init_bot.py may change on an ALREADY-provisioned
# instance, to converge it on the definitions above. Only these — nothing
# else about a live doctype is ever modified by a re-run.
MIGRATABLE_FIELDS = {
    "Qkeee Bot Audit Log": ("reference_name",),
}
