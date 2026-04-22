import requests

_NPI_API = "https://npiregistry.cms.hhs.gov/api/"
_TITLE_WORDS = {"dr", "dr.", "md", "do", "np", "pa", "fnp", "dpm", "rn", "aprn", "dnp", "pharmd"}


def lookup_npi(name: str | None, address: str, state: str, postal_code: str) -> dict:
    """
    Query the CMS NPI Registry for providers matching the given name/location.

    - name given   → search by name only; do NOT fall back to ZIP dump
    - address only → search by postal code and filter by street address match
    """
    street = address.split(",")[0].strip() if address else ""
    providers = []

    if name:
        providers = _search_by_name(name, state)
        if not providers:
            # Try without state restriction in case they're licensed elsewhere
            providers = _search_by_name(name, state=None)
    else:
        # Address-only: fetch up to 200 and filter by street
        providers = _search_by_location(street, state, postal_code)

    clinic_name = None
    clinic_address = None
    clinic_phone = None

    if providers:
        first = providers[0]
        clinic_name = first.get("current_employer")
        clinic_address = first.get("_raw_address")
        clinic_phone = first.get("phone")

    return {
        "clinic_name": clinic_name,
        "address": clinic_address,
        "phone": clinic_phone,
        "email": None,
        "website": None,
        "_source": "NPI Registry (cms.hhs.gov)",
        "providers": [
            {k: v for k, v in p.items() if not k.startswith("_raw")}
            for p in providers
        ],
    }


def _search_by_name(full_name: str, state: str | None) -> list[dict]:
    parts = [p for p in full_name.strip().split() if p.lower().strip(".,") not in _TITLE_WORDS]

    if len(parts) == 0:
        return []

    params = {"version": "2.1", "enumeration_type": "NPI-1", "limit": 10}
    if len(parts) == 1:
        params["last_name"] = parts[0]
    else:
        params["first_name"] = parts[0]
        params["last_name"] = parts[-1]

    if state:
        params["state"] = state

    return _call_api(params)


def _search_by_location(street: str, state: str, postal_code: str) -> list[dict]:
    """Fetch up to 200 providers in the ZIP and filter to those at the input street."""
    all_providers: list[dict] = []
    skip = 0
    limit = 200

    # NPI API caps at 200 per call; one pass is usually enough for a ZIP
    params = {
        "postal_code": postal_code,
        "state": state,
        "enumeration_type": "NPI-1",
        "version": "2.1",
        "limit": limit,
        "skip": skip,
    }
    all_providers = _call_api(params)

    # Filter to providers whose practice address matches the input street
    matched = _filter_by_street(all_providers, street)

    if matched:
        print(f"[npi] {len(matched)} provider(s) matched street '{street}' out of {len(all_providers)} in ZIP {postal_code}")
        return matched

    # No exact street match — return empty rather than dumping the whole ZIP
    print(f"[npi] No providers found at '{street}' in ZIP {postal_code}")
    return []


def _filter_by_street(providers: list[dict], street: str) -> list[dict]:
    """Keep providers whose practice address contains the input street number or name."""
    if not street:
        return providers

    # Normalise: uppercase, strip suite/unit suffixes for comparison
    def normalise(s: str) -> str:
        return s.upper().replace(".", "").replace(",", "")

    norm_input = normalise(street)
    # Key tokens: street number + first word of street name (e.g. "121", "CRESCENT")
    tokens = norm_input.split()
    key_tokens = tokens[:2] if len(tokens) >= 2 else tokens

    matched = []
    for p in providers:
        raw = normalise(p.get("_raw_address") or "")
        if all(t in raw for t in key_tokens):
            matched.append(p)
    return matched


def _call_api(params: dict) -> list[dict]:
    try:
        resp = requests.get(_NPI_API, params=params, timeout=15)
        resp.raise_for_status()
        results = resp.json().get("results", [])
        return [_parse_result(r) for r in results if r.get("enumeration_type") == "NPI-1"]
    except Exception as e:
        print(f"[npi] API error: {e}")
        return []


def _parse_result(r: dict) -> dict:
    basic = r.get("basic", {})
    taxonomies = r.get("taxonomies", [])
    addresses = r.get("addresses", [])

    practice = next((a for a in addresses if a.get("address_purpose") == "LOCATION"), None)
    if not practice and addresses:
        practice = addresses[0]

    first = basic.get("first_name", "")
    middle = basic.get("middle_name", "")
    last = basic.get("last_name", "")
    credential = basic.get("credential", "")
    name = " ".join(filter(None, [first, middle, last]))
    if credential:
        name = f"{name}, {credential}"

    primary_taxonomy = (
        next((t for t in taxonomies if t.get("primary")), None)
        or (taxonomies[0] if taxonomies else {})
    )
    specialty = primary_taxonomy.get("desc", None)

    employer = raw_address = phone = None

    if practice:
        employer = practice.get("organization_name") or None
        city = practice.get("city", "")
        state_abbr = practice.get("state", "")
        zip_code = practice.get("postal_code", "")[:5]
        street = practice.get("address_1", "")
        raw_address = ", ".join(filter(None, [street, city, f"{state_abbr} {zip_code}".strip()]))
        phone = _format_phone(practice.get("telephone_number", ""))

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
        "name": name,
        "title": credential or None,
        "specialty": specialty,
        "current_employer": employer,
        "phone": phone,
        "email": None,
        "license_states": license_states,
        "license_number": license_number,
        "npi": r.get("number"),
        "_raw_address": raw_address,
    }


def _format_phone(raw: str) -> str | None:
    digits = "".join(c for c in raw if c.isdigit())
    if len(digits) == 10:
        return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
    if len(digits) == 11 and digits[0] == "1":
        return f"({digits[1:4]}) {digits[4:7]}-{digits[7:]}"
    return raw or None
