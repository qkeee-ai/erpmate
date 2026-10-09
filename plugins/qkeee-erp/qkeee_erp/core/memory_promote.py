#!/usr/bin/env python3
"""
qkeee-erp-associate — memory promotion.

WHAT THIS MODULE IS NOT, first — because it shapes everything below: this
is NOT a client of Hermes' `memory`/`skill_manage` tools in the sense of
importing and calling them directly. Those two tools are implemented in
`hermes-agent`'s own process (`tools/memory_tool.py`'s `memory_tool()` /
`MemoryStore`, and `tools/skill_manager_tool.py`'s `skill_manage()`) and
require in-process state this module cannot reach from where it runs:

  - `memory_tool()` needs a live `MemoryStore` instance (built via
    `load_on_disk_store()`, which itself needs `hermes_cli.config` for
    the profile's char-limit overrides) plus the write-approval gate's
    session/origin context (`tools/write_approval.py`).
  - `skill_manage()` needs the same write-approval gate context, the
    background-review provenance contextvar (`tools/skill_provenance.py`)
    that decides whether THIS call is foreground (user-directed) or
    autonomous curation, and the security scanner
    (`tools/skills_guard.py`) — none of which exist outside a live
    `hermes-agent` process.

This module's own scripts run the way every other `qkeee-erp-associate`
script does: as a subprocess under Hermes' `execute_code`/`terminal`
sandbox, a SEPARATE process from the agent's own tool-calling loop, with
no `hermes-agent` package on its path. A subprocess script cannot call a
tool that only exists as a function bound into the live agent's own LLM
tool-calling loop.

So instead of calling `skill_manage` directly, this module's job is:
**redact, format, and hand back a plan** describing the exact
`skill_manage`/`memory` tool calls the calling agent turn should make
itself, using its own native tool access. `build_promotion_plan()` is the
single entry point; its output is a JSON-serializable list of
`{"tool": "skill_manage"|"memory", ...kwargs}` dicts, each one a
direct, ready-to-issue call in the real tool's own parameter shape
(`action`, `name`, `content`, `file_path`, `file_content`, ... for
skill_manage; `action`, `target`, `content`, `old_text` for memory) — the
agent reads this script's stdout and then makes those tool calls itself,
in the same turn, rather than this script attempting to make them.

Layout:
  qkeee-erp-learned/<env-tag>/
    SKILL.md
    references/
      environment.md
      doctypes-catalog.md
      custom-apps/<app-slug>.md      (one per custom app found)
      non-erpnext/<system-slug>.md   (one per non-ERPNext system found)

Redaction: every free-text value in the findings dict is passed through
core.client.redact_pii()/_redact_pii_deep() before it's ever formatted
into content bound for a durable, Hermes-discoverable location —
skill_manage's own security scanner looks for dangerous code patterns and
prompt-injection, not sensitive-data leakage. This pass is load-bearing,
not a courtesy.
"""

import json
import re
import sys
from datetime import datetime, timezone

from .client import redact_pii, _redact_pii_deep

LEARNED_SKILL_PREFIX = "qkeee-erp-learned"

# Skill names must be lowercase letters/digits/hyphens/dots/underscores per
# skill_manager_tool.py's VALID_NAME_RE. An env tag that doesn't already fit this shape
# (e.g. it came from a user-typed --tag with underscores, mixed case) is
# sanitized here so the resulting skill name is guaranteed valid, rather
# than discovering a rejected skill_manage(create) call at execution time.
_VALID_NAME_CHARS_RE = re.compile(r"[^a-z0-9._-]+")


def sanitize_env_tag_for_skill_name(env_tag: str) -> str:
    """Lowercase and strip anything skill_manage's VALID_NAME_RE would
    reject, collapsing runs of invalid characters to a single hyphen. Does
    NOT change the env tag used for QKEEE_ERP_<TAG>_* env vars (those stay
    whatever the user configured) — this is only for the derived skill
    name qkeee-erp-learned/<slug>."""
    slug = env_tag.strip().lower()
    slug = _VALID_NAME_CHARS_RE.sub("-", slug)
    slug = slug.strip("-._") or "env"
    return slug


