# Kanban worker's `--toolsets` pin excluded `qkeee_erp` — a setup-step gap in our own code, not a hermes-agent bug

Status: **fixed, 2026-10-27.** `erpnext-hermes@4db8a55`. Live-confirmed on `dev-erp`: `DISPATCH_TRACE` showed `qkeee_erp_in_result=True` and the next dispatched worker (`t_f114b184`) spawned with `qkeee_erp` in its `--toolsets` argv for the first time.

This entry replaces the 2026-10-26 write-up below, which diagnosed the right symptom but the wrong mechanism. Left intact further down for the record of what was ruled out and why — most of that work (ruling out the auto-decomposer, assignee, HERMES_HOME resolution, Isolation fingerprint, skill loading, log truncation, stale daemon state, and `register()` itself) still stands; only the final "root cause" section was wrong.

## The actual root cause

`hermes_cli.tools_config._get_platform_tools(cfg, platform)` includes a plugin toolset for a platform only if it's **explicitly listed in that platform's own saved `platform_toolsets` array** — unless the toolset has never been recorded in `known_plugin_toolsets` for that platform, in which case it's auto-included by default. Once `known_plugin_toolsets.<platform>` lists a toolset, that auto-include path closes permanently; from then on the toolset is only offered if it's explicitly in `platform_toolsets.<platform>`.

`erpnext-hermes/plugins/qkeee-erp/setup_steps.py`'s P1 (`_Toolsets`) step wrote `qkeee_erp` into:
- `platform_toolsets.google_chat` (`CHAT_PLATFORM`) — explicitly, creating the list from a shipped default if missing
- `known_plugin_toolsets.{cli, discord, google_chat}` — all three, unconditionally

It never wrote `platform_toolsets.cli` (`OPERATOR_PLATFORM`) itself. So once a profile ran `setup --apply-profile-fixes` once (writing `known_plugin_toolsets.cli`), `qkeee_erp` was **permanently excluded** from `cli` — the platform `_erp_chat_platforms()`'s own docstring calls "the Operator's and the Kanban workers' platform" — regardless of whether `register()` succeeded (it always did) or how the PluginManager's discovery cache behaved (irrelevant; ruled out live, see below).

This explains every observed data point with no timing/caching/race theory needed:
- `google_chat` (live interactive chat) always worked — P1 wrote its explicit list correctly.
- `cli` (every kanban-dispatched worker, every `chat -q` CLI invocation, the dispatcher's own `--toolsets` precomputation) never got `qkeee_erp` — P1 never touched its list.
- `register()` always logged success with `qkeee_erp` present in the live plugin registry (`erpnext-hermes`'s own `_trace()` instrument) — because registration and platform-toolset filtering are two independent layers; registering the tools never implied they'd be offered to a `cli`-platform session.

## How it was found (2026-10-26/27, cross-validation against the then-current hermes-agent code)

The 2026-10-26 write-up's own "root cause" (a `get_plugin_manager()` per-resolved-home cache-key mismatch from `set_hermes_home_override`) was re-examined by reading `hermes_cli/plugins.py` directly: `_plugin_home_key()` normalizes every key through `.expanduser().resolve()`, so a raw string difference couldn't actually produce two different cache entries for the same directory. That ruled out the cache-key theory as stated.

A `DISPATCH_TRACE` instrument was added (`erpnext-hermes@bb8465c`) to `plugins/qkeee-erp/__init__.py`, monkeypatching `kanban_db_dispatch._resolve_worker_cli_toolsets` from inside `register()` (possible because this gateway process is multi-tenant — chat, cron, and kanban dispatch share one process via `set_hermes_home_override`, so the plugin module is already loaded there) to log the `PluginManager` state the dispatcher's own call sees. Live result: `pre_existing_manager=True pre_discovered=True in_flight=False` — a fully, successfully discovered manager, same resolved key as everywhere else, with `qkeee_erp` genuinely absent from its *platform-filtered* toolset list. The SAME process's own `register()` trace line, moments earlier, showed `qkeee_erp` present in the raw plugin registry (`plugin_toolset_keys=[...,'qkeee_erp',...]`). Same process, same moment, two different answers from what should've been one source of truth — which pointed straight at `_get_platform_tools()`'s platform-specific filtering rather than anything about discovery or caching.

## The fix

`setup_steps.py`'s P1 `check()`/`apply()` now also ensures `platform_toolsets.cli` contains `qkeee_erp`, conservatively (only appends to an already-explicit list, mirroring P3's existing `KANBAN_PLATFORMS` pattern — it does not synthesize a cli toolset list from scratch for a profile that never had one). Regression fixture `CLI_MISSING_TOOLSET` in `plugins/qkeee-erp/tests/test_plugin_setup.py` reproduces the exact live `dev-erp` config shape. 834 tests pass.

**Existing installs must re-run `hermes -p <profile> qkeee-erp setup --apply-profile-fixes` once** to pick this up — `setup status` will show `P1` go `REFUSED`/`PENDING` with the reason `platform_toolsets.cli lacks qkeee_erp` until that run.

## Depends on

Nothing further. Issue 01's auto-decomposer problem (undispatchable sub-cards, no terminal `cancelled`/`wont-do` state) is separate and still open — it blocks `kanban_create` fan-out specifically, independent of this fix. A single, manually-`specify`d (non-decomposed) kanban task now reaches `erp_*` tools correctly once the profile has re-run setup.

---

## Original 2026-10-26 write-up (symptom correctly diagnosed, mechanism wrong — kept for the record)

Every alternative explanation checked and ruled out, live, 2026-10-24 through 2026-10-26 (still valid):

1. **Wrong assignee / decomposer picking a different profile.** Ruled out: `kanban show` confirms `assignee: dev-erp` on every test card, including with `auto_decompose: false` set (no decomposer involved at all for a manually-`specify`d card).
2. **P3 (kanban) setup prerequisite unmet.** Ruled out: `hermes -p dev-erp qkeee-erp setup status` showed every step `done`, including P3, both before and after `--apply-profile-fixes`.
3. **`auto_decompose` picking an incompatible split.** Ruled out: set `auto_decompose: false`, container restarted, a single atomic card still had zero `erp_*` tools in its dispatched worker.
4. **`HERMES_HOME` resolving to the wrong physical directory for the worker.** Ruled out by reading the daemon's own `/proc/<pid>/environ` and tracing `resolve_profile_env()` by hand.
5. **Isolation fingerprint mismatch.** Ruled out by reading the fingerprint fields directly — `cwd` isn't even part of it.
6. **`skills.external_dirs` / skill-loading gap.** Ruled out: `skill_view` succeeded in the worker; only the plugin's tools were missing, not the skill.
7. **`--toolsets` override mangling `qkeee_erp` as an unrecognized name.** Weak evidence against at the time; turned out irrelevant — `qkeee_erp` was correctly recognized, just filtered out by platform-toolset logic.
8. **Log truncation.** Ruled out: `--tail 999999` was byte-identical to the default view.
9. **Stale in-process state surviving a restart.** Ruled out: a full `docker restart dev-erp` reproduced the identical result — which, with hindsight, is exactly what a persistent `config.yaml` gap (not transient process state) predicts.
10. **`register()` itself silently refusing.** Ruled out directly via the shipped `_trace()` instrument: `OK: 5 erp_* tools registered` every single time.

The root-cause section that followed this list (a `get_plugin_manager()` cache-key mismatch) was superseded by the finding above.
