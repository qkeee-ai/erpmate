"""The plugin's own settings (agents .scratch/qkeee-erp-plugin-profile-split,
issue 11; ADR 0005).

The plugin reads `plugins.entries.qkeee-erp.settings` only:
- `active_env`: the default Instance tag (a tool's `tag` argument wins).
- `mode`: `read-only` (default) or `read-write`.

The legacy `skills.config.qkeee_erp` section was Profile config. While a
config still holds it, setup is pending: the plugin registers no `erp_*`
tools and its ERP commands refuse. Setup step S2 (`migrate_legacy_config`)
moves it once. The plugin never writes config on load (spec D7).

Pure functions over a config dict, so the tests need no Hermes.
"""

from __future__ import annotations

PLUGIN_ID = "qkeee-erp"
KEYS = ("active_env", "mode")
SETUP_HINT = "run `hermes qkeee-erp setup`"
DROPPED = ("scripts_dir",)  # issue 04: the library is in the plugin package now


def _mapping(value) -> dict:
    return value if isinstance(value, dict) else {}


def _entry(cfg) -> dict:
    return _mapping(_mapping(_mapping(_mapping(cfg).get("plugins")).get("entries")).get(PLUGIN_ID))


def _legacy(cfg) -> dict:
    return _mapping(_mapping(_mapping(_mapping(cfg).get("skills")).get("config")).get("qkeee_erp"))


def plugin_settings(cfg) -> dict:
    """`plugins.entries.qkeee-erp.settings`, or {}."""
    return dict(_mapping(_entry(cfg).get("settings")))


def setup_pending(cfg) -> str | None:
    """Why the plugin must not serve ERP calls yet, or None."""
    if _legacy(cfg):
        return (f"the profile config still holds the legacy skills.config.qkeee_erp section; "
                f"{SETUP_HINT} to move it to plugins.entries.{PLUGIN_ID}.settings")
    return None


def migrate_legacy_config(cfg: dict) -> list[str]:
    """Setup step S2, in place. Copies each legacy value the plugin section
    does not already hold, removes the legacy section and drops `scripts_dir`.
    Returns one line per change; [] when there is nothing to do."""
    changes = []
    legacy = _legacy(cfg)
    entry = _entry(cfg)
    current = _mapping(entry.get("settings"))
    copied = {k: legacy[k] for k in KEYS if k in legacy and k not in current}
    dropped = [k for k in DROPPED if k in current]
    if copied or dropped:
        parent = cfg
        for key in ("plugins", "entries", PLUGIN_ID, "settings"):
            # YAML `key:` with no value loads as None: replace it, never index it.
            if not isinstance(parent.get(key), dict):
                parent[key] = {}
            parent = parent[key]
        settings = parent
        for key, value in copied.items():
            settings[key] = value
            changes.append(f"plugins.entries.{PLUGIN_ID}.settings.{key} = {value!r} "
                           f"(from skills.config.qkeee_erp)")
        for key in dropped:
            del settings[key]
            changes.append(f"removed plugins.entries.{PLUGIN_ID}.settings.{key}")
    if legacy:
        del cfg["skills"]["config"]["qkeee_erp"]
        changes.append("removed skills.config.qkeee_erp")
    return changes