def learned_skill_name(env_tag: str) -> str:
    """Human-readable qualified id, e.g. 'qkeee-erp-learned/example-env' —
    used in prose/frontmatter/breadcrumbs ONLY. NOT a valid skill_manage
    `name=` argument: skill_manager_tool.py's VALID_NAME_RE
    (^[a-z0-9][a-z0-9._-]*$) rejects '/' outright. skill_manage nests
    skills via a separate `category=` parameter instead
    (_resolve_skill_dir() -> skills_dir/category/name) — see
    learned_skill_manage_args() for the actual create-call shape. The
    resulting ON-DISK layout is identical either way
    (skills/qkeee-erp-learned/<env-tag>/) — only the tool-call argument
    shape differs from what a literal slash-joined name would suggest."""
    return f"{LEARNED_SKILL_PREFIX}/{sanitize_env_tag_for_skill_name(env_tag)}"


def learned_skill_manage_args(env_tag: str) -> dict:
    """The actual {"name": ..., "category": ...} kwargs skill_manage()
    needs — see learned_skill_name()'s docstring. `name` is used alone
    (no category) for edit/patch/write_file/delete calls after creation:
    skill_manager_tool.py's _find_skill() locates a skill by leaf
    directory name via rglob across every skills root, regardless of
    nesting, so category is only needed on the initial create() call."""
    return {"name": sanitize_env_tag_for_skill_name(env_tag), "category": LEARNED_SKILL_PREFIX}


def redact_findings(findings: dict) -> dict:
    """Recursively redact_pii() every string value in a findings dict
    before it's formatted into durable content. Uses the same
    _redact_pii_deep() the connector already applies to channel_metadata
    — single source of redaction, per 00-conventions.md's GRC baseline,
    never re-implemented here."""
    return _redact_pii_deep(findings)


def _now_iso_date() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# --------------------------------------------------------------------------
# Content formatters — each returns the exact string that becomes a
# skill_manage(create)/write_file(file_content=...) argument. None of
# these write to disk; they only build strings.
# --------------------------------------------------------------------------

def format_learned_skill_md(env_tag: str) -> str:
    """SKILL.md for the qkeee-erp-learned/<env-tag> satellite skill —
    deliberately tiny: a pointer, not a copy of the associate's own
    router. skill_manage(create) requires valid frontmatter (name +
    description) and non-empty body content; this satisfies both with the
    minimum needed for Hermes' own skill discovery to surface it during
    qkeee-erp-associate's activation sequence step 2."""
    slug = sanitize_env_tag_for_skill_name(env_tag)
    name = learned_skill_name(env_tag)
    return f"""---
name: {name}
description: "Learned notes for ERPNext environment tag '{env_tag}' — versions, custom doctypes, non-ERPNext systems."
---

# {name}

Durable, per-environment knowledge for `qkeee-erp-associate`'s `{env_tag}`
tag — created and updated via `skill_manage` by
`scripts/core/memory_promote.py`'s promotion plan. This is a satellite
skill, not a copy of the associate itself: `qkeee-erp-associate` stays
protected/externally-owned (see its own SKILL.md status note); this
skill is the deliberately-open counterpart Hermes' background-review pass
may evolve over time as more is learned about this specific environment.

## What's here

- `references/environment.md` — Frappe/ERPNext/app versions, last
  assessed via the environment-assessment procedure
  (`qkeee-erp-associate/references/02-environment-assessment.md`).
- `references/doctypes-catalog.md` — custom doctypes discovered on this
  environment, with their owning app/module where known.
- `references/custom-apps/<slug>.md` — one file per companion/custom
  Frappe app investigated on this environment.
- `references/non-erpnext/<slug>.md` — one file per non-ERPNext system
  this environment's associate sessions have been told about (see
  `qkeee-erp-associate/references/non-erpnext-adapter.md`).

## Convention

Every entry below is appended under a `## Learned <YYYY-MM-DD>` heading —
never edit or delete a prior entry, per the naming conventions in
`qkeee-erp-associate/references/00-conventions.md`. All content here has
already been through `redact_pii()`/`_redact_pii_deep()` before landing —
see `memory_promote.py`'s module docstring for why that pass is
load-bearing, not a courtesy.
"""


