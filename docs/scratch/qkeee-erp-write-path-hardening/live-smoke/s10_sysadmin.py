"""system_admin domain: every op live, with the admin credential."""
import time
from h import (ADMIN_REQ, REQ, RUN, gated, render, execute, record, save, state, exists,
               name_of, audit_rows, CTX, sh, TAG)

A = ADMIN_REQ
narrow = f"smoke.narrow.{RUN}@example.com"
role = f"SMOKE Role {RUN}"

# --- create_user ------------------------------------------------------------
rc, d, e = gated("system_admin.create_user",
                 {"email": narrow, "first_name": "Smoke", "last_name": f"Narrow {RUN}",
                  "roles": ["Sales User"]}, requester=A)
record("SY01", "create_user narrow (Sales User)", 0, rc, d, e,
       extra_ok=rc == 0 and d.get("_audit_log_status") == "ok")
save(narrow_user=narrow)

rc, d, e = render("system_admin.create_user", {"email": f"x.{RUN}@example.com", "first_name": "X",
                                               "roles": ["No Such Role ZZ"]}, requester=A)
rc2, d2, e2 = execute("system_admin.create_user", {"email": f"x.{RUN}@example.com",
                      "first_name": "X", "roles": ["No Such Role ZZ"],},
                      requester=A, token=(d or {}).get("confirmation_token") if isinstance(d, dict) else None,
                      issued_at=(d or {}).get("issued_at") if isinstance(d, dict) else None,
                      reply="yes " + str((d or {}).get("confirmation_code") if isinstance(d, dict) else ""))
record("SY02", "create_user with nonexistent role refused", 3, rc2, d2, e2)

rc, d, e = render("system_admin.create_user", {"email": f"y.{RUN}@example.com", "first_name": "Y",
                                               "roles": ["System Manager", "Sales User"]}, requester=A)
record("SY03", "render create_user flags elevated role", 0, rc, d, e,
       extra_ok=rc == 0 and d["request"]["confirmed_facts"]["elevated_roles"] == ["System Manager"])

# --- wrong-confirmation discipline (on admin ops) ---------------------------
args = {"name": narrow, "reason": "smoke"}
rc, r, e = render("system_admin.disable_user", args, requester=A)
rc2, d2, e2 = execute("system_admin.disable_user", r["args"], requester=A,
                      token=r["confirmation_token"], issued_at=r["issued_at"], reply="yes ZZZZZZ")
record("SY04", "disable_user with wrong confirmation code refused", 3, rc2, d2, e2)
rc2, d2, e2 = execute("system_admin.disable_user", r["args"], requester=A,
                      token=r["confirmation_token"], issued_at=r["issued_at"])
record("SY05", "disable_user with no user reply refused", 3, rc2, d2, e2)
rc2, d2, e2 = execute("system_admin.disable_user", dict(r["args"], name="ravi.khandelwal@qkeee.in"),
                      requester=A, token=r["confirmation_token"], issued_at=r["issued_at"],
                      reply=f"yes {r['confirmation_code']}")
record("SY06", "token for user X replayed against user Y refused", 3, rc2, d2, e2)
rc2, d2, e2 = execute("system_admin.disable_user", r["args"], requester=A,
                      token=r["confirmation_token"], issued_at=int(time.time()) - 2000,
                      reply=f"yes {r['confirmation_code']}")
record("SY07", "stale issued_at refused", 3, rc2, d2, e2)
rc2, d2, e2 = execute("system_admin.disable_user", r["args"], requester=A,
                      token=r["confirmation_token"], issued_at="not-a-number",
                      reply=f"yes {r['confirmation_code']}")
record("SY08", "garbage issued_at refused cleanly (no traceback)", 3, rc2, d2, e2,
       extra_ok="Traceback" not in e2)
rc2, d2, e2 = execute("system_admin.disable_user", r["args"], requester=REQ,
                      token=r["confirmation_token"], issued_at=r["issued_at"],
                      reply=f"yes {r['confirmation_code']}")
record("SY09", "token bound to requester A, executed as requester B refused", 3, rc2, d2, e2)

# --- RBAC: narrow (Sales User) requester on admin ops -----------------------
rc, d, e = gated("system_admin.disable_user", {"name": narrow, "reason": "smoke"}, requester=narrow)
record("SY10", "narrow requester cannot disable_user (RBAC)", 3, rc, d, e)

# --- set_user_roles ---------------------------------------------------------
rc, d, e = gated("system_admin.set_user_roles",
                 {"name": narrow, "roles": ["Sales User", "Stock User"], "reason": "smoke"},
                 requester=A)
record("SY11", "set_user_roles add Stock User", 0, rc, d, e)

rc, r, e = render("system_admin.set_user_roles",
                  {"name": narrow, "roles": ["Sales User"], "reason": "smoke"}, requester=A)
# concurrent change between render and execute: someone else edits roles
rc_c, d_c, e_c = gated("system_admin.set_user_roles",
                       {"name": narrow, "roles": ["Sales User", "Stock User", "Purchase User"],
                        "reason": "smoke concurrent"}, requester=A)
rc2, d2, e2 = execute("system_admin.set_user_roles", r["args"], requester=A,
                      token=r["confirmation_token"], issued_at=r["issued_at"],
                      reply=f"yes {r['confirmation_code']}")
