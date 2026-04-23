"""
License verification — all applicable license types:
1. State Medical License  — FSMB MED API (docinfo.org) + NPI taxonomy fallback
2. DEA Registration       — steps/dea.py (checksum + best-effort web lookup)
3. Veterinary License     — AAVSB VetVerify (vetverify.org)
4. Pharmacy License       — NABP lookup URL + state pharmacy board links
5. Facility Registration  — FDA CDRH (check_fda_establishment)
6. Medicare Enrollment    — CMS PECOS open data API
"""

import os
import time
import requests
from bs4 import BeautifulSoup
from .dea import verify_dea

# ── FSMB MED API ─────────────────────────────────────────────────────────────
_FSMB_TOKEN_URL = "https://identity.fsmb.org/connect/token"
_FSMB_SEARCH_URL = "https://services-med.fsmb.org/v2/practitioners/search"
_FSMB_LICENSURE_URL = "https://services-med.fsmb.org/v2/licensure/{fid}/summary"
_FSMB_VERIFICATION_URL = "https://services-med.fsmb.org/v2/practitioners/{fid}/verification"
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

# ── Veterinary license — AAVSB VetVerify ─────────────────────────────────────
# Note: AAVSB blocks automated requests with Cloudflare; manual URL is for human verification
_VETVERIFY_MANUAL = "https://www.aavsb.org/public-tools/vet-verify/"

# State veterinary board license lookup URLs (for manual verification)
_STATE_VET_BOARD_URLS: dict[str, str] = {
    "MA": "https://checkalicense.dpl.state.ma.us/",
    "CA": "https://www.vmb.ca.gov/licensees/verify_license.shtml",
    "TX": "https://www.txvetboard.state.tx.us/verify.php",
    "FL": "https://mqa.doh.state.fl.us/MQASearchServices/HealthCareProviders",
    "NY": "https://www.op.nysed.gov/verification-search",
    "IL": "https://ilesonline.idfpr.illinois.gov/DFPR/Lookup/LicenseLookup.aspx",
    "CO": "https://apps.colorado.gov/dora/licensing/lookup/licenselookup.aspx",
    "WA": "https://fortress.wa.gov/doh/providercredentialsearch/",
    "GA": "https://gcmb.mylicense.com/verification/Search.aspx",
    "OH": "https://elicense.ohio.gov/oh_verifylicense/",
    "PA": "https://www.pals.pa.gov/#/page/search",
    "NC": "https://www.ncvmb.org/licensee-search",
    "AZ": "https://www.azvetboard.gov/veterinarian-verification",
    "NJ": "https://newjersey.mylicense.com/verification/Search.aspx",
    "VA": "https://dhp.virginiainteractive.org/Lookup/Index",
    "MI": "https://www.lara.michigan.gov/ormpublic/Home/SearchLicense",
    "MN": "https://mblsportal.sos.state.mn.us/Business/Search",
    "OR": "https://obve.oregon.gov/Clients/OBVE/Public/Verifications/",
    "CT": "https://www.elicense.ct.gov/Lookup/LicenseLookup.aspx",
}

# ── Pharmacy — NABP + state boards ──────────────────────────────────────────
_NABP_MANUAL = "https://nabp.pharmacy/programs/pharmacies/nabp-e-profile-id/"
_STATE_PHARMACY_URLS: dict[str, str] = {
    "CO": "https://www.dora.state.co.us/pls/real/GENAPP_MAIN.Select_Search_Choice",
    "CA": "https://www.pharmacy.ca.gov/consumers/verify_lic.shtml",
    "TX": "https://www.pharmacy.texas.gov/consumer/license_verif.asp",
    "FL": "https://mqa.doh.state.fl.us/MQASearchServices/HealthCareProviders",
    "NY": "https://www.op.nysed.gov/verification-search",
    "IL": "https://ilesonline.idfpr.illinois.gov/DFPR/Lookup/LicenseLookup.aspx",
    "MA": "https://checkalicense.dpl.state.ma.us/",
    "GA": "https://gcmb.mylicense.com/verification/Search.aspx",
    "WA": "https://fortress.wa.gov/doh/providercredentialsearch/",
}

