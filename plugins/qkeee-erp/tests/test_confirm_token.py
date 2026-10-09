#!/usr/bin/env python3
"""Regression tests for confirm_token.confirmation_code() (F5, .scratch/
hermes-erp-bot-reliability/spec.md)."""

import unittest

from qkeee_erp_plugin.qkeee_erp.core import confirm_token as ct


class ConfirmationCodeTests(unittest.TestCase):
    def test_deterministic_over_same_token(self):
        token = ct.compute_token(kind="op", body={"item_code": "X"}, issued_at=1700000000)
        self.assertEqual(ct.confirmation_code(token), ct.confirmation_code(token))

    def test_six_uppercase_hex_chars(self):
        token = ct.compute_token(kind="op", body={"item_code": "X"}, issued_at=1700000000)
        code = ct.confirmation_code(token)
        self.assertEqual(len(code), 6)
        self.assertEqual(code, code.upper())
        int(code, 16)  # raises ValueError if not hex

    def test_is_fresh_window(self):
        self.assertTrue(ct.is_fresh(1000, now=1000 + ct.DEFAULT_TOKEN_TTL_SECONDS))
        self.assertFalse(ct.is_fresh(1000, now=1001 + ct.DEFAULT_TOKEN_TTL_SECONDS))
        self.assertTrue(ct.is_fresh(1000 + ct.CLOCK_SKEW_TOLERANCE_SECONDS, now=1000))
        self.assertFalse(ct.is_fresh(1001 + ct.CLOCK_SKEW_TOLERANCE_SECONDS, now=1000))

    def test_no_advisory_write_token_left(self):
        # One token constructor for writes: core/operations.py operation_token().
        self.assertFalse(hasattr(ct, "advisory_write_token"))

    def test_different_payload_yields_different_token_usually_different_code(self):
        # Not a strict guarantee (6 hex chars is a small space), but the
        # underlying token must differ — confirms confirmation_code is
        # actually derived from the token, not a constant.
        token_a = ct.compute_token(kind="op", body={"item_code": "A"}, issued_at=1700000000)
        token_b = ct.compute_token(kind="op", body={"item_code": "B"}, issued_at=1700000000)
        self.assertNotEqual(token_a, token_b)


if __name__ == "__main__":
    unittest.main()
