#!/usr/bin/env python3
"""
Clinic Scraper POC
Usage:
    python3 clinic_scraper.py "815 W Randolph St, Chicago, IL 60607"
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
    save_json,
    save_report,
    scrape_clinic,
    search_clinic_website,
)

_ADDRESS_RE = re.compile(
    r"^(?P<street>.+?),\s*(?P<city>[^,]+),\s*(?P<state>[A-Z]{2})\s+(?P<postal>\d{5}(?:-\d{4})?)$"
)


def _parse_address(raw: str) -> tuple[str, str, str, str]:
    m = _ADDRESS_RE.match(raw.strip())
    if not m:
        print(f"ERROR: Could not parse address '{raw}'\nExpected: \"815 W Randolph St, Chicago, IL 60607\"")
        sys.exit(1)
    return m.group("street"), m.group("city"), m.group("state"), m.group("postal")


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
        print('  python3 clinic_scraper.py "Dr. John Smith" "815 W Randolph St, Chicago, IL 60607"')
        sys.exit(1)

    if "--output-dir" in args:
        idx = args.index("--output-dir")
        output_dir = args[idx + 1]
        args = args[:idx] + args[idx + 2:]

    if len(args) == 1:
        name = None
        street, city, state, postal = _parse_address(args[0])
    elif len(args) == 2:
        name = args[0]
        street, city, state, postal = _parse_address(args[1])
    else:
        print("ERROR: Too many arguments. Wrap address in quotes.")
        sys.exit(1)

    full_address = f"{street}, {city}"
    label = name or "Unknown Clinic"
    address_only_mode = name is None

    print(f"\n=== Clinic Scraper ===")
    print(f"Target : {label}, {full_address}, {state} {postal}")
    print(f"Mode   : {'Address-only (license filter: {state} only)'.format(state=state) if address_only_mode else 'Name + Address (full license lookup)'}\n")

    # ── Step 1: Find website ──────────────────────────────────────────
    print("[1/5] Searching for clinic website...")
    url = search_clinic_website(name, full_address, state, postal)
    use_npi_fallback = False

    if not url:
        print("      No website found — will use NPI Registry fallback.")
        use_npi_fallback = True
    else:
        print(f"      Found: {url}")

    # ── Step 2: Scrape ────────────────────────────────────────────────
    if not use_npi_fallback:
        print("[2/5] Scraping website...")
        scraped = scrape_clinic(url)
        print(f"      Pages scraped: {len(scraped['pages'])}")
        for p in scraped["pages"]:
            print(f"        • {p}")
        if not scraped["pages"]:
            print("      Site blocked scraping — falling back to NPI Registry.")
            use_npi_fallback = True

    # ── Step 3: Extract via Claude ────────────────────────────────────
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
        elif not _address_matches(data, street, postal):
            print(f"      Address mismatch (got: {data.get('address')}) — falling back to NPI Registry.")
            use_npi_fallback = True

    if use_npi_fallback:
        print("[NPI] Looking up providers in CMS NPI Registry...")
        data = lookup_npi(name, full_address, state, postal)
        provider_count = len(data.get("providers", []))
        print(f"      Providers found: {provider_count}")
        if provider_count == 0:
            print("      No providers found. Saving empty result.")
        else:
            print(f"      Source: {data.get('_source')}")

    # ── Step 4: License verification ──────────────────────────────────
    print("[4/5] Verifying licenses...")
    providers = data.get("providers", [])
    providers = enrich_with_licenses(providers, state)

    if address_only_mode:
        # Address-only: keep only providers licensed in the target state
        licensed = [p for p in providers if p.get("licensed_in_target_state")]
        unlicensed_count = len(providers) - len(licensed)
        print(f"      Licensed in {state}: {len(licensed)} / {len(providers)} providers")
        if unlicensed_count:
            print(f"      Excluded {unlicensed_count} provider(s) with no {state} license found")
        providers = licensed

        # If website path ran but all providers were filtered out, try NPI as last resort
        if not providers and not use_npi_fallback:
            print("      0 licensed providers after filtering — retrying with NPI Registry...")
            npi_data = lookup_npi(None, full_address, state, postal)
            if npi_data.get("providers"):
                npi_providers = enrich_with_licenses(npi_data["providers"], state)
                providers = [p for p in npi_providers if p.get("licensed_in_target_state")]
                print(f"      NPI retry: {len(providers)} licensed provider(s) found")
                if providers:
                    data.update({k: v for k, v in npi_data.items() if k != "providers"})
    else:
        # Name+address: keep all, just show license status
        in_state = sum(1 for p in providers if p.get("licensed_in_target_state"))
        print(f"      Licensed in {state}: {in_state} / {len(providers)} provider(s)")

    data["providers"] = providers

    # FDA establishment check for the clinic
    clinic_name = data.get("clinic_name")
    if clinic_name:
        print(f"      Checking FDA establishment registry for '{clinic_name}'...")
        fda = check_fda_establishment(clinic_name, state)
        data["fda_registration"] = fda
        status = "Registered" if fda.get("fda_registered") else "Not found"
        print(f"      FDA status: {status}")

    # ── Step 5: Save ──────────────────────────────────────────────────
    print("[5/5] Saving results...")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    name_slug = _slug(name) if name else _slug(f"{street}_{city}_{state}_{postal}")
    filename = f"{name_slug}_{timestamp}.json"
    save_json(data, str(Path(output_dir) / filename))
    save_report(data, str(Path(output_dir) / filename.replace(".json", ".txt")))

    print(f"\nDone. Results saved to {Path(output_dir).resolve()}")


if __name__ == "__main__":
    main()
