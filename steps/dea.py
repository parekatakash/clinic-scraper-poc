"""
DEA Registration verification.
1. Checksum validation — works offline, confirms the number is structurally valid.
2. DEA web lookup — best-effort scrape of the DEA Diversion registrant search.
   Falls back gracefully if the site blocks or changes its interface.
"""

import re
import requests
from bs4 import BeautifulSoup

_DEA_SEARCH_URL = "https://www.deadiversion.usdoj.gov/webforms/dtSearch/main.jsp"
_DEA_MANUAL_URL = "https://www.deadiversion.usdoj.gov/webforms/deaRegistrationSearch.jsp"

# DEA first-letter registrant type codes
_DEA_TYPES = {
    "A": "Researcher / Teaching",
    "B": "Practitioner (MD/DO/DVM/NP/PA/DDS)",
    "C": "Mid-Level Practitioner",
    "D": "Distributor",
    "E": "Manufacturer",
    "F": "Manufacturer (NADDIS)",
    "G": "Researcher",
    "H": "Analytical Lab",
    "J": "Importer",
    "K": "Exporter",
    "L": "Reverse Distributor",
    "M": "Narcotic Treatment Program",
    "P": "Researcher (Schedule I)",
    "R": "Narcotic Treatment Program",
    "S": "Pharmacist",
    "T": "Narcotic Treatment Program",
    "U": "Researcher",
    "X": "Suboxone Treatment (DATA Waiver)",
}


# ─────────────────────────────────────────────────────────────────────────────
# Public interface
# ─────────────────────────────────────────────────────────────────────────────

def verify_dea(dea_number: str | None = None,
               first_name: str | None = None,
               last_name: str | None = None,
               state: str | None = None) -> dict:
    """
    Verify DEA registration.
    - dea_number given → validate checksum + attempt online lookup by number
    - name + state given → attempt name-based DEA lookup
    Returns a dict with dea_number, dea_registered, dea_status, dea_schedules, dea_lookup_url.
    """
    result: dict = {}

    if dea_number:
        result = _validate_checksum(dea_number)
        if result.get("dea_valid_format"):
            online = _lookup_by_number(dea_number)
            result.update(online)

    elif first_name and last_name:
        result = _lookup_by_name(first_name, last_name, state)

    result["dea_lookup_url"] = _DEA_MANUAL_URL
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Checksum validation
# ─────────────────────────────────────────────────────────────────────────────

def _validate_checksum(raw: str) -> dict:
    clean = raw.upper().replace("-", "").replace(" ", "")
    if len(clean) != 9 or not clean[:2].isalpha() or not clean[2:].isdigit():
        return {"dea_number": raw, "dea_valid_format": False, "dea_valid_checksum": False}

    digits = [int(d) for d in clean[2:]]
    odd_sum = digits[0] + digits[2] + digits[4]
    even_sum = (digits[1] + digits[3] + digits[5]) * 2
    valid = ((odd_sum + even_sum) % 10) == digits[6]

    return {
        "dea_number": clean,
        "dea_valid_format": True,
        "dea_valid_checksum": valid,
        "dea_registrant_type": _DEA_TYPES.get(clean[0], "Unknown"),
        "dea_registered": valid,  # Best-guess without live lookup
    }


# ─────────────────────────────────────────────────────────────────────────────
# DEA web lookups (best-effort; fall back gracefully)
# ─────────────────────────────────────────────────────────────────────────────

def _lookup_by_number(dea_number: str) -> dict:
    try:
        resp = requests.get(
            "https://apps.deadiversion.usdoj.gov/webforms2/spring/lookupDeaNumbers",
            params={"lookupType": "DEANumber", "deaNumber": dea_number},
            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json, text/html"},
            timeout=10,
        )
        if resp.status_code == 200:
            return _parse_dea_response(resp)
    except Exception:
        pass
    return {}


def _lookup_by_name(first: str, last: str, state: str | None) -> dict:
    params = {
        "externalSearch": "y",
        "first": first,
        "last": last,
    }
    if state:
        params["state"] = state
    try:
        resp = requests.get(
            _DEA_SEARCH_URL,
            params=params,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10,
        )
        if resp.status_code == 200:
            return _parse_dea_response(resp)
    except Exception:
        pass
    return {}


def _parse_dea_response(resp: requests.Response) -> dict:
    """Try JSON first, then HTML table scraping."""
    try:
        data = resp.json()
        if isinstance(data, dict):
            return {
                "dea_number": data.get("deaNumber") or data.get("registrationNumber"),
                "dea_registered": True,
                "dea_status": data.get("status") or data.get("activityCode"),
                "dea_schedules": data.get("schedules") or data.get("drugSchedules"),
                "dea_expiry": data.get("expirationDate"),
            }
    except Exception:
        pass

    # HTML table fallback
    try:
        soup = BeautifulSoup(resp.text, "html.parser")
        rows = []
        for table in soup.find_all("table"):
            for row in table.find_all("tr"):
                cells = [c.get_text(strip=True) for c in row.find_all("td")]
                if cells:
                    rows.append(cells)
        if rows:
            flat = " ".join(" ".join(r) for r in rows[:5])
            dea_match = re.search(r"\b[A-Z]{2}\d{7}\b", flat)
            return {
                "dea_number": dea_match.group() if dea_match else None,
                "dea_registered": True,
                "dea_raw_result": flat[:300],
            }
    except Exception:
        pass

    return {}
