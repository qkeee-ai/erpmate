"""Instance snapshot: what fixtures exist before the run."""
import json
from h import query, read, sh, ADMIN_REQ

def show(label, fn):
    try:
        v = fn()
        print(f"== {label}: {json.dumps(v, default=str)[:900]}")
    except Exception as e:  # noqa: BLE001
        print(f"== {label}: ERROR {e}")

show("Company", lambda: query("Company", fields=["name", "abbr", "default_currency"]))
show("Fiscal Year", lambda: query("Fiscal Year", fields=["name", "year_start_date", "year_end_date"]))
show("Asset", lambda: query("Asset", fields=["name", "status", "docstatus", "item_code"], requester=ADMIN_REQ))
show("Asset Category", lambda: query("Asset Category", fields=["name"], requester=ADMIN_REQ))
show("Location", lambda: query("Location", fields=["name"], requester=ADMIN_REQ))
show("Employee", lambda: query("Employee", fields=["name", "employee_name", "company"], requester=ADMIN_REQ))
show("Leave Type", lambda: query("Leave Type", fields=["name"], requester=ADMIN_REQ))
show("Holiday List", lambda: query("Holiday List", fields=["name"], requester=ADMIN_REQ))
show("Supplier", lambda: query("Supplier", fields=["name", "disabled"]))
show("Customer", lambda: query("Customer", fields=["name", "disabled"], requester=ADMIN_REQ))
show("Item", lambda: query("Item", fields=["name", "is_stock_item", "is_fixed_asset", "disabled"]))
show("Warehouse", lambda: query("Warehouse", fields=["name", "is_group"]))
show("Account(Bank/Cash)", lambda: query("Account", filters=[["account_type", "in", ["Bank", "Cash"]]], fields=["name"]))
show("Account(Expense)", lambda: query("Account", filters=[["root_type", "=", "Expense"], ["is_group", "=", 0]], fields=["name"], limit=5))
show("PO", lambda: query("Purchase Order", fields=["name", "docstatus", "supplier"]))
show("JE", lambda: query("Journal Entry", fields=["name", "docstatus"]))
show("Webhook", lambda: query("Webhook", fields=["name"], requester=ADMIN_REQ))
show("Workflow", lambda: query("Workflow", fields=["name", "is_active", "document_type"], requester=ADMIN_REQ))
show("Audit Log last", lambda: query("Qkeee Bot Audit Log", fields=["name", "action", "reference_doctype", "reference_name", "status"], limit=5, requester=ADMIN_REQ))
print("roles nikhil", read("roles", "nikhil.sharma@qkeee.in")[1])
print("roles bot", read("roles", "dev-erp-hermes@qkeee.in")[1])
print("roles ravi", read("roles", "ravi.khandelwal@qkeee.in", requester=ADMIN_REQ)[1])
print("meta audit ref", sh("discover.py", "--tag", "demo_erp", "--requested-by", ADMIN_REQ, "meta", "Qkeee Bot Audit Log")[1:])
