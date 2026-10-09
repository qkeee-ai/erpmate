#!/usr/bin/env python3
"""Regression tests for item_write_helpers.py (F6, F9 — .scratch/
hermes-erp-bot-reliability/spec.md). Bare `import item_write_helpers`
matches this repo's convention for a top-level scripts/ module — pytest
inserts the file's own directory into sys.path when collecting it (no
__init__.py there)."""

import unittest

import item_write_helpers as iwh


class PurchaseSourcedDefaultsTests(unittest.TestCase):
    """F9: nothing in a purchase document supports 'the org resells
    this' — is_sales_item must default to 0, not 1, for a purchase-
    sourced item."""

    def test_defaults_applied_when_absent(self):
        result = iwh.apply_purchase_sourced_item_defaults(
            {"item_code": "APPLE-IPAD-AIR11-128GB"}
        )
        self.assertEqual(result["is_stock_item"], 1)
        self.assertEqual(result["is_purchase_item"], 1)
        self.assertEqual(result["is_sales_item"], 0)

    def test_explicit_caller_value_is_never_overridden(self):
        # Even a value that contradicts the "purchase-sourced" defaults
        # — the caller knows something this function doesn't (e.g. a
        # confirmed dual-use item) and always wins.
        result = iwh.apply_purchase_sourced_item_defaults(
            {"item_code": "X", "is_sales_item": 1, "is_stock_item": 0}
        )
        self.assertEqual(result["is_sales_item"], 1)
        self.assertEqual(result["is_stock_item"], 0)
        self.assertEqual(result["is_purchase_item"], 1)  # still defaulted, wasn't set

    def test_input_dict_is_not_mutated(self):
        original = {"item_code": "X"}
        iwh.apply_purchase_sourced_item_defaults(original)
        self.assertEqual(original, {"item_code": "X"})

    def test_other_fields_pass_through_unchanged(self):
        result = iwh.apply_purchase_sourced_item_defaults(
            {"item_code": "X", "item_name": "Widget", "item_group": "Products"}
        )
        self.assertEqual(result["item_name"], "Widget")
        self.assertEqual(result["item_group"], "Products")


class BareStandardRateRefusalTests(unittest.TestCase):
    """F6: standard_rate on Item create auto-creates a Standard SELLING
    Item Price, not Standard Buying — refuse before that can happen
    again, rather than silently letting a purchase cost become a sales
    price."""

    def test_bare_standard_rate_raises_before_any_defaults_applied(self):
        with self.assertRaises(iwh.BareStandardRateOnPurchaseSourcedItemError):
            iwh.apply_purchase_sourced_item_defaults(
                {"item_code": "X", "standard_rate": 40677.98}
            )

    def test_error_message_points_to_the_fix(self):
        try:
            iwh.apply_purchase_sourced_item_defaults({"item_code": "X", "standard_rate": 1.0})
            self.fail("expected BareStandardRateOnPurchaseSourcedItemError")
        except iwh.BareStandardRateOnPurchaseSourcedItemError as e:
            self.assertIn("build_standard_buying_item_price_payload", str(e))

    def test_is_a_value_error(self):
        # Callers doing a broad `except ValueError` (a common shape for
        # "bad input" in this style of helper) still catch it.
        self.assertTrue(issubclass(iwh.BareStandardRateOnPurchaseSourcedItemError, ValueError))


class StandardBuyingItemPricePayloadTests(unittest.TestCase):
    def test_shape_is_buying_not_selling(self):
        payload = iwh.build_standard_buying_item_price_payload(
            "APPLE-IPAD-AIR11-128GB", 40677.98, "INR"
        )
        self.assertEqual(payload, {
            "item_code": "APPLE-IPAD-AIR11-128GB",
            "price_list": "Standard Buying",
            "price_list_rate": 40677.98,
            "currency": "INR",
            "buying": 1,
            "selling": 0,
        })

    def test_price_list_override(self):
        payload = iwh.build_standard_buying_item_price_payload(
            "X", 1.0, "INR", price_list="Custom Vendor Price List"
        )
        self.assertEqual(payload["price_list"], "Custom Vendor Price List")


if __name__ == "__main__":
    unittest.main()
