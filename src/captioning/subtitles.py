import json
import re
import string
from dataclasses import dataclass, field
from pathlib import Path

from src.transcription.schemas import TranscriptSegment, TranscriptWord

DEFAULT_MAX_WORDS = 4
DEFAULT_MAX_DURATION = 1.5

# ASS colors are &HAABBGGRR (alpha then BGR, not RGB).
# Karaoke: SecondaryColour is the "not yet spoken" state, PrimaryColour is what a
# word switches to (and stays) once its \k timer starts — standard ASS/SSA \k
# convention. Net effect: each burst cue starts white, and each word turns
# yellow and stays yellow as the cue plays out (progressive fill), giving the
# word-locked "eye-lock" rhythm behind the ~12-25% watch-time lift cited for
# word-by-word highlighted captions. NOT yet visually confirmed against a real
# local libass render as of writing (this project's own convention is to verify
# style values against real rendered output rather than trust the spec alone —
# see the FontSize-36 story below) — if libass renders this backwards, swap
# which constant fills PrimaryColour vs SecondaryColour in ASS_TEMPLATE.
BASE_COLOR = "&H00FFFFFF"  # white — "not yet spoken"
KARAOKE_ACTIVE_COLOR = "&H0000FFFF"  # yellow — "spoken"
# Static accent for numerals — deliberately a different hue from the karaoke
# active color so a number that happens to also be the active word still reads
# as "this is the accented one", not a coincidence of the sweep.
NUMBER_ACCENT_COLOR = "&H00FFFF00"  # cyan

NUMBER_WORD_RE = re.compile(r"\d")
# Numbers-only for the MVP: objective, needs no maintenance, and matches this
# project's own "concrete insight" hook criteria (src/highlights/prompt.py).
# A curated keyword list is an easy future extension — add entries here.
EMPHASIS_KEYWORDS: frozenset[str] = frozenset()

