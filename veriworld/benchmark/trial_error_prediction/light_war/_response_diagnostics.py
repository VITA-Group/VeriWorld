def vlm_call_info(vlm) -> dict:
    """Transport-level diagnostics for the most recent VLM call.

    A response cut off at the token limit and a response that is simply
    badly formatted are indistinguishable once ``finish_reason`` has
    been discarded — which is how the 2026-07-25 truncations were
    misread as reasoning failures. Returns ``{}`` for backends that do
    not report it. Lives here (not in a per-arm ``task.py``) so every
    harness arm records the same fields.
    """
    fr = getattr(vlm, "last_finish_reason", "") or ""
    if not fr:
        return {}
    info: dict = {"finish_reason": fr,
                  "raw_finish_reason": getattr(vlm, "last_raw_finish_reason", fr),
                  "requested_model": getattr(vlm, "model", None),
                  "returned_model": getattr(vlm, "last_returned_model", None),
                  "parts": getattr(vlm, "last_part_count", 0)}
    usage = getattr(vlm, "last_usage", None)
    # gemini-direct hands back an SDK object, the OpenAI-compatible
    # transport a plain dict — read both, and keep whichever token
    # counters that backend happens to report.
    for attr in ("prompt_token_count", "candidates_token_count",
                 "thoughts_token_count", "total_token_count",
                 "prompt_tokens", "completion_tokens", "total_tokens",
                 "input_tokens", "output_tokens"):
        v = (usage.get(attr) if isinstance(usage, dict)
             else getattr(usage, attr, None))
        if v is not None:
            info[attr] = v
    return info

def is_truncated(info: dict) -> bool:
    """True when the last call hit the output-token ceiling."""
    return "MAX_TOKENS" in str(info.get("finish_reason", ""))
