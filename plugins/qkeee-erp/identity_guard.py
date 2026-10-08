"""pre_tool_call identity guard (requester identity binding, issue 03).

The agent writes the terminal command and the execute_code source, so it can
override the gateway's HERMES_SESSION_* values or read the ERPNext
credentials and call ERPNext around the erp_* tools. This hook blocks the
plain forms of that:

- session_override  terminal / execute_code sets, exports, unsets or
                    env-overrides a HERMES_SESSION_* name (or clears the
                    environment with `env -i`)
- env_file          any tool except erp_* names qkeee-erp.env or the
                    plugin-data/qkeee-erp directory
- core_client       execute_code imports the skill's core.client
- erpnext_api       execute_code calls ERPNext REST paths directly

Pattern matching only: eval, base64 or a script written to disk and run later
gets past it. Defence in depth, not a boundary; the boundary is the erp_*
tools holding the credentials gateway-side (issue 04) and the terminal
running outside the gateway (issue 06).
"""

from __future__ import annotations

import re
from typing import Any, Iterator, Optional

_SESSION = r"(?<![$\w])HERMES_SESSION_\w*"

_SESSION_OVERRIDE = [re.compile(p) for p in (
    # NAME=… (inline prefix, export/declare/env NAME=…), environ['NAME'] = …
    _SESSION + r"""['"]?\]?\s*=(?!=)""",
    # {'NAME': …} (subprocess env=, os.environ.update)
    _SESSION + r"""['"]\s*:""",
    # ${NAME:=…} / ${NAME=…} assigns a default
    r"\$\{HERMES_SESSION_\w*:?=",
    r"\b(?:export|unset|declare|typeset|readonly|local)\b[^;&|\n]*" + _SESSION,
    r"\benv\b[^;&|\n]*?\s(?:-u\s*|--unset(?:=|\s+))HERMES_SESSION_",
    r"\benv\s+(?:-i\b|--ignore-environment\b|-(?=\s|$))",
    r"\b(?:pop|del|unsetenv|putenv)\b[^\n]*" + _SESSION,
)]

_ENV_FILE = re.compile(r"qkeee-erp\.env|plugin-data/+qkeee-erp\b", re.IGNORECASE)

_CORE_CLIENT = re.compile(r"\bcore\.client\b|\bfrom\s+core\s+import\b[^\n]*\bclient\b")

_ERPNEXT_API = re.compile(r"/api/(?:resource|method)\b")

_HINT = "Use the erp_* tools for ERPNext; they bind the requester from the gateway session."


def _block(rule: str, what: str) -> dict:
    return {"action": "block", "message": f"qkeee-erp identity guard blocked this call ({rule}): {what}. {_HINT}"}


def _strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _strings(item)


def _source(tool_name: str, args: dict) -> str:
    key = {"terminal": "command", "execute_code": "code"}.get(tool_name)
    value = args.get(key) if key else None
    return value if isinstance(value, str) else ""


def check(tool_name: Optional[str], args: Any) -> Optional[dict]:
    """The block directive for one tool call, or None to let it run."""
    if not tool_name or tool_name.startswith("erp_") or not isinstance(args, dict):
        return None
    if any(_ENV_FILE.search(s) for s in _strings(args)):
        return _block("env_file", "the ERPNext credentials file is gateway-only")
    source = _source(tool_name, args)
    if not source:
        return None
    if any(p.search(source) for p in _SESSION_OVERRIDE):
        return _block("session_override", "HERMES_SESSION_* values come from the gateway and cannot be changed")
    if tool_name == "execute_code":
        if _CORE_CLIENT.search(source):
            return _block("core_client", "execute_code cannot use the qkeee-erp client library")
        if _ERPNEXT_API.search(source):
            return _block("erpnext_api", "execute_code cannot call the ERPNext API directly")
    return None


def pre_tool_call(tool_name=None, args=None, **_):
    return check(tool_name, args)
