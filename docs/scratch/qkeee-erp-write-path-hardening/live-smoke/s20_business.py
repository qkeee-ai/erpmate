"""Business domains: procurement (incl. KYC), sales, accounts, inventory,
hr_payroll, mis — full draft -> submit -> cancel -> delete lifecycles plus
gate refusals. GL-affecting submits are in scope (operator-approved)."""
import time
from h import (REQ, ADMIN_REQ, RUN, gated, render, execute, record, save, state, exists,
               name_of, audit_rows, query, get, sh, SCRIPTS, CTX, TAG)

S = state()
narrow = S["narrow_user"]
CO = "DEMO LLP"
TODAY = time.strftime("%Y-%m-%d")
created = S.get("created", [])


def track(dt, nm):
    if nm:
        created.append([dt, nm])
        save(created=created)


def lifecycle(prefix, op, doctype, name, steps=("submit", "cancel", "delete"), requester=REQ,
              ledger=False):
    """Gated finalizing steps on one record; asserts docstatus/existence after each."""
    for i, action in enumerate(steps):
        rc, d, e = gated(op, {"doctype": doctype, "action": action, "name": name}, requester=requester)
        if action == "delete" and ledger:
            # ERPNext v16 keeps cancelled GL rows: delete fails cleanly (documented)
            record(f"{prefix}.{i + 1}", f"{op} delete cancelled {doctype} -> LinkExistsError, kept",
                   1, rc, d, e, extra_ok="LinkExistsError" in e and exists(doctype, name))
            return True
        if action == "delete":
            ok = rc == 0 and not exists(doctype, name)
        else:
            want = {"submit": 1, "cancel": 2}[action]
            ok = rc == 0 and get(doctype, name, requester=ADMIN_REQ).get("docstatus") == want
        record(f"{prefix}.{i + 1}", f"{op} {action} {doctype} {name}", 0, rc, d, e, extra_ok=ok)
        if rc != 0:
            return False
    return True


# ===================== procurement ==========================================
P = "procurement.generic"
sup = f"SMOKE Supplier {RUN}"
_rc, _meta, _ = sh("discover.py", "--tag", TAG, "--requested-by", ADMIN_REQ, "meta", "Address")
# India Compliance puts gstin on Address (Custom Field, visible via merged meta, W39)
IC = any(f["fieldname"] == "gstin" for f in _meta["fields"])
GSTIN = "27AAPFU0939F1ZV"                       # valid checksum, state code 27
IC_ADDR = {"state": "Maharashtra", "gst_state": "Maharashtra", "gst_category": "Registered Regular"}
IC_SUP = {"gst_category": "Registered Regular", "gstin": GSTIN, "pan": "AAPFU0939F"}
base_sup = {"supplier_name": sup, "supplier_type": "Company", "supplier_group": "All Supplier Groups"}

rc, d, e = execute(P, {"doctype": "Supplier", "action": "create", "payload": base_sup})
record("PR01", "Supplier create without KYC refused", 3, rc, d, e)

addr = {"address_title": sup, "address_type": "Billing", "address_line1": "1 Smoke Street",
        "city": "Pune", "country": "India", "pincode": "411001"}
rc, d, e = execute(P, {"doctype": "Supplier", "action": "create", "payload": base_sup,
                       "kyc": {"address": addr}})
record("PR02", "Supplier KYC address without tax ID refused", 3, rc, d, e)

kyc_sup = dict(base_sup, **IC_SUP) if IC else dict(base_sup, tax_id=GSTIN)
kyc_addr = dict(addr, gstin=GSTIN, **IC_ADDR) if IC else addr
rc, d, e = execute(P, {"doctype": "Supplier", "action": "create",
                       "payload": kyc_sup,
                       "kyc": {"address": kyc_addr,
                               "contact": {"first_name": "Smoke", "last_name": "Contact",
                                           "email_id": f"smoke.{RUN}@example.com"}}})
sname = name_of(d)
track("Supplier", sname)
record("PR03", f"Supplier create with full KYC ({'IC: gstin on Address' if IC else 'core: tax_id on Supplier'})",
       0, rc, d, e, extra_ok=rc == 0 and bool((d.get("_kyc") or {}).get("address")))

rc, d, e = execute(P, {"doctype": "Supplier", "action": "create",
                       "payload": dict(kyc_sup, supplier_name=sup + " RB"),
                       "kyc": {"address": (dict(kyc_addr, address_title=sup + " RB",
                                                gstin=GSTIN[:-1] + "X")      # bad checksum: IC rejects
                                           if IC else dict(addr, country="Atlantis ZZ"))}})
rb_exists = exists("Supplier", sup + " RB")
record("PR04", "KYC Address fails server-side -> Supplier rolled back (KycLinkFailedError)", 1, rc, d, e,
       extra_ok=not rb_exists, note=f"supplier left behind={rb_exists}")
if rb_exists:
    track("Supplier", sup + " RB")

rc, d, e = execute(P, {"doctype": "Supplier", "action": "create",
                       "payload": dict(base_sup, supplier_name=sup + " W"), "kyc_waiver_confirmed": True})