# ── Medicare enrollment — CMS PECOS open data ────────────────────────────────
_CMS_PECOS_URL = "https://data.cms.gov/provider-data/api/1/datastore/query/1fd32a89-e3c7-41f9-b0c8-d61b05cac70e/0"


# ─────────────────────────────────────────────────────────────────────────────
# Public interface
# ─────────────────────────────────────────────────────────────────────────────

def enrich_with_licenses(providers: list[dict], target_state: str) -> list[dict]:
    """
    Enrich each provider with all applicable license types:
      - State medical license  (FSMB → NPI taxonomy → NPI name lookup)
      - DEA registration       (checksum + best-effort web lookup)
      - Veterinary license     (AAVSB VetVerify — when provider_category is Veterinarian)
      - Pharmacy license URLs  (when provider_category is Pharmacist / Pharmacy)
      - Medicare enrollment    (CMS PECOS open data)
    """
    use_fsmb = bool(os.getenv("FSMB_CLIENT_ID") and os.getenv("FSMB_CLIENT_SECRET"))
    enriched = []

    for p in providers:
        name = p.get("name", "")
        if not name:
            enriched.append(p)
            continue

        updated = {**p}

        # Infer provider_category from title when NPI taxonomy wasn't available
        # (happens when provider came from website scraping, not NPI lookup)
        category = (p.get("provider_category") or "").lower()
        if not category:
            # Normalise: upper-case, strip punctuation so "DVM," → "DVM"
            title_raw = (p.get("title") or p.get("specialty") or "").upper()
            title_tokens = {t.strip(".,;()") for t in title_raw.replace("-", " ").split()}
            if title_tokens & {"MD", "DO"}:
                category = "physician (md/do)"
                updated["provider_category"] = "Physician (MD/DO)"
                updated["requires_dea"] = True
            elif title_tokens & {"NP", "FNP", "APRN", "DNP", "CRNP"}:
                category = "nurse practitioner"
                updated["provider_category"] = "Nurse Practitioner"
                updated["requires_dea"] = True
            elif title_tokens & {"PA", "PA-C"}:
                category = "physician assistant"
                updated["provider_category"] = "Physician Assistant"
                updated["requires_dea"] = True
            elif title_tokens & {"DVM", "VMD", "VETERINARIAN"}:
                category = "veterinarian"
                updated["provider_category"] = "Veterinarian"
                updated["requires_dea"] = True
            elif title_tokens & {"PHARMD", "RPH", "PHARMACIST"}:
                category = "pharmacist"
                updated["provider_category"] = "Pharmacist"
                updated["requires_dea"] = False
            elif title_tokens & {"DDS", "DMD", "DENTIST"}:
                category = "dentist"
                updated["provider_category"] = "Dentist"
                updated["requires_dea"] = True

        # ── 1. State medical license (FSMB → NPI) ────────────────────────────
        fsmb_data = {}
        if use_fsmb and "veterinarian" not in category and "pharmacy" not in category:
            fsmb_data = _fsmb_lookup(name, target_state)

        existing = set(p.get("license_states") or [])
        fsmb_states = set(fsmb_data.get("license_states") or [])
        merged_states = sorted(existing | fsmb_states)

        npi_fallback = {}
        # Always run NPI lookup for vets (NPI taxonomy carries license numbers for
        # vets that do have NPI). For non-vets, only run when we have no data yet.
        run_npi = (
            "veterinarian" in category
            or (not merged_states and not fsmb_data.get("license_number"))
        )
        if run_npi:
            npi_fallback = _npi_license_lookup(name, target_state)
            npi_states = set(npi_fallback.get("license_states") or [])
            merged_states = sorted(existing | fsmb_states | npi_states)

        updated["license_states"]  = merged_states
        updated["license_number"]  = (fsmb_data.get("license_number")
                                      or npi_fallback.get("license_number")
                                      or p.get("license_number"))
        updated["license_status"]  = fsmb_data.get("license_status")
        updated["board_actions"]   = fsmb_data.get("board_actions", [])
        updated["npi"]             = (fsmb_data.get("npi")
                                      or npi_fallback.get("npi")
                                      or p.get("npi"))
        updated["licensed_in_target_state"] = target_state in merged_states

        if target_state in _STATE_BOARD_URLS:
            updated["state_board_url"] = _STATE_BOARD_URLS[target_state]

        # ── 2. DEA registration ───────────────────────────────────────────────
        if p.get("requires_dea") or "physician" in category or "veterinarian" in category \
                or "nurse practitioner" in category or "physician assistant" in category:
            parts = _name_parts(name)
            dea_info = verify_dea(
                first_name=parts[0] if parts else None,
                last_name=parts[-1] if len(parts) > 1 else None,
                state=target_state,
            )
            if dea_info:
                updated["dea"] = dea_info

        # ── 3. Veterinary license (AAVSB VetVerify + state board URL) ───────────
        if "veterinarian" in category:
            vet_lic = _vetverify_lookup()
            if target_state in _STATE_VET_BOARD_URLS:
                vet_lic["state_vet_board_url"] = _STATE_VET_BOARD_URLS[target_state]
            updated["veterinary_license"] = vet_lic

        # ── 4. Pharmacy license reference links ───────────────────────────────
        if "pharmacist" in category or "pharmacy" in category:
            updated["pharmacy_license_url"] = _STATE_PHARMACY_URLS.get(
                target_state, _NABP_MANUAL
            )

        # ── 5. Medicare enrollment (CMS PECOS) ───────────────────────────────
        npi_num = updated.get("npi")
        if npi_num:
            updated["medicare_enrollment"] = _cms_medicare_lookup(str(npi_num))

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