record("SY12", "set_user_roles refused when roles changed since render", 3, rc2, d2, e2,
       extra_ok=rc_c == 0)
rc, d, e = gated("system_admin.set_user_roles",
                 {"name": narrow, "roles": ["Sales User"], "reason": "smoke reset"}, requester=A)
record("SY13", "set_user_roles back to Sales User only", 0, rc, d, e)

# --- generic: Role / Custom Field; refusals ---------------------------------
rc, d, e = gated("system_admin.generic", {"doctype": "Role", "action": "create",
                                          "payload": {"role_name": role, "desk_access": 1}}, requester=A)
record("SY14", "generic Role create", 0, rc, d, e)
rc, d, e = gated("system_admin.generic", {"doctype": "Role", "action": "update", "name": role,
                                          "payload": {"desk_access": 0}}, requester=A)
record("SY15", "generic Role update", 0, rc, d, e)
cf_args = {"doctype": "Custom Field", "action": "create",
           "payload": {"dt": "Note", "fieldname": f"smoke_{RUN}", "label": f"Smoke {RUN}",
                       "fieldtype": "Data", "insert_after": "title"}}
rc, d, e = gated("system_admin.generic", cf_args, requester=A)
cf_name = name_of(d)
record("SY16", "generic Custom Field create on Note", 0, rc, d, e, note=str(cf_name))
for sid, args, desc in (
        ("SY17", {"doctype": "Webhook", "action": "update", "name": "x", "payload": {"enabled": 0}},
         "generic Webhook update refused"),
        ("SY18", {"doctype": "Workflow", "action": "create", "payload": {"workflow_name": "x"}},
         "generic Workflow create refused"),
        ("SY19", {"doctype": "User", "action": "create", "payload": {"email": "z@example.com",
                                                                    "first_name": "Z"}},
         "generic User create refused (owned by create_user)"),
        ("SY20", {"doctype": "Server Script", "action": "create", "payload": {}},
         "generic Server Script create refused (not allowlisted)")):
    rc, d, e = execute("system_admin.generic", args, requester=A)
    record(sid, desc, 3, rc, d, e)

# --- webhook ----------------------------------------------------------------
for sid, url in (("SY21", "http://hooks.example.com/x"), ("SY22", "https://10.0.0.5/x"),
                 ("SY23", "https://erp.internal/x")):
    rc, r, e = render("system_admin.create_webhook",
                      {"name": f"SMOKE Bad Hook {RUN}",
                       "payload": {"webhook_doctype": "Note", "webhook_docevent": "after_insert",
                                   "request_url": url}, "reason": "smoke"}, requester=A)
    rc2, d2, e2 = (rc, r, e) if rc != 0 else execute(
        "system_admin.create_webhook", r["args"], requester=A, token=r["confirmation_token"],
        issued_at=r["issued_at"], reply=f"yes {r['confirmation_code']}")
    record(sid, f"create_webhook refuses {url}", 3, rc2, d2, e2)
rc, d, e = gated("system_admin.create_webhook",
                 {"name": f"SMOKE Hook {RUN}", "payload": {"webhook_doctype": "Note", "webhook_docevent": "after_insert",
                              "request_url": "https://hooks.example.com/smoke", "enabled": 0},
                  "reason": "smoke"}, requester=A)
hook = name_of(d)
record("SY24", "create_webhook https public (disabled)", 0, rc, d, e, note=str(hook))

# --- permission manager -----------------------------------------------------
for sid, op, args in (
        ("SY25", "system_admin.permission_add", {"doctype": "Note", "role": role, "reason": "smoke"}),
        ("SY26", "system_admin.permission_update", {"doctype": "Note", "role": role, "ptype": "read",
                                                    "value": 1, "reason": "smoke"}),
        ("SY27", "system_admin.permission_remove", {"doctype": "Note", "role": role, "reason": "smoke"})):
    rc, d, e = gated(op, args, requester=A)
    record(sid, op, 0, rc, d, e, extra_ok=rc == 0 and d.get("_audit_log_status") == "ok")

# --- toggle_workflow: none exist on this instance ---------------------------
rc, d, e = gated("system_admin.toggle_workflow", {"name": f"No Such WF {RUN}", "is_active": 0,
                                                  "reason": "smoke"}, requester=A)
record("SY28", "toggle_workflow on missing workflow fails, nothing changed", 1, rc, d, e,
       note="no Workflow on instance; rc 1 or 3 acceptable")

# --- deletes (W31: these records now carry audit rows) ----------------------
for sid, dt, nm in (("SY29", "Webhook", hook), ("SY30", "Custom Field", cf_name),
                    ("SY31", "Role", role)):
    if not nm:
        record(sid, f"delete {dt}", 0, -1, None, "skipped: create failed")
        continue
    rc, d, e = gated("system_admin.delete", {"doctype": dt, "name": nm, "reason": "smoke cleanup"},
                     requester=A)
    record(sid, f"system_admin.delete {dt} (record has audit rows)", 0, rc, d, e,
           extra_ok=rc == 0 and not exists(dt, nm))

# --- audit trail for this session -------------------------------------------
rows = audit_rows("User", narrow)
record("SY32", "audit rows exist for User writes (W29)", 0, 0, rows, "",
       extra_ok=any(r["action"] == "Create" and r["status"] == "Success" for r in rows))
# disable/delete of the narrow user happens in s90_cleanup (other scripts use it)
