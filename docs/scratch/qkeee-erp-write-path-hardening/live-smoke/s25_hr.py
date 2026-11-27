"""hr_payroll domain on an HRMS instance: Leave, Job Offer, Onboarding,
Separation. Fixtures (Holiday List, Job Applicant, Leave Allocation) go
through unscoped.generic (token+code). Run after s10 (narrow user)."""
import time
from h import (REQ, ADMIN_REQ, RUN, gated, render, execute, record, save, state, exists,
               name_of, query, get)

S = state()
narrow = S["narrow_user"]
created = S.get("created", [])
CO = "DEMO LLP"
TODAY = time.strftime("%Y-%m-%d")
HR, U = "hr_payroll.generic", "unscoped.generic"


def track(dt, nm):
    if nm:
        created.append([dt, nm])
        save(created=created)


def step(sid, desc, op, args, want=0, check=None, gate=True, requester=REQ):
    rc, d, e = (gated if gate else execute)(op, args, requester=requester)
    ok = check(rc, d, e) if (check and rc == want) else True
    record(sid, desc, want, rc, d, e, extra_ok=ok)
    return rc, d, e


def docstatus(dt, nm):
    return get(dt, nm, requester=ADMIN_REQ).get("docstatus")


# --- fixtures ----------------------------------------------------------------
hl = f"SMOKE HL {RUN}"
rc, d, e = step("HX01", "unscoped Holiday List create", U, {"doctype": "Holiday List", "action": "create",
                "payload": {"holiday_list_name": hl, "from_date": "2026-01-01", "to_date": "2026-12-31",
                            "holidays": [{"holiday_date": "2026-10-02", "description": "Gandhi Jayanti"}]}})
track("Holiday List", name_of(d))
rc, d, e = step("HX02", "Employee create (holiday list set)", HR, {"doctype": "Employee", "action": "create",
                "payload": {"first_name": "Smoke", "last_name": f"HR {RUN}", "gender": "Male",
                            "date_of_birth": "1990-01-01", "date_of_joining": "2026-04-01",
                            "status": "Active", "company": CO, "holiday_list": hl}}, gate=False)
emp = name_of(d)
track("Employee", emp)

# HRMS 16 resolves holidays via Holiday List Assignment (not Employee.holiday_list)
rc, d, e = step("HX03", "unscoped Holiday List Assignment create", U,
                {"doctype": "Holiday List Assignment", "action": "create",
                 "payload": {"applicable_for": "Employee", "assigned_to": emp, "holiday_list": hl,
                             "from_date": "2026-01-01"}})
hla = name_of(d)
track("Holiday List Assignment", hla)
if hla:
    step("HX04", "unscoped Holiday List Assignment submit", U,
         {"doctype": "Holiday List Assignment", "action": "submit", "name": hla},
         check=lambda rc, d, e: docstatus("Holiday List Assignment", hla) == 1)

# --- Leave Without Pay (no allocation needed) --------------------------------
la_payload = {"employee": emp, "leave_type": "Leave Without Pay", "company": CO,
              "from_date": "2026-10-12", "to_date": "2026-10-13", "posting_date": TODAY,
              "status": "Open", "leave_approver": ADMIN_REQ}  # HR Settings: approver mandatory
step("HR10", "Leave Application create by Sales-User-only requester refused (RBAC)", HR,
     {"doctype": "Leave Application", "action": "create", "payload": la_payload}, want=3, gate=False,
     requester=narrow)
rc, d, e = step("HR11", "Leave Application draft create (LWP)", HR,
                {"doctype": "Leave Application", "action": "create", "payload": la_payload}, gate=False)
la = name_of(d)
track("Leave Application", la)
step("HR12", "submit while status Open -> HRMS refuses cleanly (exit 1)", HR,
     {"doctype": "Leave Application", "action": "submit", "name": la}, want=1,
     check=lambda rc, d, e: "Approved" in e and docstatus("Leave Application", la) == 0)
step("HR13", "approve (draft update status=Approved)", HR,
     {"doctype": "Leave Application", "action": "update", "name": la,
      "payload": {"status": "Approved"}}, gate=False)
step("HR14", "submit approved Leave Application", HR,
     {"doctype": "Leave Application", "action": "submit", "name": la},
     check=lambda rc, d, e: docstatus("Leave Application", la) == 1)
att = query("Attendance", filters=[["employee", "=", emp], ["docstatus", "=", 1]],
            fields=["name", "attendance_date", "status"], requester=ADMIN_REQ)
record("HR15", "submit auto-created On Leave Attendance for both days", 0, 0, att, "",
       extra_ok=len(att) == 2 and all(a["status"] == "On Leave" for a in att))
step("HR16", "cancel Leave Application", HR, {"doctype": "Leave Application", "action": "cancel", "name": la},
     check=lambda rc, d, e: docstatus("Leave Application", la) == 2)
att = query("Attendance", filters=[["employee", "=", emp], ["docstatus", "=", 1]], fields=["name"],
            requester=ADMIN_REQ)
