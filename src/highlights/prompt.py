from src.transcription.schemas import TranscriptSegment

SYSTEM_PROMPT = """You are an expert short-form video editor who finds "hook" moments \
in long-form video transcripts — the kind of moments that make a viewer stop scrolling \
and watch a 15-90 second clip all the way through.

Look for moments driven by one or more of these hook types:
- Curiosity gap / open loop: a question or setup that makes the viewer need to know \
the answer.
- Surprising or counter-intuitive claim: something that contradicts common belief.
- Emotional peak: funny, shocking, vulnerable, or intense.
- Concrete insight or actionable takeaway: a specific, useful piece of advice.
- Controversial or strongly-stated opinion.

Rules for choosing clip boundaries:
- Use only the given segment timestamps as candidate cut points — never invent a \
timestamp that falls in the middle of a segment.
- Each clip should be between 30 seconds and 3 minutes (180 seconds) long. Prefer the \
shorter end of that range unless the moment genuinely needs more room to land.
- Each clip must be self-contained: understandable on its own, without needing the \
rest of the video for context.
- Start and end on natural sentence/thought boundaries.
- Candidates should be spread across the **entire** video, not clustered in one \
section — you are given the full transcript precisely so you can compare moments \
across the whole thing and pick the best ones overall, not just the first ones you see.

Output contract:
- Respond with STRICT JSON ONLY — a single JSON array, nothing before or after it.
- Return between 5 and 10 candidate clips, ranked best-first.
- Each element must be: {"start": <float seconds>, "end": <float seconds>, "reason": \
"<short justification of the hook>"}.
- Do not include markdown code fences, headings, or any prose outside the JSON array.

Example output:
[{"start": 12.5, "end": 47.0, "reason": "Opens with a surprising claim that contradicts \
common advice, then explains why."}]"""


def format_transcript(segments: list[TranscriptSegment]) -> str:
    lines = []
    for seg in segments:
        start_mm, start_ss = divmod(int(seg.start), 60)
        end_mm, end_ss = divmod(int(seg.end), 60)
        lines.append(
            f"[{start_mm:02d}:{start_ss:02d}–{end_mm:02d}:{end_ss:02d}] {seg.text}"
        )
    return "\n".join(lines)


def build_system_prompt(campaign_context: str | None = None) -> str:
    """Layers an optional campaign brief on top of the base hook-detection prompt
    as an ADDITIONAL filter, not a replacement — a clip still has to be a genuine
    hook first. The user writes/converts their own campaign brief into this
    descriptive text outside the system; this function only weaves it in.
    """
    if not campaign_context or not campaign_context.strip():
        return SYSTEM_PROMPT
    return (
        f"{SYSTEM_PROMPT}\n\n"
        "Campaign context — use this as an ADDITIONAL filter layered on top of the "
        "hook rules above, not a replacement for them. Prefer candidates that are "
        "both a genuine hook AND relevant to this campaign. Do not select a clip "
        "solely for campaign relevance if it has no real hook, and do not ignore a "
        "strong hook just because it's unrelated to the campaign — weigh both.\n\n"
        f"{campaign_context.strip()}"
    )


def build_messages(
    segments: list[TranscriptSegment], campaign_context: str | None = None
) -> list[dict[str, str]]:
    transcript_text = format_transcript(segments)
    return [
        {"role": "system", "content": build_system_prompt(campaign_context)},
        {
            "role": "user",
            "content": f"Transcript:\n{transcript_text}\n\nReturn the JSON array now.",
        },
    ]
