import json
import os
import re

import anthropic

_CLIENT: anthropic.Anthropic | None = None

_SYSTEM_PROMPT = """You are a medical clinic data extraction specialist.
Extract structured information from clinic website text.
Always respond with valid JSON only — no markdown fences, no commentary."""

_USER_PROMPT = """Extract all providers, staff members, and clinic contact details from the text below.

Return a JSON object with this exact structure:
{{
  "clinic_name": "string or null",
  "address": "string or null",
  "phone": "string or null",
  "email": "string or null",
  "website": "string or null",
  "providers": [
    {{
      "name": "string",
      "title": "string or null",
      "specialty": "string or null",
      "current_employer": "string or null",
      "phone": "string or null",
      "email": "string or null",
      "license_states": ["list of US state abbreviations where license is mentioned, e.g. CO, CA"]
    }}
  ]
}}

Rules:
- Include every named provider/doctor/staff member you can find.
- Use null for any field not present in the text. Use [] for license_states if none mentioned.
- For current_employer: capture the hospital, clinic, or organization the provider currently works at. If the whole page is for one clinic, use that clinic name as the employer.
- For license_states: look for phrases like "licensed in", "board certified in [state]", "practicing in [state]", or any explicit state license mention. Use two-letter state abbreviations.
- Normalise phone numbers to (XXX) XXX-XXXX format when possible.
- Do not invent or infer data that is not explicitly in the text.

WEBSITE TEXT:
{text}"""


def _client() -> anthropic.Anthropic:
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _CLIENT


def extract_providers(scraped_text: str, website_url: str) -> dict:
    """Send scraped text to Claude and return structured extraction."""
    # Truncate to avoid excessive token usage (~100k chars ≈ ~25k tokens)
    text = scraped_text[:100_000]

    message = _client().messages.create(
        model="claude-sonnet-4-5",
        max_tokens=4096,
        system=_SYSTEM_PROMPT,
        messages=[
            {"role": "user", "content": _USER_PROMPT.format(text=text)}
        ],
    )

    raw = message.content[0].text.strip()
    # Strip markdown code fences if Claude wraps the response in them
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw).strip()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # Claude returned something non-JSON; return a minimal error structure
        print(f"[extractor] JSON parse error. Raw response:\n{raw[:500]}")
        data = {"error": "parse_failed", "raw": raw}

    data["_source_url"] = website_url
    data["_usage"] = {
        "input_tokens": message.usage.input_tokens,
        "output_tokens": message.usage.output_tokens,
    }

    if "providers" in data:
        data["providers"] = _deduplicate_providers(data["providers"])

    return data


_TITLE_STRIP = re.compile(
    r"\b(dr\.?|mr\.?|mrs\.?|ms\.?|prof\.?|dvm|vmd|md|do|np|pa|fnp|rn|aprn|dds|dpm)\b",
    re.IGNORECASE,
)


def _normalise_name(raw: str) -> tuple[str, str]:
    """Return (first_token, last_token) stripped of titles and middle initials."""
    cleaned = _TITLE_STRIP.sub("", raw).strip()
    parts = [p for p in cleaned.split() if len(p) > 1 and not re.match(r"^[A-Z]\.$", p)]
    if not parts:
        return ("", "")
    return (parts[0].lower(), parts[-1].lower())


def _deduplicate_providers(providers: list[dict]) -> list[dict]:
    """Remove providers that are clearly the same person listed under two name formats."""
    seen: dict[tuple[str, str], int] = {}  # (first, last) → index in output
    out: list[dict] = []
    for p in providers:
        key = _normalise_name(p.get("name") or "")
        if key == ("", ""):
            out.append(p)
            continue
        if key in seen:
            # Keep whichever entry has more non-null fields
            existing = out[seen[key]]
            if sum(1 for v in p.values() if v) > sum(1 for v in existing.values() if v):
                out[seen[key]] = p
        else:
            seen[key] = len(out)
            out.append(p)
    return out