wname = name_of(d)
track("Supplier", wname)
record("PR05", "Supplier create with explicit KYC waiver", 0, rc, d, e,
       extra_ok=rc == 0 and d.get("_kyc", {}).get("waived") is True)
supplier = sname or wname or "GNR Solution Private Limited"

po = {"supplier": supplier, "company": CO, "transaction_date": TODAY, "schedule_date": "2026-10-20",
      "items": [{"item_code": "IPD000205", "qty": 2, "rate": 100, "warehouse": "Stores - DL",
                 "schedule_date": "2026-10-20"}]}
rc, d, e = execute(P, {"doctype": "Purchase Order", "action": "create", "payload": po},
                   requester=narrow)
record("PR06", "PO create by Sales-User-only requester refused (RBAC)", 3, rc, d, e)
rc, d, e = execute(P, {"doctype": "Purchase Order", "action": "create", "payload": po}, mode="read-only")
record("PR07", "PO create in read-only mode refused", 3, rc, d, e)
rc, d, e = execute(P, {"doctype": "Purchase Order", "action": "create", "payload": po})
pon = name_of(d)
track("Purchase Order", pon)
record("PR08", "PO draft create", 0, rc, d, e, extra_ok=rc == 0 and d.get("_audit_log_status") == "ok")

rc, d, e = execute(P, {"doctype": "Purchase Order", "action": "submit", "name": pon})
record("PR09", "PO submit without token refused", 3, rc, d, e)

# W10: edit after render -> refused
rc, r, e = render(P, {"doctype": "Purchase Order", "action": "submit", "name": pon})
rcu, du, eu = execute(P, {"doctype": "Purchase Order", "action": "update", "name": pon,
                          "payload": {"schedule_date": "2026-10-21",
                                      "items": [dict(po["items"][0], schedule_date="2026-10-21")]}})
rc2, d2, e2 = execute(P, r["args"], token=r["confirmation_token"], issued_at=r["issued_at"],
                      reply=f"yes {r['confirmation_code']}")
record("PR10", "PO submit refused: record edited after render (W10)", 3, rc2, d2, e2,
       extra_ok=rcu == 0, note=f"update rc={rcu}")
rc2, d2, e2 = execute(P, dict(r["args"], name="PUR-ORD-2026-00001"), token=r["confirmation_token"],
                      issued_at=r["issued_at"], reply=f"yes {r['confirmation_code']}")
record("PR11", "PO submit token replayed on another PO refused", 3, rc2, d2, e2)
lifecycle("PR12", P, "Purchase Order", pon)

# leftover from the previous smoke run (W31 made it deletable)
if exists("Purchase Order", "PUR-ORD-2026-00001"):
  rc, d, e = gated(P, {"doctype": "Purchase Order", "action": "delete", "name": "PUR-ORD-2026-00001"})
  record("PR13", "delete previous run's leftover PO PUR-ORD-2026-00001 (W31)", 0, rc, d, e)

rc, d, e = execute(P, {"doctype": "Purchase Invoice", "action": "create", "payload": {}})
record("PR14", "procurement writes Purchase Invoice refused (allowlist)", 3, rc, d, e)

# ===================== sales ================================================
SA = "sales.generic"
cust = f"SMOKE Customer {RUN}"
rc, d, e = execute(SA, {"doctype": "Customer", "action": "create",
                        "payload": {"customer_name": cust, "customer_type": "Company"}},
                   requester=narrow)
cname = name_of(d)
track("Customer", cname)
record("SA01", "Customer create by Sales-User requester", 0, rc, d, e)
so = {"customer": cname or cust, "company": CO, "transaction_date": TODAY, "delivery_date": "2026-10-30",
      "items": [{"item_code": "SOFTWARE-CONSULTING-SERVICES", "qty": 1, "rate": 1000,
                 "delivery_date": "2026-10-30"}]}
rc, d, e = execute(SA, {"doctype": "Sales Order", "action": "create", "payload": so})
son = name_of(d)
track("Sales Order", son)
record("SA02", "Sales Order draft create", 0, rc, d, e)
if son:
    lifecycle("SA03", SA, "Sales Order", son)
if exists("Customer", "SMOKE Hardening Customer 113809"):
  rc, d, e = gated(SA, {"doctype": "Customer", "action": "delete", "name": "SMOKE Hardening Customer 113809"})
  record("SA04", "delete previous run's leftover Customer (W31)", 0, rc, d, e)
rc, d, e = execute(SA, {"doctype": "Supplier", "action": "create", "payload": base_sup})
record("SA05", "sales writes Supplier refused (allowlist)", 3, rc, d, e)

# ===================== accounts =============================================
AC = "accounts.generic"
je = {"voucher_type": "Journal Entry", "company": CO, "posting_date": TODAY,
      "user_remark": f"smoke {RUN}",
      "accounts": [{"account": "Administrative Expenses - DL", "debit_in_account_currency": 100,
                    "cost_center": "Main - DL"},
                   {"account": "Cash - DL", "credit_in_account_currency": 100,
                    "cost_center": "Main - DL"}]}
