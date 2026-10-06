#!/usr/bin/env python3
"""
qkeee-erp-associate — hr-payroll domain (HR, leave, payroll batch).

Writes run as operation "hr_payroll.generic" (core/operations.py): create/
update land drafts; submit/cancel/delete are token-gated — the code-level
backstop for "no docstatus document without confirmation" (profile.md).
Cross-check ALLOWED_WRITE_DOCTYPES against references/domains/
hr-payroll.md before expanding.
"""

import os
import sys

_SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from core import client as core_client
from core import operations

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
operations.generic_operation(DOMAIN_NAME)


def mutate(tag: str, doctype: str, action: str, **kwargs) -> dict:
    """Compatibility shim: operation "hr_payroll.generic" via
    operations.call_generic() — mutate_resource()-style keywords
    (payload, name, mode, requested_by, session_id, ..., confirmation_token,
    issued_at, user_confirmation_text, expected_modified)."""
    return operations.call_generic(f"{DOMAIN_NAME}.generic", tag, doctype, action, **kwargs)