_FSMB_PLACEHOLDER = {"your_fsmb_client_id_here", "your_fsmb_client_secret_here", ""}

def _fsmb_token() -> str | None:
    """Get or refresh the FSMB OAuth2 bearer token.
    Caches failures for 5 minutes so a network error doesn't cause one timeout per provider.
    """
    now = time.time()
    # Return cached token if still valid
    if _FSMB_TOKEN_CACHE.get("token") and _FSMB_TOKEN_CACHE.get("expires_at", 0) > now + 30:
        return _FSMB_TOKEN_CACHE["token"]
    # Skip retry window after a previous failure
    if _FSMB_TOKEN_CACHE.get("failed_until", 0) > now:
        return None

    client_id = os.getenv("FSMB_CLIENT_ID", "")
    client_secret = os.getenv("FSMB_CLIENT_SECRET", "")
    if client_id in _FSMB_PLACEHOLDER or client_secret in _FSMB_PLACEHOLDER:
        return None

    try:
        resp = requests.post(
            _FSMB_TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
                "scope": "med.read med.order_read",
            },
            timeout=10,
        )
        resp.raise_for_status()
        token_data = resp.json()
        _FSMB_TOKEN_CACHE["token"] = token_data["access_token"]
        _FSMB_TOKEN_CACHE["expires_at"] = now + token_data.get("expires_in", 3600)
        _FSMB_TOKEN_CACHE.pop("failed_until", None)
        return _FSMB_TOKEN_CACHE["token"]
    except Exception as e:
        print(f"[license] FSMB token error: {e}")
        _FSMB_TOKEN_CACHE["failed_until"] = now + 300  # stop retrying for 5 min
        return None


