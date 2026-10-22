# Terminal sidecar: the agent's terminal, file tools and execute_code run
# here over Hermes' `ssh` backend, not in the gateway container
# (agents docs/adr/0002-terminal-on-ssh-sidecar.md).
#
# Same base as the gateway, so skill scripts find the same python, venv,
# ffmpeg and playwright. It never mounts the gateway's /opt/data: the
# ERPNext credentials, the LLM keys and the gateway's ssh client key stay
# out of reach. Hermes syncs the skills tree into ~/.hermes here itself.
ARG BASE_IMAGE=nousresearch/hermes-agent:latest
FROM ${BASE_IMAGE}

USER root

# Host keys are generated at first start into a volume (entrypoint), so the
# gateway's known_hosts entry survives a rebuild. Drop the build-time ones.
RUN apt-get update \
    && apt-get install -y --no-install-recommends openssh-server \
    && rm -rf /var/lib/apt/lists/* \
    && rm -f /etc/ssh/ssh_host_*

# No Hermes CLI here. A `hermes` command in this container acts on its own
# Hermes home, not the gateway's: dev-erp 2026-10-22 built a kanban board
# no dispatcher sees, around kanban_hooks, and tried `hermes dashboard`.
# The ssh backend only syncs files and runs bash, and execute_code stages
# its own hermes_tools.py, so nothing here needs the entry points. The
# Python packages stay (skill scripts share the venv). The plugin's
# identity guard also blocks `hermes` and `python -m hermes_cli` calls.
RUN set -e; \
    for bin in hermes hermes-agent hermes-acp; do \
        while path="$(command -v "$bin")"; do rm -f "$path"; done; \
    done; \
    find / -xdev \( -name hermes -o -name hermes-agent -o -name hermes-acp \) \
        -path '*/bin/*' \( -type f -o -type l \) -delete 2>/dev/null || true

COPY docker/terminal/sshd_config /etc/ssh/sshd_config
COPY docker/terminal/entrypoint.sh /usr/local/bin/terminal-entrypoint
RUN sed -i 's/\r$//' /etc/ssh/sshd_config /usr/local/bin/terminal-entrypoint \
    && chmod 0644 /etc/ssh/sshd_config \
    && chmod 0755 /usr/local/bin/terminal-entrypoint

EXPOSE 22
ENTRYPOINT ["/usr/local/bin/terminal-entrypoint"]
CMD []
