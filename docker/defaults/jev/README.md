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

## Dev and test pools: `routing.dev.json`

Same settings as `routing.json`, but every tier uses only DeepSeek and Nex
AGI models with tool calling (OpenRouter catalog 2026-10-22, USD per 1M
tokens in/out):

| Tier | Lead (general) | Price | Vision lead |
|---|---|---|---|
| simple | deepseek-v4-flash | 0.091 / 0.182 | deepseek-v4.1-flash-1017 |
| medium | deepseek-v4.1-flash-1017 | 0.15 / 0.60 | deepseek-v4.1-flash |
| hard | deepseek-v4-pro | 0.446 / 0.892 | deepseek-v4.1-flash |

`exclude` also blocks `anthropic/*`, `openai/*`, `google/*`, `moonshotai/*`
and `x-ai/*`, so no pool edit can reach them by mistake. Jev takes the first
usable model of a pool and skips one whose context is too small, so the
163K/262K models (deepseek-v3.2, nex-n2.5-pro) sit behind a 1M model or
after the lead. nex-n2.5-pro has one provider and no uptime data, so it is
never a lead. Left out: nex-n2.5-mini (no tool calling) and the `:batch`
SKU.

Use it on a new profile: set `JEV_ROUTING_FILE=/opt/defaults/jev/routing.dev.json`.
On an existing profile (its routing.json is never overwritten): back up
`<profile>/jev/routing.json`, copy this file over it, then run `jev doctor`
and check `price_order`.
