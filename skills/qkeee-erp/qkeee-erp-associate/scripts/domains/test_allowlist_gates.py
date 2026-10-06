#!/usr/bin/env python3
"""Allowlist-gate regression tests. Run from this directory (matches the
convention in scripts/core/test_client.py): `cd scripts/domains && python -m
pytest -q`.

the domain's generic operation refuses a write whose doctype isn't
in that domain's registered ALLOWED_WRITE_DOCTYPES, per the write-allowlist
gate (see client.py's module docstring). Each domain module below registers
its own tuple at import time via register_domain_allowlist(); these tests
confirm two things per domain: (1) a doctype NOT in the tuple is refused
with DoctypeNotAllowedError before any network call, and (2) a doctype that
IS in the tuple clears the allowlist check specifically (isolated from the
RBAC/prod-requester gate downstream, which is core.client's own concern and
already covered by scripts/core/test_client.py's WritePathGatingTests).

mis.py registers an empty allowlist — MIS is read-only, no doctype can ever
be written there. The test below is written to fail loudly (not skip) if a
doctype is ever added to mis.ALLOWED_WRITE_DOCTYPES without a matching
deliberate test update.
"""

import os
import sys
import unittest
from unittest.mock import patch

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.dirname(_THIS_DIR)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

from core import client as core_client  # noqa: E402
from core import operations  # noqa: E402
import testsupport  # noqa: E402

_OFFLINE = testsupport.offline_schema()


def setUpModule():
    for p in _OFFLINE:
        p.start()


def tearDownModule():
    for p in _OFFLINE:
        p.stop()

import accounts  # noqa: E402
import fixed_assets  # noqa: E402
import hr_payroll  # noqa: E402
import inventory  # noqa: E402
import mis  # noqa: E402
import procurement  # noqa: E402
import sales  # noqa: E402
import system_admin  # noqa: E402

# One disallowed doctype per domain — deliberately NOT in that domain's
# ALLOWED_WRITE_DOCTYPES, and not a doctype any other domain's allowlist
# would plausibly want either, so the refusal is unambiguous.
_DISALLOWED_DOCTYPE = "Qkeee Bot Audit Log"

# (module, one doctype genuinely in its ALLOWED_WRITE_DOCTYPES)
#
# procurement uses "Purchase Order", not ALLOWED_WRITE_DOCTYPES[0]
# ("Supplier"): Supplier 'create' carries its own extra KYC-completeness
# gate on top of the allowlist gate (F2, .scratch/hermes-erp-bot-
# reliability/spec.md's IncompleteSupplierKYCError) — genuinely a
# different, domain-specific concern from what this file tests (the
# allowlist gate in isolation), covered on its own in
# domains/test_procurement.py instead.
_WRITER_DOMAINS = [
    (accounts, accounts.ALLOWED_WRITE_DOCTYPES[0]),
    (fixed_assets, fixed_assets.ALLOWED_WRITE_DOCTYPES[0]),
    (hr_payroll, hr_payroll.ALLOWED_WRITE_DOCTYPES[0]),
    (inventory, inventory.ALLOWED_WRITE_DOCTYPES[0]),
    (procurement, "Purchase Order"),
    (sales, sales.ALLOWED_WRITE_DOCTYPES[0]),
    # Not ALLOWED_WRITE_DOCTYPES[0] ("User"): User create is owned by the
    # gated operation system_admin.create_user and refused by
    # system_admin.generic — see test_operations.py's ownership tests.
    (system_admin, "Role"),
]


class DisallowedDoctypeIsRefusedTests(unittest.TestCase):
    """Every writer domain refuses a doctype outside its own allowlist —
    confirmed before any network call (_do_mutate is mocked and asserted
    never called)."""

    def test_every_writer_domain_refuses_a_foreign_doctype(self):
        for module, _allowed in _WRITER_DOMAINS:
            with self.subTest(domain=module.DOMAIN_NAME):
                with patch.object(core_client, "_do_mutate") as mocked_do_mutate:
                    with self.assertRaises(core_client.DoctypeNotAllowedError):
                        operations.call_generic(f'{module.DOMAIN_NAME}.generic', 'test', _DISALLOWED_DOCTYPE, 'create', payload={'x': 'y'}, mode='read-write', requested_by='tester@example.com')
                    mocked_do_mutate.assert_not_called()


