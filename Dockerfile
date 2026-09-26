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
#   016-profile-install   install the erpnext-hermes profile distribution
#   017-jev-skills        jev installer; links into every profile, so it must
#                         run after the profile exists
#   018-hermes-lcm        link + enable the LCM plugin (default + profiles)
#   02-reconcile-profiles (base image) per-profile gateway slots
COPY docker/cont-init.d/ /etc/cont-init.d/
RUN sed -i 's/\r$//' /etc/cont-init.d/016-* /etc/cont-init.d/017-* /etc/cont-init.d/018-* \
    && chmod 0755 /etc/cont-init.d/016-* /etc/cont-init.d/017-* /etc/cont-init.d/018-*
