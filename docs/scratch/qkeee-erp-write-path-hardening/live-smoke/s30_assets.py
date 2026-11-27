"""fixed_assets domain end to end. Fixtures seeded via unscoped.generic
(every unscoped action is token+code gated)."""
from h import (REQ, ADMIN_REQ, RUN, gated, render, execute, record, save, state, exists,
               name_of, query, get)

S = state()
created = S.get("created", [])
CO = "DEMO LLP"


def track(dt, nm):
    if nm:
        created.append([dt, nm])
        save(created=created)


U = "unscoped.generic"
cat, loc, item = f"SMOKE Cat {RUN}", f"SMOKE Loc {RUN}", f"SMOKE-FA-{RUN}"
rc, d, e = execute(U, {"doctype": "Location", "action": "create", "payload": {"location_name": loc}})
record("FX01", "unscoped Location create WITHOUT token refused", 3, rc, d, e)
rc, d, e = gated(U, {"doctype": "Location", "action": "create", "payload": {"location_name": loc}})
track("Location", name_of(d))
record("FX02", "unscoped Location create (token+code)", 0, rc, d, e)
rc, d, e = gated(U, {"doctype": "Asset Category", "action": "create", "payload": {
    "asset_category_name": cat,
    "accounts": [{"company_name": CO, "fixed_asset_account": "Office Equipment - DL",
                  "accumulated_depreciation_account": "Accumulated Depreciations - DL",
                  "depreciation_expense_account": "Depreciation - DL"}]}})
track("Asset Category", name_of(d))
record("FX03", "unscoped Asset Category create", 0, rc, d, e)
rc, d, e = gated(U, {"doctype": "Item", "action": "create", "payload": {
    "item_code": item, "item_name": f"Smoke laptop {RUN}", "item_group": "Products",
    "stock_uom": "Nos", "is_stock_item": 0, "is_fixed_asset": 1, "asset_category": cat,
    # India Compliance: HSN mandatory (a Custom Field: kept only via merged meta, W39);
    # schema mapping drops it on an instance without IC
    "gst_hsn_code": "84713010"}})
track("Item", name_of(d))
record("FX04", "unscoped fixed-asset Item create", 0, rc, d, e)
if rc == 0:
    hsn = get("Item", item, requester=ADMIN_REQ).get("gst_hsn_code")
    record("FX05", "custom field gst_hsn_code persisted (or absent without IC)", 0, 0, hsn, "",
           extra_ok=hsn in ("84713010", None))

FA = "fixed_assets.generic"


def new_asset(sid, label):
    payload = {"item_code": item, "asset_name": f"SMOKE {label} {RUN}", "location": loc,
               "company": CO, "asset_category": cat, "purchase_date": "2026-04-01",
               "available_for_use_date": "2026-04-01", "gross_purchase_amount": 36000,
               "net_purchase_amount": 36000, "is_existing_asset": 1, "calculate_depreciation": 1,
               "finance_books": [{"depreciation_method": "Straight Line",
                                  "total_number_of_depreciations": 36,
                                  "frequency_of_depreciation": 1,
                                  "depreciation_start_date": "2026-04-30",
                                  "expected_value_after_useful_life": 0}]}
    rc, d, e = execute(FA, {"doctype": "Asset", "action": "create", "payload": payload})
    nm = name_of(d)
    track("Asset", nm)
    record(f"{sid}.1", f"Asset draft create ({label})", 0, rc, d, e, note=str(nm))
    if not nm:
        return None
    rc, d, e = gated(FA, {"doctype": "Asset", "action": "submit", "name": nm})
    record(f"{sid}.2", f"Asset submit ({label})", 0, rc, d, e,
           extra_ok=rc == 0 and get("Asset", nm, requester=ADMIN_REQ).get("docstatus") == 1)
    return nm if rc == 0 else None


a1 = new_asset("FA01", "A1")
a2 = new_asset("FA02", "A2")
save(asset1=a1, asset2=a2)


def schedule_of(asset):
    rows = query("Asset Depreciation Schedule", filters=[["asset", "=", asset], ["docstatus", "=", 1]],
                 fields=["name"], requester=ADMIN_REQ)
    return rows[0]["name"] if rows else None


