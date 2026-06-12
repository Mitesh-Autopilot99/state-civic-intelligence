"""Batched LLM classification via OpenRouter. Frugal: keyword-filtered posts only,
25 per request, compact prompt, JSON output. Falls back to a second model on error.
After classification the raw post text is discarded by the pipeline — only the
issue-level result is kept."""
import json
import logging
import os
import re
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")
log = logging.getLogger("classifier")

API_URL = "https://openrouter.ai/api/v1/chat/completions"
BATCH_SIZE = 25
CATEGORIES = ["potholes_roads", "bins_waste", "antisocial_behaviour", "housing",
              "transport", "planning", "nhs_access", "parks_environment",
              "safety_crime", "council_services", "other"]

PROMPT = """You classify UK local social media posts for a politically neutral civic platform.
For EACH numbered post return a JSON object:
{{"n": <post number>, "is_civic": true/false, "category": one of {cats},
"urgency": 1-5, "specificity": 1-5, "area": "<specific place named in the post, or empty string>",
"summary": "<one neutral line describing the ISSUE itself, max 20 words, no usernames, no quotes>"}}

is_civic = true only if a local council/MP could plausibly act on it.
Return ONLY a JSON array of these objects, nothing else.

Posts:
{posts}"""


def _call(model: str, content: str) -> str:
    r = requests.post(
        API_URL,
        headers={"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}",
                 "Content-Type": "application/json"},
        json={"model": model,
              "messages": [{"role": "user", "content": content}],
              "temperature": 0.1},
        timeout=120,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def _extract_json(text: str) -> list:
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if not m:
        raise ValueError("no JSON array in model output")
    return json.loads(m.group(0))


def classify(posts: list[dict]) -> list[dict]:
    """Attach classification fields to each post dict. Unclassifiable posts dropped."""
    primary = os.environ.get("CLASSIFIER_MODEL", "deepseek/deepseek-chat:free")
    fallback = os.environ.get("CLASSIFIER_FALLBACK_MODEL", "meta-llama/llama-3.3-70b-instruct:free")
    results = []
    for i in range(0, len(posts), BATCH_SIZE):
        batch = posts[i:i + BATCH_SIZE]
        listing = "\n".join(
            f"{n+1}. [r/{p['subreddit']}, {p['city']}] {p['title']} — {p['body'][:400]}"
            for n, p in enumerate(batch)
        )
        content = PROMPT.format(cats=CATEGORIES, posts=listing)
        parsed = None
        for model in (primary, fallback):
            try:
                parsed = _extract_json(_call(model, content))
                break
            except Exception as e:
                log.warning("model %s failed on batch %d: %s", model, i // BATCH_SIZE, e)
                time.sleep(5)
        if parsed is None:
            log.error("Both models failed on batch %d — skipping %d posts", i // BATCH_SIZE, len(batch))
            continue
        by_n = {item.get("n"): item for item in parsed if isinstance(item, dict)}
        for n, p in enumerate(batch):
            item = by_n.get(n + 1)
            if not item or not item.get("is_civic"):
                continue
            results.append({**p,
                            "category": item.get("category", "other"),
                            "urgency": int(item.get("urgency", 2)),
                            "specificity": int(item.get("specificity", 2)),
                            "area": (item.get("area") or "").strip(),
                            "summary": (item.get("summary") or p["title"])[:140]})
        time.sleep(3)  # stay under 20 req/min free-tier limit with margin
    log.info("Classified %d posts -> %d civic items", len(posts), len(results))
    return results