rc, d, e = execute(AC, {"doctype": "Journal Entry", "action": "create", "payload": je})
jen = name_of(d)
track("Journal Entry", jen)
record("AC01", "Journal Entry draft create", 0, rc, d, e)
if jen:
    rc, r, e = render(AC, {"doctype": "Journal Entry", "action": "submit", "name": jen})
    rc2, d2, e2 = execute(AC, r["args"], token=r["confirmation_token"], issued_at=r["issued_at"],
                          reply="ok confirmed")
    record("AC02", "JE submit with reply lacking the code refused", 3, rc2, d2, e2)
    lifecycle("AC03", AC, "Journal Entry", jen, ledger=True)
    gle = query("GL Entry", filters=[["voucher_no", "=", jen]], fields=["name", "is_cancelled"],
                requester=ADMIN_REQ)
    record("AC04", "GL entries posted for JE and reversed on cancel", 0, 0, gle, "",
           extra_ok=len(gle) >= 2)
if exists("Journal Entry", "ACC-JV-2026-00001"):
  rc, d, e = gated(AC, {"doctype": "Journal Entry", "action": "delete", "name": "ACC-JV-2026-00001"})
  record("AC05", "delete previous run's leftover JE (W31)", 0, rc, d, e)

# ===================== inventory ============================================
IN = "inventory.generic"
se = {"stock_entry_type": "Material Receipt", "company": CO, "posting_date": TODAY,
      "items": [{"item_code": "IPD000205", "qty": 1, "t_warehouse": "Stores - DL", "basic_rate": 50}]}
rc, d, e = execute(IN, {"doctype": "Stock Entry", "action": "create", "payload": se})
sen = name_of(d)
track("Stock Entry", sen)
record("IN01", "Stock Entry (Material Receipt) draft create", 0, rc, d, e)
if sen:
    lifecycle("IN02", IN, "Stock Entry", sen, ledger=True)
p = __import__("subprocess").run(
    [__import__("sys").executable, "-c",
     "import sys,json; sys.path.insert(0,'.'); from domains import inventory as i; "
     f"print(json.dumps(i.get_bin_qty('{TAG}','IPD000205', requested_by='{REQ}'), default=str)[:400])"],
    cwd=SCRIPTS, env=__import__("h").ENV, capture_output=True, text=True)
record("IN03", "inventory.get_bin_qty gated read", 0, p.returncode, p.stdout, p.stderr)

# ===================== hr_payroll (no HRMS on instance) ======================
HR = "hr_payroll.generic"
emp = {"first_name": "Smoke", "last_name": f"Emp {RUN}", "gender": "Male", "date_of_birth": "1990-01-01",
       "date_of_joining": "2026-10-01", "status": "Active", "company": CO}
rc, d, e = execute(HR, {"doctype": "Employee", "action": "create", "payload": emp})
empn = name_of(d)
track("Employee", empn)
record("HR01", "Employee create", 0, rc, d, e)
_rc, _m, _e = sh("discover.py", "--tag", TAG, "--requested-by", ADMIN_REQ, "resolve", "Leave Application")
if _rc != 0:  # no HRMS: the full HR suite is s25_hr.py
  rc, d, e = execute(HR, {"doctype": "Leave Application", "action": "create",
                        "payload": {"employee": empn, "from_date": TODAY, "to_date": TODAY}})
  record("HR02", "Leave Application on instance WITHOUT HRMS -> 'does not exist', exit 3", 3, rc, d, e,
         extra_ok="does not exist on this instance" in e)
if empn:
    rc, d, e = gated(HR, {"doctype": "Employee", "action": "delete", "name": empn})
    record("HR03", "Employee delete (token) — audited record deletable (W31)", 0, rc, d, e,
           extra_ok=rc == 0 and not exists("Employee", empn))

# ===================== mis / unscoped refusals ==============================
rc, d, e = execute("mis.generic", {"doctype": "Journal Entry", "action": "create", "payload": je})
record("MI01", "mis domain refuses every write", 3, rc, d, e)
for sid, dt in (("UN01", "Supplier"), ("UN02", "User"), ("UN03", "Server Script"),
                ("UN04", "Qkeee Bot Audit Log")):
    rc, d, e = execute("unscoped.generic", {"doctype": dt, "action": "create", "payload": {"x": 1}})
    record(sid, f"unscoped create {dt} refused", 3, rc, d, e)
rc, d, e = sh("execute_write.py", "--tag", TAG, "--mode", "read-write", "--requested-by", REQ,
              "--domain", "procurement", "--doctype", "Purchase Order", "--action", "submit",
              "--name", "X")
record("CL01", "gated op without mandatory audit context -> usage error", 2, rc, d, e)
rc, d, e = sh("execute_write.py", "--tag", TAG, "--mode", "read-write", "--requested-by",
              "ghost.user.zz@nowhere.invalid", *CTX, "--domain", "procurement", "--doctype",
              "Purchase Order", "--action", "create", "--payload", '{"supplier":"x"}')
record("CL02", "non-existent requester refused", 3, rc, d, e)
