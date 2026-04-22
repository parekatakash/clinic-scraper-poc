import requests
from bs4 import BeautifulSoup

_NPI_API = "https://npiregistry.cms.hhs.gov/api/"
_FDA_URL = "https://www.accessdata.fda.gov/scripts/cdrh/cfdocs/cfRL/rl.cfm"

_TITLE_WORDS = {"dr", "dr.", "md", "do", "np", "pa", "fnp", "dpm", "rn", "aprn", "dnp", "pharmd"}


def enrich_with_licenses(providers: list[dict], target_state: str) -> list[dict]:
    """
    For each provider, look up NPI taxonomy to get license number + states.
    Adds: license_number, license_states, licensed_in_target_state.
    """
    enriched = []
    for p in providers:
        name = p.get("name", "")
        if not name:
            enriched.append(p)
            continue

        license_info = _lookup_license(name, target_state)
        updated = {**p}

        # Merge license states — keep any already found, add NPI taxonomy ones
        existing = set(p.get("license_states") or [])
        npi_states = set(license_info.get("license_states") or [])
        merged_states = sorted(existing | npi_states)

        updated["license_states"] = merged_states
        updated["license_number"] = license_info.get("license_number") or p.get("license_number")
        updated["npi"] = license_info.get("npi") or p.get("npi")
        updated["licensed_in_target_state"] = target_state in merged_states

        enriched.append(updated)
    return enriched


def check_fda_establishment(clinic_name: str, state: str) -> dict:
    """
    Query FDA CDRH Establishment Registration & Device Listing database.
    Checks if the clinic is registered as a medical device establishment.
    Returns registration details or None if not found.
    """
    if not clinic_name:
        return {"fda_registered": False, "fda_establishments": []}

    try:
        resp = requests.get(
            _FDA_URL,
            params={
                "establishmentName": clinic_name,
                "StateName": state,
                "start_search": 1,
            },
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=15,
        )
        resp.raise_for_status()
        return _parse_fda_response(resp.text)
    except Exception as e:
        print(f"[license] FDA lookup error: {e}")
        return {"fda_registered": False, "fda_establishments": []}


def _lookup_license(full_name: str, target_state: str) -> dict:
    """Look up a provider by name in NPI and extract taxonomy license data."""
    parts = [p for p in full_name.strip().split() if p.lower().strip(".,") not in _TITLE_WORDS]

    if len(parts) < 1:
        return {}

    params = {
        "version": "2.1",
        "enumeration_type": "NPI-1",
        "limit": 5,
    }
    if len(parts) == 1:
        params["last_name"] = parts[0]
    else:
        params["first_name"] = parts[0]
        params["last_name"] = parts[-1]
        params["state"] = target_state

    try:
        resp = requests.get(_NPI_API, params=params, timeout=15)
        resp.raise_for_status()
        results = resp.json().get("results", [])
    except Exception as e:
        print(f"[license] NPI lookup error for '{full_name}': {e}")
        return {}

    if not results:
        return {}

    # Use the first matching result
    r = results[0]
    taxonomies = r.get("taxonomies", [])

    license_states = []
    license_number = None

    for t in taxonomies:
        t_state = t.get("state", "").strip()
        t_license = t.get("license", "").strip()
        if t_state and t_state not in license_states:
            license_states.append(t_state)
        # Prefer the license number for the target state
        if t_state == target_state and t_license and not license_number:
            license_number = t_license

    # If no target state license found, take any available license number
    if not license_number:
        for t in taxonomies:
            if t.get("license"):
                license_number = t["license"].strip()
                break

    return {
        "npi": r.get("number"),
        "license_number": license_number,
        "license_states": license_states,
    }


def _parse_fda_response(html: str) -> dict:
    """Parse FDA CDRH HTML response for establishment records."""
    soup = BeautifulSoup(html, "html.parser")
    establishments = []

    # FDA results are typically in a table
    tables = soup.find_all("table")
    for table in tables:
        rows = table.find_all("tr")
        headers = []
        for row in rows:
            cells = row.find_all(["th", "td"])
            texts = [c.get_text(strip=True) for c in cells]
            if not texts:
                continue
            if any(h in " ".join(texts).lower() for h in ["establishment", "registration", "owner"]):
                if row.find("th"):
                    headers = texts
                    continue
            if headers and len(texts) == len(headers):
                establishments.append(dict(zip(headers, texts)))

    return {
        "fda_registered": len(establishments) > 0,
        "fda_establishments": establishments[:5],
    }
