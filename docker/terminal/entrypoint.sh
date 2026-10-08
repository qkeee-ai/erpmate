#!/bin/sh
# Terminal sidecar entrypoint: match the hermes uid/gid to the cwd bind
# mount, make the account usable for key login, keep the host key on its
# volume, then run sshd in the foreground.
#
#   HERMES_UID / HERMES_GID  same values as the gateway service (compose)
set -eu

log() { echo "[terminal] $*"; }

valid_id() { case "$1" in ''|*[!0-9]*) return 1 ;; 0) return 1 ;; *) return 0 ;; esac; }

if [ -n "${HERMES_GID:-}" ] && valid_id "$HERMES_GID" && [ "$HERMES_GID" != "$(id -g hermes)" ]; then
    log "hermes gid -> $HERMES_GID"
    groupmod -o -g "$HERMES_GID" hermes
fi
if [ -n "${HERMES_UID:-}" ] && valid_id "$HERMES_UID" && [ "$HERMES_UID" != "$(id -u hermes)" ]; then
    log "hermes uid -> $HERMES_UID"
    usermod -o -u "$HERMES_UID" hermes
fi

# useradd leaves the password locked ("!"); with UsePAM no, sshd refuses
# every login, key included, for a locked account. "*" is no password at all.
usermod -p '*' hermes

# The sidecar's own home (terminal-home volume): Hermes syncs skills into
# ~/.hermes here. Small, and only this container writes it.
HOME_DIR="$(getent passwd hermes | cut -d: -f6)"
mkdir -p "$HOME_DIR"
chown -R hermes:hermes "$HOME_DIR"

KEY_DIR=/etc/ssh/hostkeys
mkdir -p "$KEY_DIR"
chmod 700 "$KEY_DIR"
if [ ! -f "$KEY_DIR/ssh_host_ed25519_key" ]; then
    ssh-keygen -q -t ed25519 -N '' -C terminal-sidecar -f "$KEY_DIR/ssh_host_ed25519_key"
    log "generated host key"
fi
chmod 600 "$KEY_DIR/ssh_host_ed25519_key"

if [ ! -s /opt/terminal-auth/authorized_keys ]; then
    log "WARNING: /opt/terminal-auth/authorized_keys is missing or empty. The gateway's"
    log "  0157-terminal-ssh hook writes it at boot; logins fail until then."
fi

mkdir -p /run/sshd
chmod 755 /run/sshd
/usr/sbin/sshd -t
log "sshd starting"
exec /usr/sbin/sshd -D -e
