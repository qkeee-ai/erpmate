#!/usr/bin/env python3
"""
Item payload helpers for the domain-less gated_mutate_resource() write
path. `Item` has no owning domain yet — these two narrow data-quality
fixes live at the payload-shaping step, ahead of and independent from
whatever a larger schema-first design eventually does with Item. If/when
Item gets a real domain module, that module's `mutate()` should call into
these same functions rather than re-deriving the logic — don't duplicate
it there.

Deliberately NOT wired into `core/client.py` or `execute_write.py`'s
generic dispatch as a hardcoded rule: Item-specific business judgment
belongs at the Item call site, not the ERP-agnostic connector (same
layering `01-connectivity.md` already draws for domain judgment
generally). `execute_write.py`'s `--purchase-sourced-item` flag is a
thin, optional, Item-specific conditional around these functions — see
that file.

Pure functions, no I/O, no ERPNext calls — easy to unit-test in
isolation and safe to call before any write fires.
"""

# Nothing in a purchase document supports "the org resells this" — a
# Purchase Order/Invoice/GRN line only establishes the org BOUGHT the
# item, so is_sales_item defaults to 0. is_stock_item defaults on too (a
# purchased item overwhelmingly tracks stock) but, like the other two,
# only as a default — an explicit caller value always wins, see
# apply_purchase_sourced_item_defaults().
PURCHASE_SOURCED_ITEM_DEFAULTS = {
    "is_stock_item": 1,
    "is_purchase_item": 1,
    "is_sales_item": 0,
}


class BareStandardRateOnPurchaseSourcedItemError(ValueError):
    """Setting `standard_rate` on an Item `create` makes ERPNext
    auto-create an Item Price against **Standard Selling** (`selling=1,
    buying=0`) — not Standard Buying. A supplier invoice's cost (what the
    org paid) would land, unnoticed, as this org's sales price (what it
    would charge a customer), with no buying-side price created at all.
    Raised by apply_purchase_sourced_item_defaults() rather than letting
    that happen silently — see that function's docstring for the fix."""


def apply_purchase_sourced_item_defaults(payload: dict) -> dict:
    """Returns a NEW dict (the input is never mutated) with
    PURCHASE_SOURCED_ITEM_DEFAULTS filled in for any of those three keys
    the caller's payload doesn't already set explicitly — an explicit
    caller value always wins, this only fills gaps left blank. Use for
    an Item whose only evidence is a purchase document (PO, purchase
    invoice, GRN) — not a general-purpose Item default.

    Refuses a bare `standard_rate` key outright
    (BareStandardRateOnPurchaseSourcedItemError, see its own docstring)
    rather than silently creating a Standard Selling Item Price from a
    purchase cost. Pop `standard_rate` out of your payload before calling
    this, create the Item, then set the buying-side rate explicitly —
    see build_standard_buying_item_price_payload() below — as its own
    separate confirmed write, never auto-chained onto the Item create
    (00-conventions.md's save-draft-then-review-then-submit spirit: an
    auto-chained second write with no confirmation of its own is a
    version of the same mistake this function exists to stop).
    """
    if "standard_rate" in payload:
        raise BareStandardRateOnPurchaseSourcedItemError(
            "Refusing to build a purchase-sourced Item payload with a bare 'standard_rate' key "
            "present — ERPNext auto-creates a Standard SELLING Item Price from it, not Standard "
            "Buying. Remove 'standard_rate' from the payload, create the Item, then create "
            "the buying-side Item Price explicitly via "
            "build_standard_buying_item_price_payload() as its own confirmed write."
        )
    result = dict(payload)
    for field, default in PURCHASE_SOURCED_ITEM_DEFAULTS.items():
        result.setdefault(field, default)
    return result


def build_standard_buying_item_price_payload(item_code: str, rate: float, currency: str,
                                              price_list: str = "Standard Buying") -> dict:
    """The buying-side counterpart to what a bare `standard_rate` on Item
    create would auto-create on the selling side instead. Feed this to a
    separate `gated_mutate_resource(tag, "Item Price", "create", ...)`
    call (`Item Price` is itself domain-less — no domain's
    ALLOWED_WRITE_DOCTYPES claims it — same advisory-token/
    user_confirmation_text path as Item) — a distinct, explicitly
    confirmed write, not something this helper fires on its own."""
    return {
        "item_code": item_code,
        "price_list": price_list,
        "price_list_rate": rate,
        "currency": currency,
        "buying": 1,
        "selling": 0,
    }
