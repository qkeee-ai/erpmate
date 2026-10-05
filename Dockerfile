FROM nousresearch/hermes-agent:latest

# Pin to a tag or commit SHA for reproducible builds:
#   docker compose build --build-arg JEV_REF=<sha> --build-arg LCM_REF=<sha>
ARG JEV_REPO=https://github.com/kerpopule/hermes-jev-skills
ARG JEV_REF=main
ARG LCM_REPO=https://github.com/stephenschoettler/hermes-lcm
ARG LCM_REF=main

USER root

# Bake checkouts into the immutable image layer, NOT into $HERMES_HOME.
# /opt/data is a VOLUME, so anything written there at build time is hidden by
# the volume overlay at runtime. The installers symlink into their checkouts,
# so the checkouts must live at stable paths outside the volume.
RUN git clone "${JEV_REPO}" /opt/jev-skills \
    && git -C /opt/jev-skills checkout "${JEV_REF}" \
    && rm -rf /opt/jev-skills/.git \
    && chmod -R a+rX,go-w /opt/jev-skills \
    && git clone "${LCM_REPO}" /opt/hermes-lcm \
    && git -C /opt/hermes-lcm checkout "${LCM_REF}" \
    && rm -rf /opt/hermes-lcm/.git \
    && chmod -R a+rX,go-w /opt/hermes-lcm

# Boot hooks (s6 cont-init.d, lexicographic order). Everything targets
# $HERMES_HOME (a volume) so it runs at every boot and is idempotent.
#   01-hermes-setup       (base image) volume chown + config seed
#   0155-cwd-setup        create + chown the $HERMES_CWD bind mount so
#                         TERMINAL_CWD is actually enterable by the gateway
#                         (a root-owned fresh bind silently relocates the
#                         agent to the nearest usable ancestor)
#   016-profile-install   install the erpnext-hermes profile distribution
#   017-jev-skills        jev installer; links into every profile, so it must
#                         run after the profile exists
#   018-hermes-lcm        link + enable the LCM plugin (default + profiles)
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
# Globbed in three parts: `01[6-9]-*` does not match the 4-digit 0155/019x
# names (the `-` has nothing to match against their 4th character).
RUN set -eu; for f in /etc/cont-init.d/0155-* /etc/cont-init.d/01[6-9]-* \
        /etc/cont-init.d/019[5-7]-*; do \
        sed -i 's/\r$//' "$f" && chmod 0755 "$f"; \
    done
