#!/usr/bin/env python3
"""
Schema-first attribute mapping for every create/update. Fetches the live
doctype schema (`discover.py meta`) and matches whatever fields a caller
or an extraction step proposes against it, so a field is only dropped
because it genuinely isn't on the live doctype — never because a domain
doc's hand-curated field list forgot to mention it.

## Design decisions

- **Lives at the call-site tier (execute_write.py calls in), not inside
  mutate_resource()/gated_mutate_resource().** Doctype-field-shape
  judgment is call-site judgment, not ERP-agnostic connector logic — the
  same layering item_write_helpers.py already draws for Item's business
  defaults, and 01-connectivity.md draws for domain judgment generally.
  Embedding this in mutate_resource() would also mean every one of
  core/test_client.py's existing tests (which build payloads with
  already-correct fieldnames and mock at the HTTP layer) would need a new
  DocType-meta mock just to keep passing, for a check that changes
  nothing about a payload that was already correct. execute_write.py is
  the single write entry point every real write goes through, so "runs
  on every create/update, unconditionally" holds in practice without
  touching the connector's own tested surface. A caller that mutates
  outside execute_write.py — e.g. procurement.py's own
  `_create_linked_kyc_record()`, which calls `core_client.mutate_resource`
  directly for the Address/Contact half of a Supplier-KYC create — does
  NOT get a second, redundant schema-mapping pass at THAT call site;
  instead, `execute_write.py`'s own `_apply_kyc_schema_mapping()` maps
  `kyc["address"]`/`kyc["contact"]` against Address's/Contact's own live
  schema BEFORE dispatch, so by the time `_create_linked_kyc_record()`
  actually fires, the sub-payload it receives is already schema-mapped —
  same "call-site tier, not the connector" placement as everything else
  in this module, just at the point where the KYC sub-payload's shape is
  actually known (`execute_write.py`'s `--kyc` handling), not inside
  `procurement.py`'s own `mutate()`.
- **Exact/normalized match auto-applies; fuzzy/synonym matches are
  suggestions, never silently written.** A candidate key that equals a
  live fieldname, or normalizes to one (case/space/hyphen/underscore-
  insensitive), maps automatically — this is the working behavior for a
  caller that already uses correct fieldnames, just now confirmed against
  the live schema instead of assumed. Anything beyond that (a small
  hardcoded synonym table for common India-compliance abbreviations —
  HSN, GSTIN, PAN, TIN) is surfaced as a `suggested_mappings` entry that
  needs an explicit `confirmed_mappings` entry to ever reach a payload:
  fuzzy matching risks mapping the wrong field, so it gets a confirmation
  step, not best-effort silence.
- **Degrade to passthrough (payload sent unmapped) on any schema-fetch
  failure — warn, don't refuse the write.** A correctly least-privileged
  bot can legitimately lack System-Manager-level DocType read and 403 on
  the schema fetch; refusing every write instance-wide over that would be
  a worse regression than an occasional unmapped field, matching the
  existing best-effort posture this connector already applies to audit
  logging and to a write that proceeds under an RBAC-precheck-unreliable
  warning. A fetch failure is cached per (tag, doctype) so a doctype this
  bot can't read metadata for doesn't retry the fetch on every subsequent
  write in the same process.
- **Cache scope: in-process, per (tag, doctype) — not cross-process or
  disk-backed.** execute_write.py is a fresh process per CLI invocation
  (one write per call), so an in-process cache only pays off for multiple
  writes to the same doctype within one process — still worth it (mirrors
  core/client.py's own `_BOT_IDENTITY_CACHE`/`_RBAC_PRECHECK_TRUST_CACHE`
  pattern) but a persistent disk cache was deliberately not built: one
  extra GET per write is the same order of cost this connector already
  pays for the RBAC pre-check and the audit pre-image fetch on every
  single write, and a disk cache would add staleness/invalidation
  questions a one-write-per-process pattern doesn't need answered yet.
- **KYC interaction: complementary, not merged.** procurement.py's
  `IncompleteSupplierKYCError` gate answers a *policy* question (is KYC
  data present, or was a waiver explicitly confirmed) — this module
  answers a different, *mechanical* one (do the fields actually offered
  land on real schema fields). Both run, unchanged relative to each
  other: the KYC gate first, then — for a Supplier/Address/Contact create
  that goes through execute_write.py — this module maps whatever fields
  were supplied against that doctype's live schema, the same as any other
  create.
- **doc-extraction handoff: schema-mapping consumes doc-extraction's
  staged-report shape directly when given one** (`match_staged_report()`
  below) — `{"field", "value", "confidence", "row"?}` per
  doc-extraction.md step 6 — refusing on a field missing
  `confidence`/`value` (`MalformedStagedReportError`) and flagging — not
  just warning about — a field that is BOTH `low`-confidence AND
  unmatched against the live schema, since neither signal alone would
  catch it. `execute_write.py`'s plain `--payload` path (no staged report
  given) still runs the plain dict-keyed matcher — warn-only, never
  blocks — since there's no per-field confidence to escalate on there.
- **Known adjacent gap, not fixed here:** doc-extraction.md step 6 says
  to "render the staged report through a script" — no such script
  (`render_*.py` or otherwise) exists anywhere in this tree yet.
  `match_staged_report()` gives that future script something real to hand
  off to, but does not itself create the missing renderer.

Pure functions throughout, except `get_doctype_schema()`/
`map_payload_for_write()` — the only two that talk to ERPNext (via
discover.py's already-gated `doctype_meta()`). Everything else is a plain
data transform, easy to unit-test without a live instance.
"""

