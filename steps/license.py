"""
License verification using:
1. FSMB MED API  — official Federation of State Medical Boards API
   Requires: FSMB_CLIENT_ID + FSMB_CLIENT_SECRET in .env
   Register free at: https://developer.fsmb.org
2. NPI Registry  — already enriched upstream; used as base data
3. State Medical Board direct lookup — fallback for key states
"""

import os
import time
import requests
from bs4 import BeautifulSoup

# ── FSMB MED API ─────────────────────────────────────────────────────────────
_FSMB_TOKEN_URL = "https://identity.fsmb.org/connect/token"
_FSMB_SEARCH_URL = "https://services-med.fsmb.org/v2/practitioners/search"
_FSMB_TOKEN_CACHE: dict = {}   # {token: str, expires_at: float}

# ── State medical board lookup URLs (HTML-based, no JS required) ──────────────
_STATE_BOARD_URLS: dict[str, str] = {
    "CO": "https://apps.colorado.gov/dora/licensing/lookup/licenselookup.aspx",
    "CA": "https://www.breeze.ca.gov/datamart/SearchByName.do;jsessionid=",
    "TX": "https://www.tmb.state.tx.us/page/license-verification",
    "FL": "https://mqa.doh.state.fl.us/MQASearchServices/HealthCareProviders",
    "NY": "https://www.op.nysed.gov/verification-search",
    "IL": "https://ilesonline.idfpr.illinois.gov/DFPR/Lookup/LicenseLookup.aspx",
    "AZ": "https://azbomex.az.gov/verification",
    "NV": "https://nvdoctor.org/verify-license/",
    "GA": "https://gcmb.mylicense.com/verification/Search.aspx",
    "WA": "https://fortress.wa.gov/doh/providercredentialsearch/",
}

_FDA_URL = "https://www.accessdata.fda.gov/scripts/cdrh/cfdocs/cfRL/rl.cfm"
_TITLE_WORDS = {"dr", "dr.", "md", "do", "np", "pa", "fnp", "dpm", "rn", "aprn", "dnp", "pharmd"}


# ─────────────────────────────────────────────────────────────────────────────
# Public interface
# ─────────────────────────────────────────────────────────────────────────────

def enrich_with_licenses(providers: list[dict], target_state: str) -> list[dict]:
    """
    Enrich each provider with license verification data.
    Tries FSMB MED API first, falls back to NPI taxonomy already on the record.
    """
    use_fsmb = bool(os.getenv("FSMB_CLIENT_ID") and os.getenv("FSMB_CLIENT_SECRET"))
    enriched = []

    for p in providers:
        name = p.get("name", "")
        if not name:
            enriched.append(p)
            continue

        updated = {**p}
        fsmb_data = {}

        if use_fsmb:
            fsmb_data = _fsmb_lookup(name, target_state)

        # Merge license states from NPI (already on record) + FSMB
        existing = set(p.get("license_states") or [])
        fsmb_states = set(fsmb_data.get("license_states") or [])
        merged_states = sorted(existing | fsmb_states)

        updated["license_states"] = merged_states
        updated["license_number"] = (
            fsmb_data.get("license_number")
            or p.get("license_number")
        )
        updated["license_status"] = fsmb_data.get("license_status")  # Active / Inactive / None
        updated["board_actions"] = fsmb_data.get("board_actions", [])
        updated["npi"] = fsmb_data.get("npi") or p.get("npi")
        updated["licensed_in_target_state"] = target_state in merged_states

        # State board URL for manual verification
        if target_state in _STATE_BOARD_URLS:
            updated["state_board_url"] = _STATE_BOARD_URLS[target_state]

        enriched.append(updated)

    return enriched


def check_fda_establishment(clinic_name: str, state: str) -> dict:
    """Check FDA CDRH Establishment Registration & Device Listing for the clinic."""
    if not clinic_name:
        return {"fda_registered": False, "fda_establishments": []}
    try:
        resp = requests.get(
            _FDA_URL,
            params={"establishmentName": clinic_name, "StateName": state, "start_search": 1},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=15,
        )
        resp.raise_for_status()
        return _parse_fda_response(resp.text)
    except Exception as e:
        print(f"[license] FDA lookup error: {e}")
        return {"fda_registered": False, "fda_establishments": []}


