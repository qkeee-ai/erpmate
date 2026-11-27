# Context: memory, compaction size, mid-turn compaction

Status: ready-for-human
Type: task
Blocked by: none
Findings: F17

## Problem

The fixed context is a 31K-character system prompt plus a 34K-character compaction summary. A compaction in the middle of a turn re-sent the whole history (part 2 request B repeats A). Memory is 97% full (2,143 of 2,200 characters).

## Change

- Consolidate the profile memory on dev-erp. Move task facts (for example the GST Settings note) into `qkeee-erp-learned`. Target: under 70%.
- Check the Hermes compaction settings for a cap on the summary size (`compression.*` in `config.yaml`). Set one if it exists; otherwise add it to issue 14 as upstream.
- Check whether `compression.threshold: 0.30` causes the mid-turn compaction. Measure one turn before and after a change.

## Acceptance

- Memory under 70%.
- A comment in this file lists the compaction settings tried and their effect.
