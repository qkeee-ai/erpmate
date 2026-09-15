#!/usr/bin/env python3
"""
qkeee-erp-associate — hr-payroll domain (HR, leave, payroll batch).

Offer/onboarding DRAFT composition belongs in render_employee_draft.py /
render_advisory_draft.py; those advisory-draft scripts are not yet part
of this skill's scripts/. The hard block on submit/cancel is
code-enforced: register_domain_token_gate() below opts this domain into
core.client.mutate_resource()'s generic confirmation-token check — see
core/confirm_token.py's advisory-token CLI.

ALLOWED_WRITE_DOCTYPES covers render_employee_draft.py's target doctypes
(Employee Onboarding, Job Offer) plus Employee/Leave Application. Cross-check
against references/domains/hr-payroll.md before expanding.
"""

import os
import sys

_SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from core import client as core_client

DOMAIN_NAME = "hr_payroll"

ALLOWED_WRITE_DOCTYPES = (
    "Employee",
    "Employee Onboarding",
    "Employee Separation",
    "Job Offer",
    "Leave Application",
)

core_client.register_domain_allowlist(DOMAIN_NAME, ALLOWED_WRITE_DOCTYPES)

# Submit/cancel require a fresh confirmation_token from
# core/confirm_token.py's advisory-token CLI, verified in mutate_resource()
# — the code-level backstop for "no docstatus document without
# confirmation" (profile.md).
core_client.register_domain_token_gate(DOMAIN_NAME, {"submit", "cancel"})


def mutate(tag: str, doctype: str, action: str, **kwargs) -> dict:
    """This domain's write entry point — plain mutate_resource() gated by
    ALLOWED_WRITE_DOCTYPES above (domain="hr_payroll")."""
    return core_client.mutate_resource(tag, doctype, action, domain=DOMAIN_NAME, **kwargs)
