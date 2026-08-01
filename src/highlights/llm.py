import json
import logging
import os
import re
import time

from src.highlights.exceptions import HighlightError
from src.highlights.prompt import build_messages
from src.transcription.schemas import TranscriptSegment, TranscriptWord

LLM_MODEL_NAME = os.getenv("HIGHLIGHT_LLM_MODEL", "claude-sonnet-5")
# claude-sonnet-5 runs adaptive thinking by default when `thinking` is omitted, and
# max_tokens is a hard cap on thinking + text output combined. Unlike older models,
# Sonnet 5 has no `budget_tokens` escape hatch to cap thinking directly — passing one
# is rejected outright (400) — so the only lever against "thinking ate the whole
# budget" is a generous max_tokens ceiling. 2048 was observed insufficient on a real
# transcript in local testing, and even 8192 was fully consumed by thinking on a real
# ~57-minute video's transcript on a real vast.ai run (2026-08-01), leaving zero room
# for the actual JSON output. 32000 leaves generous headroom for both on top of a full
# up-to-15-candidate JSON array (a few KB of text, well under 1k tokens) — revisit
# upward again if a future run's transcript is long/complex enough to exhaust this too.
MAX_NEW_TOKENS = 32000
# Not a target — the prompt deliberately leaves the actual candidate count up to
# Claude's judgment of how much genuine hook material the video contains (see
# prompt.py's output contract). This is purely a defensive ceiling so a runaway
# response can't blow up downstream render/reframe/captioning cost unbounded.
MAX_CANDIDATES = 15
MIN_CLIP_SECONDS = 30
# 60 remains the default/target ceiling (see prompt.py's duration rule); 75 is a
# deliberate stretch allowance so Claude isn't forced to truncate a genuinely strong
# idea mid-thought just to hit 60 — not a general widening of the target range.
MAX_CLIP_SECONDS = 75
# Jump-cut clips (>1 segment): kept conservative so stitched clips still feel like one
# coherent moment rather than a choppy compilation — see prompt.py's "Jump-cuts" rules.
MAX_SEGMENTS_PER_CLIP = 3
MIN_SEGMENT_SECONDS = 8

# Bounded search radius (seconds) for word-boundary snapping around Claude's raw
# start/end guess. With segment timestamps now shown to Claude at 1-decimal precision
# (prompt.py), most cuts landing on an actual segment boundary should already be close;
# this window mainly catches genuinely mid-segment sentence-boundary estimates, which
# are inherently interpolated and can be off by more. Not yet tuned against a real
# transcript (no local GPU — see CLAUDE.md's highlights-module verification caveat);
# revisit after a real vast.ai run if snaps are observed landing on the wrong word.
SNAP_WINDOW_SECONDS = 2.0

JSON_ARRAY_RE = re.compile(r"\[.*\]", re.DOTALL)


def generate_candidates(
    segments: list[TranscriptSegment],
    campaign_context: str | None = None,
    logger: logging.Logger | None = None,
) -> str:
    import anthropic

    messages = build_messages(segments, campaign_context)
    system_prompt = next(m["content"] for m in messages if m["role"] == "system")
    user_messages = [m for m in messages if m["role"] != "system"]

    if logger:
        transcript_chars = sum(len(m["content"]) for m in user_messages)
        logger.info(
            "calling Claude API model=%s (transcript ~%d chars, no chunking needed — "
            "well within context window)%s",
            LLM_MODEL_NAME, transcript_chars,
            f" [campaign context: {len(campaign_context)} chars]" if campaign_context else "",
        )

    client = anthropic.Anthropic()
    call_start = time.monotonic()
    # Streaming (not a plain .create() call): the Anthropic SDK refuses a non-streaming
    # request at this max_tokens size if it estimates the call could run past ~10
    # minutes, raising a client-side ValueError before any request is even sent.
    # .stream()/.get_final_message() returns the exact same Message shape as .create()
    # (content/usage/stop_reason), so nothing below this needs to change.
    with client.messages.stream(
        model=LLM_MODEL_NAME,
        max_tokens=MAX_NEW_TOKENS,
        system=system_prompt,
        messages=user_messages,
    ) as stream:
        response = stream.get_final_message()
    raw_response = "".join(block.text for block in response.content if block.type == "text")

    if logger:
        logger.info(
            "Claude API call done in %.1fs: input_tokens=%d output_tokens=%d stop_reason=%s",
            time.monotonic() - call_start, response.usage.input_tokens, response.usage.output_tokens,
            response.stop_reason,
        )
        logger.info("raw LLM response:\n%s", raw_response)

    if response.stop_reason == "max_tokens" and not raw_response.strip():
        raise HighlightError(
            "Claude response truncated at max_tokens before producing any text "
            "output (likely all-thinking, no text blocks) — increase MAX_NEW_TOKENS",
            stage="detection",
        )

    return raw_response


