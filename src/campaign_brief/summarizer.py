import base64
import logging
import os
import time

from src.campaign_brief.exceptions import CampaignBriefError

# Separate from HIGHLIGHT_LLM_MODEL on purpose — the two calls have unrelated
# cost/quality requirements (bounded extraction task vs. reasoning over a full
# transcript) and shouldn't be forced to move together.
CAMPAIGN_BRIEF_LLM_MODEL = os.getenv("CAMPAIGN_BRIEF_LLM_MODEL", "claude-haiku-4-5")
# The summary becomes IngestionJob.campaign_context, which rides along on every
# highlight-detection request for the job (src/highlights/prompt.py) — must stay
# short. Target ~150-400 words (~450 tokens); 1024 leaves headroom without inviting
# a long bulleted dump. claude-haiku-4-5 does not think by default when `thinking`
# is omitted, unlike claude-sonnet-5's adaptive-by-default behavior (see
# src/highlights/llm.py's MAX_NEW_TOKENS comment for that very different sizing
# rationale), so no thinking-budget headroom is needed here.
MAX_NEW_TOKENS = 1024
MAX_PDF_BYTES = 20 * 1024 * 1024  # well under the API's 32MB request cap

SYSTEM_PROMPT = """You convert a campaign brief document into a short, freeform \
descriptive prompt that will steer selection of short-form video highlight clips \
toward this campaign's intent. The brief may include charts, tables, and images as \
well as text — read all of it.

Write your output as flowing descriptive prose (2-4 short paragraphs) — NOT a \
bulleted list, NOT JSON, NOT a structured outline. It will be inserted directly as a \
paragraph of context into another AI's instructions, so it must read naturally, e.g. \
"This campaign promotes [product] to [audience], emphasizing [themes]... prioritize \
moments that discuss [X, Y, Z] and avoid dwelling on [...]".

Include, where the brief actually covers them: the product/service and its core \
value proposition, the target audience, key messages or themes, desired tone, and \
any specific topics or moments to prioritize or avoid. Omit anything the brief \
doesn't cover — do not invent or assume information that isn't in the document. \
Ignore administrative content unrelated to content strategy (budgets, timelines, \
contacts, legal boilerplate).

Keep the entire output between roughly 150 and 400 words. Respond with ONLY the \
descriptive prompt text — no preamble, no headings, no markdown, no "Summary:" \
label."""


def summarize_campaign_brief(
    pdf_bytes: bytes, logger: logging.Logger | None = None, api_key: str | None = None
) -> str:
    import anthropic

    if len(pdf_bytes) > MAX_PDF_BYTES:
        raise CampaignBriefError(
            f"campaign brief PDF too large ({len(pdf_bytes)} bytes, max {MAX_PDF_BYTES})",
            stage="validation",
        )
    if not pdf_bytes.startswith(b"%PDF-"):
        raise CampaignBriefError("file does not look like a valid PDF", stage="validation")

    if logger:
        logger.info(
            "calling Claude API model=%s for campaign brief summarization (%d bytes PDF)",
            CAMPAIGN_BRIEF_LLM_MODEL, len(pdf_bytes),
        )

    # api_key comes straight from the request (never logged, never persisted) —
    # falls back to the SDK's own ANTHROPIC_API_KEY env var lookup when not given.
    client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
    call_start = time.monotonic()
    try:
        response = client.messages.create(
            model=CAMPAIGN_BRIEF_LLM_MODEL,
            max_tokens=MAX_NEW_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "document",
                            "source": {
                                "type": "base64",
                                "media_type": "application/pdf",
                                "data": base64.standard_b64encode(pdf_bytes).decode("utf-8"),
                            },
                        },
                        {"type": "text", "text": "Summarize this campaign brief as instructed."},
                    ],
                }
            ],
        )
    except anthropic.APIStatusError as e:
        raise CampaignBriefError(f"Claude API call failed: {e}", stage="summarization")
    except anthropic.APIConnectionError as e:
        raise CampaignBriefError(f"Claude API connection failed: {e}", stage="summarization")

    raw_response = "".join(block.text for block in response.content if block.type == "text")

    if logger:
        logger.info(
            "Claude API call done in %.1fs: input_tokens=%d output_tokens=%d stop_reason=%s",
            time.monotonic() - call_start, response.usage.input_tokens,
            response.usage.output_tokens, response.stop_reason,
        )

    if response.stop_reason == "max_tokens" and not raw_response.strip():
        raise CampaignBriefError(
            "Claude response truncated at max_tokens before producing any text "
            "output (likely all-thinking, no text blocks) — increase MAX_NEW_TOKENS",
            stage="summarization",
        )
    if not raw_response.strip():
        raise CampaignBriefError("Claude returned an empty summary", stage="summarization")

    return raw_response.strip()
