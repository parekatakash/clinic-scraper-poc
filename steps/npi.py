import requests

_NPI_API = "https://npiregistry.cms.hhs.gov/api/"


def lookup_npi(name: str | None, address: str, state: str, postal_code: str) -> dict:
    """
    Query the CMS NPI Registry for providers matching the given name/location.
    Returns data in the same shape as extractor.extract_providers().
    """
    providers = []

    # Try name-based lookup first if a name was given
    if name:
        providers = _search_by_name(name, state, postal_code)

    # Fall back to address/location lookup
    if not providers:
        providers = _search_by_location(state, postal_code)

    clinic_name = None
    clinic_address = None
    clinic_phone = None

    if providers:
        # Use the first result's practice location as clinic info
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


def _search_by_name(full_name: str, state: str, postal_code: str) -> list[dict]:
    parts = full_name.strip().split()
    # Strip common titles so NPI search works cleanly
    titles = {"dr", "dr.", "md", "do", "np", "pa", "fnp", "dpm"}
    parts = [p for p in parts if p.lower().strip(".") not in titles]

    if len(parts) == 0:
        return []
    elif len(parts) == 1:
        params = {"first_name": parts[0], "state": state, "version": "2.1", "limit": 10}
    else:
        params = {
            "first_name": parts[0],
            "last_name": parts[-1],
            "state": state,
            "version": "2.1",
            "limit": 10,
        }

    return _call_api(params)


def _search_by_location(state: str, postal_code: str) -> list[dict]:
    params = {
        "postal_code": postal_code,
        "state": state,
        "enumeration_type": "NPI-1",  # Individual providers only
        "version": "2.1",
        "limit": 20,
    }
    return _call_api(params)


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

    # Prefer practice location address
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

    primary_taxonomy = next((t for t in taxonomies if t.get("primary")), None) or (taxonomies[0] if taxonomies else {})
    specialty = primary_taxonomy.get("desc", None)

    employer = None
    raw_address = None
    phone = None

    if practice:
        employer = practice.get("organization_name") or None
        city = practice.get("city", "")
        state_abbr = practice.get("state", "")
        zip_code = practice.get("postal_code", "")[:5]
        street = practice.get("address_1", "")
        raw_address = ", ".join(filter(None, [street, city, f"{state_abbr} {zip_code}".strip()]))
        phone_raw = practice.get("telephone_number", "")
        phone = _format_phone(phone_raw)

    # Extract license states AND license numbers directly from taxonomy records
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
