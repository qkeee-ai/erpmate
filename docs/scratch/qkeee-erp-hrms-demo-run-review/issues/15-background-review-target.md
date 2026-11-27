# Background skill review: where its updates land

Status: ready-for-human
Type: research
Blocked by: none
Findings: F19

## Problem

After the run, Hermes started a background skill review on the aux model (part 2 request C). The log stops after `skills_list`. The `qkeee-erp` skill tree is mounted read-only, so the review can only write somewhere else: `qkeee-erp-learned`, a new skill, or nowhere.

## Change

- On dev-erp, list the skills changed since 2026-10-22 06:00 UTC (`skill_manage` history, or file times under the profile's `skills/`).
- Record what the review wrote, and whether it holds session-specific content.
- If it wrote outside `qkeee-erp-learned`, a human decides whether to restrict the review's target or turn the review off for this profile.

## Acceptance

- An `## Answer` section lists the files the review changed and the decision taken.