record("HR17", "cancel also cancelled the Attendance", 0, 0, att, "", extra_ok=len(att) == 0)
rc, d, e = gated(HR, {"doctype": "Leave Application", "action": "delete", "name": la})
record("HR18", "delete cancelled Leave Application (Attendance chain may block: Link error ok)",
       rc if rc in (0, 1) else 0, rc, d, e,
       extra_ok=(rc == 0 and not exists("Leave Application", la)) or (rc == 1 and "Link" in e),
       note="rc=0 deleted / rc=1 LinkExistsError")

# --- allocated leave (Casual Leave) ------------------------------------------
rc, d, e = step("HR20", "unscoped Leave Allocation create (hr allowlist has no Leave Allocation)", U,
                {"doctype": "Leave Allocation", "action": "create",
                 "payload": {"employee": emp, "leave_type": "Casual Leave", "from_date": "2026-04-01",
                             "to_date": "2027-03-31", "new_leaves_allocated": 5}})
alloc = name_of(d)
track("Leave Allocation", alloc)
if alloc:
    step("HR21", "unscoped Leave Allocation submit", U,
         {"doctype": "Leave Allocation", "action": "submit", "name": alloc},
         check=lambda rc, d, e: docstatus("Leave Allocation", alloc) == 1)
    over = dict(la_payload, leave_type="Casual Leave", from_date="2026-11-02", to_date="2026-11-10",
                status="Approved")
    rc, d, e = step("HR22", "9-day Casual Leave over a 5-day balance -> HRMS refuses (exit 1)", HR,
                    {"doctype": "Leave Application", "action": "create", "payload": over}, want=1, gate=False)
    ok_la = dict(over, to_date="2026-11-03")
    rc, d, e = step("HR23", "2-day Casual Leave create (approved draft)", HR,
                    {"doctype": "Leave Application", "action": "create", "payload": ok_la}, gate=False)
    la2 = name_of(d)
    track("Leave Application", la2)
    if la2:
        step("HR24", "submit Casual Leave", HR, {"doctype": "Leave Application", "action": "submit", "name": la2},
             check=lambda rc, d, e: docstatus("Leave Application", la2) == 1)
        step("HR25", "cancel Casual Leave", HR, {"doctype": "Leave Application", "action": "cancel", "name": la2})
    step("HR26", "unscoped Leave Allocation cancel", U,
         {"doctype": "Leave Allocation", "action": "cancel", "name": alloc})

# --- recruitment: Job Applicant -> Job Offer -> Onboarding --------------------
rc, d, e = step("HR30", "unscoped Job Applicant create", U, {"doctype": "Job Applicant", "action": "create",
                "payload": {"applicant_name": f"Smoke Applicant {RUN}",
                            "email_id": f"smoke.applicant.{RUN}@example.com", "status": "Open"}})
ja = name_of(d)
track("Job Applicant", ja)
rc, d, e = step("HR31", "Job Offer draft create", HR, {"doctype": "Job Offer", "action": "create",
                "payload": {"job_applicant": ja, "applicant_name": f"Smoke Applicant {RUN}",
                            "offer_date": TODAY, "designation": "Analyst", "company": CO,
                            "status": "Accepted"}}, gate=False)
jo = name_of(d)
track("Job Offer", jo)
if jo:
    step("HR32", "Job Offer submit", HR, {"doctype": "Job Offer", "action": "submit", "name": jo},
         check=lambda rc, d, e: docstatus("Job Offer", jo) == 1)
    rc, d, e = step("HR33", "Employee Onboarding draft create", HR, {"doctype": "Employee Onboarding",
                    "action": "create", "payload": {"job_applicant": ja, "job_offer": jo, "company": CO,
                                                    "employee_name": f"Smoke Applicant {RUN}",
                                                    "date_of_joining": "2026-11-01",
                                                    "boarding_begins_on": "2026-10-20"}}, gate=False)
    onb = name_of(d)
    track("Employee Onboarding", onb)
    if onb:
        step("HR34a", "Onboarding delete by HR Manager refused: only System Manager may (RBAC)", HR,
             {"doctype": "Employee Onboarding", "action": "delete", "name": onb}, want=3)
        step("HR34", "Employee Onboarding delete by System Manager requester (token)", HR,
             {"doctype": "Employee Onboarding", "action": "delete", "name": onb}, requester=ADMIN_REQ,
             check=lambda rc, d, e: not exists("Employee Onboarding", onb))
    step("HR35", "Job Offer cancel", HR, {"doctype": "Job Offer", "action": "cancel", "name": jo})
    step("HR36", "Job Offer delete", HR, {"doctype": "Job Offer", "action": "delete", "name": jo},
         check=lambda rc, d, e: not exists("Job Offer", jo))

# --- separation ---------------------------------------------------------------
rc, d, e = step("HR40", "Employee Separation draft create", HR, {"doctype": "Employee Separation",
                "action": "create", "payload": {"employee": emp, "company": CO,
                                                "boarding_begins_on": "2026-12-01"}}, gate=False)
sep = name_of(d)
track("Employee Separation", sep)
if sep:
    step("HR41", "Employee Separation delete by System Manager requester (token)", HR,
         {"doctype": "Employee Separation", "action": "delete", "name": sep}, requester=ADMIN_REQ,
         check=lambda rc, d, e: not exists("Employee Separation", sep))
step("HR42", "hr_payroll refuses Salary Slip (not allowlisted)", HR,
     {"doctype": "Salary Slip", "action": "create", "payload": {"employee": emp}}, want=3, gate=False)