def parse_candidates(
    raw_text: str, video_duration: float, logger: logging.Logger | None = None
) -> list[dict]:
    match = JSON_ARRAY_RE.search(raw_text)
    if match is None:
        raise HighlightError(
            "LLM response did not contain a JSON array", stage="detection"
        )

    try:
        candidates = json.loads(match.group(0))
    except json.JSONDecodeError as e:
        raise HighlightError(
            f"LLM response JSON array was malformed: {e}", stage="detection"
        )

    if not isinstance(candidates, list):
        raise HighlightError("LLM response JSON was not an array", stage="detection")

    if logger:
        logger.info("LLM returned %d raw candidates", len(candidates))

    valid: list[dict] = []
    for i, item in enumerate(candidates):
        if not isinstance(item, dict):
            if logger:
                logger.info("candidate #%d rejected: not a JSON object (%r)", i, item)
            continue
        raw_segments = item.get("segments")
        reason = item.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            if logger:
                logger.info("candidate #%d rejected: missing/empty reason", i)
            continue
        if (
            not isinstance(raw_segments, list)
            or not raw_segments
            or len(raw_segments) > MAX_SEGMENTS_PER_CLIP
        ):
            if logger:
                logger.info(
                    "candidate #%d rejected: segments must be a non-empty list of at "
                    "most %d entries (got %r)", i, MAX_SEGMENTS_PER_CLIP, raw_segments,
                )
            continue

        segments: list[tuple[float, float]] = []
        segments_valid = True
        for seg in raw_segments:
            if not isinstance(seg, dict):
                segments_valid = False
                if logger:
                    logger.info("candidate #%d rejected: segment not a JSON object (%r)", i, seg)
                break
            seg_start, seg_end = seg.get("start"), seg.get("end")
            if not isinstance(seg_start, (int, float)) or not isinstance(seg_end, (int, float)):
                segments_valid = False
                if logger:
                    logger.info(
                        "candidate #%d rejected: segment start/end not numeric (start=%r end=%r)",
                        i, seg_start, seg_end,
                    )
                break
            seg_start, seg_end = float(seg_start), float(seg_end)
            if not (0 <= seg_start < seg_end <= video_duration):
                segments_valid = False
                if logger:
                    logger.info(
                        "candidate #%d rejected: segment out of range (start=%.1f end=%.1f video_duration=%.1f)",
                        i, seg_start, seg_end, video_duration,
                    )
                break
            if seg_end - seg_start < MIN_SEGMENT_SECONDS:
                segments_valid = False
                if logger:
                    logger.info(
                        "candidate #%d rejected: segment length %.1fs below minimum %ds",
                        i, seg_end - seg_start, MIN_SEGMENT_SECONDS,
                    )
                break
            segments.append((seg_start, seg_end))
        if not segments_valid:
            continue

        # chronological, non-overlapping
        if any(segments[j][1] > segments[j + 1][0] for j in range(len(segments) - 1)):
            if logger:
                logger.info("candidate #%d rejected: segments not chronological/non-overlapping (%r)", i, segments)
            continue

        total_seconds = sum(e - s for s, e in segments)
        if not (MIN_CLIP_SECONDS <= total_seconds <= MAX_CLIP_SECONDS):
            if logger:
                logger.info(
                    "candidate #%d rejected: total clip length %.1fs outside [%d, %d]",
                    i, total_seconds, MIN_CLIP_SECONDS, MAX_CLIP_SECONDS,
                )
            continue

        valid.append({
            "segments": [{"start": s, "end": e} for s, e in segments],
            "reason": reason.strip(),
        })

    if logger:
        logger.info("%d/%d candidates survived validation", len(valid), len(candidates))
        for v in valid:
            span = " + ".join(f"[{s['start']:.1f}-{s['end']:.1f}]" for s in v["segments"])
            logger.info("  %s %s", span, v["reason"])

    if not valid:
        raise HighlightError(
            "no valid highlight candidates survived validation", stage="detection"
        )

    return valid[:MAX_CANDIDATES]


