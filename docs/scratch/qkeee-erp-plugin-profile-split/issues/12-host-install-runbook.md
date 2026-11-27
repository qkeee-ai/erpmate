# Linux/macOS host install with Isolation: runbook + verification

Status: ready-for-human (docs + code done 2026-10-10; VM verification open)
Type: task
Blocked by: 07, 10

## Change

- README section "Host install (Linux/macOS)" following the spec's host flow: root creates OS user `hermes-terminal` with no read access to `HERMES_HOME`; sshd key-only, no forwarding (template: `docker/terminal/sshd_config`); `profile install`; `plugins enable qkeee-erp`; `setup --apply-profile-fixes`; root adds the printed public key; credentials; `setup`; `setup status`.
- File permissions: `HERMES_HOME` mode 700 for the gateway user (the probe proves it).
- State plainly: without these steps a host install has no ERPNext access (ADR 0006); Windows hosts are not supported yet.

## Verification (clean Linux VM, then macOS)

- `setup status`: Isolation proven.
- Point `ssh_user` at the gateway's own user → `setup` reports not Isolated; no `erp_*` tools.
- A skill script runs in the terminal; a generated file reaches chat.

## Outcome (2026-10-10)

- README section "Host install (Linux/macOS)": steps 0-5 (clone, root user + sshd, profile install + `chmod 700 ~/.hermes`, setup with the flag, root authorizes the key with `restrict,from="127.0.0.1,::1"`, credentials + setup + status), update flow, self-check, troubleshooting table, limits (ADR 0006, Windows not supported, `cache/` gap).
- sshd drop-in `host/hermes-terminal.sshd.conf` (installed as `/etc/ssh/sshd_config.d/10-hermes-terminal.conf`): `Match User hermes-terminal,hermes-terminal-*`, key login only, no forwarding. Not in `adminops/` (that folder is for container tools).
- Code gap found and fixed: the shipped `config.yaml` carries the Docker sidecar terminal (`ssh_host: terminal`, `ssh_user: hermes`, key `/opt/data/...`, `cwd: /opt/cwd`), so on a host P2 counted as done and `--apply-profile-fixes` changed nothing. P2 now reports sidecar values on a host install as pending; the flag replaces them with host values (`localhost`, `hermes-terminal`, `<profile>/.terminal-ssh/id_ed25519`, `cwd: ~`).
- `tests/test_host_runbook.py`: every `qkeee-erp` command in the section parses with the real CLI parser; the drop-in keeps the sidecar's forwarding rules; the section names the host values setup writes.
- Partial: S3 prints the public key path, not the key itself (spec step 3 says "prints the public key"); the runbook reads it with `cat`.
- **Open (human):** the Verification list below on a clean Linux VM, then macOS. Nothing here was run on a real host.