# ─────────────────────────────────────────────────────────────────────────────
# FSMB MED API
# ─────────────────────────────────────────────────────────────────────────────

def _fsmb_token() -> str | None:
    """Get or refresh the FSMB OAuth2 bearer token."""
    now = time.time()
    if _FSMB_TOKEN_CACHE.get("token") and _FSMB_TOKEN_CACHE.get("expires_at", 0) > now + 30:
        return _FSMB_TOKEN_CACHE["token"]

    client_id = os.getenv("FSMB_CLIENT_ID")
    client_secret = os.getenv("FSMB_CLIENT_SECRET")
    if not client_id or not client_secret:
        return None

    try:
        resp = requests.post(
            _FSMB_TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
                "scope": "med.read",
            },
            timeout=10,
        )
        resp.raise_for_status()
        token_data = resp.json()
        _FSMB_TOKEN_CACHE["token"] = token_data["access_token"]
        _FSMB_TOKEN_CACHE["expires_at"] = now + token_data.get("expires_in", 3600)
        return _FSMB_TOKEN_CACHE["token"]
    except Exception as e:
        print(f"[license] FSMB token error: {e}")
        return None


def _fsmb_lookup(full_name: str, state: str) -> dict:
    """Query FSMB MED API for a provider's license info."""
    token = _fsmb_token()
    if not token:
        return {}

    parts = [p for p in full_name.strip().split() if p.lower().strip(".,") not in _TITLE_WORDS]
    if len(parts) < 2:
        return {}

    try:
        resp = requests.get(
            _FSMB_SEARCH_URL,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            params={
                "name.firstName": parts[0],
                "name.lastName": parts[-1],
            },
            timeout=15,
        )
        if resp.status_code == 404:
            return {}
        resp.raise_for_status()
        results = resp.json().get("practitioners", []) or resp.json().get("items", []) or [resp.json()]
        return _parse_fsmb_result(results, state)
    except Exception as e:
        print(f"[license] FSMB search error for '{full_name}': {e}")
        return {}


def _parse_fsmb_result(results: list, target_state: str) -> dict:
    """Extract license info from FSMB API response."""
    if not results:
        return {}

    license_states = []
    license_number = None
    license_status = None
    board_actions = []
    npi = None

    for practitioner in results:
        # NPI
        ids = practitioner.get("ids", []) or []
        for id_rec in ids:
            if id_rec.get("type", "").upper() == "NPI" and not npi:
                npi = id_rec.get("value")

        # Licenses
        licenses = practitioner.get("licenses", []) or []
        for lic in licenses:
            state = lic.get("state", "").upper()
            if state and state not in license_states:
                license_states.append(state)
            if state == target_state:
                license_number = lic.get("licenseNumber") or license_number
                license_status = lic.get("status") or license_status

        # Board actions / disciplinary
        actions = practitioner.get("boardActions", []) or []
        for action in actions:
            desc = action.get("category") or action.get("actionType") or str(action)
            if desc and desc not in board_actions:
                board_actions.append(desc)

    return {
        "npi": npi,
        "license_states": license_states,
        "license_number": license_number,
        "license_status": license_status,
        "board_actions": board_actions,
    }


# ─────────────────────────────────────────────────────────────────────────────
# FDA CDRH parser
# ─────────────────────────────────────────────────────────────────────────────

def _parse_fda_response(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    establishments = []
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        headers = []
        for row in rows:
            cells = row.find_all(["th", "td"])
            texts = [c.get_text(strip=True) for c in cells]
            if not texts:
                continue
            if row.find("th"):
                headers = texts
                continue
            if headers and len(texts) == len(headers):
                establishments.append(dict(zip(headers, texts)))
    return {
        "fda_registered": len(establishments) > 0,
        "fda_establishments": establishments[:5],
    }
