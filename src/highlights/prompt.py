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
- Each clip should be roughly 15-60 seconds, and no more than 90 seconds, unless the \
moment genuinely cannot be told any shorter.
- Each clip must be self-contained: understandable on its own, without needing the \
rest of the video for context.
- Start and end on natural sentence/thought boundaries.

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


def build_messages(segments: list[TranscriptSegment]) -> list[dict[str, str]]:
    transcript_text = format_transcript(segments)
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"Transcript:\n{transcript_text}\n\nReturn the JSON array now.",
        },
    ]