def format_environment_md(env_tag: str, findings: dict, gaps: list = None) -> str:
    """references/environment.md content. `findings` (already redacted by
    the caller via redact_findings()) is expected to carry whatever subset
    of these keys the environment-assessment procedure actually resolved:
    frappe_version, erpnext_version, installed_apps (list of {name,
    version}), health (dict from health_check()), notes (free text).

    `gaps` (the `health` gap shape, issue 09): the permission gaps still
    open when the catalog was built. Any gap writes `catalog_complete:
    false` and lists it, so a later session re-runs the failed steps
    instead of trusting a catalog that is missing data."""
    date = _now_iso_date()
    header_lines = [f"# Environment: {env_tag}", "", f"## Learned {date}", ""]
    lines = list(header_lines)
    lines.append(f"- catalog_complete: {'false' if gaps else 'true'}")
    if gaps:
        lines.append("- Gaps (re-run the failed assessment steps every session until they pass):")
        for g in gaps:
            lines.append(f"  - {g.get('capability', '?')}: missing {g.get('perm', '?')} on "
                         f"\"{g.get('doctype', '?')}\" for {g.get('who', '?')} {g.get('user', '?')} "
                         f"(role \"{g.get('role', '?')}\") — {g.get('effect', '')}".rstrip(" —"))
    header_lines = list(lines)
    frappe_v = findings.get("frappe_version")
    erpnext_v = findings.get("erpnext_version")
    if frappe_v or erpnext_v:
        lines.append(f"- Frappe: {frappe_v or 'unknown'} / ERPNext: {erpnext_v or 'unknown'}")
    apps = findings.get("installed_apps") or []
    if apps:
        lines.append("- Installed apps:")
        for app in apps:
            if isinstance(app, dict):
                lines.append(f"  - {app.get('name', '?')} {app.get('version', '')}".rstrip())
            else:
                lines.append(f"  - {app}")
    health = findings.get("health")
    if isinstance(health, dict):
        lines.append(f"- Last health check: {json.dumps(health, sort_keys=True)}")
    notes = findings.get("notes")
    if notes:
        lines.append(f"- Notes: {notes}")
    if lines == header_lines:  # nothing beyond the heading was supplied
        lines.append("- (no findings supplied yet — populate via the environment-assessment procedure)")
    lines.append("")
    return "\n".join(lines)


def format_doctypes_catalog_md(env_tag: str, doctypes: list) -> str:
    """references/doctypes-catalog.md content. `doctypes` is a list of
    dicts like {"name": ..., "module": ..., "app": ..., "custom": bool},
    as returned by discover.py resolve/meta — already redacted by the
    caller."""
    date = _now_iso_date()
    lines = [f"# Custom doctypes catalog: {env_tag}", "", f"## Learned {date}", ""]
    if not doctypes:
        lines.append("- (none cataloged yet)")
    for d in doctypes:
        name = d.get("name", "?")
        module = d.get("module", "?")
        app = d.get("app", "?")
        custom = "custom" if d.get("custom") else "stock"
        lines.append(f"- **{name}** — module `{module}`, app `{app}` ({custom})")
    lines.append("")
    return "\n".join(lines)


def format_custom_app_md(env_tag: str, app_slug: str, findings: dict) -> str:
    """references/custom-apps/<app-slug>.md content — per
    02-environment-assessment.md's investigation method for a companion
    Frappe-ecosystem app or a genuinely org-specific custom app."""
    date = _now_iso_date()
    lines = [f"# Custom app: {app_slug} ({env_tag})", "", f"## Learned {date}", ""]
    summary = findings.get("summary")
    if summary:
        lines.append(f"- Summary: {summary}")
    key_doctypes = findings.get("key_doctypes") or []
    if key_doctypes:
        lines.append(f"- Key doctypes: {', '.join(key_doctypes)}")
    source = findings.get("source")
    lines.append(f"- Source: {source or 'live metadata + user explanation (no public repo found)'}")
    lines.append("")
    return "\n".join(lines)


