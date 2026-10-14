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
# notify is not a business domain (no generic op): its one operation logs
# the task summary email.
from .domains import (  # noqa: F401
    accounts,
    fixed_assets,
    hr_payroll,
    inventory,
    mis,
    notify,
    procurement,
    sales,
    system_admin,
)
from .core import client, operations

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


def list_ops(op: str = None, domain: str = None) -> list:
    """Every CLI operation, or only `op` (one key) / `domain` (key prefix,
    e.g. "inventory", "unscoped"): the full list is ~14k chars of context
    when the agent needs one operation."""
    ops = operations.list_operations()
    keys = sorted(o.key for o in ops)
    if op is not None:
        ops = [o for o in ops if o.key == op]
    if domain is not None:
        ops = [o for o in ops if o.key.split(".", 1)[0] == domain]
    if not ops:
        raise client.InvalidArgumentsError(
            f"list_ops: no operation matches op={op!r} domain={domain!r}. Registered: {keys}.")
    out = []
    for o in ops:
        out.append({
            "op": o.key, "summary": o.summary, "credential": o.credential,
            "confirmation": _confirmation_label(o),
            "owns": sorted(f"{d} {a}" for d, a in o.owns),
            "args": o.args_help,
            "example_args": o.example_args,
        })
    return out
