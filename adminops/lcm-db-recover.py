#!/usr/bin/env python3
"""lcm-db-recover - diagnose and rebuild a damaged hermes-lcm SQLite database.

Stdlib only: the Hermes image ships python3 but no sqlite3 CLI, so `.recover`
is not available. Runbook: adminops/lcm-db-recover.md.

Subcommands (read-only unless stated):
  diagnose  integrity scan of a snapshot copy; maps damaged trees to tables
  rebuild   writes a NEW database file from a snapshot (live DB untouched)
  verify    integrity/FTS/row-count checks on a candidate file
  swap      WRITES: replaces the live DB with a verified file (backup first);
            refuses while any process holds the live DB open

Why a logical rebuild and not VACUUM INTO: VACUUM INTO uses SQLite's transfer
path, which copies rows in the order the damaged B-tree yields them, so an
out-of-order rowid survives into the new file. Row-by-row INSERT places every
row correctly. FTS indexes are derived data: they are recreated from their
original DDL and rebuilt from the content table, never copied.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

DATA_ROOT = "/opt/data"
FTS_SHADOW_SUFFIXES = ("_data", "_idx", "_docsize", "_config", "_content")
# Background-scan marker hermes-lcm writes into `metadata`; a stale one keeps
# `/lcm doctor` reporting repair-needed for an index that is now healthy.
LCM_FTS_FLAG_PREFIX = "fts_integrity_failed:"


def q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def resolve_db(args) -> Path:
    if args.db:
        return Path(args.db)
    if args.profile:
        root = Path(args.data_root)
        base = root if args.profile == "default" else root / "profiles" / args.profile
        return base / "lcm.db"
    sys.exit("error: pass --profile NAME or --db PATH")


def sidecars(db: Path) -> list[Path]:
    return [db, Path(f"{db}-wal"), Path(f"{db}-shm")]


def holders(db: Path):
    """PIDs holding db/-wal/-shm open, via /proc. None when /proc is absent."""
    proc = Path("/proc")
    if not proc.is_dir():
        return None
    names = {os.path.realpath(p) for p in sidecars(db)}
    found = []
    for d in proc.iterdir():
        if not d.name.isdigit():
            continue
        try:
            fds = list((d / "fd").iterdir())
        except OSError:
            continue
        for fd in fds:
            try:
                if os.readlink(fd) in names:
                    cmd = (d / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
                    found.append((int(d.name), cmd.strip()[:120]))
                    break
            except OSError:
                continue
    return found


def mount_of(path: Path):
    try:
        lines = Path("/proc/mounts").read_text().splitlines()
    except OSError:
        return None
    real = os.path.realpath(path)
    best = None
    for line in lines:
        dev, mnt, fstype = line.split()[:3]
        if (real == mnt or real.startswith(mnt.rstrip("/") + "/")) and (best is None or len(mnt) > len(best[1])):
            best = (dev, mnt, fstype)
    return best


def snapshot(db: Path, workdir: Path) -> Path:
    """Copy db + -wal into workdir; every read happens on the copy."""
    if not db.is_file():
        sys.exit(f"error: {db} not found")
    snap = workdir / "snapshot.db"
    shutil.copy2(db, snap)
    wal = Path(f"{db}-wal")
    if wal.is_file():
        shutil.copy2(wal, Path(f"{snap}-wal"))
    return snap


def make_workdir() -> Path:
    return Path(tempfile.mkdtemp(prefix="lcm-recover-"))


def integrity(conn: sqlite3.Connection, limit: int = 100) -> list[str]:
    try:
        rows = conn.execute(f"PRAGMA integrity_check({limit})").fetchall()
    except sqlite3.DatabaseError as exc:
        return [f"integrity_check aborted: {exc}"]
    return [line for (r,) in rows for line in str(r).splitlines()]


def schema(conn: sqlite3.Connection):
    return conn.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_master "
        "WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite\\_%' ESCAPE '\\' ORDER BY rowid"
    ).fetchall()


def classify(rows):
    """Split schema into ordinary tables, external-content FTS5 tables and the rest."""
    fts, unsupported = {}, []
    for typ, name, _, sql in rows:
        if typ == "table" and sql.upper().lstrip().startswith("CREATE VIRTUAL TABLE"):
            up = sql.upper()
            if "USING FTS5" in up and re.search(r"CONTENT\s*=\s*(?!''|\"\")\S", up):
                fts[name] = sql
            else:
                unsupported.append(name)
    shadow = {f"{v}{s}" for v in fts for s in FTS_SHADOW_SUFFIXES}
    tables = [(n, s) for t, n, _, s in rows if t == "table" and n not in fts and n not in shadow and n not in unsupported]
    indexes = [(n, s) for t, n, tbl, s in rows if t == "index" and tbl not in shadow]
    views = [(n, s) for t, n, _, s in rows if t == "view"]
    triggers = [(n, s) for t, n, _, s in rows if t == "trigger"]
    return tables, indexes, views, triggers, fts, shadow, unsupported


def scan_rows(conn: sqlite3.Connection, table: str, cols: list[str], use_rowid: bool):
    """Full-scan rows without trusting the B-tree to be fully readable.

    Forward scan first; on a read error, scan backwards too so rows past the
    damaged page are still recovered. Returns (rows_by_key, errors).
    """
    sel = ", ".join(([ "rowid"] if use_rowid else []) + [q(c) for c in cols])
    got, errors = {}, []
    orders = ["", " ORDER BY rowid DESC"] if use_rowid else [""]
    for i, order in enumerate(orders):
        try:
            for n, row in enumerate(conn.execute(f"SELECT {sel} FROM {q(table)} NOT INDEXED{order}")):
                got.setdefault(row[0] if use_rowid else n, row)
            if i == 0:
                break  # forward scan finished cleanly
        except sqlite3.DatabaseError as exc:
            errors.append(f"{'backward' if order else 'forward'} scan stopped after {len(got)} rows: {exc}")
    return got, errors


def table_shape(conn: sqlite3.Connection, table: str, sql: str):
    info = conn.execute(f"PRAGMA table_info({q(table)})").fetchall()
    cols = [r[1] for r in info]
    notnull = {r[1] for r in info if r[3]}
    pk = [r for r in info if r[5]]
    ipk = len(pk) == 1 and pk[0][2].upper() == "INTEGER"
    use_rowid = not ipk and "WITHOUT ROWID" not in sql.upper()
    return cols, notnull, use_rowid


# ---------------------------------------------------------------- diagnose
def cmd_diagnose(args) -> int:
    db = resolve_db(args)
    work = make_workdir()
    print(f"database: {db}")
    for p in sidecars(db):
        if p.exists():
            print(f"  {p.name}: {p.stat().st_size} bytes, mtime {time.ctime(p.stat().st_mtime)}")
    m = mount_of(db)
    if m:
        print(f"filesystem: {m[2]} on {m[0]} (mounted at {m[1]})")
        if m[0].startswith("/dev/mmcblk") or m[2] in ("nfs", "nfs4", "cifs", "smb3") or m[2].startswith("fuse"):
            print("  WARNING: SD/eMMC or network/FUSE storage - a known cause of SQLite page corruption")
    h = holders(db)
    print("open by: " + ("(no /proc - cannot tell)" if h is None else ", ".join(f"{p} {c}" for p, c in h) or "nobody"))

    snap = snapshot(db, work)
    c = sqlite3.connect(snap, isolation_level=None)
    for pragma in ("journal_mode", "user_version", "page_size", "page_count", "freelist_count"):
        try:
            print(f"{pragma}: {c.execute(f'PRAGMA {pragma}').fetchone()[0]}")
        except sqlite3.DatabaseError as exc:
            print(f"{pragma}: ERROR {exc}")

    lines = integrity(c, args.max_errors)
    if lines and lines[0].startswith("integrity_check aborted") and "vtable" in lines[0]:
        # A broken FTS5 vtable aborts the whole check. Hide the vtables in a
        # scratch copy so the base tables and FTS shadow B-trees still get checked.
        bare = work / "snapshot-novtab.db"
        shutil.copy2(snap, bare)
        if Path(f"{snap}-wal").exists():
            shutil.copy2(f"{snap}-wal", f"{bare}-wal")
        b = sqlite3.connect(bare, isolation_level=None)
        b.execute("PRAGMA writable_schema=ON")
        b.execute("DELETE FROM sqlite_master WHERE type='table' AND sql LIKE 'CREATE VIRTUAL TABLE%'")
        b.execute("PRAGMA writable_schema=OFF")
        b.close()
        b = sqlite3.connect(bare, isolation_level=None)
        lines += ["(re-run with virtual tables hidden:)"] + integrity(b, args.max_errors)
        b.close()
    healthy = lines == ["ok"]
    print("--- integrity_check")
    for line in lines[:25]:
        print(f"  {line}")
    if len(lines) > 25:
        print(f"  ... {len(lines) - 25} more lines")

    rows = schema(c)
    tables, _, _, _, fts, shadow, unsupported = classify(rows)
    trees = sorted({int(t) for t in re.findall(r"Tree (\d+)", "\n".join(lines))})
    damaged_names = []
    if trees:
        print("--- damaged trees")
        marks = ",".join("?" * len(trees))
        for root, typ, name in c.execute(f"SELECT rootpage, type, name FROM sqlite_master WHERE rootpage IN ({marks})", trees):
            kind = "derived FTS index (rebuildable)" if name in shadow else "BASE DATA"
            damaged_names.append((name, name in shadow))
            print(f"  tree {root}: {typ} {name} - {kind}")
    if "row" in "\n".join(lines) and "missing from index" in "\n".join(lines):
        print("  (index entries missing: indexes are rebuilt by `rebuild`)")

    print("--- full-scan readability (NOT NULL violations use typeof(), the optimizer folds IS NULL)")
    scan_ok = True
    for name, sql in tables:
        try:
            cols, notnull, use_rowid = table_shape(c, name, sql)
        except sqlite3.DatabaseError as exc:
            print(f"  {name}: schema read failed: {exc}")
            scan_ok = False
            continue
        got, errors = scan_rows(c, name, cols, use_rowid)
        bad = 0
        if notnull:
            cond = " OR ".join(f"typeof({q(col)})='null'" for col in notnull)
            try:
                bad = c.execute(f"SELECT count(*) FROM {q(name)} NOT INDEXED WHERE {cond}").fetchone()[0]
            except sqlite3.DatabaseError:
                bad = -1
        flag = "OK" if not errors and bad == 0 else "PROBLEM"
        scan_ok &= flag == "OK"
        extra = f", {bad} NOT NULL violations" if bad else ""
        print(f"  {flag:7} {name}: {len(got)} rows{extra}")
        for e in errors:
            print(f"          {e}")
    for name in fts:
        try:
            c.execute(f"INSERT INTO {q(name)}({q(name)}) VALUES('integrity-check')")
            print(f"  OK      {name} (fts5 integrity-check)")
        except sqlite3.DatabaseError as exc:
            print(f"  PROBLEM {name} (fts5 integrity-check): {exc}")
    for name in unsupported:
        print(f"  NOTE    {name}: virtual table type not handled by `rebuild`")
    c.close()

    if args.scan_all:
        print(f"--- quick_check of every *.db under {args.data_root}")
        for p in sorted(Path(args.data_root).rglob("*.db")):
            try:
                r = sqlite3.connect(f"file:{p}?mode=ro", uri=True).execute("PRAGMA quick_check(3)").fetchall()
                print(f"  {'ok' if r == [('ok',)] else 'DAMAGED'}  {p}")
            except sqlite3.DatabaseError as exc:
                print(f"  ERROR    {p}: {exc}")

    print("--- verdict")
    if healthy and scan_ok:
        print("  healthy - nothing to do")
        rc = 0
    elif damaged_names and all(derived for _, derived in damaged_names) and scan_ok:
        print("  only FTS index damaged - try `/lcm doctor repair apply` first; if it fails, run `rebuild`")
        rc = 1
    else:
        print("  base data damaged - stop the gateway, then run `rebuild`")
        rc = 1
    print(f"(snapshot kept in {work})")
    return rc


# ----------------------------------------------------------------- rebuild
def cmd_rebuild(args) -> int:
    db = resolve_db(args)
    out = Path(args.out or f"/tmp/lcm-rebuilt-{time.strftime('%Y%m%d_%H%M%S')}.db")
    if any(p.exists() for p in sidecars(out)):
        if not args.force:
            sys.exit(f"error: {out} exists (use --force to overwrite)")
        for p in sidecars(out):
            p.unlink(missing_ok=True)
    h = holders(db)
    if h:
        print("WARNING: live DB is open by: " + ", ".join(f"{p} {c}" for p, c in h))
        print("         a snapshot taken now can miss in-flight writes; stop the gateway first for a final rebuild")

    work = make_workdir()
    snap = snapshot(db, work)
    s = sqlite3.connect(snap, isolation_level=None)
    tables, indexes, views, triggers, fts, _, unsupported = classify(schema(s))
    if unsupported:
        sys.exit(f"error: unsupported virtual tables {unsupported}; this tool rebuilds external-content FTS5 only")

    o = sqlite3.connect(out, isolation_level=None)
    o.execute("BEGIN")
    for _, sql in tables:
        o.execute(sql)
    # Indexes before data: unique constraints then drop duplicate rows at insert.
    for _, sql in indexes:
        o.execute(sql)

    print(f"source: {db} (snapshot {snap})")
    print(f"output: {out}")
    print("--- tables (kept / read; dropped rows violate NOT NULL or UNIQUE)")
    for name, sql in tables:
        cols, notnull, use_rowid = table_shape(s, name, sql)
        got, errors = scan_rows(s, name, cols, use_rowid)
        ins_cols = (["rowid"] if use_rowid else []) + cols
        stmt = f"INSERT OR IGNORE INTO {q(name)} ({', '.join(q(x) for x in ins_cols)}) VALUES ({', '.join('?' * len(ins_cols))})"
        nn_idx = [ins_cols.index(c) for c in notnull]
        kept = dropped_null = dropped_conflict = 0
        # Newest rowid first, so a duplicate key keeps its most recent row.
        for key in sorted(got, reverse=True):
            row = got[key]
            if any(row[i] is None for i in nn_idx):
                dropped_null += 1
                continue
            try:
                cur = o.execute(stmt, row)
            except sqlite3.IntegrityError:
                dropped_conflict += 1
                continue
            if cur.rowcount:
                kept += 1
            else:
                dropped_conflict += 1
        note = []
        if dropped_null:
            note.append(f"{dropped_null} NOT NULL")
        if dropped_conflict:
            note.append(f"{dropped_conflict} conflict")
        print(f"  {name}: {kept} / {len(got)}" + (f"  dropped: {', '.join(note)}" if note else ""))
        for e in errors:
            print(f"    {e}")

    for name, sql in fts.items():
        o.execute(sql)
        o.execute(f"INSERT INTO {q(name)}({q(name)}) VALUES('rebuild')")
        print(f"  {name}: recreated + rebuilt")
    for _, sql in views + triggers:
        o.execute(sql)
    print(f"  triggers restored: {len(triggers)}, views: {len(views)}")

    try:
        for name, seq in s.execute("SELECT name, seq FROM sqlite_sequence").fetchall():
            if o.execute("UPDATE sqlite_sequence SET seq = max(seq, ?) WHERE name = ?", (seq, name)).rowcount == 0:
                o.execute("INSERT INTO sqlite_sequence(name, seq) VALUES (?, ?)", (name, seq))
    except sqlite3.OperationalError:
        pass  # no AUTOINCREMENT tables

    if not args.keep_fts_flags and "metadata" in {n for n, _ in tables}:
        if "key" in table_shape(o, "metadata", "")[0]:
            n = o.execute("DELETE FROM metadata WHERE key LIKE ?", (LCM_FTS_FLAG_PREFIX + "%",)).rowcount
            if n:
                print(f"  cleared {n} stale '{LCM_FTS_FLAG_PREFIX}*' marker(s) from metadata")
    o.execute("COMMIT")

    for pragma in ("user_version", "application_id"):
        o.execute(f"PRAGMA {pragma}={int(s.execute(f'PRAGMA {pragma}').fetchone()[0])}")
    o.execute("PRAGMA journal_mode=WAL")
    o.close()
    s.close()

    print("--- verify")
    ok = verify(out, against=snap)
    print(f"(snapshot kept in {work})")
    if ok:
        print(f"next: stop the gateway, then `swap --profile ... --new {out} --yes`")
    return 0 if ok else 1


# ------------------------------------------------------------------ verify
def verify(path: Path, against: Path | None = None) -> bool:
    if not path.is_file():
        print(f"  FAIL {path} not found")
        return False
    c = sqlite3.connect(path, isolation_level=None)
    ok = True
    lines = integrity(c)
    if lines == ["ok"]:
        print("  ok   integrity_check")
    else:
        ok = False
        print("  FAIL integrity_check:")
        for line in lines[:20]:
            print(f"         {line}")
    tables, _, _, triggers, fts, _, _ = classify(schema(c))
    for name, sql in fts.items():
        try:
            c.execute(f"INSERT INTO {q(name)}({q(name)}) VALUES('integrity-check')")
            m = re.search(r"CONTENT\s*=\s*['\"]?(\w+)", sql, re.IGNORECASE)
            content = m.group(1) if m else None
            n_fts = c.execute(f"SELECT count(*) FROM {q(name + '_docsize')}").fetchone()[0]
            n_src = c.execute(f"SELECT count(*) FROM {q(content)}").fetchone()[0] if content else "?"
            same = n_fts == n_src
            ok &= same
            print(f"  {'ok  ' if same else 'FAIL'} {name}: {n_fts} indexed / {n_src} content rows")
        except sqlite3.DatabaseError as exc:
            ok = False
            print(f"  FAIL {name}: {exc}")
    print(f"  info triggers: {', '.join(n for n, _ in triggers) or 'none'}")

    ref = sqlite3.connect(against, isolation_level=None) if against else None
    for name, _ in tables:
        n = c.execute(f"SELECT count(*) FROM {q(name)}").fetchone()[0]
        line = f"  info {name}: {n} rows"
        if ref is not None:
            try:
                r = sum(1 for _ in ref.execute(f"SELECT 1 FROM {q(name)} NOT INDEXED"))
                if r != n:
                    line += f"  (source scan: {r}, delta {n - r})"
            except sqlite3.DatabaseError as exc:
                line += f"  (source unreadable: {exc})"
        print(line)
    cols = {r[1] for r in c.execute("PRAGMA table_info(messages)")}
    if "store_id" in cols:
        ids = {r[0] for r in c.execute("SELECT store_id FROM messages")}
        if ids:
            missing = sorted(set(range(min(ids), max(ids) + 1)) - ids)
            shown = missing[:50]
            print(f"  info messages store_id gaps: {len(missing)}" + (f" {shown}{' ...' if len(missing) > 50 else ''}" if missing else ""))
    c.close()
    if ref is not None:
        ref.close()
    print("  RESULT: " + ("PASS" if ok else "FAIL"))
    return ok


def cmd_verify(args) -> int:
    return 0 if verify(Path(args.path), Path(args.against) if args.against else None) else 1


# -------------------------------------------------------------------- swap
def cmd_swap(args) -> int:
    live = resolve_db(args)
    new = Path(args.new)
    if not live.is_file():
        sys.exit(f"error: live DB {live} not found")
    h = holders(live)
    if h is None:
        print("WARNING: no /proc - cannot check for open handles; make sure the gateway is stopped")
    elif h:
        print("REFUSED: live DB is open by:")
        for pid, cmd in h:
            print(f"  {pid} {cmd}")
        print("stop the gateway (and any CLI session on this profile) first")
        return 2
    if Path(f"{new}-wal").exists():
        print(f"REFUSED: {new}-wal exists - the candidate is still open somewhere")
        return 2
    newest_live = max(p.stat().st_mtime for p in sidecars(live) if p.exists())
    if newest_live > new.stat().st_mtime and not args.allow_stale:
        print("REFUSED: the live DB changed after the candidate was built; re-run `rebuild`")
        print("         (or pass --allow-stale to discard those writes)")
        return 2
    print("--- verify candidate")
    if not verify(new):
        print("REFUSED: candidate failed verification")
        return 2
    backup_dir = live.parent / "backups" / "lcm" / f"pre-swap-{time.strftime('%Y%m%d_%H%M%S')}"
    print(f"plan: back up {live.name}* -> {backup_dir}/, then replace {live} with {new}")
    if not args.yes:
        print("dry run - re-run with --yes to apply")
        return 2

    backup_dir.mkdir(parents=True, exist_ok=True)
    for p in sidecars(live):
        if p.exists():
            shutil.copy2(p, backup_dir / p.name)
    st = live.stat()
    tmp = live.with_name(live.name + ".swap-tmp")
    shutil.copy2(new, tmp)
    try:
        os.chown(tmp, st.st_uid, st.st_gid)
    except (PermissionError, AttributeError):
        print("WARNING: could not chown - run as root or fix ownership by hand")
    os.chmod(tmp, st.st_mode & 0o777)
    for p in sidecars(live)[1:]:
        p.unlink(missing_ok=True)
    os.replace(tmp, live)
    print(f"swapped. backup: {backup_dir}")
    print("next: start the gateway, then run `/lcm doctor repair` (expect status: ok)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="lcm-db-recover", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def target(p):
        p.add_argument("--profile", help="profile name ('default' = data root); resolves <data-root>/profiles/<name>/lcm.db")
        p.add_argument("--db", help="explicit database path (overrides --profile)")
        p.add_argument("--data-root", default=os.environ.get("LCM_RECOVER_DATA_ROOT", DATA_ROOT))

    p = sub.add_parser("diagnose", help="read-only integrity scan of a snapshot")
    target(p)
    p.add_argument("--max-errors", type=int, default=100)
    p.add_argument("--scan-all", action="store_true", help="also quick_check every *.db under --data-root")
    p.set_defaults(fn=cmd_diagnose)

    p = sub.add_parser("rebuild", help="write a clean copy to --out (live DB untouched)")
    target(p)
    p.add_argument("--out", help="output file (default /tmp/lcm-rebuilt-<ts>.db)")
    p.add_argument("--force", action="store_true", help="overwrite --out if it exists")
    p.add_argument("--keep-fts-flags", action="store_true", help=f"keep '{LCM_FTS_FLAG_PREFIX}*' metadata markers")
    p.set_defaults(fn=cmd_rebuild)

    p = sub.add_parser("verify", help="check a candidate file")
    p.add_argument("path")
    p.add_argument("--against", help="source DB to compare row counts with")
    p.set_defaults(fn=cmd_verify)

    p = sub.add_parser("swap", help="WRITES: replace the live DB with a verified file")
    target(p)
    p.add_argument("--new", required=True, help="candidate built by `rebuild`")
    p.add_argument("--yes", action="store_true", help="apply (without it: dry run)")
    p.add_argument("--allow-stale", action="store_true", help="swap even if the live DB changed after the rebuild")
    p.set_defaults(fn=cmd_swap)

    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
