#!/usr/bin/env python3
"""
Clinic Scraper POC — three operating modes:

  Address only:
    python3 clinic_scraper.py "815 W Randolph St, Chicago, IL 60607"

  Organization name + address  (hospital, clinic, animal hospital, etc.):
    python3 clinic_scraper.py "Rush University Medical Center" "1653 W Congress Pkwy, Chicago, IL 60612"

  Person name + address  (specific doctor lookup):
    python3 clinic_scraper.py "Dr. John Smith" "815 W Randolph St, Chicago, IL 60607"
"""

import re
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from steps import (
    check_fda_establishment,
    enrich_with_licenses,
    extract_providers,
    lookup_npi,
    lookup_npi_org,
    save_json,
    save_report,
    scrape_clinic,
    search_clinic_website,
)

_ADDRESS_RE = re.compile(
    r"^(?P<street>.+?),\s*(?P<city>[^,]+),\s*(?P<state>[A-Z]{2})\s+(?P<postal>\d{5}(?:-\d{4})?)$"
)

# Credential suffixes that always mean a person name
_PERSON_CREDENTIALS = {"md", "do", "dvm", "vmd", "np", "pa", "fnp", "rn", "aprn",
                       "dds", "dpm", "pharmd", "phd", "dpt", "ot", "slp"}
# Salutations that always mean a person name
_PERSON_TITLES = {"dr", "dr.", "mr", "mrs", "ms", "prof"}

# Words that indicate the first argument is an organization, not a person
_ORG_KEYWORDS = {
    "hospital", "clinic", "medical", "animal", "center", "group",
    "llc", "inc", "ltd", "associates", "practice", "healthcare",
    "health", "veterinary", "vet", "care", "services", "institute",
    "university", "college", "laboratory", "lab", "pharmacy",
    "dental", "vision", "rehab", "rehabilitation", "therapy",
    "urgent", "emergency", "wellness", "family", "surgery", "surgical",
    "orthopedic", "pediatric", "oncology", "cardiology", "neurology",
}
# Strict subset used when evaluating auto-discovered names from search results —
# must contain an unambiguously healthcare-related word, not just any org word
_HEALTHCARE_KEYWORDS = {
    "hospital", "clinic", "medical", "animal", "veterinary", "vet",
    "healthcare", "health", "pharmacy", "dental", "surgery", "surgical",
    "orthopedic", "pediatric", "oncology", "cardiology", "neurology",
    "rehabilitation", "therapy", "urgent", "emergency", "wellness",
    "physician", "doctor", "care center", "med center",
}


def _parse_address(raw: str) -> tuple[str, str, str, str]:
    m = _ADDRESS_RE.match(raw.strip())
    if not m:
        print(f"ERROR: Could not parse address '{raw}'")
        print('Expected format: "815 W Randolph St, Chicago, IL 60607"')
        sys.exit(1)
    return m.group("street"), m.group("city"), m.group("state"), m.group("postal")


def _is_org_name(s: str) -> bool:
    """Return True if s looks like an organization name rather than a person name."""
    words = [w.lower().strip(".,") for w in s.split()]
    if words and words[0] in _PERSON_TITLES:
        return False
    if any(w in _PERSON_CREDENTIALS for w in words):
        return False
    if any(w in _ORG_KEYWORDS for w in words):
        return True
    # 4+ words with no person title and no credential → almost certainly an org
    if len(words) >= 4:
        return True
    return False


def _is_healthcare_name(s: str) -> bool:
    """Stricter check: True only if s contains an unambiguous healthcare keyword.
    Used when evaluating auto-discovered names from search results so we don't
    accept a pub, restaurant, or gym as the 'clinic' at an address.
    """
    words = [w.lower().strip(".,") for w in s.split()]
    return any(w in _HEALTHCARE_KEYWORDS for w in words)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def _address_matches(extracted: dict, street: str, postal: str) -> bool:
    extracted_addr = (extracted.get("address") or "").lower()
    if not extracted_addr:
        return True
    if postal[:5] in extracted_addr:
        return True
    street_token = street.strip().split()[0].lower()
    if street_token and street_token in extracted_addr:
        return True
    return False


