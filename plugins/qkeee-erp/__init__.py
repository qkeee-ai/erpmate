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

register() never raises: Hermes rolls back every registration of a plugin
whose register() raises. If the library cannot load, only the identity
guard is registered and the error is logged.

Enabled at boot by docker/cont-init.d/018-qkeee-erp-plugin.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def _skill_config() -> dict:
    """`skills.config.qkeee_erp` from the profile config, read per call."""
    from hermes_cli.config import load_config
    cfg = load_config() or {}
    value = ((cfg.get("skills") or {}).get("config") or {}).get("qkeee_erp") or {}
    return value if isinstance(value, dict) else {}


def register(ctx) -> None:
    from . import identity_guard

    # The guard protects; it grants nothing, so it registers first and always.
    ctx.register_hook("pre_tool_call", identity_guard.pre_tool_call)

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

    tools = erp_tools.ErpTools(settings=_skill_config)
    for name, schema in erp_tools.SCHEMAS.items():
        ctx.register_tool(name=name, toolset=erp_tools.TOOLSET, schema=schema,
                          handler=tools.handler(name), emoji="📒")

    hooks = kanban_hooks.KanbanOriginHooks(
        session_env=get_session_env,
        store_path=lambda: kanban_origin.default_store_path(str(get_hermes_home())))
    ctx.register_hook("pre_tool_call", hooks.pre_tool_call)
    ctx.register_hook("post_tool_call", hooks.post_tool_call)
