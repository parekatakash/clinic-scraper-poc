#!/usr/bin/env python3
"""
Clinic Scraper POC
Usage:
    python clinic_scraper.py "815 W Randolph St, Chicago, IL 60607"
    python clinic_scraper.py "Sunrise Family Clinic" "815 W Randolph St, Chicago, IL 60607"
"""

import re
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from steps import extract_providers, lookup_npi, save_json, save_report, scrape_clinic, search_clinic_website

_ADDRESS_RE = re.compile(
    r"^(?P<street>.+?),\s*(?P<city>[^,]+),\s*(?P<state>[A-Z]{2})\s+(?P<postal>\d{5}(?:-\d{4})?)$"
)


def _parse_address(raw: str) -> tuple[str, str, str, str]:
    """Parse 'Street, City, ST 00000' into (street, city, state, postal)."""
    m = _ADDRESS_RE.match(raw.strip())
    if not m:
        print(
            f"ERROR: Could not parse address '{raw}'\n"
            "Expected format: \"815 W Randolph St, Chicago, IL 60607\""
        )
        sys.exit(1)
    return m.group("street"), m.group("city"), m.group("state"), m.group("postal")


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def _address_matches(extracted: dict, street: str, postal: str) -> bool:
    """Check if extracted clinic address is close enough to the input address."""
    extracted_addr = (extracted.get("address") or "").lower()
    if not extracted_addr:
        return True  # Can't verify — let it pass

    # Match on postal code (most reliable)
    if postal[:5] in extracted_addr:
        return True

    # Match on first word of street number/name (e.g. "121" from "121 S Crescent Dr")
    street_token = street.strip().split()[0].lower()
    if street_token and street_token in extracted_addr:
        return True

    return False


def main() -> None:
    args = sys.argv[1:]
    output_dir = "output"

    if not args:
        print("Usage:")
        print('  python clinic_scraper.py "815 W Randolph St, Chicago, IL 60607"')
        print('  python clinic_scraper.py "Sunrise Family Clinic" "815 W Randolph St, Chicago, IL 60607"')
        sys.exit(1)

    # Detect optional --output-dir flag
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

    print(f"\n=== Clinic Scraper ===")
    print(f"Target : {label}, {full_address}, {state} {postal}\n")

    print("[1/4] Searching for clinic website...")
    url = search_clinic_website(name, full_address, state, postal)
    use_npi_fallback = False

    if not url:
        print("      No website found — will use NPI Registry fallback.")
        use_npi_fallback = True
    else:
        print(f"      Found: {url}")

    if not use_npi_fallback:
        print("[2/4] Scraping website...")
        scraped = scrape_clinic(url)
        print(f"      Pages scraped: {len(scraped['pages'])}")
        for p in scraped["pages"]:
            print(f"        • {p}")

        if not scraped["pages"]:
            print("      Site blocked scraping — falling back to NPI Registry.")
            use_npi_fallback = True

    if not use_npi_fallback:
        print("[3/4] Extracting providers via Claude NER...")
        data = extract_providers(scraped["text"], url)
        provider_count = len(data.get("providers", []))
        print(f"      Providers extracted: {provider_count}")
        usage = data.get("_usage", {})
        print(f"      Tokens used: {usage.get('input_tokens', '?')} in / {usage.get('output_tokens', '?')} out")

        if provider_count == 0:
            print("      No providers found on website — falling back to NPI Registry.")
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
            print("      No providers found in NPI Registry either. Saving empty result.")
        else:
            print(f"      Source: {data.get('_source')}")

    print("[4/4] Saving results...")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    name_slug = _slug(name) if name else _slug(f"{street}_{city}_{state}_{postal}")
    filename = f"{name_slug}_{timestamp}.json"
    save_json(data, str(Path(output_dir) / filename))
    save_report(data, str(Path(output_dir) / filename.replace(".json", ".txt")))

    print(f"\nDone. Results saved to {Path(output_dir).resolve()}")


if __name__ == "__main__":
    main()
