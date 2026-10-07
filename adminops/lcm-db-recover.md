# Recover a damaged hermes-lcm database

Runbook for `lcm-db-recover.py`. Use it when the [hermes-lcm](https://github.com/stephenschoettler/hermes-lcm) store (`lcm.db`) has page-level corruption that the plugin's own repair cannot fix.

## Symptoms

All three of these appear:

1. Every agent turn that compacts context fails. The gateway log shows this:
   ```
   File ".../plugins/hermes-lcm/store.py", line 543, in _append_protected_batch
   sqlite3.DatabaseError: database disk image is malformed
   ```
   The turn fails again on every later message in that thread.
2. At startup, the background check logs `Background FTS integrity-check found corruption in 'messages_fts'`.
3. `/lcm doctor repair apply` returns `error: FTS repair failed: database disk image is malformed`.

If only 1 and 2 appear, run `/lcm doctor repair apply` first. It fixes FTS index drift. This runbook is for the case where it fails.

### Why one damaged index breaks every turn

hermes-lcm keeps the FTS5 index `messages_fts` in step with the `messages` table through a trigger:

```sql
CREATE TRIGGER msg_fts_insert AFTER INSERT ON messages BEGIN
  INSERT INTO messages_fts(rowid, content) VALUES (new.store_id, new.content);
END;
```

Each compaction inserts into `messages`, so each insert writes to the damaged index and fails.

The plugin's repair drops the index and rebuilds it from `messages`. That cannot work when the damage is to the B-tree pages, or when `messages` itself is damaged.

## What the tool does

| Subcommand | Writes to live data? | Action |
|---|---|---|
| `diagnose` | no | Runs an integrity scan on a snapshot copy, maps the damaged B-trees to table names, and checks that each table can be read in full. It also reports the filesystem type. |
| `rebuild` | no | Writes a **new** file from the snapshot: it copies every table row by row and recreates the FTS5 indexes from their original DDL. Then it runs `verify`. |
| `verify` | no | Checks a candidate file: integrity, FTS row counts, row counts compared with the source, and gaps in `store_id`. |
| `swap` | **yes** | Replaces the live DB with the verified candidate after a backup. It refuses while any process holds the DB open. Without `--yes` it is a dry run. |

### Why rebuild row by row

`rebuild` reads the damaged tables and writes their rows into a new file. Each step below avoids a failure that happened during the manual recovery on 2026-10-07:

- **No `VACUUM INTO`.** Its transfer path copies rows in the order the damaged B-tree returns them. A `Rowid N out of order` error therefore stays in the new file. A row-by-row `INSERT` puts each row in the correct place.
- **The FTS indexes are rebuilt, not copied.** They are derived data, so the tool recreates them from their original `CREATE VIRTUAL TABLE` DDL and runs `INSERT INTO fts(fts) VALUES('rebuild')`. It never reads the damaged shadow tables.
- **Forward and backward scans.** If a read error stops the forward scan, the tool also scans backwards, which recovers the rows that come after the bad page.
- **Garbage rows are dropped.** When pages are cross-linked, rows from one table can appear inside another. On 2026-10-07, `metadata` rows appeared in `messages` with `NULL` in `role` and `timestamp`. The tool drops rows that break a `NOT NULL` or `UNIQUE` constraint. When a key is duplicated, it keeps the row with the newest rowid.
- **Schema settings are kept.** The tool keeps `user_version`, `application_id`, `sqlite_sequence` (AUTOINCREMENT counters), the indexes, the views and the triggers.
- **The stale flag is cleared.** It deletes the `fts_integrity_failed:*` marker from `metadata`. Otherwise `/lcm doctor` keeps reporting repair-needed for an index that is now healthy. Use `--keep-fts-flags` to keep the marker.

## Procedure

Run every step inside the container as root, so that `swap` can restore the file owner:

```sh
docker exec -it <container> bash
T=/opt/adminops/lcm-db-recover.py      # or /tmp/lcm-db-recover.py after docker cp
P=dev-erp                              # profile name; "default" = /opt/data/lcm.db
```

`--profile P` resolves to `/opt/data/profiles/P/lcm.db`. If you set `LCM_DATABASE_PATH`, pass `--db <path>` instead.

### 1. Diagnose

Diagnose is read-only, so you can run it while the gateway is up:

```sh
python3 $T diagnose --profile $P --scan-all
```

Read the output like this:

| Output | Meaning |
|---|---|
| `verdict: healthy` | Nothing to do. |
| `damaged trees: ... derived FTS index (rebuildable)` only, and all tables `OK` | Try `/lcm doctor repair apply` first. If it fails, continue with step 2. |
| `damaged trees: ... BASE DATA`, or `PROBLEM <table>` | Continue with step 2. |
| `N NOT NULL violations` | Rows written onto the wrong table's page. `rebuild` drops them. |
| `2nd reference to page N` | Cross-linked pages: two tables claim the same page. The usual cause is lost writes from storage or a power failure. See [Root cause](#root-cause). |
| `--scan-all` lists another DB as `DAMAGED` | The damage is not limited to LCM. Suspect the storage first. |
| `filesystem: ... WARNING` | The data directory is on SD/eMMC, NFS, CIFS or FUSE storage. |

### 2. Stop every writer

hermes-lcm supports only **one writer per profile**. A snapshot taken while the gateway writes can miss rows. A swap done while the gateway is running corrupts the file again.

```sh
hermes -p $P gateway stop
ps aux | grep -E "hermes.*$P" | grep -v grep     # expect nothing, CLI sessions included
```

### 3. Rebuild

```sh
python3 $T rebuild --profile $P --out /tmp/lcm-clean.db
```

The output lists `kept / read` for each table, with the reason for each dropped row, and ends with the `verify` report. Before you continue, check all of the following:

- `RESULT: PASS`.
- `messages`: `kept` is equal to `read`, or lower only by the garbage rows that `diagnose` reported.
- `messages_fts: N indexed / N content rows`, with the same N on both sides.
- `info triggers` lists `msg_fts_insert`, `msg_fts_delete` and `msg_fts_update`, and every other trigger the DB had.
- `messages store_id gaps`. Each gap is a message that is gone. Gaps from earlier normal deletes are expected. A cluster of new gaps near the damaged rows is real loss.

### 4. Swap

```sh
python3 $T swap --profile $P --new /tmp/lcm-clean.db          # dry run: checks + plan
python3 $T swap --profile $P --new /tmp/lcm-clean.db --yes    # apply
```

`swap` refuses in these cases:

- A process holds the live DB, its `-wal` file or its `-shm` file open.
- The candidate fails `verify`.
- The live DB changed after the candidate was built. Run `rebuild` again, or pass `--allow-stale` to discard those writes.

Before it replaces the file, it copies the old `lcm.db`, `lcm.db-wal` and `lcm.db-shm` to `<profile>/backups/lcm/pre-swap-<timestamp>/`. The new file gets the old file's owner and permissions.

### 5. Start and confirm

```sh
hermes -p $P gateway start
```

1. Run `/lcm doctor repair`. Expect `status: ok`, `messages_fts_integrity_status: pass`, and equal values for `content_rows` and `fts_rows`.
2. Send a message in the thread that was failing. It should compact without the error.
3. Remove the temporary files: `rm -rf /tmp/lcm-clean.db /tmp/lcm-recover-*`. Keep the `backups/lcm/pre-swap-*` folder until you are sure nothing is missing.

### If rebuild fails, or loses too much

Reset the LCM store. Stop the gateway, move `lcm.db*` into `backups/lcm/`, and start the gateway. LCM creates a new empty database.

You lose LCM's summary DAG and its searchable history for that profile. Hermes' own transcripts in `state.db` are not affected. This is acceptable for a dev profile.

## Root cause

SQLite does not cross-link pages by itself. When the damage includes `2nd reference to page` or `Child page depth differs`, writes that SQLite had already synced were lost or reordered. In the order to check:

1. **Storage.** Run `df -T /opt/data` in the container. `/dev/mmcblk*` means SD or eMMC flash, which loses writes on power loss and as the card wears. Network filesystems (NFS, CIFS) and FUSE filesystems break SQLite locking.
2. **Unclean shutdowns.** On the **host**, run:
   ```sh
   last -x | head
   dmesg | grep -iE "mmc|ext4|I/O error"
   ```
3. **Concurrent writers.** Look for a second container on the same `HERMES_DATA_DIR`, or a host-side copy or rsync of `lcm.db` that leaves out its `-wal` file.
4. **OOM kills during a write.** Run `docker inspect <container> --format '{{.State.OOMKilled}}'`.

The durable fix for storage problems is to move `HERMES_DATA_DIR` to an SSD and give the board a stable power supply. Until then, `diagnose --scan-all` is a quick health check for every SQLite file in the data directory.

## SQLite pitfalls on a damaged file

These caught us during the manual recovery, and the tool already handles them. They matter if you write your own queries against a damaged DB:

- **`IS NULL` on a `NOT NULL` column is always false.** The query planner folds `col IS NULL` to false and `col IS NOT NULL` to true, so a filter for garbage rows matches nothing. Use `typeof(col) = 'null'`.
- **Rowid searches skip rows in an out-of-order B-tree.** `WHERE store_id >= 745` and `WHERE rowid = 45` use a binary search and miss rows that are out of place. To force a full scan, write `store_id+0` and add `NOT INDEXED`.
- **`PRAGMA journal_mode=WAL` fails inside a transaction.** Python's `sqlite3` module opens a transaction before any `INSERT`, including an FTS `'integrity-check'` insert. Connect with `isolation_level=None`.
- **A broken FTS5 table aborts `integrity_check` entirely** with `vtable constructor failed`. `diagnose` then checks a copy with the virtual tables hidden, so the base tables are still checked.

## History

- **2026-10-07, dev-erp:** `lcm.db` on `/dev/mmcblk0p2` (ext4). `messages_fts_data`, `messages` and `metadata` were cross-linked. Recovered by hand. The result was 754 of 757 `messages` rows: the 3 rows dropped were `metadata` rows misplaced inside `messages`. 3 `metadata` rows were lost: telemetry and 2 empty `{}` values. All other DBs passed `quick_check`. This tool automates those manual steps.