if a1:
    ads = schedule_of(a1)
    rc, r, e = render("fixed_assets.depreciation_run", {"depr_schedule_name": ads, "date": "2026-07-31"})
    record("FA03", "render depreciation_run shows 4 pending rows / 12000", 0, rc, r, e,
           extra_ok=rc == 0 and r["args"]["pending_rows"] == 4
           and abs(r["args"]["total_depreciation"] - 4000) < 1,
           note=f"pending={r['args'].get('pending_rows') if rc == 0 else '?'} "
                f"total={r['args'].get('total_depreciation') if rc == 0 else '?'}")
    rc2, d2, e2 = execute("fixed_assets.depreciation_run", dict(r["args"], date="2026-09-30"),
                          token=r["confirmation_token"], issued_at=r["issued_at"],
                          reply=f"yes {r['confirmation_code']}")
    record("FA04", "depreciation_run with date changed after render refused", 3, rc2, d2, e2)
    if a2:
        rc2, d2, e2 = gated("fixed_assets.depreciation_run",
                            {"depr_schedule_name": ads, "date": "2026-07-31", "asset": a2})
        record("FA05", "depreciation_run naming the wrong asset refused", 3, rc2, d2, e2)
    rc2, d2, e2 = render("fixed_assets.depreciation_run",
                         {"depr_schedule_name": ads, "date": "2026-07-31", "sch_start_idx": 1})
    record("FA06", "depreciation_run with slice index refused as malformed args (W24)", 2, rc2, d2, e2)
    rc2, d2, e2 = execute("fixed_assets.depreciation_run", r["args"], token=r["confirmation_token"],
                          issued_at=r["issued_at"], reply=f"yes {r['confirmation_code']}")
    sched = get("Asset Depreciation Schedule", ads, requester=ADMIN_REQ)
    booked = [x for x in sched.get("depreciation_schedule", []) if x.get("journal_entry")]
    record("FA07", "depreciation_run posts exactly the 4 due rows", 0, rc2, d2, e2,
           extra_ok=rc2 == 0 and len(booked) == 4, note=f"booked={len(booked)}")
    rc2, r2, e2 = render("fixed_assets.depreciation_run", {"depr_schedule_name": ads, "date": "2026-07-31"})
    record("FA08", "re-render after run shows 0 pending (no double-post)", 0, rc2, r2, e2,
           extra_ok=rc2 == 0 and r2["args"]["pending_rows"] == 0)

    # scrap: tamper, then real
    rc, r, e = render("fixed_assets.scrap", {"asset": a1, "scrap_date": "2026-10-06", "reason": "smoke"})
    rc2, d2, e2 = execute("fixed_assets.scrap", dict(r["args"], scrap_date="2026-08-01"),
                          token=r["confirmation_token"], issued_at=r["issued_at"],
                          reply=f"yes {r['confirmation_code']}")
    record("FA09", "scrap with scrap_date changed after render refused (W25)", 3, rc2, d2, e2)
    rc2, d2, e2 = execute("fixed_assets.scrap", r["args"], token=r["confirmation_token"],
                          issued_at=r["issued_at"], reply=f"yes {r['confirmation_code']}")
    st = get("Asset", a1, requester=ADMIN_REQ)
    record("FA10", "scrap asset -> status Scrapped + scrap JE", 0, rc2, d2, e2,
           extra_ok=rc2 == 0 and st.get("status") == "Scrapped" and st.get("journal_entry_for_scrap"),
           note=f"status={st.get('status')} je={st.get('journal_entry_for_scrap')} "
                f"audit={d2.get('_audit_log_status') if isinstance(d2, dict) else None}")
    scrap_je = st.get("journal_entry_for_scrap")
    rc2, d2, e2 = execute("fixed_assets.restore", {"asset": a1, "reason": "smoke"})
    record("FA11", "restore without token refused (W06)", 3, rc2, d2, e2)
    rc2, d2, e2 = gated("fixed_assets.restore", {"asset": a1, "reason": "smoke restore"})
    st = get("Asset", a1, requester=ADMIN_REQ)
    je_ds = get("Journal Entry", scrap_je, requester=ADMIN_REQ).get("docstatus") if scrap_je else None
    record("FA12", "restore -> status not Scrapped, scrap JE cancelled", 0, rc2, d2, e2,
           extra_ok=rc2 == 0 and st.get("status") != "Scrapped" and je_ds == 2,
           note=f"status={st.get('status')} scrap_je_docstatus={je_ds}")

if a2:
    cust = [n for dt, n in created if dt == "Customer"][-1]  # from s20 (SA01)
    rc, r, e = render("fixed_assets.sell", {"asset": a2, "item_code": item, "company": CO, "sell_qty": 1,
                                            "customer": cust, "reason": "smoke sale",
                                            "sale_proceeds": 30000})
    record("FA13", "render sell maps a Sales Invoice", 0, rc, r, e)
    if rc == 0:
        tampered = dict(r["args"])
        inv = dict(tampered["invoice"])
        inv["posting_date"] = "2026-04-01"  # rate is normalized from sale_proceeds; edit a free field
        tampered["invoice"] = inv
        rc2, d2, e2 = execute("fixed_assets.sell", tampered, token=r["confirmation_token"],
                              issued_at=r["issued_at"], reply=f"yes {r['confirmation_code']}")
        record("FA14", "sell with invoice edited after render refused", 3, rc2, d2, e2)
        rc2, d2, e2 = execute("fixed_assets.sell", r["args"], token=r["confirmation_token"],
                              issued_at=r["issued_at"], reply=f"yes {r['confirmation_code']}")
        si = name_of(d2)
        track("Sales Invoice", si)
        si_doc = get("Sales Invoice", si, requester=ADMIN_REQ) if si else {}
        record("FA15", "sell creates DRAFT Sales Invoice with the asset row", 0, rc2, d2, e2,
               extra_ok=rc2 == 0 and si_doc.get("docstatus") == 0
               and any(i.get("asset") == a2 for i in si_doc.get("items", [])), note=str(si))
        if si:
            rc3, d3, e3 = gated("accounts.generic", {"doctype": "Sales Invoice", "action": "delete",
                                                     "name": si})
            record("FA16", "delete the draft disposal Sales Invoice (token)", 0, rc3, d3, e3,
                   extra_ok=rc3 == 0 and not exists("Sales Invoice", si))
