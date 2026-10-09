#!/usr/bin/env python3
"""
qkeee-erp-associate — accounts domain (AP/AR, Journal Entry, tax).

Writes run as operation "accounts.generic" (core/operations.py): create/
update land drafts; submit/cancel/delete need a rendered confirmation
token (`confirm_token.py render --op accounts.generic`) plus the user's
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
operations.generic_operation(DOMAIN_NAME, example_args={"doctype": "Journal Entry", "action": "submit", "name": "ACC-JV-2026-00001"})
