"""Build-time patch for the google_meet plugin's join-button race.

Targets ``/opt/hermes/plugins/google_meet/meet_bot.py`` in the base
``nousresearch/hermes-agent`` image. ``_visible()`` checked
``locator.first.count()``/``.is_visible()`` immediately after
``page.goto(..., wait_until="domcontentloaded")`` — neither call auto-waits,
and Meet's pre-join UI is client-rendered (measured ~9s to appear), so the
check missed both the "Join now" and "Ask to join" buttons, clicked nothing,
and left the bot sitting on the pre-join screen with a healthy-looking
status forever: no exception, no error, no real join request ever sent to
Google. See NousResearch/hermes-agent#74680 and #118049 (item 4) — both open,
unfixed upstream as of 2026-11-26. Observed on qk-prod-erp: Google Admin's
Meet logs showed zero join attempts for a run that still reported
``captionsEnabledAttempted: true`` and eventually a misleading
``"lobby timeout — host never admitted the bot"`` error.

Fails the build loudly (not a silent skip) if the anchor text below doesn't
match verbatim — this patches a floating ``:latest`` base image, so a future
upstream rewrite of this function must be noticed, not quietly dropped. If
this fails after a base image update, re-capture the live file
(``docker exec <container> cat /opt/hermes/plugins/google_meet/meet_bot.py``),
check whether hermes-agent#74680/#118049 landed a real fix upstream (drop
this patch if so), and otherwise update OLD/NEW below to match.
"""
import sys

TARGET = "/opt/hermes/plugins/google_meet/meet_bot.py"

OLD = '''def _visible(locator):
    """``locator.first`` if it exists and is visible, else None (swallows Playwright errors)."""
    return _quiet(lambda: locator.first if locator.first.count() and locator.first.is_visible() else None)
'''

NEW = '''def _visible(locator, timeout_ms: int = 15_000):
    """``locator.first`` once visible within *timeout_ms*, else None.

    Meet's pre-join UI is client-rendered and may not exist yet right after
    ``page.goto(..., wait_until="domcontentloaded")`` — the original immediate
    ``count()``/``is_visible()`` check (no auto-wait) silently missed both the
    "Join now" and "Ask to join" buttons, clicked nothing, and left the bot
    sitting on the pre-join screen with a healthy-looking status forever (no
    error, no exception, no real join request sent to Google — see
    NousResearch/hermes-agent#74680 and #118049 item 4). Waiting for
    visibility first fixes the race; swallows Playwright's TimeoutError the
    same as the original swallowed every exception.
    """
    first = locator.first
    try:
        first.wait_for(state="visible", timeout=timeout_ms)
    except Exception:
        return None
    return first
'''


def main() -> int:
    text = open(TARGET, encoding="utf-8").read()
    if OLD not in text:
        sys.stderr.write(
            f"google-meet-join-race patch: anchor text not found verbatim in {TARGET}.\n"
            "The base image's meet_bot.py has changed since this patch was written — "
            "re-capture the live file and update docker/patches/google-meet-join-race.py "
            "(or drop it if hermes-agent#74680/#118049 fixed this upstream).\n")
        return 1
    if text.count(OLD) != 1:
        sys.stderr.write("google-meet-join-race patch: anchor text matches more than once; refusing.\n")
        return 1
    patched = text.replace(OLD, NEW)
    compile(patched, TARGET, "exec")  # fail loudly on a syntax mistake in NEW, not at runtime
    open(TARGET, "w", encoding="utf-8").write(patched)
    print(f"google-meet-join-race patch: applied to {TARGET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