def main() -> None:
    args = sys.argv[1:]
    output_dir = "output"

    if not args:
        print("Usage:")
        print('  python3 clinic_scraper.py "815 W Randolph St, Chicago, IL 60607"')
        print('  python3 clinic_scraper.py "Agawam Animal Hospital" "65 Mill St #1, Agawam, MA 01001"')
        print('  python3 clinic_scraper.py "Dr. John Smith" "815 W Randolph St, Chicago, IL 60607"')
        sys.exit(1)

    if "--output-dir" in args:
        idx = args.index("--output-dir")
        output_dir = args[idx + 1]
        args = args[:idx] + args[idx + 2:]

    # ── Parse arguments into mode ─────────────────────────────────────────────
    if len(args) == 1:
        mode = "address"
        org_name = None
        person_name = None
        street, city, state, postal = _parse_address(args[0])
    elif len(args) == 2:
        street, city, state, postal = _parse_address(args[1])
        if _is_org_name(args[0]):
            mode = "org"
            org_name = args[0]
            person_name = None
        else:
            mode = "person"
            org_name = None
            person_name = args[0]
    else:
        print("ERROR: Too many arguments. Wrap name and address each in quotes.")
        sys.exit(1)

    full_address = f"{street}, {city}"
    label = org_name or person_name or "Unknown Clinic"

    mode_label = {
        "address": f"Address-only (returns all licensed providers at this location)",
        "org":     f"Organization lookup (find all doctors at '{org_name}')",
        "person":  f"Person lookup (full license info for '{person_name}')",
    }[mode]

    print(f"\n=== Clinic Scraper ===")
    print(f"Target : {label}, {full_address}, {state} {postal}")
    print(f"Mode   : {mode_label}\n")

    # ── Step 1: Find website ──────────────────────────────────────────────────
    print("[1/5] Searching for clinic website...")
    search_name = org_name or person_name  # used to build search query
    search_result = search_clinic_website(search_name, full_address, state, postal)
    use_npi_fallback = False

    # search returns (url, discovered_name) or None
    if search_result:
        url, discovered_name = search_result
    else:
        url, discovered_name = None, None

    # In address-only mode, use the business name the search engine returned
    # only if it looks like a healthcare org (not a pub, restaurant, etc.)
    if mode == "address" and discovered_name and not org_name:
        if _is_healthcare_name(discovered_name):
            org_name = discovered_name
            print(f"      Discovered: {discovered_name}")
            # If the initial search didn't return a usable URL, re-search by org name
            if not url:
                print(f"      Re-searching with org name...")
                targeted = search_clinic_website(org_name, full_address, state, postal)
                if targeted:
                    url = targeted[0]
                    if url:
                        print(f"      Found: {url}")
        else:
            print(f"      Skipped non-healthcare result: '{discovered_name}'")

    if not url:
        print("      No website found — will use NPI Registry fallback.")
        use_npi_fallback = True
    else:
        print(f"      Found: {url}")

    # ── Step 2: Scrape ────────────────────────────────────────────────────────
    if not use_npi_fallback:
        print("[2/5] Scraping website...")
        scraped = scrape_clinic(url)
        print(f"      Pages scraped: {len(scraped['pages'])}")
        for p in scraped["pages"]:
            print(f"        • {p}")
        if not scraped["pages"]:
            print("      Site blocked scraping — falling back to NPI Registry.")
            use_npi_fallback = True

    # ── Step 3: Extract via Claude ────────────────────────────────────────────
    if not use_npi_fallback:
        print("[3/5] Extracting providers via Claude NER...")
        data = extract_providers(scraped["text"], url)
        provider_count = len(data.get("providers", []))
        print(f"      Providers extracted: {provider_count}")
        usage = data.get("_usage", {})
        print(f"      Tokens used: {usage.get('input_tokens', '?')} in / {usage.get('output_tokens', '?')} out")

        if provider_count == 0:
            print("      No providers found — falling back to NPI Registry.")
            use_npi_fallback = True
        elif mode != "org" and not org_name and not _address_matches(data, street, postal):
            # Trust the website when we have a confirmed org name (user-provided or discovered)
            print(f"      Address mismatch (got: {data.get('address')}) — falling back to NPI Registry.")
            use_npi_fallback = True

    # ── NPI fallback ──────────────────────────────────────────────────────────
    if use_npi_fallback:
        print("[NPI] Looking up providers in CMS NPI Registry...")
        if mode == "person":
            # Search by person name
            data = lookup_npi(person_name, full_address, state, postal)
        else:
            # Address-only or org mode: search by address (NPI-1 individual providers)
            data = lookup_npi(None, full_address, state, postal)

        provider_count = len(data.get("providers", []))
        print(f"      Providers found: {provider_count}")

    # ── Org info via NPI-2 (always run for org/address modes) ─────────────────
    org_info = {}
    if mode in ("org", "address"):
        print("[NPI-2] Looking up organization record...")
        org_info = lookup_npi_org(org_name, street, state, postal)
        if org_info.get("org_name"):
            print(f"      Org: {org_info['org_name']} (NPI-2: {org_info.get('org_npi', 'N/A')})")
            print(f"      Type: {org_info.get('org_type', 'N/A')}")
            if org_info.get("org_authorized_official"):
                print(f"      Authorized official: {org_info['org_authorized_official']}")
            # Fill in clinic name / address if not already set from website
            if not data.get("clinic_name"):
                data["clinic_name"] = org_info.get("org_name")
            if not data.get("address"):
                data["address"] = org_info.get("org_address")
            if not data.get("phone"):
                data["phone"] = org_info.get("org_phone")
            data["org_npi"] = org_info.get("org_npi")
            data["org_type"] = org_info.get("org_type")
            data["org_authorized_official"] = org_info.get("org_authorized_official")
        else:
            print("      No NPI-2 organization record found.")

    # ── Step 4: License verification ──────────────────────────────────────────
    print("[4/5] Verifying licenses...")
    providers = data.get("providers", [])
    providers = enrich_with_licenses(providers, state)

    if mode == "address":
        # Keep licensed providers; also keep vets with vet titles since AAVSB
        # blocks automated verification — a DVM on a vet clinic's staff page is
        # almost certainly licensed in the state where they practice.
        def _include(p: dict) -> bool:
            if p.get("licensed_in_target_state"):
                return True
            return "veterinarian" in (p.get("provider_category") or "").lower()

        licensed = [p for p in providers if _include(p)]
        unlicensed_count = len(providers) - len(licensed)
        print(f"      Licensed/verified in {state}: {len(licensed)} / {len(providers)} providers")
        if unlicensed_count:
            print(f"      Excluded {unlicensed_count} provider(s) with no {state} license found")
        providers = licensed

        # Secondary NPI fallback if website path produced 0 licensed providers
        if not providers and not use_npi_fallback:
            print("      0 licensed providers after filtering — retrying with NPI Registry...")
            npi_data = lookup_npi(None, full_address, state, postal)
            if npi_data.get("providers"):
                npi_providers = enrich_with_licenses(npi_data["providers"], state)
                providers = [p for p in npi_providers if p.get("licensed_in_target_state")]
                print(f"      NPI retry: {len(providers)} licensed provider(s) found")
                if providers:
                    data.update({k: v for k, v in npi_data.items() if k != "providers"})

    elif mode == "org":
        # Show ALL providers found — org mode returns everyone at that location
        in_state = sum(1 for p in providers if p.get("licensed_in_target_state"))
        print(f"      Providers found: {len(providers)} total, {in_state} licensed in {state}")

        # If website returned 0, retry with NPI-1 by address
        if not providers:
            print("      No providers from website — searching NPI by address...")
            npi_data = lookup_npi(None, full_address, state, postal)
            if npi_data.get("providers"):
                providers = enrich_with_licenses(npi_data["providers"], state)
                print(f"      NPI found {len(providers)} provider(s)")
                data.update({k: v for k, v in npi_data.items() if k != "providers"})

    else:  # person mode
        in_state = sum(1 for p in providers if p.get("licensed_in_target_state"))
        print(f"      Licensed in {state}: {in_state} / {len(providers)} provider(s)")

    data["providers"] = providers

    # FDA establishment check
    clinic_name = data.get("clinic_name")
    if clinic_name:
        print(f"      Checking FDA establishment registry for '{clinic_name}'...")
        fda = check_fda_establishment(clinic_name, state)
        data["fda_registration"] = fda
        status = "Registered" if fda.get("fda_registered") else "Not found"
        print(f"      FDA status: {status}")

    # ── Step 5: Save ──────────────────────────────────────────────────────────
    print("[5/5] Saving results...")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    slug_base = org_name or person_name or f"{street}_{city}_{state}_{postal}"
    filename = f"{_slug(slug_base)}_{timestamp}.json"
    save_json(data, str(Path(output_dir) / filename))
    save_report(data, str(Path(output_dir) / filename.replace(".json", ".txt")))

    print(f"\nDone. Results saved to {Path(output_dir).resolve()}")


if __name__ == "__main__":
    main()
