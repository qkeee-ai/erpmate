#!/usr/bin/env python3
"""
qkeee-erp-associate core — confirmation-token primitives, and the RENDER
CLI that computes a token for any registered write operation.

A gated write is two steps, never one turn:

1. **Render** — `confirm_token.py render --op <key> --args '<json>' ...`
   prepares the EXACT request the operation will send (schema mapping,
   defaults, live facts such as the record's `modified`) and prints it,
   together with the full `args` to pass back, a `confirmation_token`, its
   `issued_at`, and a 6-character `confirmation_code`. The agent shows the
   prepared request and the code to the user.
2. **Execute** — after the user's own reply containing the code,
   `execute_write.py --op <key> --args '<printed args>'
   --confirmation-token ... --issued-at ... --user-confirmation-text
   '<their reply>'`. The pipeline re-prepares the request, recomputes the
   token over it (core/operations.py operation_token()) and refuses on any
   difference.

This module owns the primitives:
  - compute_token(**fields)  — deterministic hash over arbitrary facts.
  - is_fresh(issued_at, ...) — reject stale (replayed) or implausibly-
    future tokens.
  - confirmation_code(token) — the short code shown to the user.

There is ONE token constructor for writes — core/operations.py
operation_token() — built on compute_token(). (The former per-domain
constructors and advisory_write_token() were folded into it — write-path
hardening ticket 07.)
"""

import hashlib
import json
import os
import sys
import time

# 15 minutes: long enough to cover a realistic render-then-confirm human
# turnaround, short enough that a token can't be usefully replayed against
# facts that have since changed (e.g. a revalued asset, an amended draft).
DEFAULT_TOKEN_TTL_SECONDS = 900

# Small tolerance for clock skew between the process that issued the token
# and the process that later validates it — not a security boundary, just
# enough to avoid rejecting a legitimately-fresh token over a few seconds
# of drift.
CLOCK_SKEW_TOLERANCE_SECONDS = 30


def compute_token(**fields) -> str:
    """Deterministic short token over the given facts."""
    canonical = json.dumps(fields, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def is_fresh(issued_at: int, max_age_seconds: int = DEFAULT_TOKEN_TTL_SECONDS,
             now: int = None) -> bool:
    """True if issued_at is within [now - max_age_seconds, now + skew-tolerance].

    Rejects both stale tokens (replay of an old render/confirm) and
    implausibly-future ones (clock manipulation / fabricated issued_at).
    """
    now = int(now) if now is not None else int(time.time())
    age = now - int(issued_at)
    return -CLOCK_SKEW_TOLERANCE_SECONDS <= age <= max_age_seconds


def confirmation_code(token: str) -> str:
    """A short, human-typeable code derived from a confirmation token —
    6 uppercase hex characters, e.g. "3F0A9C".

    What this closes, and what it deliberately doesn't: a token is
    unsalted and computable by anyone, including the same process that
    then verifies it — it proves a request matches what was rendered,
    never that a human reviewed that render. This code is not secret
    either (deriving it from the token an agent already holds is
    trivial), so it does not defend against an agent that deliberately
    fabricates a user reply. What it does do: turn "pass the token back"
    (possible with no human involved) into "get this specific short code
    into an actual inbound message from the user" — a concrete, checkable
    discipline point, the same kind of convention this skill already
    leans on for `requested_by`. Whatever renders the request for the
    user MUST display this code and ask them to include it in their
    reply, e.g. "reply 'yes 3F0A9C' to confirm"."""
    return token[:6].upper()


def _cli():
    """`render`: prepare a registered operation's exact request and print
    it with its confirmation token — see the module docstring."""
    import argparse

    scripts_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    import execute_write  # noqa: F401 — imports every domain module (registers operations)
    from core import operations
    from core.client import ConnectorError, _parse_json_arg, resolve_requested_by

    p = argparse.ArgumentParser(description="Render a write operation for user confirmation.")
    sub = p.add_subparsers(dest="command", required=True)
    r = sub.add_parser("render", help="prepare the exact request and print token + code")
    r.add_argument("--op", required=True, help="operation key, see `execute_write.py --list-ops`")
    r.add_argument("--args", default="{}", help="JSON object of the operation's arguments")
    r.add_argument("--tag", required=True)
    r.add_argument("--requested-by", required=True,
                   help="the requester the token is bound to — the same value the execute step uses")
    r.add_argument("--session-id")
    r.add_argument("--domain-code", default="qkeee-erp-associate")
    r.add_argument("--channel")
    r.add_argument("--channel-metadata")
    r.add_argument("--prompt-summary")
    r.add_argument("--latest-prompt")
    a = p.parse_args()

    try:
        op_args = _parse_json_arg("--args", a.args, dict) or {}
        ctx = operations.WriteContext(
            tag=a.tag, requested_by=resolve_requested_by(a.requested_by),
            session_id=a.session_id, domain_code=a.domain_code, channel=a.channel,
            channel_metadata=_parse_json_arg("--channel-metadata", a.channel_metadata, dict),
            prompt_summary=a.prompt_summary, latest_prompt=a.latest_prompt,
        )
        out = operations.prepare_only(a.op, op_args, ctx)
    except ConnectorError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(3)
    for note in out.get("notes", []):
        print(f"WARN: {note}", file=sys.stderr)
    if out["policy"] == operations.POLICY_NONE:
        out["_note"] = "This operation needs no confirmation token for these args."
    else:
        out["_note"] = ("Show `request` and `confirmation_code` to the user. Execute with "
                        "execute_write.py --op <op> --args '<args above, unchanged>' "
                        "--confirmation-token/--issued-at as printed and "
                        "--user-confirmation-text '<the user's own reply>'.")
    print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    _cli()