def _fsmb_lookup(full_name: str, state: str) -> dict:
    """
    Two-step FSMB lookup (mirrors what docinfo.org does):
      1. Search by name → get FID
      2. Fetch licensure summary + verification using FID
    Falls back to search result data if FID endpoints fail.
    """
    token = _fsmb_token()
    if not token:
        return {}

    parts = [p for p in full_name.strip().split() if p.lower().strip(".,") not in _TITLE_WORDS]
    if len(parts) < 2:
        return {}

    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    # Step 1 — search by name to get practitioners + FID
    try:
        resp = requests.get(
            _FSMB_SEARCH_URL,
            headers=headers,
            params={"name.firstName": parts[0], "name.lastName": parts[-1]},
            timeout=15,
        )
        if resp.status_code == 404:
            return {}
        resp.raise_for_status()
        body = resp.json()
        results = body.get("practitioners") or body.get("items") or ([body] if body.get("fid") else [])
    except Exception as e:
        print(f"[license] FSMB search error for '{full_name}': {e}")
        return {}

    if not results:
        return {}

    # Extract FID from the best-matching practitioner
    fid = None
    for r in results:
        fid = r.get("fid") or r.get("id")
        if fid:
            break

    # Step 2 — fetch richer data via FID endpoints when available
    licensure_data: dict = {}
    verification_data: dict = {}

    if fid:
        try:
            lic_resp = requests.get(
                _FSMB_LICENSURE_URL.format(fid=fid),
                headers=headers,
                timeout=15,
            )
            if lic_resp.status_code == 200:
                licensure_data = lic_resp.json()
        except Exception as e:
            print(f"[license] FSMB licensure summary error for FID {fid}: {e}")

        try:
            ver_resp = requests.get(
                _FSMB_VERIFICATION_URL.format(fid=fid),
                headers=headers,
                timeout=15,
            )
            if ver_resp.status_code == 200:
                verification_data = ver_resp.json()
        except Exception as e:
            print(f"[license] FSMB verification error for FID {fid}: {e}")

    # Parse — prefer FID-based data, fall back to search result data
    return _parse_fsmb_result(results, state, licensure_data, verification_data)


def _parse_fsmb_result(
    results: list,
    target_state: str,
    licensure_data: dict | None = None,
    verification_data: dict | None = None,
) -> dict:
    """
    Extract license info from FSMB API responses.
    Prefers FID-based licensure_data/verification_data when available;
    falls back to the search result records.
    """
    if not results:
        return {}

    license_states = []
    license_number = None
    license_status = None
    board_actions = []
    npi = None

    # ── Pull NPI from search results ─────────────────────────────────────────
    for practitioner in results:
        ids = practitioner.get("ids", []) or []
        for id_rec in ids:
            if id_rec.get("type", "").upper() == "NPI" and not npi:
                npi = id_rec.get("value")

    # ── License states + status from FID licensure summary (richest source) ──
    if licensure_data:
        for lic in licensure_data.get("licenses", []) or []:
            state = (lic.get("state") or lic.get("licenseState") or "").upper()
            if state and state not in license_states:
                license_states.append(state)
            if state == target_state:
                license_number = lic.get("licenseNumber") or lic.get("number") or license_number
                license_status = lic.get("status") or lic.get("licenseStatus") or license_status
        # Some FSMB responses wrap licenses differently
        for lic in licensure_data.get("licensures", []) or []:
            state = (lic.get("state") or lic.get("licenseState") or "").upper()
            if state and state not in license_states:
                license_states.append(state)
            if state == target_state:
                license_number = lic.get("licenseNumber") or lic.get("number") or license_number
                license_status = lic.get("status") or lic.get("licenseStatus") or license_status
    else:
        # Fall back to license data embedded in search results
        for practitioner in results:
            for lic in practitioner.get("licenses", []) or []:
                state = (lic.get("state") or "").upper()
                if state and state not in license_states:
                    license_states.append(state)
                if state == target_state:
                    license_number = lic.get("licenseNumber") or license_number
                    license_status = lic.get("status") or license_status

    # ── Board actions from FID verification endpoint (most authoritative) ────
    if verification_data:
        for action in verification_data.get("boardOrders", []) or []:
            desc = (
                action.get("actionType")
                or action.get("category")
                or action.get("description")
                or str(action)
            )
            if desc and desc not in board_actions:
                board_actions.append(desc)
        for action in verification_data.get("boardActions", []) or []:
            desc = (
                action.get("actionType")
                or action.get("category")
                or action.get("description")
                or str(action)
            )
            if desc and desc not in board_actions:
                board_actions.append(desc)
    else:
        # Fall back to board actions embedded in search results
        for practitioner in results:
            for action in practitioner.get("boardActions", []) or []:
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
# Shared utility
# ─────────────────────────────────────────────────────────────────────────────

