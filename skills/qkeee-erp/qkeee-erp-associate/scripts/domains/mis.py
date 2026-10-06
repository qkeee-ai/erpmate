#!/usr/bin/env python3
"""
qkeee-erp-associate — mis domain (GL / MIS reporting, read-only).

MIS's read-only posture is enforced by an EMPTY allowlist:

    ALLOWED_WRITE_DOCTYPES = ()

so operation "mis.generic" refuses every doctype at the allowlist step,
unconditionally — before any read. Keep this covered by
scripts/domains/test_allowlist_gates.py.
"""

import os
import sys

_SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from core import client as core_client
from core import operations

DOMAIN_NAME = "mis"

# Deliberately empty — see module docstring. This IS the read-only
# guarantee for this domain; do not add doctypes here without a
# deliberate decision to make MIS a writer, which contradicts its whole
# purpose.
ALLOWED_WRITE_DOCTYPES = ()

core_client.register_domain_allowlist(DOMAIN_NAME, ALLOWED_WRITE_DOCTYPES)
operations.generic_operation(DOMAIN_NAME, summary="mis is read-only — every write is refused")
