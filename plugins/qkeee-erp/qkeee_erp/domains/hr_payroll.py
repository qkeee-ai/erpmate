#!/usr/bin/env python3
"""
qkeee-erp-associate — hr-payroll domain (HR, leave, payroll batch).

Writes run as operation "hr_payroll.generic" (core/operations.py): create/
update land drafts; submit/cancel/delete are token-gated — the code-level
backstop for "no docstatus document without confirmation" (profile.md).
Cross-check ALLOWED_WRITE_DOCTYPES against references/domains/
hr-payroll.md before expanding.
"""

from ..core import client as core_client
from ..core import operations

DOMAIN_NAME = "hr_payroll"

ALLOWED_WRITE_DOCTYPES = (
    "Employee",
    "Employee Onboarding",
    "Employee Separation",
    "Job Offer",
    "Leave Application",
)

core_client.register_domain_allowlist(DOMAIN_NAME, ALLOWED_WRITE_DOCTYPES)

# Operation "hr_payroll.generic": create/update are ungated draft steps;
# submit/cancel/delete need a rendered confirmation token + the user's
# confirmation code, and `expected_modified` (the record must not have
# changed since it was confirmed). See core/operations.py.
operations.generic_operation(DOMAIN_NAME, example_args={"doctype": "Leave Application", "action": "submit", "name": "HR-LAP-2026-00001"})
