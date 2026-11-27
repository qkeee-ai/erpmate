"""Remove what the smoke runs created, through the skill's own gated ops.
Ledger-posted records (cancelled JE/Stock Entry, submitted Assets with
depreciation) stay — ERPNext keeps their GL rows; listed as leftovers."""
import json
import sys
from h import ADMIN_REQ, REQ, gated, record, exists, get, query

leftovers = []
for state_file in sys.argv[1:]:
    S = json.load(open(state_file, encoding="utf-8"))
    created = S.get("created", [])
    order = {"Contact": 0, "Address": 1, "Sales Invoice": 2, "Supplier": 3, "Customer": 4,
             "Employee Onboarding": 5, "Employee Separation": 5, "Job Offer": 6, "Job Applicant": 7}
    for dt, nm in sorted(created, key=lambda x: order.get(x[0], 9)):
        if not nm or not exists(dt, nm):
            continue
        op = {"Supplier": "procurement.generic", "Address": "procurement.generic",
              "Contact": "procurement.generic", "Customer": "sales.generic",
              "Sales Invoice": "accounts.generic", "Employee Onboarding": "hr_payroll.generic",
              "Employee Separation": "hr_payroll.generic", "Job Offer": "hr_payroll.generic",
              "Job Applicant": "unscoped.generic"}.get(dt)
        # only System Manager may delete these in standard HRMS
        who = ADMIN_REQ if dt in ("Employee Onboarding", "Employee Separation") else REQ
        if op and dt == "Job Offer" and get(dt, nm, requester=ADMIN_REQ).get("docstatus") == 1:
            gated(op, {"doctype": dt, "action": "cancel", "name": nm})
        if not op:
            leftovers.append(f"{dt} {nm} (docstatus {get(dt, nm, requester=ADMIN_REQ).get('docstatus')})")
            continue
        rc, d, e = gated(op, {"doctype": dt, "action": "delete", "name": nm}, requester=who)
        record("CU", f"delete {dt} {nm}", 0, rc, d, e)
        if rc != 0:
            leftovers.append(f"{dt} {nm} (delete failed)")
users = [u["name"] for u in query("User", filters=[["name", "like", "smoke.narrow.%"],
                                                    ["enabled", "=", 1]], fields=["name"],
                                  requester=ADMIN_REQ)]
for user in users:
    if True:
        rc, d, e = gated("system_admin.disable_user", {"name": user, "reason": "smoke cleanup"},
                         requester=ADMIN_REQ)
        record("CU", f"disable_user {user}", 0, rc, d, e)
        rc, d, e = gated("system_admin.delete", {"doctype": "User", "name": user,
                                                 "reason": "smoke cleanup"}, requester=ADMIN_REQ)
        record("CU", f"delete User {user}: Frappe refuses a user with history (disable instead)", 1, rc, d, e)
        if rc != 0:
            leftovers.append(f"User {user} (disabled, delete failed)")
# Note runs on Custom DocPerm only because the permission tests touched it
rc, d, e = gated("system_admin.permission_reset", {"doctype": "Note", "reason": "smoke cleanup"},
                 requester=ADMIN_REQ)
record("CU", "permission_reset Note (restore standard permissions)", 0, rc, d, e)
print("LEFTOVERS:\n  " + "\n  ".join(leftovers))
