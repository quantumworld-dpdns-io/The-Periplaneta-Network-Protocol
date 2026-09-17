"""
Generate an SRT subtitle file from the timing table in docs/netsim/VIDEO_SCRIPT.md.

    python -m netsim.make_subtitles          # -> docs/netsim/VIDEO_SCRIPT.srt

Each script row (t range | screen | voice) becomes one or more cues: the voice
text is split into sentences and the row's time span is divided among them in
proportion to sentence length, so cues stay under ~2 lines. Also checks that
the script ends before 3:00 and prints the words-per-minute the narration
implies, so the speaker knows whether the pace is realistic.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "docs" / "netsim" / "VIDEO_SCRIPT.md"
OUT = ROOT / "docs" / "netsim" / "VIDEO_SCRIPT.srt"
HARD_CAP_S = 180


def parse_ts(s: str) -> float:
    m, sec = s.split(":")
    return int(m) * 60 + int(sec)


def fmt(t: float) -> str:
    h = int(t // 3600)
    m = int(t % 3600 // 60)
    s = t % 60
    return f"{h:02d}:{m:02d}:{int(s):02d},{int(round((s - int(s)) * 1000)):03d}"


def wrap(text: str, width: int = 42) -> str:
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width and cur:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return "\n".join(lines[:2]) if len(lines) <= 2 else "\n".join([" ".join(lines[:-1]), lines[-1]]) if len(lines) == 3 else "\n".join(lines)


def main() -> int:
    rows = []
    for line in SRC.read_text().splitlines():
        m = re.match(r"^\|\s*(\d+:\d\d)[–-](\d+:\d\d)\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|\s*$", line)
        if m:
            rows.append((parse_ts(m.group(1)), parse_ts(m.group(2)), m.group(4)))
    if not rows:
        print("no timing rows found in", SRC)
        return 1
    cues = []
    total_words = 0
    for t0, t1, voice in rows:
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", voice) if s.strip()]
        lens = [len(s) for s in sentences]
        span = t1 - t0
        t = t0
        for s, L in zip(sentences, lens):
            d = span * L / sum(lens)
            cues.append((t, t + d - 0.05, s))
            t += d
            total_words += len(s.split())
    end = rows[-1][1]
    with OUT.open("w") as f:
        for i, (a, b, s) in enumerate(cues, 1):
            f.write(f"{i}\n{fmt(a)} --> {fmt(b)}\n{wrap(s)}\n\n")
    wpm = total_words / (end / 60)
    print(f"wrote {OUT.relative_to(ROOT)}: {len(cues)} cues, script ends at {int(end // 60)}:{int(end % 60):02d} "
          f"(hard cap {HARD_CAP_S // 60}:00), {total_words} words, {wpm:.0f} words/min")
    if end >= HARD_CAP_S:
        print("FAIL: script runs to or past the 3:00 hard cap")
        return 1
    if wpm > 170:
        print("WARN: narration pace above 170 words/min; trim the voice text")
    return 0


if __name__ == "__main__":
    sys.exit(main())
