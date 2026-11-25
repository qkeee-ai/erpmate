"""Build-time patch for the google_meet plugin's caption-toggle bug.

Targets ``/opt/hermes/plugins/google_meet/meet_bot.py`` in the base
``nousresearch/hermes-agent`` image. Two compounding bugs (NousResearch/
hermes-agent#118049, item 5 — open, unfixed as of 2026-11-26):

1. ``_ENABLE_CAPTIONS_JS`` dispatches a synthetic ``KeyboardEvent`` via
   ``document.body.dispatchEvent(...)``. Synthetic DOM events are
   ``isTrusted: false``, and Meet's own ``c`` shortcut handler ignores
   them — the call never throws (so ``captions_enabled_attempted`` gets set
   regardless), but captions never actually turn on.
2. It runs immediately after ``_join()``, before admission is confirmed —
   while still in the lobby, Meet hasn't even rendered the captions button,
   so even a trusted key press would have nothing to act on yet.

Confirmed on qk-prod-erp after the join race (hermes-agent#74680) was fixed:
the bot joins cleanly (``inCall: true``) but ``transcriptLines`` stays 0
forever with no error — captions were never really toggled on.

Fix: drop the pre-admission synthetic-event attempt, and once
``_drain_loop`` confirms admission (``in_call`` flips true for the first
time), send a real trusted key press via Playwright's
``page.keyboard.press("c")`` — real input events bypass the
isTrusted check, and the captions button now actually exists.

Fails the build loudly (not a silent skip) if either anchor text below
doesn't match verbatim — this patches a floating ``:latest`` base image, so
a future upstream rewrite must be noticed, not quietly dropped. If this
fails after a base image update, re-capture the live file (``docker exec
<container> cat /opt/hermes/plugins/google_meet/meet_bot.py``), check
whether hermes-agent#118049 landed a real fix upstream (drop this patch if
so), and otherwise update the anchors below to match.
"""
import sys

TARGET = "/opt/hermes/plugins/google_meet/meet_bot.py"

REPLACEMENTS = [
    (
        '''            _join(page, cfg, state)
            if _quiet(page.evaluate, _ENABLE_CAPTIONS_JS):
                state.set(captions_enabled_attempted=True)
            try:
                page.evaluate(_CAPTION_OBSERVER_JS)
            except Exception as e:
                state.set(error=f"caption observer install failed: {e}")
            # in_call stays False until admission is confirmed by the drain loop.
            state.set(captioning=True, join_attempted_at=time.time())
''',
        '''            _join(page, cfg, state)
            try:
                page.evaluate(_CAPTION_OBSERVER_JS)
            except Exception as e:
                state.set(error=f"caption observer install failed: {e}")
            # Captions are toggled via a real key press once admission is
            # confirmed (see _drain_loop) — a synthetic KeyboardEvent here is
            # isTrusted: false (Meet ignores it) and the captions button
            # doesn't exist pre-admission anyway (hermes-agent#118049 item 5).
            # in_call stays False until admission is confirmed by the drain loop.
            state.set(captioning=True, join_attempted_at=time.time())
''',
    ),
    (
        '''            if _probe(page, _ADMISSION_PROBE_JS):
                state.set(in_call=True, lobby_waiting=False, joined_at=now)
''',
        '''            if _probe(page, _ADMISSION_PROBE_JS):
                state.set(in_call=True, lobby_waiting=False, joined_at=now)
                # Real, trusted key press — now that the captions button
                # actually exists (hermes-agent#118049 item 5).
                _quiet(page.keyboard.press, "c")
                state.set(captions_enabled_attempted=True)
''',
    ),
]


def main() -> int:
    text = open(TARGET, encoding="utf-8").read()
    for old, new in REPLACEMENTS:
        if old not in text:
            sys.stderr.write(
                f"google-meet-captions-untrusted-event patch: anchor text not found verbatim in {TARGET}:\n"
                f"{old!r}\n"
                "The base image's meet_bot.py has changed since this patch was written — "
                "re-capture the live file and update "
                "docker/patches/google-meet-captions-untrusted-event.py "
                "(or drop it if hermes-agent#118049 fixed this upstream).\n")
            return 1
        if text.count(old) != 1:
            sys.stderr.write(
                "google-meet-captions-untrusted-event patch: an anchor matches more than once; refusing.\n")
            return 1
        text = text.replace(old, new)
    compile(text, TARGET, "exec")  # fail loudly on a syntax mistake in NEW, not at runtime
    open(TARGET, "w", encoding="utf-8").write(text)
    print(f"google-meet-captions-untrusted-event patch: applied to {TARGET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
