"""
qkeee-erp.env's required/optional key manifest — the machine-readable
twin of skills/qkeee-erp/qkeee-erp-associate/qkeee-erp-associate.env.example.
A new required or optional var belongs in BOTH places: this module (so
init_bot.py/health_check() can report a gap) and the .env.example (so a
human setting up credentials from scratch sees it). tests/test_env_defs.py
round-trips the two so they can't drift apart the way doctype_defs.py and
a live instance's schema did (the bug that motivated this module).

Pure data + pure functions, no imports — same posture as doctype_defs.py.
Deliberately never reads or returns a credential's VALUE: every check
here is presence-only (is the key set at all), consistent with
init_bot.ensure_qkeee_env_file_skeleton()'s non-negotiable that this
tooling never writes, prints, or otherwise handles a secret value.

Keys below are bare suffixes (e.g. "BASE_URL"), not full var names — a
caller builds the real name with core.client._tag_env_var(tag, suffix)
for a per-tag key, or reads a global key (OPTIONAL_GLOBAL) as-is. This
module doesn't import core.client itself: core.client.health_check()
calls into this module, so the reverse import would cycle.
"""

REQUIRED_PER_TAG = {
    "BASE_URL": "ERPNext instance URL the bot/admin keys call.",
    "API_KEY": "Bot service-account API key (steady-state business calls).",
    "API_SECRET": "Bot service-account API secret.",
    "ADMIN_API_KEY": "System-Manager admin key pair for the RBAC precheck and "
                      "init-bot (agents ADR 0003 amendment).",
    "ADMIN_API_SECRET": "Admin key pair's secret half.",
}

OPTIONAL_PER_TAG = {
    "ADMIN_EMAIL": "ERP admin cc'd on a kanban job's approval email; audit/oversight only.",
    "OPS_CHAT_WEBHOOK": "Google Chat webhook for lcm-corruption-check findings, alongside "
                         "ADMIN_EMAIL.",
    "ALLOW_INSECURE": "Skip TLS verification for this tag (test/demo instances only).",
    "ENVIRONMENT": "'non-production' extends the confirm-token lifetime to 60 min; else "
                   "production (15 min).",
    "KYC_TAX_ID_FIELD": "Tax-ID field on a Supplier's KYC Address when it isn't "
                        "gstin/tax_id/pan.",
}

OPTIONAL_GLOBAL = {
    "AUDIT_MASK_FIELDS": "Extra comma-separated field names masked in audit payloads.",
}


def missing_required_keys(present_suffixes) -> list:
    """REQUIRED_PER_TAG suffixes not in `present_suffixes` — the bare
    suffixes (BASE_URL, not QKEEE_ERP_<TAG>_BASE_URL) a caller found set
    for one tag, typically:

        env = core_client._qkeee_env()
        present = {s for s in env_defs.REQUIRED_PER_TAG
                   if core_client._tag_env_var(tag, s) in env}
        gaps = env_defs.missing_required_keys(present)

    Sorted for stable output. Pure: never sees or returns a value, only
    whether a key is set."""
    return sorted(set(REQUIRED_PER_TAG) - set(present_suffixes))
