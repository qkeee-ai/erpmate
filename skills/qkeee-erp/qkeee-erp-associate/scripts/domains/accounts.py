#!/usr/bin/env python3
"""
qkeee-erp-associate — accounts domain (AP/AR, Journal Entry, tax).

JE-narration/cancel-confirmation DRAFT composition belongs in
render_je_draft.py / render_cancel_confirmation.py; those advisory-draft
scripts are not yet part of this skill's scripts/. The double gate on
submit/cancel is code-enforced: register_domain_token_gate() below opts
this domain's submit/cancel into core.client.mutate_resource()'s generic
confirmation-token check — see core/confirm_token.py's advisory-token CLI
for computing the token over what the user actually confirmed.

Cross-check ALLOWED_WRITE_DOCTYPES below against
references/domains/accounts.md before expanding it: Journal Entry
(render_je_draft.py), plus generic cancel (render_cancel_confirmation.py,
targets whatever doctype the caller names — Purchase Invoice / Sales
Invoice / Payment Entry are the ones actually in use).
"""

import os
import sys

_SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from core import client as core_client

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

# submit/cancel require a fresh confirmation_token from
# core/confirm_token.py's advisory-token CLI, verified inside
# mutate_resource() itself.
core_client.register_domain_token_gate(DOMAIN_NAME, {"submit", "cancel"})


def mutate(tag: str, doctype: str, action: str, **kwargs) -> dict:
    """This domain's write entry point — plain mutate_resource() gated by
    ALLOWED_WRITE_DOCTYPES above (domain="accounts"). See
    core.client.mutate_resource()'s docstring for the full parameter set
    (payload, name, mode, requested_by, session_id, ...) — pass them as
    keyword arguments through **kwargs."""
    return core_client.mutate_resource(tag, doctype, action, domain=DOMAIN_NAME, **kwargs)
