"""One-off probe: which gateway-pingable models actually accept an mp4?

Sends the same 6.8 MB mp4 to 6 candidate models via the OpenAI-compatible
gateway, using a `video_url` content block with a base64 data URL.
Reports HTTP status + first 200 chars of response per model.
"""
from __future__ import annotations

import base64
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

VIDEO_PATH = Path(r"C:\Users\yanzh\projects\VeriWorld\veriworld\results\computational\feedback\surface_billiards\seed_0000_20260420_020327\gpt-4o\round_00_observe.mp4")
CFG_PATH = Path(r"C:\Users\yanzh\projects\VeriWorld\model_configs.json")

CANDIDATES = [
    "qwen-vl-max",
    "qwen3-vl-235b",
    "doubao-seed-1-6-flash",
    "doubao-seed-1-6",
    "gemini-3-flash",
    "gemini-3-pro-thinking",
]

PROMPT = "What does this video show? Answer in one sentence."


def load_candidate_configs():
    entries = json.loads(CFG_PATH.read_text(encoding="utf-8"))
    by_name = {e["name"]: e for e in entries}
    missing = [n for n in CANDIDATES if n not in by_name]
    if missing:
        raise SystemExit(f"Missing in model_configs.json: {missing}")
    return [by_name[n] for n in CANDIDATES]


def build_payload(model: str, data_url: str) -> dict:
    return {
        "model": model,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "video_url", "video_url": {"url": data_url}},
                {"type": "text", "text": PROMPT},
            ],
        }],
        "max_tokens": 512,
        "temperature": 0.3,
    }


def probe_one(entry: dict, data_url: str) -> tuple[str, str]:
    url = entry["base_url"].rstrip("/") + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {entry['api_key']}",
        "Content-Type": "application/json",
    }
    payload = build_payload(entry["model"], data_url)
    t0 = time.time()
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=240)
        dt = time.time() - t0
    except requests.exceptions.RequestException as e:
        dt = time.time() - t0
        return entry["name"], f"[{dt:5.1f}s] NETWORK ERROR: {type(e).__name__}: {e}"

    status = resp.status_code
    try:
        body = resp.json()
    except ValueError:
        body = resp.text[:400]

    if status == 200:
        try:
            text = body["choices"][0]["message"]["content"]
            return entry["name"], f"[{dt:5.1f}s] 200 OK -> {text[:200]!r}"
        except (KeyError, IndexError, TypeError):
            return entry["name"], f"[{dt:5.1f}s] 200 but odd body: {str(body)[:300]}"
    else:
        snippet = body if isinstance(body, str) else json.dumps(body)[:400]
        return entry["name"], f"[{dt:5.1f}s] HTTP {status}: {snippet}"


def main():
    if not VIDEO_PATH.exists():
        raise SystemExit(f"Video not found: {VIDEO_PATH}")
    size_mb = VIDEO_PATH.stat().st_size / 1e6
    print(f"Video: {VIDEO_PATH.name} ({size_mb:.2f} MB)")
    b64 = base64.b64encode(VIDEO_PATH.read_bytes()).decode("ascii")
    data_url = f"data:video/mp4;base64,{b64}"
    print(f"Base64 length: {len(b64)/1e6:.2f} MB")

    entries = load_candidate_configs()

    print(f"\nProbing {len(entries)} models in parallel...\n")
    results: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=len(entries)) as pool:
        futs = {pool.submit(probe_one, e, data_url): e["name"] for e in entries}
        for fut in as_completed(futs):
            name, msg = fut.result()
            results[name] = msg
            print(f"{name:32s} {msg}")

    print("\n--- summary ---")
    for name in CANDIDATES:
        print(f"{name:32s} {results.get(name, '(no result)')}")


if __name__ == "__main__":
    main()
