#!/usr/bin/env python3
"""
qkeee-erp-associate — sales domain (Customer, Quotation, Sales Order,
Delivery Note).

Writes run as operation "sales.generic" (core/operations.py): create/
update land drafts; submit/cancel/delete are token-gated. Cross-check
ALLOWED_WRITE_DOCTYPES against references/domains/sales.md before
expanding.
"""


from ..core import client as core_client
from ..core import operations

DOMAIN_NAME = "sales"

ALLOWED_WRITE_DOCTYPES = (
    "Customer",
    "Quotation",
    "Sales Order",
    "Delivery Note",
)

core_client.register_domain_allowlist(DOMAIN_NAME, ALLOWED_WRITE_DOCTYPES)

# Operation "sales.generic": create/update are ungated draft steps;
# submit/cancel/delete need a rendered confirmation token + the user's
# confirmation code, and `expected_modified` (the record must not have
# changed since it was confirmed). See core/operations.py.
operations.generic_operation(DOMAIN_NAME, example_args={"doctype": "Sales Order", "action": "submit", "name": "SAL-ORD-2026-00001"})
