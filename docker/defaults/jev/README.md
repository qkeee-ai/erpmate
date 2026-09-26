# Default jev routing

Drop a curated `routing.json` here. It is baked into the image at
`/opt/defaults/jev/routing.json` and copied to `<profile>/jev/routing.json`
on first boot by `docker/cont-init.d/019-jev-init`, only if the profile has no
routing.json yet. An existing file (operator edits, a restored volume) is never
overwritten.

With no file here (and no `JEV_ROUTING_FILE`), first boot falls back to
`jev models suggest --write`.

To change routing on an already-initialised profile, edit
`<profile>/jev/routing.json` in the volume (or delete it and restart to
re-seed). The baked file only affects new profiles.