def format_non_erpnext_md(env_tag: str, system_slug: str, findings: dict) -> str:
    """references/non-erpnext/<system-slug>.md content — per
    non-erpnext-adapter.md's catalog convention."""
    date = _now_iso_date()
    lines = [f"# Non-ERPNext system: {system_slug} ({env_tag})", "", f"## Learned {date}", ""]
    docs_source = findings.get("docs_source")
    lines.append(f"- Docs/API reference provided by user: {docs_source or '(none given yet)'}")
    summary = findings.get("summary")
    if summary:
        lines.append(f"- Summary: {summary}")
    lines.append("")
    return "\n".join(lines)


def format_memory_breadcrumb(env_tag: str, one_line_summary: str) -> str:
    """The <profile>/memories/MEMORY.md breadcrumb line: one line per known
    env tag. Kept well under the memory tool's
    ~2,200-char whole-store budget (confirmed by inspecting
    tools/memory_tool.py: MemoryStore defaults to
    memory_char_limit=2200) — this single entry should be a small fraction
    of that, since MEMORY.md holds entries for every env tag plus whatever
    else the agent has saved."""
    skill_name = learned_skill_name(env_tag)
    line = f"qkeee-erp env {env_tag}: {one_line_summary} — full notes: skill {skill_name}"
    return redact_pii(line)


# --------------------------------------------------------------------------
# The promotion plan — the actual entry point.
# --------------------------------------------------------------------------

