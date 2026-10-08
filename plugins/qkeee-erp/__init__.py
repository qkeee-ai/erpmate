"""qkeee-erp plugin: ERPNext tools bound to the gateway sender, the Kanban
requester origin, and a guard against identity override
(agents/.scratch/qkeee-erp-requester-identity-binding, issues 03, 04 and 09).

- erp_tools.py: erp_query, erp_get, erp_report, erp_discover,
  erp_execute_write. They run in the gateway process and reuse the
  qkeee-erp-associate skill scripts as a library.
- kanban_hooks.py: kanban_create from a chat turn starts in triage; every
  created task records its requester origin (core/kanban_origin.py).
- identity_guard.py: blocks terminal / execute_code calls that override
  HERMES_SESSION_* or reach the ERPNext credentials around the erp_* tools.

Settings (`plugins.entries.qkeee-erp.settings`):
  scripts_dir  the skill's scripts/ directory. Default:
               <HERMES_HOME>/skills/qkeee-erp/qkeee-erp-associate/scripts

Enabled at boot by docker/cont-init.d/018-qkeee-erp-plugin.
"""

from __future__ import annotations

import logging
import os
import sys

logger = logging.getLogger(__name__)

_SKILL_SCRIPTS = os.path.join("skills", "qkeee-erp", "qkeee-erp-associate", "scripts")


def _skill_config() -> dict:
    """`skills.config.qkeee_erp` from the profile config, read per call."""
    from hermes_cli.config import load_config
    cfg = load_config() or {}
    value = ((cfg.get("skills") or {}).get("config") or {}).get("qkeee_erp") or {}
    return value if isinstance(value, dict) else {}


def _load_skill_library(scripts_dir: str) -> None:
    """Put the skill scripts on sys.path and check that `core` resolves to
    them, not to some other installed package of the same name."""
    if not os.path.isdir(scripts_dir):
        raise FileNotFoundError(f"qkeee-erp-associate scripts not found at {scripts_dir}")
    if scripts_dir not in sys.path:
        sys.path.append(scripts_dir)
    import core.client
    found = os.path.dirname(os.path.dirname(os.path.abspath(core.client.__file__)))
    if os.path.normcase(found) != os.path.normcase(os.path.abspath(scripts_dir)):
        raise ImportError(f"`core` resolved to {found}, not the skill scripts at {scripts_dir}")


def register(ctx) -> None:
    from hermes_constants import get_hermes_home
    from gateway.session_context import get_session_env

    scripts_dir = ctx.get_config("scripts_dir") or os.path.join(str(get_hermes_home()), _SKILL_SCRIPTS)
    try:
        _load_skill_library(scripts_dir)
    except Exception as e:
        # Fail visibly: without the library there are no ERPNext tools at all.
        logger.error("qkeee-erp plugin not loaded: %s", e)
        raise

    from core import client, kanban_origin
    from . import erp_tools, identity_guard, kanban_hooks

    client.set_session_env_reader(get_session_env)
    client.set_hermes_home_reader(lambda: str(get_hermes_home()))

    tools = erp_tools.ErpTools(settings=_skill_config)
    for name, schema in erp_tools.SCHEMAS.items():
        ctx.register_tool(name=name, toolset=erp_tools.TOOLSET, schema=schema,
                          handler=tools.handler(name), emoji="📒")

    ctx.register_hook("pre_tool_call", identity_guard.pre_tool_call)

    hooks = kanban_hooks.KanbanOriginHooks(
        session_env=get_session_env,
        store_path=lambda: kanban_origin.default_store_path(str(get_hermes_home())))
    ctx.register_hook("pre_tool_call", hooks.pre_tool_call)
    ctx.register_hook("post_tool_call", hooks.post_tool_call)
