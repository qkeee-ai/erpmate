"""
qkeee-erp write-operation registry: importing this module imports every
domain module, which registers its allowlist and its operations.
`list_ops()` describes every operation. Writes run only through the
erp_execute_write tool (erp_tools.py) and the Operator CLI's
`hermes qkeee-erp render` / `execute-write` (cli.py), which runs that
tool's handler.

Every write is a named operation (core/operations.py) run through one
pipeline: mode -> requester -> allowlist -> ownership -> preconditions ->
confirmation token -> RBAC -> audit -> send.
"""

# Import every domain module for its side effect: registering its allowlist
# and its operations. manufacturing/doc_extraction have no write path.
from .domains import (  # noqa: F401
    accounts,
    fixed_assets,
    hr_payroll,
    inventory,
    mis,
    procurement,
    sales,
    system_admin,
)
from .core import operations

_DOMAIN_MODULES = {
    m.DOMAIN_NAME: m for m in (accounts, fixed_assets, hr_payroll, inventory, mis,
                               procurement, sales, system_admin)
}
_KNOWN_DOMAINS = set(_DOMAIN_MODULES)


def _confirmation_label(op) -> str:
    """Which actions of this operation need a rendered confirmation —
    computed from the operation's own policy, never hand-maintained."""
    if not op.generic:
        return "none" if op.token_policy == operations.POLICY_NONE else "always"
    gated = [a for a in operations.RESOURCE_ACTIONS
             if operations.requires_confirmation(op.key, {"action": a})]
    if len(gated) == len(operations.RESOURCE_ACTIONS):
        return "always"
    return f"per action: {'/'.join(gated)}" if gated else "none"


def list_ops() -> list:
    out = []
    for op in operations.list_operations():
        out.append({
            "op": op.key, "summary": op.summary, "credential": op.credential,
            "confirmation": _confirmation_label(op),
            "owns": sorted(f"{d} {a}" for d, a in op.owns),
            "args": op.args_help,
            "example_args": op.example_args,
        })
    return out