def build_promotion_plan(env_tag: str, findings: dict, *,
                          doctypes: list = None,
                          custom_apps: dict = None,
                          non_erpnext_systems: dict = None,
                          one_line_summary: str = None,
                          skill_already_exists: bool = False,
                          gaps: list = None) -> list:
    """Build the ordered list of tool-call descriptors the CALLING AGENT
    should issue, in order, via its own native `skill_manage`/`memory`
    tools — this function performs no I/O itself.

    Args:
        env_tag: the environment tag (qkeee_erp.active_env value).
        findings: dict for format_environment_md() — see that function's
            docstring for the expected keys. Redacted internally; pass
            raw findings, not pre-redacted ones.
        doctypes: optional list for format_doctypes_catalog_md().
        custom_apps: optional {app_slug: findings_dict} for
            format_custom_app_md(), one entry per investigated app.
        non_erpnext_systems: optional {system_slug: findings_dict} for
            format_non_erpnext_md().
        one_line_summary: short human-readable summary for the MEMORY.md
            breadcrumb (e.g. "Frappe 15.4/ERPNext 15.2, custom app:
            qkeee-lending"). Falls back to a generic line if omitted.
        skill_already_exists: pass True if a prior promotion already
            created qkeee-erp-learned/<env-tag> this session — plan uses
            write_file for every reference instead of create+write_file,
            since skill_manage(create) refuses a name collision.
        gaps: the `gaps[]` from `client.py health` (and any preflight)
            still open. Non-empty -> environment.md says
            `catalog_complete: false` and lists them, and the breadcrumb
            says PARTIAL (issue 09).

    Returns:
        A list of dicts, each shaped as a direct kwargs call for either
        `skill_manage(...)` or `memory(...)` — e.g.
        {"tool": "skill_manage", "action": "create", "name": "...",
         "content": "..."} or
        {"tool": "memory", "action": "add", "target": "memory",
         "content": "..."}.
        The caller (the agent) should issue these IN ORDER (SKILL.md
        before its own references/, so skill_manage always has a valid
        skill to write supporting files into) and stop at the first
        failure — reporting a partial promotion back to the user rather
        than silently continuing.
    """
    findings = redact_findings(findings or {})
    gaps = redact_findings(gaps or [])
    doctypes = redact_findings(doctypes or [])
    custom_apps = redact_findings(custom_apps or {})
    non_erpnext_systems = redact_findings(non_erpnext_systems or {})

    # `name` below is the leaf skill_manage name (e.g. "example-env"), NOT
    # the qualified "qkeee-erp-learned/example-env" — see
    # learned_skill_name()'s docstring for why a literal slash would be
    # rejected by skill_manage's own name validation. `category` nests it
    # under skills/qkeee-erp-learned/ on disk.
    manage_args = learned_skill_manage_args(env_tag)
    name = manage_args["name"]
    category = manage_args["category"]
    plan = []

    skill_md_content = format_learned_skill_md(env_tag)
    if skill_already_exists:
        plan.append({
            "tool": "skill_manage", "action": "edit", "name": name,
            "content": skill_md_content,
        })
    else:
        plan.append({
            "tool": "skill_manage", "action": "create", "name": name,
            "category": category, "content": skill_md_content,
        })

    plan.append({
        "tool": "skill_manage", "action": "write_file", "name": name,
        "file_path": "references/environment.md",
        "file_content": format_environment_md(env_tag, findings, gaps),
    })
    plan.append({
        "tool": "skill_manage", "action": "write_file", "name": name,
        "file_path": "references/doctypes-catalog.md",
        "file_content": format_doctypes_catalog_md(env_tag, doctypes),
    })
    for app_slug, app_findings in sorted(custom_apps.items()):
        plan.append({
            "tool": "skill_manage", "action": "write_file", "name": name,
            "file_path": f"references/custom-apps/{app_slug}.md",
            "file_content": format_custom_app_md(env_tag, app_slug, app_findings),
        })
    for system_slug, system_findings in sorted(non_erpnext_systems.items()):
        plan.append({
            "tool": "skill_manage", "action": "write_file", "name": name,
            "file_path": f"references/non-erpnext/{system_slug}.md",
            "file_content": format_non_erpnext_md(env_tag, system_slug, system_findings),
        })

    summary = one_line_summary or "environment assessed, see full notes"
    if gaps:
        summary = f"PARTIAL catalog ({len(gaps)} gap(s), re-assess); {summary}"
    breadcrumb = format_memory_breadcrumb(env_tag, summary)
    # 'replace' vs 'add' is a judgment call the agent should make after
    # checking current MEMORY.md content (a stale breadcrumb for the same
    # env_tag should be replaced, not duplicated) — this plan defaults to
    # 'add' since it has no visibility into current memory state; the
    # agent should swap the action to 'replace' with an appropriate
    # old_text substring if a breadcrumb for this env_tag already exists.
    plan.append({
        "tool": "memory", "action": "add", "target": "memory",
        "content": breadcrumb,
    })

    return plan


def _cli():
    """Manual/debug entry point: read a JSON findings document from a file
    or stdin, print the resulting promotion plan as JSON to stdout. The
    calling agent is expected to read this output and issue the listed
    tool calls itself — this script never calls skill_manage/memory (see
    module docstring for why it can't)."""
    import argparse

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--env-tag", required=True)
    p.add_argument("--findings-file", help="path to a JSON file shaped like build_promotion_plan()'s "
                                            "findings/doctypes/custom_apps/non_erpnext_systems args; "
                                            "reads stdin if omitted")
    p.add_argument("--summary", help="one-line MEMORY.md breadcrumb summary")
    p.add_argument("--skill-exists", action="store_true",
                   help="pass if qkeee-erp-learned/<env-tag> already exists this session")
    args = p.parse_args()

    if args.findings_file:
        with open(args.findings_file, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
    else:
        raw = sys.stdin.read()
        doc = json.loads(raw) if raw.strip() else {}

    plan = build_promotion_plan(
        args.env_tag,
        doc.get("findings", {}),
        doctypes=doc.get("doctypes"),
        custom_apps=doc.get("custom_apps"),
        non_erpnext_systems=doc.get("non_erpnext_systems"),
        one_line_summary=args.summary,
        skill_already_exists=args.skill_exists,
        gaps=doc.get("gaps"),
    )
    print(json.dumps(plan, indent=2, ensure_ascii=False))

