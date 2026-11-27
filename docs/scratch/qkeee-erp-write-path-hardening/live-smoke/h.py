"""Live smoke harness: drives the real CLIs (client.py, confirm_token.py,
execute_write.py, discover.py) as subprocesses against DEMO_ERP.

Usage (order matters — later suites read state.json from earlier ones):

    export HERMES_HOME=<dir holding qkeee-erp.env with QKEEE_ERP_DEMO_ERP_*
                         bot + ADMIN keys>      # never commit that file
    export SMOKE_RUN=$(date +%H%M)              # suffix for every test record
    python s00_state.py                          # read-only instance snapshot
    python s10_sysadmin.py && python s20_business.py && python s30_assets.py         && python s40_reads.py
    python s90_cleanup.py state.json             # gated deletes; lists leftovers

Exit-code expectations follow execute_write.py: 0 ok, 1 ERPNext error,
2 usage/malformed args, 3 gate refusal (nothing sent), 4 unknown outcome.
The harness supplies the user's confirmation reply ("yes <code>") itself —
only on an operator's explicit instruction for a test run. Writes post to
the GL (JE/Stock Entry submit, depreciation, scrap) — run only on a
disposable/demo instance. Each scenario appends to results.jsonl."""
import json
import os
import subprocess
import sys
import time

SCRIPTS = r"D:\workspace\projects\qkeee\hermes-agents-repos\erpnext-hermes\skills\qkeee-erp\qkeee-erp-associate\scripts"
HERE = os.path.dirname(os.path.abspath(__file__))
ENV = dict(os.environ, PYTHONIOENCODING="utf-8",
           HERMES_HOME=os.environ.get("HERMES_HOME") or os.path.join(os.path.dirname(HERE), "hermes"))
TAG = "demo_erp"
REQ = "nikhil.sharma@qkeee.in"        # narrow-role requester
ADMIN_REQ = "ravi.khandelwal@qkeee.in"  # System Manager requester
RUN = os.environ.get("SMOKE_RUN", time.strftime("%H%M%S"))
SESSION = f"smoke-{time.strftime('%Y%m%d')}-{RUN}"
CTX = ["--session-id", SESSION, "--channel", "CLI",
       "--channel-metadata", json.dumps({"harness": "live-smoke", "run": RUN}),
       "--latest-prompt", "live smoke test run (write-path hardening)",
       "--prompt-summary", "write-path hardening live smoke"]
RESULTS = os.path.join(HERE, "results.jsonl")


def sh(script, *args, timeout=240):
    for attempt in (1, 2):
        p = subprocess.run([sys.executable, os.path.join(SCRIPTS, script), *args], cwd=SCRIPTS,
                           env=ENV, capture_output=True, text=True, encoding="utf-8", timeout=timeout)
        # A DNS failure happens before any request is sent, so one retry is safe.
        if "getaddrinfo failed" not in p.stderr:
            break
        time.sleep(3)
    out = p.stdout.strip()
    try:
        data = json.loads(out) if out else None
    except json.JSONDecodeError:
        data = out
    return p.returncode, data, p.stderr.strip()


def read(sub, *args, requester=REQ):
    return sh("core/client.py", "--tag", TAG, "--requested-by", requester, sub, *args)


def query(doctype, filters=None, fields=None, limit=50, requester=REQ):
    a = [doctype, "--limit", str(limit)]
    if filters is not None:
        a += ["--filters", json.dumps(filters)]
    if fields is not None:
        a += ["--fields", json.dumps(fields)]
    rc, data, err = read("query", *a, requester=requester)
    if rc != 0:
        raise RuntimeError(f"query {doctype} rc={rc}: {err[-800:]}")
    return data.get("data", data) if isinstance(data, dict) else data


def get(doctype, name, requester=REQ):
    rc, data, err = read("get", doctype, name, requester=requester)
    if rc != 0:
        raise RuntimeError(f"get {doctype} {name} rc={rc}: {err[-800:]}")
    return data.get("data", data) if isinstance(data, dict) else data


def render(op, args, requester=REQ):
    return sh("core/confirm_token.py", "render", "--op", op, "--args", json.dumps(args),
              "--tag", TAG, "--requested-by", requester, *CTX)


def execute(op, args, requester=REQ, mode="read-write", token=None, issued_at=None,
            reply=None, extra=()):
    a = ["--op", op, "--args", json.dumps(args), "--tag", TAG, "--mode", mode,
         "--requested-by", requester, *CTX, *extra]
    if token:
        a += ["--confirmation-token", token]
    if issued_at is not None:
        a += ["--issued-at", str(issued_at)]
    if reply:
        a += ["--user-confirmation-text", reply]
    return sh("execute_write.py", *a)


def gated(op, args, requester=REQ, mutate_args=None, reply_code=True):
    """Render -> (optionally tamper) -> execute. The user's reply is supplied
    by the harness on the operator's explicit instruction (test run)."""
    rc, r, err = render(op, args, requester)
    if rc != 0:
        return rc, r, "RENDER: " + err
    exec_args = r["args"] if mutate_args is None else mutate_args(json.loads(json.dumps(r["args"])))
    reply = f"yes {r.get('confirmation_code')}" if reply_code else "yes go ahead"
    return execute(op, exec_args, requester, token=r.get("confirmation_token"),
                   issued_at=r.get("issued_at"), reply=reply if r.get("confirmation_code") else None)


def record(sid, desc, expect_rc, rc, data, err, extra_ok=True, note=""):
    ok = (rc == expect_rc) and bool(extra_ok)
    row = {"id": sid, "desc": desc, "expect_rc": expect_rc, "rc": rc, "pass": ok, "note": note,
           "err": (err or "")[-600:],
           "out": (json.dumps(data, default=str)[:600] if data is not None else None)}
    with open(RESULTS, "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
    print(f"[{'PASS' if ok else 'FAIL'}] {sid} {desc} (rc={rc}, want {expect_rc}) {note}")
    if not ok:
        print("   err:", (err or "")[-600:])
        print("   out:", row["out"])
    return ok


STATE = os.path.join(HERE, "state.json")


def state():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def save(**kw):
    s = state()
    s.update(kw)
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(s, f, indent=1)


def audit_rows(ref_doctype=None, ref_name=None, limit=50):
    flt = [["session", "=", SESSION]]
    if ref_doctype:
        flt.append(["reference_doctype", "=", ref_doctype])
    if ref_name:
        flt.append(["reference_name", "=", ref_name])
    return query("Qkeee Bot Audit Log", filters=flt,
                 fields=["name", "action", "reference_doctype", "reference_name", "status"],
                 limit=limit, requester=ADMIN_REQ)


def exists(doctype, name, requester=ADMIN_REQ):
    rc, data, err = read("get", doctype, name, requester=requester)
    if rc == 0:
        return True
    if "404" in err or "DoesNotExist" in err or "not found" in err:
        return False
    raise RuntimeError(f"exists {doctype} {name}: rc={rc} {err[-400:]}")


def name_of(data):
    if isinstance(data, dict):
        d = data.get("data") or data.get("message") or {}
        if isinstance(d, dict):
            return d.get("name")
    return None
