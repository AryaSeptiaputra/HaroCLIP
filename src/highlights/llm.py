import json
import logging
import os
import re
import time

from src.highlights.exceptions import HighlightError
from src.highlights.prompt import build_messages
from src.transcription.schemas import TranscriptSegment
from src.utils.logging import log_vram

LLM_MODEL_NAME = os.getenv("HIGHLIGHT_LLM_MODEL", "Qwen/Qwen2.5-7B-Instruct")
LLM_USE_4BIT = os.getenv("HIGHLIGHT_LLM_4BIT", "1") != "0"
LLM_DEVICE_MAP = os.getenv("HIGHLIGHT_LLM_DEVICE_MAP", "cuda")
MAX_NEW_TOKENS = 2048
MAX_CANDIDATES = 10
MIN_CLIP_SECONDS = 5
MAX_CLIP_SECONDS = 120

JSON_ARRAY_RE = re.compile(r"\[.*\]", re.DOTALL)


def generate_candidates(segments: list[TranscriptSegment], logger: logging.Logger | None = None) -> str:
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    if logger:
        logger.info("loading highlight LLM model=%s 4bit=%s device_map=%s", LLM_MODEL_NAME, LLM_USE_4BIT, LLM_DEVICE_MAP)
    load_start = time.monotonic()
    tokenizer = AutoTokenizer.from_pretrained(LLM_MODEL_NAME)
    if LLM_USE_4BIT:
        from transformers import BitsAndBytesConfig

        model = AutoModelForCausalLM.from_pretrained(
            LLM_MODEL_NAME,
            quantization_config=BitsAndBytesConfig(load_in_4bit=True),
            device_map=LLM_DEVICE_MAP,
        )
    else:
        # fp16 on CPU can hit "not implemented for Half" on some ops; fp32 on CPU,
        # fp16 on CUDA (matches production).
        dtype = torch.float16 if LLM_DEVICE_MAP == "cuda" else torch.float32
        model = AutoModelForCausalLM.from_pretrained(
            LLM_MODEL_NAME, torch_dtype=dtype, device_map=LLM_DEVICE_MAP
        )
    if logger:
        logger.info("highlight LLM loaded in %.1fs", time.monotonic() - load_start)
        log_vram(logger, "highlight LLM load")
    try:
        messages = build_messages(segments)
        prompt = tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
        inputs = tokenizer(prompt, return_tensors="pt").to(model.device)
        generate_start = time.monotonic()
        output_ids = model.generate(
            **inputs, max_new_tokens=MAX_NEW_TOKENS, do_sample=False
        )
        new_tokens = output_ids[0][inputs["input_ids"].shape[1] :]
        raw_response = tokenizer.decode(new_tokens, skip_special_tokens=True)
        if logger:
            logger.info("LLM generation done in %.1fs, %d new tokens", time.monotonic() - generate_start, len(new_tokens))
            logger.info("raw LLM response:\n%s", raw_response)
        return raw_response
    finally:
        del model
        if torch.cuda.is_available():
            torch.cuda.empty_cache()


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
