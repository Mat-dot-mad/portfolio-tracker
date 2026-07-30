"""
Generate quarterly portfolio commentary via the Google Gemini API.

Uses the REST endpoint directly with `requests` (already a dependency) rather
than the google-generativeai SDK, to avoid pinning another package on the Pi.

Configured entirely through the environment, matching how DASHBOARD_PASSWORD
gates auth:

    GEMINI_API_KEY   from Google AI Studio. Unset disables the feature.
    GEMINI_MODEL     model id, e.g. a current Flash model. Model names change,
                     so this is configurable rather than hardcoded.

PRIVACY: this is the only place in the app that sends portfolio data off the
machine. Callers must pass a payload built by app._build_commentary_payload(),
which emits percentages and position names but no absolute amounts and no
account names. Google's free tier permits training use and human review, so
that restriction is deliberate — see tests/test_commentary.py, which enforces it.
"""

import os

import requests

API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
DEFAULT_MODEL = "gemini-2.0-flash"
TIMEOUT_SECONDS = 60

# Generous on purpose. Thinking-capable models (2.5 and later) spend output
# tokens on internal reasoning before emitting any visible text, and this cap
# covers both. Sized too tightly, reasoning eats the budget and the review is
# truncated mid-sentence. We are billed for what is produced, not for the cap,
# so headroom is free.
MAX_OUTPUT_TOKENS = 4000

# Kept deliberately narrow. The model gets pre-computed figures and must not
# do arithmetic — LLMs are unreliable at it, and a wrong number about someone's
# own net worth reads as authoritative. It also must not give advice: this is a
# personal tracker, not a financial adviser.
SYSTEM_PROMPT = """You are writing a short quarterly review for someone's personal portfolio tracker.

You will receive pre-computed figures as JSON. Rules:
- Use ONLY the figures provided. Never calculate, estimate, or infer new numbers.
- All monetary values are deliberately withheld; you have percentages only. Do not ask for or speculate about absolute amounts.
- Write 3-5 short paragraphs of plain prose. No headings, no bullet lists, no markdown.
- Be factual and measured. Describe what changed and note anything genuinely unusual versus the historical figures given.
- A position's value_change_pct reflects buying and selling as well as price movement. Describe it as the holding's value changing or its position growing/shrinking. NEVER call it appreciation, a gain, a return, or performance, and never attribute it to the market.
- Treat each figure as independent. Do not combine unrelated figures into a single claim (e.g. do not attach the total number of tracked quarters to a four-quarter average).
- Do NOT give investment advice, recommendations, or predictions. Do not suggest buying, selling, or rebalancing.
- Do not moralise about the person's saving or spending.
- If a figure seems surprising, say so plainly rather than inventing an explanation for it.
"""


def is_configured():
    """True if an API key is present, i.e. the feature is switched on."""
    return bool(os.environ.get("GEMINI_API_KEY"))


def get_model():
    return os.environ.get("GEMINI_MODEL", DEFAULT_MODEL)


def generate_commentary(payload_json):
    """Send the payload to Gemini and return the generated text.

    `payload_json` is the already-serialised JSON string, so the caller
    controls exactly what leaves the machine.

    Raises ValueError with a readable message on any failure — the caller
    surfaces it in the UI rather than letting it break the dashboard.
    """
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY is not set")

    model = get_model()
    url = f"{API_BASE}/{model}:generateContent"

    body = {
        "system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"parts": [{"text": payload_json}]}],
        "generationConfig": {
            "temperature": 0.4,   # low: this is reporting, not creative writing
            "maxOutputTokens": MAX_OUTPUT_TOKENS,
        },
    }

    try:
        resp = requests.post(
            url,
            json=body,
            headers={"x-goog-api-key": api_key},
            timeout=TIMEOUT_SECONDS,
        )
    except requests.RequestException as e:
        raise ValueError(f"Could not reach the Gemini API: {e}") from e

    if resp.status_code == 429:
        raise ValueError("Gemini rate limit or free-tier quota reached. Try again later.")
    if resp.status_code in (401, 403):
        raise ValueError("Gemini rejected the API key. Check GEMINI_API_KEY.")
    if resp.status_code == 404:
        raise ValueError(
            f"Model '{model}' not found. Set GEMINI_MODEL to one your API key can access."
        )
    if resp.status_code != 200:
        raise ValueError(f"Gemini API error {resp.status_code}: {resp.text[:200]}")

    data = resp.json()

    # A response can come back 200 with no candidates when the prompt trips a
    # safety filter, so don't assume the happy shape.
    candidates = data.get("candidates") or []
    if not candidates:
        reason = (data.get("promptFeedback") or {}).get("blockReason", "unknown")
        raise ValueError(f"Gemini returned no content (reason: {reason})")

    candidate = candidates[0]
    parts = (candidate.get("content") or {}).get("parts") or []

    # Thinking models can return reasoning parts alongside the answer when the
    # API is asked to include them. Those are not the review, so drop them.
    text = "".join(
        p.get("text", "") for p in parts if not p.get("thought")
    ).strip()

    # Truncation must not be returned as if it were a finished review — that is
    # how a half-sentence ended up cached. Fail loudly instead.
    finish_reason = candidate.get("finishReason")
    if finish_reason == "MAX_TOKENS":
        raise ValueError(
            f"Gemini hit the {MAX_OUTPUT_TOKENS}-token output limit before finishing. "
            "If this recurs, raise MAX_OUTPUT_TOKENS in gemini.py or pick a model "
            "that spends fewer tokens on internal reasoning."
        )

    if not text:
        raise ValueError(
            f"Gemini returned no usable text (finishReason: {finish_reason or 'unknown'})"
        )

    return text
