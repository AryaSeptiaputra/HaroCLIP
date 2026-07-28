import json
import logging
import os
import re
import time

from src.highlights.exceptions import HighlightError
from src.highlights.prompt import build_messages
from src.transcription.schemas import TranscriptSegment

LLM_MODEL_NAME = os.getenv("HIGHLIGHT_LLM_MODEL", "claude-sonnet-5")
# claude-sonnet-5 runs adaptive thinking by default when `thinking` is omitted, and
# max_tokens is a hard cap on thinking + text output combined — 2048 was observed to
# be entirely consumed by thinking on a real transcript, leaving zero text/JSON
# output. 8192 leaves headroom for both on top of a full 5-10-candidate JSON array
# (a few KB of text, well under 1k tokens).
MAX_NEW_TOKENS = 8192
MAX_CANDIDATES = 10
MIN_CLIP_SECONDS = 30
MAX_CLIP_SECONDS = 180

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
    response = client.messages.create(
        model=LLM_MODEL_NAME,
        max_tokens=MAX_NEW_TOKENS,
        system=system_prompt,
        messages=user_messages,
    )
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
        start = item.get("start")
        end = item.get("end")
        reason = item.get("reason")
        if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
            if logger:
                logger.info("candidate #%d rejected: start/end not numeric (start=%r end=%r)", i, start, end)
            continue
        if not isinstance(reason, str) or not reason.strip():
            if logger:
                logger.info("candidate #%d rejected: missing/empty reason", i)
            continue
        start, end = float(start), float(end)
        if not (0 <= start < end <= video_duration):
            if logger:
                logger.info(
                    "candidate #%d rejected: start/end out of range (start=%.1f end=%.1f video_duration=%.1f)",
                    i, start, end, video_duration,
                )
            continue
        if not (MIN_CLIP_SECONDS <= end - start <= MAX_CLIP_SECONDS):
            if logger:
                logger.info(
                    "candidate #%d rejected: clip length %.1fs outside [%d, %d]",
                    i, end - start, MIN_CLIP_SECONDS, MAX_CLIP_SECONDS,
                )
            continue
        valid.append({"start": start, "end": end, "reason": reason.strip()})

    if logger:
        logger.info("%d/%d candidates survived validation", len(valid), len(candidates))
        for v in valid:
            logger.info("  [%.1f-%.1f] %s", v["start"], v["end"], v["reason"])

    if not valid:
        raise HighlightError(
            "no valid highlight candidates survived validation", stage="detection"
        )

    return valid[:MAX_CANDIDATES]
