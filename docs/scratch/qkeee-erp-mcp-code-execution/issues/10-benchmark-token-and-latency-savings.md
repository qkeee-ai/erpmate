# Benchmark token/latency savings on a real hermes run

Status: needs-info
Type: research
Blocked by: 02, 04

## Problem

Anthropic's article reports a 150,000 → 2,000 token drop (98.7%) for one example, with no detail on how it was measured. We shouldn't carry that number into our own planning without checking it against an actual qkeee-erp run.

## Change

- Pick one representative run that's expensive today (an Item run with a large row pull, or an HRMS demo run) and measure its token usage and wall-clock time both with direct tool calls and with the wrapper/sandbox path.
- Record the comparison here, including cases where the sandbox path is *not* a clear win (e.g. a run with few, small tool results, where the wrapper-generation and sandbox startup overhead might dominate).

## Done when

A measured before/after exists for at least one real run, not just the Anthropic example.

## Comments