import os
import re
import sys

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from core.client import ConnectorError  # noqa: E402
import discover  # noqa: E402

# Small, deliberately narrow synonym table — see module docstring's
# "Exact/normalized match auto-applies; fuzzy/synonym matches are
# suggestions" decision. A hit here is NEVER auto-applied, only offered
# as a suggested_mappings entry — expand cautiously; every entry here is
# a live opportunity to map the wrong field with false confidence.
SYNONYM_HINTS = {
    "HSN": ("gst_hsn_code", "hsn_code"),
    "HSN CODE": ("gst_hsn_code", "hsn_code"),
    "HSN/SAC": ("gst_hsn_code", "hsn_code"),
    "GSTIN": ("gstin",),
    "GST NUMBER": ("gstin",),
    "GST NO": ("gstin",),
    "PAN": ("pan",),
    "PAN NUMBER": ("pan",),
    "TAX ID": ("tax_id", "gstin", "pan"),
    "TIN": ("tax_id",),
}


def _normalize(s: str) -> str:
    """Case/space/hyphen/underscore-insensitive key for near-exact
    matching — 'HSN Code', 'hsn-code', 'hsn_code' all normalize the same.
    Deliberately NOT stemming/soundex/anything fuzzier than this — see
    module docstring."""
    return re.sub(r"[\s\-_/]+", "", s or "").strip().lower()


class MalformedStagedReportError(ConnectorError):
    """Raised by match_staged_report() when a field is missing its
    `confidence` key or has no `value` key at all — doc-extraction.md
    step 6's own render-refusal rule, code-enforced here since no
    staged-report renderer exists yet (see module docstring)."""


def match_fields(schema_fields: list, candidate: dict) -> dict:
    """Match a flat candidate dict's keys against a live doctype's field
    list — the shape `discover.doctype_meta()` returns under `"fields"`
    (each entry carrying at least `fieldname`, usually `label`).

    Returns:
      {
        "mapped": {<live_fieldname>: value, ...},           # tier 1+2, auto-applied
        "matched_via": {<live_fieldname>: "exact"|"normalized", ...},
        "suggested_mappings": [
            {"candidate_key": ..., "suggested_fieldname": ..., "value": ...},
            ...
        ],                                                    # tier 3, needs confirmation
        "unmatched": [<candidate_key>, ...],                  # no schema field found at all
      }

    A `candidate` key that already exactly matches a live fieldname is
    unaffected in behavior — an existing caller passing correct
    fieldnames sees that key mapped through unchanged, just now confirmed
    against the live schema rather than assumed.
    """
    by_exact = {f["fieldname"]: f for f in schema_fields if f.get("fieldname")}
    by_normalized_fieldname = {
        _normalize(f["fieldname"]): f["fieldname"] for f in schema_fields if f.get("fieldname")
    }
    by_normalized_label = {
        _normalize(f["label"]): f["fieldname"]
        for f in schema_fields if f.get("label") and f.get("fieldname")
    }

    mapped, matched_via, suggested, unmatched = {}, {}, [], []

    for key, value in (candidate or {}).items():
        if key in by_exact:
            mapped[key] = value
            matched_via[key] = "exact"
            continue
        norm = _normalize(key)
        target = by_normalized_fieldname.get(norm) or by_normalized_label.get(norm)
        if target:
            mapped[target] = value
            matched_via[target] = "normalized"
            continue
        hint_targets = SYNONYM_HINTS.get(key.strip().upper(), ())
        live_hint = next((t for t in hint_targets if t in by_exact), None)
        if live_hint:
            suggested.append({"candidate_key": key, "suggested_fieldname": live_hint, "value": value})
            continue
        unmatched.append(key)

    return {"mapped": mapped, "matched_via": matched_via,
            "suggested_mappings": suggested, "unmatched": unmatched}


