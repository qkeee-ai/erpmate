# adminops

Operator maintenance tools for a running ERPmate container. They are for incidents and one-off fixes, so nothing here runs at boot.

## Where the tools are

- **In the image:** the Dockerfile copies this folder to `/opt/adminops/`. Use these from images built after 2026-10-07.
- **In an older image:** copy the tool in by hand.

  ```sh
  docker cp adminops/lcm-db-recover.py <container>:/tmp/
  ```

## Rules every tool here follows

- **Standard library only.** The image has `python3` but no `sqlite3` CLI and no pip packages.
- **Read-only by default.** A tool reads a snapshot copy, never the live file. A command that writes to live data has a dry-run mode, and you apply it with `--yes`.
- **Backup first.** Every write to live data first copies the old files to `<profile>/backups/`.
- **Refuse when unsafe.** A tool stops when the live file is in use or when a check fails. There is no flag that skips the checks.

## Tools

| Tool | Use it when | Runbook |
|---|---|---|
| `lcm-db-recover.py` | `lcm.db` reports `database disk image is malformed` and `/lcm doctor repair apply` cannot fix it | [lcm-db-recover.md](lcm-db-recover.md) |

## Adding a tool

1. Put one script in this folder with a usage docstring.
2. Add a runbook `<tool>.md` that covers the symptom, the steps, and how to read the output.
3. Add a row to the table above.
