"""Build-time patch for the google_meet plugin's duplicate-caption bug.

Targets ``/opt/hermes/plugins/google_meet/meet_bot.py`` in the base
``nousresearch/hermes-agent`` image. ``_CAPTION_OBSERVER_JS`` fires its
MutationObserver callback on every DOM mutation inside the caption region —
which, since Meet streams live captions token-by-token, means once per
partial update of the *same* utterance. ``_BotState.record_caption()``
dedupes only on an *exact* ``speaker|text`` match, so each growing partial
("Hello", "Hello,", "Hello, this", "Hello, this is", ...) is a distinct key
and gets written as its own transcript line. Confirmed on qk-prod-erp: a
single ~15s utterance produced ~30 near-duplicate lines, each one token
longer than the last — unusable as input to any minutes/summary step.

Fix: buffer the growing/revising line instead of committing it immediately.
Meet doesn't just append words, it also revises already-emitted ones
("San." -> "Sanjay" once more context arrives), so a plain
``text.startswith(buffered_text)`` continuation check still produced
near-duplicates whenever a revision broke the prefix match. Instead, any
update from the *same speaker* within ``_CONTINUATION_WINDOW_S`` (2.5s) of
the last one just replaces the buffer with Meet's latest wording — no text
comparison. Committing to ``transcript.txt`` happens in
``_commit_pending()``, triggered by a different speaker starting, or by
``flush_stale_pending()`` (called once per drain-loop tick, ~1s) once the
buffered line has gone quiet for the window — so a pause or the bot leaving
doesn't strand the last utterance unwritten.

Fails the build loudly (not a silent skip) if any anchor text below doesn't
match verbatim — this patches a floating ``:latest`` base image. If this
fails after a base image update, re-capture the live file (``docker exec
<container> cat /opt/hermes/plugins/google_meet/meet_bot.py``) and update
the anchors below to match (no upstream issue number for this one yet —
file one alongside #74680/#118049 if it's still unfixed).
"""
import sys

TARGET = "/opt/hermes/plugins/google_meet/meet_bot.py"

REPLACEMENTS = [
    (
        '''    def __init__(self, out_dir: Path, meeting_id: str, url: str):
        self.__dict__.update({attr: default for _, attr, default in _STATUS_FIELDS if attr})
        self.__dict__.update(out_dir=out_dir, meeting_id=meeting_id, url=url, _seen=set(),  # seen "speaker|text"
                             transcript_path=out_dir / "transcript.txt", status_path=out_dir / "status.json")
        out_dir.mkdir(parents=True, exist_ok=True)
        self._flush()

    def record_caption(self, speaker: str, text: str) -> None:
        """Append a caption line unless this exact (speaker, text) was already seen."""
        speaker, text = (speaker or "").strip() or "Unknown", (text or "").strip()
        key = f"{speaker}|{text}"
        if not text or key in self._seen:
            return
        self._seen.add(key)
        self.transcript_lines += 1
        self.last_caption_at = time.time()
        ts = time.strftime("%H:%M:%S", time.localtime(self.last_caption_at))
        with self.transcript_path.open("a", encoding="utf-8") as f:
            f.write(f"[{ts}] {speaker}: {text}\\n")
        self._flush()
''',
        '''    def __init__(self, out_dir: Path, meeting_id: str, url: str):
        self.__dict__.update({attr: default for _, attr, default in _STATUS_FIELDS if attr})
        self.__dict__.update(out_dir=out_dir, meeting_id=meeting_id, url=url, _seen=set(),  # seen "speaker|text"
                             _pending=None,  # (speaker, text, last_update_ts) — buffered, not yet final
                             transcript_path=out_dir / "transcript.txt", status_path=out_dir / "status.json")
        out_dir.mkdir(parents=True, exist_ok=True)
        self._flush()

    _CONTINUATION_WINDOW_S = 2.5  # same speaker within this long of the last update = same utterance

    def record_caption(self, speaker: str, text: str) -> None:
        """Buffer a growing/revising caption line; commit it once a different speaker
        starts or the buffered line goes quiet for ``_CONTINUATION_WINDOW_S`` (see
        ``flush_stale_pending``, called every drain-loop tick). Meet streams captions
        token-by-token AND revises already-emitted words ("San." -> "Sanjay") as more
        context arrives, so committing on every mutation — or requiring the new text to
        be a superset of the old — produced dozens of near-duplicate/half-corrected
        lines per utterance. A same-speaker update inside the window always replaces
        the buffer with Meet's latest wording, corrections included."""
        speaker, text = (speaker or "").strip() or "Unknown", (text or "").strip()
        if not text:
            return
        now = time.time()
        if self._pending is not None:
            p_speaker, _, p_ts = self._pending
            if speaker != p_speaker or (now - p_ts) > self._CONTINUATION_WINDOW_S:
                self._commit_pending()
        self._pending = (speaker, text, now)

    def _commit_pending(self) -> None:
        if self._pending is None:
            return
        speaker, text, ts = self._pending
        self._pending = None
        key = f"{speaker}|{text}"
        if key in self._seen:
            return
        self._seen.add(key)
        self.transcript_lines += 1
        self.last_caption_at = ts
        stamp = time.strftime("%H:%M:%S", time.localtime(ts))
        with self.transcript_path.open("a", encoding="utf-8") as f:
            f.write(f"[{stamp}] {speaker}: {text}\\n")
        self._flush()

    def flush_stale_pending(self, max_age_s: float = 2.5) -> None:
        """Commit the buffered line once it's been quiet for *max_age_s* seconds —
        otherwise the last utterance before a pause (or before the bot leaves) never
        gets written, since nothing ever arrives to signal it's done. Call once per
        drain-loop tick."""
        if self._pending is not None and (time.time() - self._pending[2]) >= max_age_s:
            self._commit_pending()
''',
    ),
    (
        '''        if rt["session"] is not None:
            state.set(audio_bytes_out=rt["session"].audio_bytes_out,
                      last_audio_out_at=rt["session"].last_audio_out_at)
        time.sleep(1.0)
''',
        '''        if rt["session"] is not None:
            state.set(audio_bytes_out=rt["session"].audio_bytes_out,
                      last_audio_out_at=rt["session"].last_audio_out_at)
        state.flush_stale_pending()
        time.sleep(1.0)
''',
    ),
    (
        '''            _drain_loop(page, cfg, state, rt, stop_flag)
            _quiet(page.evaluate, _LEAVE_CALL_JS)
''',
        '''            _drain_loop(page, cfg, state, rt, stop_flag)
            state.flush_stale_pending(max_age_s=0)  # don't strand the last utterance
            _quiet(page.evaluate, _LEAVE_CALL_JS)
''',
    ),
]


def main() -> int:
    text = open(TARGET, encoding="utf-8").read()
    for old, new in REPLACEMENTS:
        if old not in text:
            sys.stderr.write(
                f"google-meet-caption-dedup patch: anchor text not found verbatim in {TARGET}:\n"
                f"{old!r}\n"
                "The base image's meet_bot.py has changed since this patch was written — "
                "re-capture the live file and update docker/patches/google-meet-caption-dedup.py.\n")
            return 1
        if text.count(old) != 1:
            sys.stderr.write("google-meet-caption-dedup patch: an anchor matches more than once; refusing.\n")
            return 1
        text = text.replace(old, new)
    compile(text, TARGET, "exec")  # fail loudly on a syntax mistake in NEW, not at runtime
    open(TARGET, "w", encoding="utf-8").write(text)
    print(f"google-meet-caption-dedup patch: applied to {TARGET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
