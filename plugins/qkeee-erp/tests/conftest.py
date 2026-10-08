"""Puts the plugin's own modules and the qkeee-erp-associate skill scripts
on sys.path, as the plugin's register() does in the gateway. Run with
`python -m pytest plugins/qkeee-erp/tests` from the repo root. No Hermes
install needed: the tests pass in fake session readers and settings."""

import os
import sys

_PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REPO = os.path.dirname(os.path.dirname(_PLUGIN_DIR))
_SCRIPTS = os.path.join(_REPO, "skills", "qkeee-erp", "qkeee-erp-associate", "scripts")
for _path in (_PLUGIN_DIR, _SCRIPTS):
    if _path not in sys.path:
        sys.path.insert(0, _path)
