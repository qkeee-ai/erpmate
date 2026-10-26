"""qkeee-erp plugin: ERPNext tools bound to the gateway sender, the Kanban
requester origin, and a guard against identity override
(agents/.scratch/qkeee-erp-requester-identity-binding, issues 03, 04 and 09).

- qkeee_erp/: the ERP library (connector, write pipeline, domains,
  discovery, provisioning). Runs only in the gateway process.
- erp_tools.py: erp_query, erp_get, erp_report, erp_discover,
  erp_execute_write.
- kanban_hooks.py: kanban_create from a chat turn starts in triage; every
  created task records its requester origin (qkeee_erp/core/kanban_origin.py).
- identity_guard.py: blocks terminal / execute_code calls that override
  HERMES_SESSION_* or reach the ERPNext credentials around the erp_* tools.

- skills/usage/SKILL.md: `qkeee-erp:usage`, the rules for calling the
  erp_* tools (load on demand with skill_view).
- cli.py: the Operator CLI, `hermes -p <profile> qkeee-erp <command>`.
- settings.py: the plugin's own settings (plugins.entries.qkeee-erp.settings).
- setup_steps.py: `hermes qkeee-erp setup`. Until every setup item is done
  for this plugin version, no erp_* tools register; the hooks, the CLI and
  the usage skill always register. Setup items I2/I1 (qkeee_erp/isolation.py)
  require proven Isolation whose fingerprint equals the live terminal config
  (agents ADR 0006).

register() never raises: Hermes rolls back every registration of a plugin
whose register() raises. If the library cannot load, only the identity
guard is registered and the error is logged.

Enabled at boot by docker/cont-init.d/018-qkeee-erp-plugin.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_dispatch_trace_installed = False


def _trace(hermes_home, msg: str) -> None:
    """Append one line to `<hermes_home>/plugin-data/qkeee-erp/register_trace.log`,
    bypassing Python `logging` entirely.

    Live finding, 2026-10-26 (agents repo, qkeee-erp-kanban-execution-steering
    issue 11): every refusal branch in register() already logs its own reason
    via `logger.warning`/`logger.error`, but a kanban-dispatched worker spawns
    as `hermes ... chat -q` (quiet mode) and nothing from those calls ever
    showed up in the worker's own captured log, even with `--tail 999999` on a
    freshly-restarted daemon. Writing straight to a file sidesteps whatever
    quiet mode or logging-handler configuration swallows them, so the next
    live run shows the real reason instead of leaving us to guess at it from
    outside. Never raises: register() must never raise (this module's own
    docstring) and a tracing failure is not a reason to break registration."""
    try:
        if not hermes_home:
            return  # no known home: never guess by writing relative to cwd
        path = Path(hermes_home) / "plugin-data" / "qkeee-erp" / "register_trace.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        pid, task = os.getpid(), os.environ.get("HERMES_KANBAN_TASK") or ""
        with path.open("a", encoding="utf-8") as f:
            f.write(f"{stamp} pid={pid} kanban_task={task or '-'} {msg}\n")
    except Exception:
        pass


def _patch_dispatch_trace(home_for_trace: str) -> None:
    """One-time instrument for hermes-agent issue 11's still-open half.

    register()'s own `_trace()` calls above only cover the WORKER side
    (confirmed: always "OK, 5 tools" there). The dispatcher's own
    `--toolsets` pin — computed by `kanban_db_dispatch._resolve_worker_cli_
    toolsets()` → `plugins.get_plugin_toolset_keys_nowait()` →
    `_nowait_plugin_set()` — has never been traced directly; its argv
    output just shows `qkeee_erp` missing, with no visibility into WHY
    (`agents` repo, qkeee-erp-kanban-execution-steering issue 11,
    2026-10-26 cross-validation). Wrapping the dispatch-side function here
    (rather than patching hermes-agent's own source, which we don't own)
    logs the PluginManager state that call sees — whether a manager already
    existed for that resolved home key, its `_discovered` flag, and whether
    a background discovery thread was in flight — so the next live kanban
    dispatch shows which branch fires instead of leaving it a guess. Only
    possible because this gateway process is multi-tenant (chat/cron/kanban
    dispatch share one process via `set_hermes_home_override`, not separate
    processes), so `register()` running here at all means this module is
    already imported in the same process the dispatcher runs in. Never
    raises: a failed patch install is not a reason to break registration.
    """
    global _dispatch_trace_installed
    if _dispatch_trace_installed:
        return
    try:
        import hermes_cli.kanban_db_dispatch as _dispatch_mod
        import hermes_cli.plugins as _plugins_mod

        original = _dispatch_mod._resolve_worker_cli_toolsets

        def _traced(hermes_home, _original=original):
            key = None
            pre_mgr = None
            pre_discovered = None
            try:
                if hermes_home:
                    key = Path(hermes_home).expanduser().resolve()
                    pre_mgr = _plugins_mod._plugin_managers_by_home.get(key)
                    pre_discovered = getattr(pre_mgr, "_discovered", None)
            except Exception:
                pass
            thread = _plugins_mod._background_discovery_thread
            in_flight = thread is not None and thread.is_alive()
            result = _original(hermes_home)
            _trace(home_for_trace,
                   f"DISPATCH_TRACE hermes_home={hermes_home!r} resolved_key={key!r} "
                   f"pre_existing_manager={pre_mgr is not None!r} pre_discovered={pre_discovered!r} "
                   f"in_flight={in_flight!r} result={result!r} "
                   f"qkeee_erp_in_result={('qkeee_erp' in (result or []))!r}")
            return result

        _dispatch_mod._resolve_worker_cli_toolsets = _traced
        _dispatch_trace_installed = True
    except Exception as e:
        _trace(home_for_trace, f"DISPATCH_TRACE install failed: {e!r}")


def _profile_config() -> dict:
    """The profile's config.yaml, read-only (never mutate the result)."""
    from hermes_cli.config import load_config_readonly
    return load_config_readonly() or {}


