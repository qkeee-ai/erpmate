FROM nousresearch/hermes-agent:latest

# Both components track a MOVING ref by default, so a rebuild can change agent
# behaviour with no diff on our side. That has already happened twice: jev
# 0.21.0 -> 0.22.1 moved 11 jev-* skills into plugin tools (they vanished from
# `hermes skills list` with no error anywhere), and the same bump added five
# toggles beyond `routing` that 019-jev-init did not know to set. Neither showed
# up as a failure — just silently different behaviour.
#
# Mitigation here is detection, not pinning: 0156-component-versions records the
# resolved SHA of each checkout on the volume and shouts when it changes between
# boots. To pin instead (reproducible builds, no surprise upgrades), set a tag or
# SHA — either on the command line:
#   docker compose build --build-arg JEV_REF=v0.22.1 --build-arg LCM_REF=<tag>
# or by uncommenting the pinned defaults below. Check what exists first:
#   git ls-remote --tags https://github.com/kerpopule/hermes-jev-skills
#   git ls-remote --tags https://github.com/stephenschoettler/hermes-lcm
ARG JEV_REPO=https://github.com/kerpopule/hermes-jev-skills
ARG JEV_REF=main
# ARG JEV_REF=v0.22.1
ARG LCM_REPO=https://github.com/stephenschoettler/hermes-lcm
ARG LCM_REF=main
# ARG LCM_REF=<tag-or-sha>

USER root

# Bake checkouts into the immutable image layer, NOT into $HERMES_HOME.
# /opt/data is a VOLUME, so anything written there at build time is hidden by
# the volume overlay at runtime. The installers symlink into their checkouts,
# so the checkouts must live at stable paths outside the volume.
#
# `.build-ref` captures the requested ref and the SHA it resolved to BEFORE
# .git is deleted — without it there is no version metadata left at runtime, so
# a floating ref would be undetectable. Read by 0156-component-versions.
RUN git clone "${JEV_REPO}" /opt/jev-skills \
    && git -C /opt/jev-skills checkout "${JEV_REF}" \
    && printf '%s %s\n' "${JEV_REF}" "$(git -C /opt/jev-skills rev-parse HEAD)" \
        > /opt/jev-skills/.build-ref \
    && rm -rf /opt/jev-skills/.git \
    && chmod -R a+rX,go-w /opt/jev-skills \
    && git clone "${LCM_REPO}" /opt/hermes-lcm \
    && git -C /opt/hermes-lcm checkout "${LCM_REF}" \
    && printf '%s %s\n' "${LCM_REF}" "$(git -C /opt/hermes-lcm rev-parse HEAD)" \
        > /opt/hermes-lcm/.build-ref \
    && rm -rf /opt/hermes-lcm/.git \
    && chmod -R a+rX,go-w /opt/hermes-lcm

# Boot hooks (s6 cont-init.d, lexicographic order). Everything targets
# $HERMES_HOME (a volume) so it runs at every boot and is idempotent.
#   01-hermes-setup       (base image) volume chown + config seed
#   0155-cwd-setup        create + chown the $HERMES_CWD bind mount so
#                         TERMINAL_CWD is actually enterable by the gateway
#                         (a root-owned fresh bind silently relocates the
#                         agent to the nearest usable ancestor)
#   0156-component-versions  report the baked jev / LCM / base-image versions
#                         and shout when one changed since the last boot —
#                         JEV_REF and LCM_REF float, so a rebuild can move
#                         capabilities with no error anywhere
#   0157-terminal-ssh     gateway ssh client key for the terminal sidecar;
#                         publish its public key to the terminal-auth volume
#                         (docker/terminal, agents ADR 0002)
#   016-profile-install   install the erpnext-hermes profile distribution
#   017-jev-skills        jev installer; links into every profile, so it must
#                         run after the profile exists
#   018-hermes-lcm        link + enable the LCM plugin (default + profiles)
#   018-qkeee-erp-plugin  enable the profile's qkeee-erp plugin (gateway
#                         ERPNext tools, Kanban requester origin) and move
#                         qkeee-erp.env to plugin-data/qkeee-erp/
#   019-jev-init          first-boot jev setup: models suggest, doctor, routing
#   0195-gateway-state    first-boot only: mark the custom profile
#                         desired_state=running, default desired_state=stopped
#                         — a plain gateway_state.json write, must run BEFORE
#                         02- (see the hook's own header for why: at cont-init
#                         time there's no live s6 supervisor to `gateway
#                         start` against yet, only 02-reconcile-profiles'
#                         registration pass reads this and decides)
#   0196-dashboard-auth   mirror the profile's dashboard.basic_auth into the
#                         default home (the dashboard service reads /opt/data,
#                         not the profile), persist a session-signing secret,
#                         and disable the dashboard instead of letting it
#                         crash-loop when its auth gate has no provider
#   0197-skills-lockdown  restrict the profile to the skills it ships: opt-out
#                         marker + prune pristine bundled copies + recompute
#                         skills.disabled. After 017/018 so skills those hooks
#                         link into the profile are allowed automatically
#   02-reconcile-profiles (base image) creates the s6 slots per the above
COPY docker/defaults/ /opt/defaults/
RUN chmod -R a+rX,go-w /opt/defaults
COPY docker/cont-init.d/ /etc/cont-init.d/
# Globbed in three parts: `01[6-9]-*` does not match the 4-digit 015x/019x
# names (the `-` has nothing to match against their 4th character).
RUN set -eu; for f in /etc/cont-init.d/015[5-7]-* /etc/cont-init.d/01[6-9]-* \
        /etc/cont-init.d/019[5-7]-*; do \
        sed -i 's/\r$//' "$f" && chmod 0755 "$f"; \
    done
# Operator maintenance tools (see adminops/README.md). Baked in so they are
# available inside the container without a copy step; not run at boot.
COPY adminops/ /opt/adminops/
RUN set -eu; find /opt/adminops -type f -exec sed -i 's/\r$//' {} +; \
    chmod -R a+rX,go-w /opt/adminops; chmod 0755 /opt/adminops/*.py