# FontSize=36 (not the original 14, far too small on a 1080-wide vertical frame
# — but 72 was tried first and, verified visually against real footage, was FAR
# too large, covering nearly half the frame; 36 is a real, image-verified middle
# ground, not a formula-derived guess). FontName names the bold weight directly
# (sidesteps ASS Bold-flag parsing ambiguity) — real font must be installed
# system-side, see Dockerfile/VAST_GUIDE.md/scripts/entrypoint.sh
# (fonts-dejavu-core).
#
# MarginV=480, MarginR=140, MarginL=60 (2026-08-01, narrowed from a generic
# TikTok/Reels/Shorts MarginV=260 estimate to TikTok + YouTube Shorts specifically
# — Reels dropped from the target set per user direction). Based on published
# 2026 safe-zone guides for each app's 1080x1920 layout:
#   TikTok:         bottom ~484px (caption bar/sound/username/overlays),
#                    right ~140px (profile/like/comment/share/bookmark stack),
#                    left ~44px (no UI element, edge-crop safety only)
#   YouTube Shorts:  bottom ~300-400px (channel/subscribe/description/audio —
#                    sources recommend designing for the expanded 400px state),
#                    right ~96-120px (like/dislike/comment/share column),
#                    left ~60px (no UI element)
# The two apps aren't rendered separately — one burned-in output has to clear
# both — so each margin takes the STRICTER (larger) of the two: bottom takes
# TikTok's ~484px (rounded to 480), right takes TikTok's 140px, left stays at
# 60px (already covers both, no UI element on either app). A side effect worth
# noting: with Alignment=2 (bottom-center), libass centers text within
# [MarginL, PlayResX-MarginR], so the asymmetric 60/140 box centers around
# x≈500 rather than true frame-center x≈540 — a deliberate bias away from the
# right-side icon column present on both platforms, not a mistake.
# These figures come from third-party creator-tool safe-zone guides (neither
# platform publishes official specs) — best available estimates, not a
# substitute for confirming against the real apps on an actual phone (still
# open, see the verification-caveat section in CLAUDE.md).
ASS_TEMPLATE = """[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Karaoke,DejaVu Sans Bold,36,{active},{base},&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,3,0,2,60,140,480,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


@dataclass
class CaptionCue:
    start: float
    end: float
    text: str
    words: list[TranscriptWord] = field(default_factory=list)


def load_transcript(transcript_path: Path) -> list[TranscriptSegment]:
    raw = json.loads(Path(transcript_path).read_text(encoding="utf-8"))
    segments = []
    for seg in raw:
        words = [TranscriptWord(**w) for w in seg.get("words", [])]
        segments.append(
            TranscriptSegment(start=seg["start"], end=seg["end"], text=seg["text"], words=words)
        )
    return segments


def slice_words_to_clip(
    segments: list[TranscriptSegment], clip_segments: list[tuple[float, float]]
) -> list[TranscriptWord]:
    """Flattens every word across all transcript segments and maps it onto the
    rendered clip's own timeline, which may be stitched from more than one
    non-contiguous (seg_start, seg_end) window of the source video (a jump-cut
    clip — see src/rendering/clipper.py, which renders clip_segments in this same
    order via ffmpeg concat).

    For each clip_segments window, in order: keep only words overlapping that
    window, clamp each word's start/end to the window's bounds (so a word
    straddling a jump-cut boundary doesn't end up spanning into the next kept
    segment's shifted time), then shift by that window's own start plus the
    cumulative duration of windows already placed — i.e. concatenated clip-local
    time, not a single flat subtraction. A normal (non-jump-cut) clip is just the
    len(clip_segments) == 1 case of this same logic.
    """
    sliced: list[TranscriptWord] = []
    cumulative_offset = 0.0
    for seg_start, seg_end in clip_segments:
        for segment in segments:
            for word in segment.words:
                if word.end <= seg_start or word.start >= seg_end:
                    continue
                clamped_start = max(word.start, seg_start)
                clamped_end = min(word.end, seg_end)
                sliced.append(
                    TranscriptWord(
                        word=word.word,
                        start=clamped_start - seg_start + cumulative_offset,
                        end=clamped_end - seg_start + cumulative_offset,
                    )
                )
        cumulative_offset += seg_end - seg_start
    # Already chronological by construction (windows processed in order, words
    # within each transcript segment already time-ordered) — sorted defensively
    # in case that transcript invariant is ever violated upstream.
    sliced.sort(key=lambda w: w.start)
    return sliced


def build_burst_cues(
    words: list[TranscriptWord],
    max_words: int = DEFAULT_MAX_WORDS,
    max_duration: float = DEFAULT_MAX_DURATION,
) -> list[CaptionCue]:
    """Greedily groups consecutive words into short "burst" cues (2-4 words,
    TikTok-style), closing a cue once adding the next word would exceed
    max_words or max_duration, whichever comes first.
    """
    cues: list[CaptionCue] = []
    current: list[TranscriptWord] = []

    def flush() -> None:
        if current:
            cues.append(
                CaptionCue(
                    start=current[0].start,
                    end=current[-1].end,
                    text=" ".join(w.word for w in current).strip(),
                    words=list(current),
                )
            )

    for word in words:
        if current and (
            len(current) >= max_words or word.end - current[0].start > max_duration
        ):
            flush()
            current = []
        current.append(word)
    flush()

    return cues


def _is_emphasized_word(word: str, extra_keywords: frozenset[str] = EMPHASIS_KEYWORDS) -> bool:
    if NUMBER_WORD_RE.search(word):
        return True
    return word.strip(string.punctuation).lower() in extra_keywords


def _escape_ass_text(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


def _cue_dialogue_text(cue: CaptionCue, extra_keywords: frozenset[str] = EMPHASIS_KEYWORDS) -> str:
    parts = []
    for i, word in enumerate(cue.words):
        # Duration until the NEXT word's onset (not this word's own end - start)
        # so the color transition lands exactly when the next word is actually
        # spoken, independent of any inter-word pause the transcript captured.
        # The last word extends to the cue's own end. \k unit is centiseconds.
        next_start = cue.words[i + 1].start if i + 1 < len(cue.words) else cue.end
        cs = max(1, round((next_start - word.start) * 100))
        text = _escape_ass_text(word.word)
        if _is_emphasized_word(word.word, extra_keywords):
            # Static accent: override both Primary and Secondary to the accent
            # color for this word's run, so it stays accented whether or not
            # it's currently "active". \k duration is kept so the cumulative
            # timing map for later words stays correct even though colour
            # doesn't visibly change for this run. \r resets to the style
            # default so words after this one resume the normal karaoke sweep.
            parts.append(
                f"{{\\1c{NUMBER_ACCENT_COLOR}\\2c{NUMBER_ACCENT_COLOR}\\k{cs}}}{text}{{\\r}}"
            )
        else:
            parts.append(f"{{\\k{cs}}}{text}")
    return " ".join(parts)


def _format_ass_timestamp(seconds: float) -> str:
    total_cs = max(0, round(seconds * 100))
    hours, rem = divmod(total_cs, 360000)
    minutes, rem = divmod(rem, 6000)
    secs, cs = divmod(rem, 100)
    return f"{hours:d}:{minutes:02d}:{secs:02d}.{cs:02d}"


def write_ass(
    cues: list[CaptionCue],
    out_path: Path,
    video_width: int,
    video_height: int,
    extra_keywords: frozenset[str] = EMPHASIS_KEYWORDS,
) -> None:
    lines = [
        ASS_TEMPLATE.format(
            width=video_width,
            height=video_height,
            active=KARAOKE_ACTIVE_COLOR,
            base=BASE_COLOR,
        )
    ]
    for cue in cues:
        start = _format_ass_timestamp(cue.start)
        end = _format_ass_timestamp(cue.end)
        text = _cue_dialogue_text(cue, extra_keywords)
        lines.append(f"Dialogue: 0,{start},{end},Karaoke,,0,0,0,,{text}\n")
    Path(out_path).write_text("".join(lines), encoding="utf-8")
