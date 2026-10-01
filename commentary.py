"""Quarterly reviews: Claude when configured, with legacy Gemini support."""
import os
import time

import requests

import gemini


CLAUDE_SYSTEM_PROMPT = """Write a factual quarterly portfolio review in 3-5 short paragraphs.
Use only supplied, precomputed numbers. Do not calculate new numbers or give investment advice.
Use PLN amounts selectively to explain scale, and quarterly history to describe supported trends.
Null means unavailable or unconfirmed, not zero. Mention material missing data.
Portfolio growth, net-worth growth and approximate returns have DIFFERENT asset scopes.
NEVER compare or subtract their percentages, or use their difference to explain contributions.
Only the same-scope return_breakdown can describe the balance change after contributions.
Call its residual a change after recorded contributions, not pure market performance.
Position value changes include trading: always say holding/position value changed, never that
Microsoft/Meta/etc rose or fell as a security. Never invent causes, buying/selling, or market news.
PPK balance changes include unknown payroll contributions, so are not investment returns.
Historical comparisons and extrema must refer only to the provided snapshots, never lifetime history.
Respect snapshot dates and missing quarters. Account types are categories, not withdrawal advice.
No headings, bullets, markdown, predictions, moralising or recommendations.
"""


def is_configured():
    return bool(os.environ.get("ANTHROPIC_API_KEY")) or gemini.is_configured()


def get_model():
    if os.environ.get("ANTHROPIC_API_KEY"):
        return os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5")
    return gemini.get_model()


def generate_commentary(payload_json, claude_payload_json=None):
    """Return text and actual model; never label fallback output as Claude."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return gemini.generate_commentary(payload_json), gemini.get_model()
    model = get_model()
    headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
    if os.environ.get("ANTHROPIC_WORKSPACE_ID"):
        headers["anthropic-workspace-id"] = os.environ["ANTHROPIC_WORKSPACE_ID"]
    for attempt in range(3):
        try:
            response = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers=headers,
                json={"model": model, "max_tokens": 4000,
                      "thinking": {"type": "between_tools"},
                      "system": CLAUDE_SYSTEM_PROMPT,
                      "messages": [{"role": "user", "content": claude_payload_json or payload_json}]},
                timeout=(3, 10),
            )
        except requests.RequestException:
            response = None
        if response is not None and response.status_code == 200:
            try:
                data = response.json()
                text = "".join(part.get("text", "") for part in data.get("content", [])
                               if part.get("type") == "text").strip()
                complete = data.get("stop_reason") == "end_turn"
            except (ValueError, TypeError, AttributeError):
                raise ValueError("Claude returned an invalid response.") from None
            if not text or not complete:
                raise ValueError("Claude did not return a complete review. Please try again.")
            return text, model
        status = response.status_code if response is not None else None
        if status not in (None, 429, 500, 502, 503, 504, 529):
            if status in (401, 403):
                raise ValueError("Claude rejected the API key. Check ANTHROPIC_API_KEY and access.")
            if status == 404:
                raise ValueError("Claude model unavailable. Check ANTHROPIC_MODEL and model access.")
            raise ValueError(f"Claude API error ({status}). Check API billing and configuration.")
        if attempt < 2:
            time.sleep(2 ** attempt)
    if gemini.is_configured():
        try:
            return gemini.generate_commentary(payload_json), gemini.get_model()
        except ValueError:
            raise ValueError("Claude and Gemini are temporarily unavailable. Your saved review is unchanged.") from None
    raise ValueError("Claude is temporarily unavailable after three attempts. Your saved review is unchanged.")
