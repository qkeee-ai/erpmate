"""Lets the whole suite run from one place: `cd qkeee-erp-associate &&
python -m pytest scripts` (or just `pytest` from the skill root, since
pytest walks down into scripts/).

Every test module under scripts/core and scripts/domains uses bare local
imports (`import client`, `import accounts`, ...) rather than package-
qualified ones (`from core import client`), matching the sys.path
bootstrap every scripts/domains/*.py module already does at import time
for its own `from core import client` line. Without this conftest adding
both directories to sys.path up front, those bare imports only resolve
when pytest's cwd is already the test's own directory (python -m pytest
adds cwd to sys.path[0] itself, nothing else does) — running from the
skill root would fail with ModuleNotFoundError otherwise.
"""

import os
import sys

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
for _sub in ("core", "domains"):
    _path = os.path.join(_SCRIPTS_DIR, _sub)
    if _path not in sys.path:
        sys.path.insert(0, _path)

# One module object per file. Tests import some modules bare (`import client`,
# `import fixed_assets`) and others package-qualified (`from core import
# client`, `from domains import fixed_assets`). Without aliasing, Python
# loads each file TWICE as two unrelated modules: a patch applied to one is
# invisible to the other, and each copy keeps its own operation registry.
# Load the package-qualified module first and register it under the bare
# name too. (Production code only ever uses the package-qualified names.)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import importlib  # noqa: E402

for _bare, _qualified in (
        ("confirm_token", "core.confirm_token"),
        ("kanban_origin", "core.kanban_origin"),
        ("client", "core.client"),
        ("operations", "core.operations"),
        ("memory_promote", "core.memory_promote"),
        ("accounts", "domains.accounts"),
        ("fixed_assets", "domains.fixed_assets"),
        ("hr_payroll", "domains.hr_payroll"),
        ("inventory", "domains.inventory"),
        ("mis", "domains.mis"),
        ("procurement", "domains.procurement"),
        ("sales", "domains.sales"),
        ("system_admin", "domains.system_admin")):
    sys.modules[_bare] = importlib.import_module(_qualified)