class AllowedDoctypeClearsTheGateTests(unittest.TestCase):
    """A doctype genuinely in the domain's own allowlist clears the
    allowlist check specifically — isolated from the RBAC/prod-requester
    gate downstream by mocking it to a no-op, since that gate is
    core.client's own concern (see test_client.py)."""

    def test_every_writer_domain_accepts_its_own_doctype(self):
        for module, allowed_doctype in _WRITER_DOMAINS:
            with self.subTest(domain=module.DOMAIN_NAME):
                with patch.object(core_client, "get_env_config", return_value={"tag": "test"}), \
                     patch.object(core_client, "_validate_prod_requester"), \
                     patch.object(core_client, "record_audit_log_start", return_value="AUDITLOG-TEST"), \
                     patch.object(core_client, "record_audit_log_finish"), \
                     patch.object(core_client, "_do_mutate",
                                   return_value={"data": {"name": "TEST-0001"}}) as mocked_do_mutate:
                    if module is system_admin:
                        # Every system_admin.generic action is confirmed (it
                        # sends with the admin key). The allowlist runs before
                        # the token step, so reaching ConfirmationRequiredError
                        # means the allowlist was cleared.
                        with self.assertRaises(core_client.ConfirmationRequiredError):
                            operations.call_generic(f'{module.DOMAIN_NAME}.generic', 'test', allowed_doctype, 'create', payload={'x': 'y'}, mode='read-write', requested_by='tester@example.com')
                        mocked_do_mutate.assert_not_called()
                        continue
                    operations.call_generic(f'{module.DOMAIN_NAME}.generic', 'test', allowed_doctype, 'create', payload={'x': 'y'}, mode='read-write', requested_by='tester@example.com')
                    mocked_do_mutate.assert_called_once()


class MisIsAlwaysReadOnlyTests(unittest.TestCase):
    """mis.py's empty ALLOWED_WRITE_DOCTYPES is what keeps MIS read-only —
    this test carries that guarantee. Refuses BEFORE the RBAC/prod-requester
    gate too, confirming the empty-tuple path is checked ahead of (not
    instead of) that gate."""

    def test_empty_allowlist_is_unchanged(self):
        self.assertEqual(mis.ALLOWED_WRITE_DOCTYPES, ())

    def test_mis_refuses_every_doctype_unconditionally(self):
        for doctype in ("GL Entry", "Journal Entry", "Sales Invoice", _DISALLOWED_DOCTYPE):
            with self.subTest(doctype=doctype):
                with patch.object(core_client, "_validate_prod_requester") as mocked_rbac, \
                     patch.object(core_client, "_do_mutate") as mocked_do_mutate:
                    with self.assertRaises(core_client.DoctypeNotAllowedError):
                        operations.call_generic(f'{mis.DOMAIN_NAME}.generic', 'test', doctype, 'create', payload={'x': 'y'}, mode='read-write', requested_by='tester@example.com')
                    mocked_rbac.assert_not_called()
                    mocked_do_mutate.assert_not_called()


class UnregisteredDomainFailsClosedTests(unittest.TestCase):
    """A domain name with no registered allowlist (typo, or module never
    imported in this process) is refused, not treated as unrestricted —
    per core/operations.py _check_allowlist()."""

    def test_unknown_domain_name_is_refused(self):
        with patch.object(core_client, "_do_mutate") as mocked_do_mutate:
            with self.assertRaises(core_client.DoctypeNotAllowedError):
                operations.call_generic('not-a-real-domain.generic', 'test', 'Sales Order', 'create', payload={'x': 'y'}, mode='read-write', requested_by='tester@example.com')
            mocked_do_mutate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
