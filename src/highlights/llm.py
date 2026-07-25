import json
import os
import re

from src.highlights.exceptions import HighlightError
from src.highlights.prompt import build_messages
from src.transcription.schemas import TranscriptSegment

LLM_MODEL_NAME = os.getenv("HIGHLIGHT_LLM_MODEL", "Qwen/Qwen2.5-7B-Instruct")
LLM_USE_4BIT = os.getenv("HIGHLIGHT_LLM_4BIT", "1") != "0"
LLM_DEVICE_MAP = os.getenv("HIGHLIGHT_LLM_DEVICE_MAP", "cuda")
MAX_NEW_TOKENS = 2048
MAX_CANDIDATES = 10
MIN_CLIP_SECONDS = 5
MAX_CLIP_SECONDS = 120

JSON_ARRAY_RE = re.compile(r"\[.*\]", re.DOTALL)


def generate_candidates(segments: list[TranscriptSegment]) -> str:
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

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
    try:
        messages = build_messages(segments)
        prompt = tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
        inputs = tokenizer(prompt, return_tensors="pt").to(model.device)
        output_ids = model.generate(
            **inputs, max_new_tokens=MAX_NEW_TOKENS, do_sample=False
        )
        new_tokens = output_ids[0][inputs["input_ids"].shape[1] :]
        return tokenizer.decode(new_tokens, skip_special_tokens=True)
    finally:
        del model
        if torch.cuda.is_available():
            torch.cuda.empty_cache()


def parse_candidates(raw_text: str, video_duration: float) -> list[dict]:
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

    valid: list[dict] = []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        start = item.get("start")
        end = item.get("end")
        reason = item.get("reason")
        if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
            continue
        if not isinstance(reason, str) or not reason.strip():
            continue
        start, end = float(start), float(end)
        if not (0 <= start < end <= video_duration):
            continue
        if not (MIN_CLIP_SECONDS <= end - start <= MAX_CLIP_SECONDS):
            continue
        valid.append({"start": start, "end": end, "reason": reason.strip()})

    if not valid:
        raise HighlightError(
            "no valid highlight candidates survived validation", stage="detection"
        )

    return valid[:MAX_CANDIDATES]