def _snap_edge(
    raw_value: float,
    words: list[TranscriptWord],
    edge: str,
    window: float,
    logger: logging.Logger | None = None,
) -> float:
    """Snap a candidate's raw start/end to the nearest real word boundary within
    +/- window seconds. "start" always snaps to some word's own start, "end" always
    snaps to some word's own end (never the opposite) — this guarantees the word at
    the cut point is either fully included or fully excluded, never split in half.
    Falls back to the raw value (with a warning, not a rejection) if no word boundary
    falls within the window — an empty window can be a legitimate situation (e.g. a
    clip starting right as speech resumes after a silence/music intro), so this is
    surfaced for manual spot-check rather than treated as an error.
    """
    attr = "start" if edge == "start" else "end"
    nearby = [
        getattr(w, attr) for w in words
        if raw_value - window <= getattr(w, attr) <= raw_value + window
    ]
    if not nearby:
        if logger:
            logger.warning(
                "snap: no word %s within %.1fs of raw %s=%.2f — keeping raw value",
                attr, window, edge, raw_value,
            )
        return raw_value
    return min(nearby, key=lambda v: abs(v - raw_value))


def snap_candidates(
    candidates: list[dict],
    transcript_segments: list[TranscriptSegment],
    video_duration: float,
    logger: logging.Logger | None = None,
) -> list[dict]:
    """Corrects parse_candidates' output by snapping each segment's start/end to the
    nearest real TranscriptWord boundary, then re-validates exactly as parse_candidates
    did (range/min-length/chronology/total-duration) since snapping can shift a
    segment enough to fail one of those checks. This is what guarantees the final cut
    never lands mid-word, regardless of how close Claude's own guess was.
    """
    all_words: list[TranscriptWord] = sorted(
        (w for seg in transcript_segments for w in seg.words),
        key=lambda w: w.start,
    )

    snapped: list[dict] = []
    for i, candidate in enumerate(candidates):
        segments: list[tuple[float, float]] = []
        segments_valid = True
        for seg in candidate["segments"]:
            snapped_start = _snap_edge(
                seg["start"], all_words, "start", SNAP_WINDOW_SECONDS, logger
            )
            snapped_end = _snap_edge(
                seg["end"], all_words, "end", SNAP_WINDOW_SECONDS, logger
            )

            if not (0 <= snapped_start < snapped_end <= video_duration):
                segments_valid = False
                if logger:
                    logger.info(
                        "candidate #%d rejected after snapping: segment out of range "
                        "(start=%.2f end=%.2f video_duration=%.1f)",
                        i, snapped_start, snapped_end, video_duration,
                    )
                break
            if snapped_end - snapped_start < MIN_SEGMENT_SECONDS:
                segments_valid = False
                if logger:
                    logger.info(
                        "candidate #%d rejected after snapping: segment length %.1fs "
                        "below minimum %ds",
                        i, snapped_end - snapped_start, MIN_SEGMENT_SECONDS,
                    )
                break
            segments.append((snapped_start, snapped_end))
        if not segments_valid:
            continue

        if any(segments[j][1] > segments[j + 1][0] for j in range(len(segments) - 1)):
            if logger:
                logger.info(
                    "candidate #%d rejected after snapping: segments not "
                    "chronological/non-overlapping (%r)", i, segments,
                )
            continue

        total_seconds = sum(e - s for s, e in segments)
        if not (MIN_CLIP_SECONDS <= total_seconds <= MAX_CLIP_SECONDS):
            if logger:
                logger.info(
                    "candidate #%d rejected after snapping: total clip length %.1fs "
                    "outside [%d, %d]", i, total_seconds, MIN_CLIP_SECONDS, MAX_CLIP_SECONDS,
                )
            continue

        snapped.append({
            "segments": [{"start": s, "end": e} for s, e in segments],
            "reason": candidate["reason"],
        })

    if logger:
        logger.info("%d/%d candidates survived snapping", len(snapped), len(candidates))
        for v in snapped:
            span = " + ".join(f"[{s['start']:.1f}-{s['end']:.1f}]" for s in v["segments"])
            logger.info("  %s %s", span, v["reason"])

    if not snapped:
        raise HighlightError(
            "no valid highlight candidates survived snapping", stage="detection"
        )

    return snapped
