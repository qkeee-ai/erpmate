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
  While the legacy skills.config.qkeee_erp section is present, setup is
  pending and no erp_* tools register; the hooks always register.

register() never raises: Hermes rolls back every registration of a plugin
whose register() raises. If the library cannot load, only the identity
guard is registered and the error is logged.

Enabled at boot by docker/cont-init.d/018-qkeee-erp-plugin.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def _profile_config() -> dict:
    """The profile's config.yaml, read-only (never mutate the result)."""
    from hermes_cli.config import load_config_readonly
    return load_config_readonly() or {}


def _settings() -> dict:
    """`plugins.entries.qkeee-erp.settings`, read per call so a change
    applies to the next tool call."""
    from . import settings
    return settings.plugin_settings(_profile_config())


def register(ctx) -> None:
    # pytest imports this file outside its package, so relative imports stay
    # inside functions.
    from . import cli, identity_guard, settings

    # The guard protects; it grants nothing, so it registers first and always.
    ctx.register_hook("pre_tool_call", identity_guard.pre_tool_call)
    # The Operator CLI registers before the library loads: `setup` must be
    # reachable exactly when the plugin cannot serve ERP calls.
    try:
        ctx.register_cli_command(name=cli.COMMAND, help=cli.HELP, setup_fn=cli.setup_parser,
                                 handler_fn=cli.OperatorCli(read_config=_profile_config).run)
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
        logger.error("qkeee-erp plugin: ERP library not loaded, no erp_* tools: %s", e)
        return

    client.set_session_env_reader(get_session_env)
    client.set_hermes_home_reader(lambda: str(get_hermes_home()))

    try:
        hooks = kanban_hooks.KanbanOriginHooks(
            session_env=get_session_env,
            store_path=lambda: kanban_origin.default_store_path(str(get_hermes_home())))
        ctx.register_hook("pre_tool_call", hooks.pre_tool_call)
        ctx.register_hook("post_tool_call", hooks.post_tool_call)
    except Exception as e:
        logger.error("qkeee-erp plugin: Kanban origin hooks not registered: %s", e)

    # Tools grant ERPNext access, so they register only when setup is done.
    try:
        pending = settings.setup_pending(_profile_config())
    except Exception as e:
        logger.error("qkeee-erp plugin: profile config not readable, no erp_* tools: %s", e)
        return
    if pending:
        logger.warning("qkeee-erp plugin: no erp_* tools: %s", pending)
        return
    tools = erp_tools.ErpTools(settings=_settings)
    try:
        for name, schema in erp_tools.SCHEMAS.items():
            ctx.register_tool(name=name, toolset=erp_tools.TOOLSET, schema=schema,
                              handler=tools.handler(name), emoji="📒")
    except Exception as e:
        logger.error("qkeee-erp plugin: erp_* tools not (fully) registered: %s", e)