def apply_confirmed_mappings(result: dict, confirmed_mappings: dict) -> dict:
    """Fold explicitly-confirmed tier-3 suggestions into `mapped` — the
    only way a `suggested_mappings` entry ever reaches a payload sent to
    ERPNext. `confirmed_mappings` is `{candidate_key: live_fieldname}`,
    normally copied straight from a `suggested_mappings` entry the caller
    showed the user and got an explicit yes on — a mismatched target
    (confirming a different fieldname than what was actually suggested)
    is refused rather than silently substituted. Returns a NEW dict;
    `result` is never mutated."""
    mapped = dict(result.get("mapped", {}))
    still_suggested = []
    for entry in result.get("suggested_mappings", []):
        confirmed_target = (confirmed_mappings or {}).get(entry["candidate_key"])
        if confirmed_target and confirmed_target == entry["suggested_fieldname"]:
            mapped[confirmed_target] = entry["value"]
        else:
            still_suggested.append(entry)
    out = dict(result)
    out["mapped"] = mapped
    out["suggested_mappings"] = still_suggested
    return out


def match_staged_report(schema_fields: list, staged_fields: list) -> dict:
    """Consumes doc-extraction's staged-report shape directly: a list of
    `{"field", "value", "confidence", "row"?}` dicts (doc-extraction.md
    step 6). Refuses (`MalformedStagedReportError`) on any entry missing
    a `confidence` key or a `value` key at all — the same rule
    doc-extraction.md's own render step is supposed to enforce. A
    `row`-tagged entry (repeating line item) is matched by its own
    `field` name only; `confidence_by_field`/`row` are carried through on
    the result for the caller to regroup by.

    Returns the same shape as `match_fields()`, plus a `high_risk` list:
    candidate keys that are BOTH `confidence == "low"` AND unmatched
    against the live schema — the specific worst case, since neither
    signal alone would catch it. A high-risk field is never in `mapped`,
    and never in `suggested_mappings` either, even if a synonym hint
    would otherwise apply — it needs a human's eyes before anything is
    offered, not an auto-suggestion on top of a low-confidence value.
    """
    for entry in staged_fields or []:
        if "confidence" not in entry or "value" not in entry:
            raise MalformedStagedReportError(
                f"Refusing to schema-map staged field {entry.get('field')!r}: missing a "
                f"'confidence' or 'value' key. doc-extraction.md step 6 requires every field "
                f"to carry both before it reaches a write path — fix the extraction/staging "
                f"step, don't patch it up here."
            )

    confidence_by_field = {e["field"]: e.get("confidence") for e in (staged_fields or [])}
    row_by_field = {e["field"]: e.get("row") for e in (staged_fields or []) if e.get("row")}
    candidate = {e["field"]: e["value"] for e in (staged_fields or []) if e.get("value") not in (None, "")}

    result = match_fields(schema_fields, candidate)

    # "Unmatched against the live schema" for high_risk purposes means
    # "not tier-1/2 auto-mapped" -- a synonym-only hit (tier 3, still in
    # suggested_mappings at this point) is just as unconfirmed as a
    # genuinely unmatched key, so both buckets are checked here. Iterates
    # `candidate` (insertion-ordered) rather than a set for a deterministic
    # result order.
    not_auto_mapped = set(result["unmatched"]) | {
        s["candidate_key"] for s in result["suggested_mappings"]
    }
    high_risk = [key for key in candidate
                 if key in not_auto_mapped and confidence_by_field.get(key) == "low"]
    if high_risk:
        result["unmatched"] = [k for k in result["unmatched"] if k not in high_risk]
        result["suggested_mappings"] = [
            s for s in result["suggested_mappings"] if s["candidate_key"] not in high_risk
        ]
    result["high_risk"] = high_risk
    result["confidence_by_field"] = confidence_by_field
    result["row_by_field"] = row_by_field
    return result


