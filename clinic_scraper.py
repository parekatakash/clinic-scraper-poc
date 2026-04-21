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

from steps import extract_providers, save_json, save_report, scrape_clinic, search_clinic_website

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
    if not url:
        print("ERROR: Could not find a website for this clinic. Exiting.")
        sys.exit(1)
    print(f"      Found: {url}")

    print("[2/4] Scraping website...")
    scraped = scrape_clinic(url)
    print(f"      Pages scraped: {len(scraped['pages'])}")
    for p in scraped["pages"]:
        print(f"        • {p}")

    if not scraped["pages"]:
        print("ERROR: Could not scrape any pages (site may be blocking bots). Exiting.")
        sys.exit(1)

    print("[3/4] Extracting providers via Claude NER...")
    data = extract_providers(scraped["text"], url)
    provider_count = len(data.get("providers", []))
    print(f"      Providers extracted: {provider_count}")
    usage = data.get("_usage", {})
    print(f"      Tokens used: {usage.get('input_tokens', '?')} in / {usage.get('output_tokens', '?')} out")

    print("[4/4] Saving results...")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    name_slug = _slug(name) if name else _slug(f"{street}_{city}_{state}_{postal}")
    filename = f"{name_slug}_{timestamp}.json"
    save_json(data, str(Path(output_dir) / filename))
    save_report(data, str(Path(output_dir) / filename.replace(".json", ".txt")))

    print(f"\nDone. Results saved to {Path(output_dir).resolve()}")


if __name__ == "__main__":
    main()
