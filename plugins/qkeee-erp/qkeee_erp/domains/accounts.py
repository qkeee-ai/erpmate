#!/usr/bin/env python3
"""
qkeee-erp-associate — accounts domain (AP/AR, Journal Entry, tax).

Writes run as operation "accounts.generic" (core/operations.py): create/
update land drafts; submit/cancel/delete need a rendered confirmation
token (erp_execute_write phase render, op accounts.generic) plus the user's
confirmation code, and the record must be unchanged since render.

Cross-check ALLOWED_WRITE_DOCTYPES below against
references/domains/accounts.md before expanding it.
"""

from ..core import client as core_client
from ..core import operations

DOMAIN_NAME = "accounts"

# See module docstring. Cross-check against references/domains/accounts.md
# before expanding.
ALLOWED_WRITE_DOCTYPES = (
    "Journal Entry",
    "Payment Entry",
    "Purchase Invoice",
    "Sales Invoice",
)

core_client.register_domain_allowlist(DOMAIN_NAME, ALLOWED_WRITE_DOCTYPES)

# Operation "accounts.generic": create/update are ungated draft steps;
# submit/cancel/delete need a rendered confirmation token + the user's
# confirmation code, and `expected_modified` (the record must not have
# changed since it was confirmed). See core/operations.py.
#
# auto_verify_write=True (issue 09, D4): a Payment Entry create/update
# attaches a bot-verified read-back (result["_verified_record"]) — the
# write's own parent record, including its child-table rows (e.g. Payment
# Entry Reference), read with the bot's own credential. Payment Entry
# Reference has its own doctype permissions distinct from its parent's;
# the triggering incident was a separate, requester-gated read of that
# child doctype failing even though the parent write — and this bundled
# read-back of it — succeeded. Scoped to this domain only: the other
# domains haven't shown this gap and get no extra read cost by default.
operations.generic_operation(DOMAIN_NAME, auto_verify_write=True,
                             example_args={"doctype": "Journal Entry", "action": "submit", "name": "ACC-JV-2026-00001"})
