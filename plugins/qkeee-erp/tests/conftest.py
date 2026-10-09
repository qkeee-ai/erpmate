"""Loads the plugin as the package `qkeee_erp_plugin`, the way Hermes'
loader loads it as `hermes_plugins.qkeee_erp`: the plugin directory is the
package path, so the plugin's relative imports (`from .qkeee_erp.core import
client`) resolve the same way. Tests import `qkeee_erp_plugin.erp_tools`,
`qkeee_erp_plugin.qkeee_erp.core.client`, and so on.

Run with `python -m pytest plugins/qkeee-erp` from the repo root. No Hermes
install needed: the tests pass in fake session readers and settings.

Do not also put the plugin directory on sys.path: a module imported under a
second name loads twice (two operation registries, patches that miss)."""

import importlib.util
import os
import sys

_NAME = "qkeee_erp_plugin"
_PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

if _NAME not in sys.modules:
    _spec = importlib.util.spec_from_file_location(
        _NAME, os.path.join(_PLUGIN_DIR, "__init__.py"), submodule_search_locations=[_PLUGIN_DIR])
    _module = importlib.util.module_from_spec(_spec)
    sys.modules[_NAME] = _module
    _spec.loader.exec_module(_module)

# Register every domain's allowlist and operations up front, as the gateway
# does (erp_tools imports execute_write). Tests that loop over the operation
# registry then see the full registry whatever ran first.
importlib.import_module(_NAME + ".qkeee_erp.execute_write")