def _settings() -> dict:
    """`plugins.entries.qkeee-erp.settings`, read per call so a change
    applies to the next tool call."""
    from . import settings
    return settings.plugin_settings(_profile_config())


def _setup_unmet() -> list:
    """Why ERP tools must not register now (setup_steps.unmet); [] when
    setup is complete for this plugin version."""
    from . import setup_steps
    return setup_steps.unmet(setup_steps.production_env())


def register(ctx) -> None:
    # pytest imports this file outside its package, so relative imports stay
    # inside functions.
    from . import identity_guard

    # Best-effort home for tracing, refined below once get_hermes_home loads;
    # register() must still trace a library-load failure, so this can't wait.
    home_for_trace = os.environ.get("HERMES_HOME") or ""

    # The guard protects; it grants nothing, so it registers first and always.
    ctx.register_hook("pre_tool_call", identity_guard.pre_tool_call)
    # The Operator CLI registers before the library loads: `setup` must be
    # reachable exactly when the plugin cannot serve ERP calls.
    try:
        from . import cli, setup_steps
        operator = cli.OperatorCli(read_config=_profile_config, setup_env=setup_steps.production_env)
        ctx.register_cli_command(name=cli.COMMAND, help=cli.HELP, setup_fn=cli.setup_parser,
                                 handler_fn=operator.run)
    except Exception as e:
        logger.error("qkeee-erp plugin: Operator CLI not registered: %s", e)
    try:
        ctx.register_skill("usage", Path(__file__).parent / "skills" / "usage" / "SKILL.md",
                           description="Rules for calling the erp_* tools safely.")
    except Exception as e:
        logger.error("qkeee-erp plugin: skill qkeee-erp:usage not registered: %s", e)

    try:
        from hermes_constants import get_hermes_home
        from gateway.session_context import get_session_env

        from . import erp_tools, kanban_hooks
        from .qkeee_erp.core import client, kanban_origin
    except Exception as e:
        _trace(home_for_trace, f"ERP library not loaded, no erp_* tools: {e!r}")
        logger.error("qkeee-erp plugin: ERP library not loaded, no erp_* tools: %s", e)
        return

    try:
        home_for_trace = str(get_hermes_home())
    except Exception:
        pass  # keep the os.environ fallback above

    _patch_dispatch_trace(home_for_trace)

    client.set_session_env_reader(get_session_env)
    client.set_hermes_home_reader(lambda: str(get_hermes_home()))
    try:
        from gateway.platforms._shared import get_scoped_secret
        from .qkeee_erp import mailer
        mailer.set_secret_reader(get_scoped_secret)
    except Exception as e:  # mailer falls back to os.environ
        logger.warning("qkeee-erp plugin: profile secret reader not set for email: %s", e)

    try:
        hooks = kanban_hooks.KanbanOriginHooks(
            session_env=get_session_env,
            store_path=lambda: kanban_origin.default_store_path(str(get_hermes_home())))
        ctx.register_hook("pre_tool_call", hooks.pre_tool_call)
        ctx.register_hook("post_tool_call", hooks.post_tool_call)
    except Exception as e:
        logger.error("qkeee-erp plugin: Kanban origin hooks not registered: %s", e)

    # Tools grant ERPNext access, so they register only when setup is
    # complete (spec D7). The plugin never writes config on load.
    try:
        unmet = _setup_unmet()
    except Exception as e:
        _trace(home_for_trace, f"setup state not readable, no erp_* tools: {e!r}")
        logger.error("qkeee-erp plugin: setup state not readable, no erp_* tools: %s", e)
        return
    if unmet:
        _trace(home_for_trace, f"REFUSED (unmet setup): {'; '.join(unmet)}")
        logger.warning("qkeee-erp plugin: no erp_* tools until setup is complete; see "
                       "`hermes qkeee-erp setup status`: %s", "; ".join(unmet))
        return
    tools = erp_tools.ErpTools(settings=_settings)
    try:
        for name, schema in erp_tools.SCHEMAS.items():
            ctx.register_tool(name=name, toolset=erp_tools.TOOLSET, schema=schema,
                              handler=tools.handler(name), emoji="📒")
    except Exception as e:
        _trace(home_for_trace, f"erp_* tools not (fully) registered: {e!r}")
        logger.error("qkeee-erp plugin: erp_* tools not (fully) registered: %s", e)
    else:
        # Live finding, 2026-10-26: registration itself succeeds inside a
        # kanban worker, yet tool_search never shows the tools — the gap is
        # downstream, in whether this process's effective toolset list
        # actually includes erp_tools.TOOLSET. argv carries the dispatcher's
        # literal `--toolsets` value (kanban_db_dispatch._resolve_worker_cli_
        # toolsets); confirmed it never includes 'qkeee_erp' there. Also call
        # hermes_cli.plugins.get_plugin_toolset_keys_nowait() right here, in
        # the SAME process we just registered in: if 'qkeee_erp' is present
        # here but absent from the dispatcher's own --toolsets computation,
        # the gap is specifically in set_hermes_home_override's manager
        # scoping, not a general registry issue.
        try:
            from hermes_cli.plugins import get_plugin_toolset_keys_nowait
            keys = sorted(get_plugin_toolset_keys_nowait())
        except Exception as e:
            keys = f"<unavailable: {e!r}>"
        _trace(home_for_trace, f"OK: {len(erp_tools.SCHEMAS)} erp_* tools registered "
                                f"toolset={erp_tools.TOOLSET!r} plugin_toolset_keys={keys!r} "
                                f"argv={sys.argv!r}")
