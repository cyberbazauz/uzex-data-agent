from __future__ import annotations

import json
import os
import sys
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1 else os.getenv("UZEX_API_BASE", "http://127.0.0.1:8000")).rstrip("/") + "/api/v1"


def get(path: str) -> dict:
    with urllib.request.urlopen(BASE + path, timeout=10) as response:
        return json.load(response)


def post(path: str, payload: dict) -> dict:
    request = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


for path in ("/health", "/ready", "/meta", "/examples"):
    data = get(path)
    print(path, json.dumps(data, ensure_ascii=False)[:400])

QUESTIONS = [
    "Сколько сделок было в 2024 году?",
    "Сколько сделок было в 2027 году?",
    "Покажи брокера KAHIR",
    "Спрогнозируй количество сделок в 2027 году",
    "2024-yilda nechta bitim bo'ldi?",
    "Битим рақами 100001",
]
for question in QUESTIONS:
    data = post("/chat", {"question": question})
    print("=" * 70)
    print("Q:", question)
    print("status:", data.get("status"), "locale:", data.get("language"))
    print("answer:", (data.get("answer") or "")[:240].replace("\n", " | "))
    print(
        "data:", json.dumps(data.get("data"), ensure_ascii=False)[:160],
        "| evidence matched:", (data.get("evidence") or {}).get("matched_rows"),
        "| request_id:", data.get("request_id"),
    )
