"""Retest of every scenario that failed in the first pass, against the fixed code."""
from h import (REQ, ADMIN_REQ, RUN, gated, render, execute, record, save, state, exists,
               name_of, get, read, query)

S = state()
created = S.get("created", [])
narrow = S["narrow_user"]


def track(dt, nm):
    if nm:
        created.append([dt, nm])
        save(created=created)


# SY24/SY29 — webhook create now takes `name`
hook = f"SMOKE Hook R {RUN}"
rc, d, e = execute("system_admin.create_webhook",
                   {"payload": {"webhook_doctype": "Note", "webhook_docevent": "after_insert",
                                "request_url": "https://hooks.example.com/smoke"}, "reason": "smoke"},
                   requester=ADMIN_REQ)
record("R-SY24a", "create_webhook without name -> usage refusal before any send", 2, rc, d, e,
       note="usage error at CLI (missing required arg)")
rc, d, e = gated("system_admin.create_webhook",
                 {"name": hook, "payload": {"webhook_doctype": "Note", "webhook_docevent": "after_insert",
                                            "request_url": "https://hooks.example.com/smoke", "enabled": 0},
                  "reason": "smoke"}, requester=ADMIN_REQ)
record("R-SY24", "create_webhook with name", 0, rc, d, e, extra_ok=rc == 0 and name_of(d) == hook)
for h_ in (hook, S.get("hook")):
    if h_ and exists("Webhook", h_):
        rc, d, e = gated("system_admin.delete", {"doctype": "Webhook", "name": h_, "reason": "cleanup"},
                         requester=ADMIN_REQ)
        record("R-SY29", f"system_admin.delete Webhook {h_}", 0, rc, d, e,
               extra_ok=rc == 0 and not exists("Webhook", h_))

# PR03/PR04 — KYC on a core (no India Compliance) instance: tax ID on Supplier.tax_id
P = "procurement.generic"
sup = f"SMOKE Supplier K {RUN}"
addr = {"address_title": sup, "address_type": "Billing", "address_line1": "1 Smoke Street",
        "city": "Pune", "country": "India", "pincode": "411001"}
rc, d, e = execute(P, {"doctype": "Supplier", "action": "create",
                       "payload": {"supplier_name": sup, "supplier_type": "Company"},
                       "kyc": {"address": dict(addr, gstin="27AAPFU0939F1ZV")}})
record("R-PR02b", "KYC with gstin on Address only -> refused, message points to Supplier.tax_id", 3,
       rc, d, e, extra_ok="tax_id" in e and "Supplier" in e)
rc, d, e = execute(P, {"doctype": "Supplier", "action": "create",
                       "payload": {"supplier_name": sup, "supplier_type": "Company",
                                   "tax_id": "27AAPFU0939F1ZV"},
                       "kyc": {"address": addr, "contact": {"first_name": "Smoke", "last_name": "K",
                                                            "email_id": f"smoke.k.{RUN}@example.com"}}})
sn = name_of(d)
track("Supplier", sn)
kyc = d.get("_kyc", {}) if isinstance(d, dict) else {}
a_name = ((kyc.get("address") or {}).get("data") or {}).get("name")
c_name = ((kyc.get("contact") or {}).get("data") or {}).get("name")
track("Address", a_name)
track("Contact", c_name)
linked = bool(a_name) and any(l.get("link_name") == sn for l in
                              get("Address", a_name, requester=ADMIN_REQ).get("links", []))
record("R-PR03", "Supplier+KYC (tax_id on Supplier) -> Supplier, Address, Contact linked", 0, rc, d, e,
       extra_ok=rc == 0 and linked and bool(c_name), note=f"{sn} / {a_name} / {c_name}")
rb = f"SMOKE Supplier RB {RUN}"
rc, d, e = execute(P, {"doctype": "Supplier", "action": "create",
                       "payload": {"supplier_name": rb, "supplier_type": "Company", "tax_id": "X1"},
                       "kyc": {"address": dict(addr, address_title=rb, country="Atlantis ZZ")}})
left = exists("Supplier", rb)
if left:
    track("Supplier", rb)
record("R-PR04", "KYC Address fails server-side -> Supplier rolled back (W31 makes it deletable)",
       1, rc, d, e, extra_ok=not left and "deleted again" in e, note=f"left_behind={left}")

# FA15/FA16 — sell with customer + proceeds
cust = [n for dt, n in created if dt == "Customer"][-1]
a2 = S["asset2"]
rc, r, e = render("fixed_assets.sell", {"asset": a2, "item_code": f"SMOKE-FA-{RUN}", "company": "DEMO LLP",
                                        "sell_qty": 1, "customer": cust, "reason": "smoke sale",
                                        "sale_proceeds": 30000})
row = (r["request"]["body"]["items"][0] if rc == 0 else {})
record("R-FA13", "render sell shows customer and rate = proceeds", 0, rc, r, e,
       extra_ok=rc == 0 and r["request"]["body"]["customer"] == cust and row.get("rate") == 30000)
if rc == 0:
    rc2, d2, e2 = execute("fixed_assets.sell", r["args"], token=r["confirmation_token"],
                          issued_at=r["issued_at"], reply=f"yes {r['confirmation_code']}")
    si = name_of(d2)
    track("Sales Invoice", si)
    doc = get("Sales Invoice", si, requester=ADMIN_REQ) if si else {}
    record("R-FA15", "sell creates DRAFT SI, customer set, asset row at 30000", 0, rc2, d2, e2,
           extra_ok=rc2 == 0 and doc.get("docstatus") == 0 and doc.get("customer") == cust
           and any(i.get("asset") == a2 and i.get("rate") == 30000 for i in doc.get("items", [])),
           note=str(si))
    if si:
        rc3, d3, e3 = gated("accounts.generic", {"doctype": "Sales Invoice", "action": "delete", "name": si})
        record("R-FA16", "delete the draft disposal SI", 0, rc3, d3, e3,
               extra_ok=rc3 == 0 and not exists("Sales Invoice", si))

# HR02 / RD11 / RD12 — missing doctype + read exit codes
rc, d, e = execute("hr_payroll.generic", {"doctype": "Leave Application", "action": "create",
                                          "payload": {"from_date": "2026-10-06"}})
record("R-HR02", "Leave Application on instance without HRMS -> 'does not exist', exit 3", 3, rc, d, e,
       extra_ok="does not exist on this instance" in e)
rc, d, e = read("query", "Purchase Order", requester=narrow)
record("R-RD11", "read RBAC refusal exits 3", 3, rc, d, e)
rc, d, e = read("query", "Leave Application", requester=ADMIN_REQ)
record("R-RD12", "read of missing doctype exits 3 with clear message", 3, rc, d, e,
       extra_ok="does not exist on this instance" in e)

# AC03.3 / IN02.3 — re-classified: ERPNext keeps cancelled ledger rows (documented now)
for sid, dt in (("R-AC03.3", "Journal Entry"), ("R-IN02.3", "Stock Entry")):
    nm = [n for t, n in created if t == dt][-1]
    rc, d, e = gated({"Journal Entry": "accounts.generic", "Stock Entry": "inventory.generic"}[dt],
                     {"doctype": dt, "action": "delete", "name": nm})
    record(sid, f"delete cancelled {dt} -> clean LinkExistsError (GL), exit 1, record kept", 1, rc, d, e,
           extra_ok="LinkExistsError" in e and exists(dt, nm))
