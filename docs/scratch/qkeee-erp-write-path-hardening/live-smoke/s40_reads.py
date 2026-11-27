"""Read paths: client.py CLI, discover.py, domain read helpers."""
import json
import subprocess
import sys
import h
from h import REQ, ADMIN_REQ, TAG, read, sh, record, state, SCRIPTS, ENV

S = state()
narrow = S.get("narrow_user")

rc, d, e = sh("core/client.py", "list-envs")
record("RD01", "list-envs", 0, rc, d, e, extra_ok=rc == 0 and "DEMO_ERP" in d["configured_tags"])
rc, d, e = sh("core/client.py", "--tag", TAG, "health")
record("RD02", "health", 0, rc, d, e, extra_ok=rc == 0 and d["status"] == "ok")
rc, d, e = read("query", "Purchase Order", "--fields", '["name","docstatus"]', "--limit", "5")
record("RD03", "query Purchase Order", 0, rc, d, e)
rc, d, e = read("query", "Item", "--filters", '[["item_code","=","IPD000205"]]')
record("RD04", "query with filters", 0, rc, d, e, extra_ok=rc == 0 and len(d["data"]) == 1)
rc, d, e = read("get", "Item", "IPD000205")
record("RD05", "get Item", 0, rc, d, e)
rc, d, e = read("get", "Item", "NO-SUCH-ITEM-ZZ")
record("RD06", "get missing record -> clean error, no traceback", 1, rc, d, e,
       extra_ok="Traceback" not in e)
rc, d, e = read("report", "Accounts Receivable", "--filters",
                json.dumps({"company": "DEMO LLP", "report_date": "2026-10-06", "range": "30, 60, 90, 120"}))
record("RD07", "report Accounts Receivable", 0, rc, d, e)
rc, d, e = read("report", "General Ledger", "--filters",
                json.dumps({"company": "DEMO LLP", "from_date": "2026-04-01", "to_date": "2026-10-06",
                            "group_by": "Group by Voucher (Consolidated)"}))
record("RD08", "report General Ledger", 0, rc, d, e)
rc, d, e = read("report", "Stock Balance", "--filters",
                json.dumps({"company": "DEMO LLP", "from_date": "2026-04-01", "to_date": "2026-10-06"}))
record("RD09", "report Stock Balance", 0, rc, d, e)
rc, d, e = read("roles", "--user", REQ)
record("RD10", "roles --user", 0, rc, d, e)
if narrow:
    rc, d, e = read("query", "Purchase Order", requester=narrow)
    record("RD11", "Sales-User-only requester cannot read Purchase Order (read RBAC)", 3, rc, d, e)
rc, d, e = read("query", "Qkeee No Such Doctype", requester=ADMIN_REQ)
record("RD12", "read of doctype missing on instance -> 'does not exist', not permission", 3, rc, d, e,
       extra_ok="does not exist on this instance" in e)

D = ("discover.py", "--tag", TAG, "--requested-by", ADMIN_REQ)
rc, d, e = sh(*D, "apps")
record("RD13", "discover apps", 0, rc, d, e)
rc, d, e = sh(*D, "modules")
record("RD14", "discover modules", 0, rc, d, e)
rc, d, e = sh(*D, "meta", "Purchase Order")
record("RD15", "discover meta Purchase Order", 0, rc, d, e)
rc, d, e = sh(*D, "resolve", "Asset")
record("RD16", "discover resolve Asset", 0, rc, d, e)


def py(sid, desc, code, expect=0):
    p = subprocess.run([sys.executable, "-c", "import sys,json; sys.path.insert(0,'.'); " + code],
                       cwd=SCRIPTS, env=ENV, capture_output=True, text=True, encoding="utf-8")
    record(sid, desc, expect, p.returncode, p.stdout[:400], p.stderr)


py("RD17", "system_admin.get_permissions(Note)",
   f"from domains import system_admin as s; r=s.get_permissions('{TAG}','Note', requested_by='{ADMIN_REQ}'); print(len(r))")
py("RD18", "system_admin.get_roles_and_doctypes",
   f"from domains import system_admin as s; r=s.get_roles_and_doctypes('{TAG}', requested_by='{ADMIN_REQ}'); print(list(r)[:3])")
py("RD19", "system_admin.get_scheduler_status",
   f"from domains import system_admin as s; print(s.get_scheduler_status('{TAG}', requested_by='{ADMIN_REQ}'))")
py("RD20", "inventory.get_stock_reconciliation_items",
   f"from domains import inventory as i; print(json.dumps(i.get_stock_reconciliation_items('{TAG}','Stores - DL','DEMO LLP','2026-10-06', requested_by='{REQ}'), default=str)[:300])")
if narrow:
    py("RD21", "narrow requester cannot read permissions (admin read RBAC)",
       f"from domains import system_admin as s; s.get_permissions('{TAG}','Note', requested_by='{narrow}')",
       expect=1)