# --------------------------------------------------------------------------
# I/O layer — the only two functions here that talk to ERPNext.
# --------------------------------------------------------------------------

_SCHEMA_CACHE: dict = {}


def get_doctype_schema(tag: str, doctype: str, *, requested_by: str, **discover_kwargs):
    """Fetch + cache `discover.doctype_meta()`'s field list for (tag,
    doctype), in-process only — see module docstring's caching decision.
    A fetch failure is cached too (as `None`) so a doctype this bot can't
    read metadata for doesn't retry the fetch on every subsequent write
    in the same process. Returns `(fields_or_None, error_message_or_None)`
    — caller decides how to degrade on a `None` schema."""
    cache_key = (tag, doctype)
    if cache_key not in _SCHEMA_CACHE:
        try:
            meta = discover.doctype_meta(tag, doctype, requested_by=requested_by, **discover_kwargs)
            _SCHEMA_CACHE[cache_key] = meta.get("fields", [])
        except ConnectorError as e:
            _SCHEMA_CACHE[cache_key] = ("__error__", str(e))
    cached = _SCHEMA_CACHE[cache_key]
    if isinstance(cached, tuple) and cached and cached[0] == "__error__":
        return None, cached[1]
    return cached, None


def map_payload_for_write(tag: str, doctype: str, payload: dict, *, requested_by: str,
                           staged_fields: list = None, confirmed_mappings: dict = None,
                           **discover_kwargs) -> dict:
    """The one call execute_write.py makes before every create/update.
    Fetches the live schema (best-effort — degrades to passthrough on
    failure, see module docstring) and maps `payload`'s own keys against
    it, folding in any `confirmed_mappings` the caller already has a yes
    on. When `staged_fields` is given (doc-extraction's confidence-rated
    output), matches THAT instead of `payload`'s keys — see
    `match_staged_report()`.

    Returns:
      {
        "payload": <dict to actually send — "mapped" fields only>,
        "status": "ok" | "unavailable" | "high_risk",
        "detail": str | None,
        "suggested_mappings": [...], "unmatched": [...], "high_risk": [...],
      }

    `status == "unavailable"` means the schema fetch itself failed —
    `payload` is the caller's original dict, unchanged, so the write can
    still proceed exactly as it would have before this module existed.
    `status == "high_risk"` is the one case worth a caller hard-refusing
    on — a staged field that's both low-confidence and schema-unmatched;
    `payload` still omits those fields either way. Every other status
    still carries a usable `payload`; the caller decides how loudly to
    warn about `suggested_mappings`/`unmatched`.
    """
    schema_fields, fetch_error = get_doctype_schema(tag, doctype, requested_by=requested_by, **discover_kwargs)
    if schema_fields is None:
        return {"payload": dict(payload or {}), "status": "unavailable", "detail": fetch_error,
                "suggested_mappings": [], "unmatched": [], "high_risk": []}

    if staged_fields is not None:
        result = match_staged_report(schema_fields, staged_fields)
    else:
        result = dict(match_fields(schema_fields, payload or {}), high_risk=[])

    if confirmed_mappings:
        result = apply_confirmed_mappings(result, confirmed_mappings)

    status = "high_risk" if result["high_risk"] else "ok"
    return {"payload": result["mapped"], "status": status, "detail": None,
            "suggested_mappings": result["suggested_mappings"],
            "unmatched": result["unmatched"], "high_risk": result["high_risk"]}