def _name_parts(full_name: str) -> list[str]:
    """Strip title words and return remaining name tokens."""
    return [p for p in full_name.strip().split() if p.lower().strip(".,") not in _TITLE_WORDS]


# ─────────────────────────────────────────────────────────────────────────────
# Veterinary license — AAVSB VetVerify
# ─────────────────────────────────────────────────────────────────────────────

def _vetverify_lookup() -> dict:
    """AAVSB VetVerify blocks automated requests (Cloudflare); return manual URL only."""
    return {"vetverify_lookup_url": _VETVERIFY_MANUAL}


# ─────────────────────────────────────────────────────────────────────────────
# Medicare enrollment — CMS PECOS open data
# ─────────────────────────────────────────────────────────────────────────────

def _cms_medicare_lookup(npi: str) -> dict:
    """Check Medicare enrollment status via CMS PECOS open data API."""
    try:
        resp = requests.get(
            _CMS_PECOS_URL,
            params={
                "limit": 1,
                "filters[0][property]": "npi",
                "filters[0][value]": npi,
            },
            headers={"User-Agent": "clinic-scraper/1.0"},
            timeout=10,
        )
        if resp.status_code != 200:
            return {"medicare_enrolled": None}
        results = resp.json().get("results", [])
        if not results:
            return {"medicare_enrolled": False}
        row = results[0]
        return {
            "medicare_enrolled": True,
            "medicare_provider_type": row.get("provider_type"),
            "medicare_state": row.get("state"),
            "medicare_city": row.get("city"),
        }
    except Exception as e:
        print(f"[license] CMS Medicare lookup error: {e}")
        return {"medicare_enrolled": None}


# ─────────────────────────────────────────────────────────────────────────────
# NPI name lookup — fallback when provider came from website (no NPI taxonomy)
# ─────────────────────────────────────────────────────────────────────────────

_NPI_API = "https://npiregistry.cms.hhs.gov/api/"


def _npi_license_lookup(full_name: str, state: str) -> dict:
    """Quick NPI name search to retrieve license states + NPI number."""
    parts = [p for p in full_name.strip().split() if p.lower().strip(".,") not in _TITLE_WORDS]
    if len(parts) < 2:
        return {}
    try:
        resp = requests.get(
            _NPI_API,
            params={
                "version": "2.1",
                "enumeration_type": "NPI-1",
                "first_name": parts[0],
                "last_name": parts[-1],
                "state": state,
                "limit": 5,
            },
            timeout=10,
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
        if not results:
            # Retry nationally
            resp2 = requests.get(
                _NPI_API,
                params={
                    "version": "2.1",
                    "enumeration_type": "NPI-1",
                    "first_name": parts[0],
                    "last_name": parts[-1],
                    "limit": 5,
                },
                timeout=10,
            )
            resp2.raise_for_status()
            results = resp2.json().get("results", [])
        if not results:
            return {}
        r = results[0]
        taxonomies = r.get("taxonomies", [])
        license_states = []
        license_number = None
        for t in taxonomies:
            t_state = t.get("state", "").strip()
            t_license = t.get("license", "").strip()
            if t_state and t_state not in license_states:
                license_states.append(t_state)
            if t_license and not license_number:
                license_number = t_license
        return {
            "npi": r.get("number"),
            "license_states": license_states,
            "license_number": license_number,
        }
    except Exception as e:
        print(f"[license] NPI fallback lookup error for '{full_name}': {e}")
        return {}


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
