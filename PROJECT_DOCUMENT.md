# Clinic Scraper POC — Project Document

---

## 1. Agenda / Purpose

This tool automates the process of finding and extracting structured provider information from medical clinic websites. Given a clinic address (and optionally a provider name), it:

1. Finds the clinic's official website via search APIs
2. Scrapes the website for staff and provider pages
3. Uses Claude AI to extract structured data (names, titles, specialties, contact info)
4. Validates the extracted address against the input to confirm the right clinic was found
5. Falls back to the official CMS NPI Registry if the website fails or address doesn't match
6. Verifies each provider's license using the FSMB MED API (DocInfo) and NPI taxonomy data
7. Checks the FDA CDRH establishment database for clinic device registration
8. Saves results as a timestamped JSON file and a plain-text report

---

## 2. Two Operating Modes

| Mode | How to invoke | Behaviour |
|---|---|---|
| **Address-only** | `python3 clinic_scraper.py "Street, City, ST ZIP"` | Returns only providers licensed in the input state at that address |
| **Name + Address** | `python3 clinic_scraper.py "Dr. Name" "Street, City, ST ZIP"` | Finds that specific provider and returns full license info across all states |

---

## 3. High-Level Flow

```
User Input
  [optional name]  "Street, City, ST ZIP"
        │
        ▼
 [1/5] Search
  Serper.dev (Google) → fallback DuckDuckGo
  Find clinic's official website URL
        │
        ▼ no URL, blocked, or address mismatch → skip to NPI
 [2/5] Scrape
  Fetch homepage + up to 5 staff/provider sub-pages
        │
        ▼ 0 pages or address mismatch → skip to NPI
 [3/5] Extract (Claude AI)
  Send combined page text to claude-sonnet-4-5
  Returns: clinic info + provider list with employer, specialty, contact
        │
        ▼ 0 providers or address mismatch → skip to NPI
 [NPI Fallback] CMS NPI Registry
  Name mode   → search by first/last name (state-scoped, then national)
  Address mode → fetch ZIP results, filter to providers at exact street
        │
        ▼
 [4/5] License Verification
  For each provider:
    → FSMB MED API (DocInfo): license status, number, board actions
    → NPI taxonomy: license states + numbers (fallback if no FSMB creds)
    → FDA CDRH: check clinic establishment registration
  Address-only mode: filter out providers with no license in target state
        │
        ▼
 [5/5] Save
  output/<slug>_<timestamp>.json   (machine-readable)
  output/<slug>_<timestamp>.txt    (human-readable report)
```

---

## 4. Project Structure

```
clinic-scraper-poc/
│
├── clinic_scraper.py       # Entry point — CLI, orchestration, mode logic
├── requirements.txt        # Python dependencies
├── .env                    # API keys (never committed to git)
├── .gitignore
├── PROJECT_DOCUMENT.md     # This document
│
├── steps/
│   ├── __init__.py         # Exports all public step functions
│   ├── search.py           # Step 1 — find clinic website URL
│   ├── scraper.py          # Step 2 — scrape website pages
│   ├── extractor.py        # Step 3 — Claude AI NER extraction
│   ├── npi.py              # NPI Registry lookup + provider category detection
│   ├── dea.py              # DEA registration: checksum validation + web lookup
│   ├── license.py          # All license types: FSMB, DEA, VetVerify, Pharmacy, Medicare, FDA
│   └── output.py           # Save JSON + plain-text report
│
└── output/                 # Generated results (gitignored)
    ├── <slug>_<timestamp>.json
    └── <slug>_<timestamp>.txt
```

---

## 5. How to Run

**First-time setup:**
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

**Every new terminal session:**
```bash
source venv/bin/activate
```

**Run using the venv Python directly (most reliable):**
```bash
# Address only — returns all licensed providers at that location
venv/bin/python3 clinic_scraper.py "121 S Crescent Dr Ste B, Pueblo, CO 81003"

# Name + address — finds that specific provider with full license info
venv/bin/python3 clinic_scraper.py "Dr. Abner Fernandez" "121 S Crescent Dr Ste B, Pueblo, CO 81003"

# Custom output directory
venv/bin/python3 clinic_scraper.py "Rush University" "1653 W Congress Pkwy, Chicago, IL 60612" --output-dir my_results
```

Address format required: `"Street, City, ST 00000"` — two-letter state, 5-digit ZIP.

---

## 6. Code Walkthrough

### `clinic_scraper.py` — Entry Point

Parses arguments, sets the operating mode, and orchestrates all 5 steps.

**Two modes:**
- `address_only_mode = True` when no name is given — filters final provider list to those licensed in the target state only
- `address_only_mode = False` when a name is given — returns all license info for that provider regardless of state

**Fallback triggers (any of these causes NPI to be used instead of the website):**
- No website URL found by search
- Website returns 0 pages (blocked/403)
- Claude extracts 0 providers
- Extracted clinic address postal code or street number doesn't match input

---

### `steps/search.py` — Website Discovery

Builds a search query and returns the first non-aggregator result URL.

- **With name:** `"<name> <address> <state> <ZIP> official website"`
- **Without name:** `"<address>" <state> <ZIP> clinic"` (quoted address for exact match)
- Tries **Serper.dev** (Google Search API) first, falls back to **DuckDuckGo**

**Blocked aggregator sites:** yelp.com, healthgrades.com, zocdoc.com, facebook.com, zoominfo.com, linkedin.com, vitals.com, webmd.com, yellowpages.com, mapquest.com, bbb.org, dnb.com

---

### `steps/scraper.py` — Website Scraping

Fetches and cleans text from the homepage and up to 5 staff/provider sub-pages.

- Uses a Chrome browser User-Agent to reduce bot detection
- Parses HTML with **BeautifulSoup + html.parser** (built-in, no extra install)
- Strips `<script>`, `<style>`, `<noscript>`, `<head>` tags
- Discovers staff/provider pages by scanning `<a>` links for keywords:
  `staff`, `provider`, `physician`, `doctor`, `team`, `meet-our`, `our-team`, `directory`, `faculty`, `clinician`, `practitioner`, `specialist`, `about-us`, `about/team`

---

### `steps/extractor.py` — Claude AI NER

Sends combined page text to Claude and returns structured provider data.

- **Model:** `claude-sonnet-4-5`
- **Max input:** 100,000 characters (~25k tokens)
- Strips markdown fences from response before JSON parsing
- Records token usage for cost tracking

**Extracted fields per provider:**
`name`, `title`, `specialty`, `current_employer`, `phone`, `email`, `license_states`

---

### `steps/npi.py` — NPI Registry Lookup

Queries the CMS NPI Registry (free, no API key needed).

**Name mode:**
1. Search by first + last name scoped to target state
2. If no results, retry nationally (provider may be licensed elsewhere)
3. Never falls back to a ZIP code dump when a name is given

**Address-only mode:**
1. Fetch up to 200 individual providers in the ZIP code
2. Filter to those whose practice address contains both the **street number** AND the **first meaningful street name word** (skipping directionals like N/S/E/W and suffixes like ST/AVE/DR)
   - e.g. `"121 S Crescent Dr Ste B"` → matches on `"121"` + `"CRESCENT"`
3. Returns empty if no providers match — never dumps the whole ZIP

**Data extracted from NPI taxonomy:**
- License state and license number per taxonomy record
- Primary specialty
- Practice address, phone, employer

---

### `steps/dea.py` — DEA Registration Verification

Verifies DEA (Drug Enforcement Administration) registration for any provider who handles controlled substances.

**Two methods:**
1. **Checksum validation** (offline, always runs) — DEA numbers have a defined checksum algorithm. Confirms the number is structurally valid before any API call.
2. **DEA web lookup** (best-effort) — attempts `apps.deadiversion.usdoj.gov` registrant search by name/number. Falls back gracefully if the site is inaccessible.

**DEA registrant types detected:** Practitioner (B), Mid-Level (C), Pharmacist (S), Researcher (A/G), Narcotic Treatment (M/R/T), Suboxone (X), and more.

**Fields returned:** `dea_number`, `dea_valid_format`, `dea_valid_checksum`, `dea_registrant_type`, `dea_registered`, `dea_schedules`, `dea_expiry`, `dea_lookup_url`

---

### `steps/license.py` — License Verification

Enriches each provider with **all applicable license types** based on their provider category:

| Provider Category | License Sources Used |
|---|---|
| Physician (MD/DO) | FSMB + NPI + DEA + Medicare |
| Nurse Practitioner / PA | FSMB + NPI + DEA + Medicare |
| Veterinarian | AAVSB VetVerify + DEA + NPI |
| Pharmacist | NPI + Pharmacy board URL + Medicare |
| All others | NPI + Medicare |

**Provider category** is detected from NPI taxonomy codes (set in `npi.py`). For providers extracted from websites (no NPI taxonomy), the title field is used (`MD`/`DO` → Physician, `NP`/`FNP` → Nurse Practitioner, `DVM`/`VMD` → Veterinarian, etc.).

#### 1. FSMB MED API / DocInfo (primary source)
- Official API of the Federation of State Medical Boards — the same backend that powers https://www.docinfo.org/
- **Two-step lookup (mirrors docinfo.org):**
  1. `GET /v2/practitioners/search` — search by first/last name, returns practitioners + FID (Federation ID)
  2. `GET /v2/licensure/{fid}/summary` — full license list with status per state (Active/Inactive/Expired)
  3. `GET /v2/practitioners/{fid}/verification` — board orders and disciplinary history
- OAuth2 scopes requested: `med.read med.order_read`
- Uses OAuth2 client credentials (`FSMB_CLIENT_ID` + `FSMB_CLIENT_SECRET`)
- Token is cached in memory and auto-refreshed
- Falls back to search-result embedded data if FID endpoints return non-200
- **Register free at:** https://developer.fsmb.org
- Gracefully skipped if credentials are not set

#### 2. NPI Registry — license data fallback (three-tier)
- **Tier 1 (NPI path):** When the tool falls back to the NPI Registry for provider discovery, license states and numbers come from NPI taxonomy fields already on the record.
- **Tier 2 (live name lookup):** When a provider came from website scraping (no NPI taxonomy), and FSMB is also unavailable, a live NPI name search is performed automatically to fill in `license_states`, `license_number`, and `npi` number. This ensures license data is always populated regardless of which discovery path was used.
- NPI national retry: if a state-scoped search returns nothing, retries without state filter.

#### 3. DEA Registration (`steps/dea.py`)
- Called for all providers where `requires_dea: true` (physicians, NPs, PAs, vets, pharmacists)
- Checksum validation always runs; web lookup is best-effort
- Manual DEA lookup URL always included in output for manual fallback

#### 4. Veterinary License — AAVSB VetVerify
- Called when `provider_category` is `Veterinarian`
- POST to `https://www.vetverify.org/verification/SearchDVM`
- Returns `vet_license_found`, raw table data, and manual lookup URL

#### 5. Pharmacy License Reference
- Called when `provider_category` is `Pharmacist` or `Pharmacy`
- Returns state-specific pharmacy board URL (CO, CA, TX, FL, NY, IL, MA, GA, WA)
- Falls back to NABP e-Profile lookup URL for other states

#### 6. Medicare Enrollment — CMS PECOS Open Data
- Called for all providers with a known NPI number
- `GET https://data.cms.gov/provider-data/api/1/datastore/query/...`
- Returns `medicare_enrolled`, `medicare_provider_type`, state, city

#### 7. FDA CDRH Establishment Registry
- Checks if the clinic is registered as a medical device establishment
- URL: `https://www.accessdata.fda.gov/scripts/cdrh/cfdocs/cfRL/rl.cfm`
- Returns: `fda_registered: true/false` and establishment details

#### 8. State Medical Board URLs (reference links)
For manual verification, a direct link to the state medical board lookup page is included for:
CO, CA, TX, FL, NY, IL, AZ, NV, GA, WA

**Fields added per provider:**
- `provider_category` — detected type (Physician MD/DO, Nurse Practitioner, Veterinarian, Pharmacist, etc.)
- `requires_dea` — true / false based on provider category
- `license_number` — state medical license number
- `license_status` — Active / Inactive / Expired (from FSMB)
- `license_states` — all states where licensed
- `licensed_in_target_state` — true / false
- `board_actions` — list of disciplinary actions (from FSMB)
- `state_board_url` — manual verification link for the input state
- `dea` — DEA validation result including `dea_number`, `dea_valid_checksum`, `dea_registrant_type`, `dea_lookup_url`
- `veterinary_license` — VetVerify result (veterinarians only)
- `pharmacy_license_url` — state pharmacy board URL (pharmacists only)
- `medicare_enrollment` — Medicare enrollment status from CMS

---

### `steps/output.py` — Save Results

**`save_json`** — pretty-printed JSON with all raw data

**`save_report`** — plain-text report showing per provider:
- `[LICENSED]` or `[NOT VERIFIED]` badge
- NPI number
- Title and specialty
- Current employer
- License number and states
- License status and board actions
- Phone and email
- FDA registration status for the clinic at the bottom

---

## 7. Dependencies

| Package | Purpose |
|---|---|
| `anthropic` | Claude AI API client |
| `beautifulsoup4` | HTML parsing |
| `requests` | HTTP calls to websites and APIs |
| `python-dotenv` | Loads `.env` into environment |

*`html.parser` (Python built-in) is used as the BS4 parser — no lxml needed.*

---

## 8. Environment Variables (`.env`)

| Variable | Required | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | Claude AI — `sk-ant-api03-...` |
| `SERPER_API_KEY` | No | Serper.dev Google Search. Falls back to DuckDuckGo if missing |
| `FSMB_CLIENT_ID` | No | FSMB MED API client ID — enables DocInfo license verification |
| `FSMB_CLIENT_SECRET` | No | FSMB MED API client secret |

**Get FSMB credentials free at:** https://developer.fsmb.org  
Without FSMB credentials the tool still works — license states come from NPI taxonomy.

---

## 9. License Data Sources Compared

| Source | License Number | License Status | Board Actions | Cost | Key |
|---|---|---|---|---|---|
| **FSMB MED API** | Yes | Yes (Active/Inactive) | Yes | Free (registration) | `FSMB_CLIENT_ID/SECRET` |
| **NPI Registry** | Yes (taxonomy) | No | No | Free | None |
| **FDA CDRH** | N/A (clinic only) | Device registration | No | Free | None |
| **ABMS Certification Matters** | N/A | Board certification | No | Paid institutional | Not implemented |

---

## 10. Edge Cases & Behaviour

| Situation | Behaviour |
|---|---|
| No website found by search | Falls back to NPI Registry |
| Website blocked (403 / bot detection) | Falls back to NPI Registry |
| Extracted address postal code doesn't match input | Falls back to NPI Registry |
| Claude extracts 0 providers | Falls back to NPI Registry |
| NPI name search returns nothing | Retries without state restriction |
| NPI address search finds no street match | Returns empty — no ZIP dump |
| FSMB credentials not set | License states from NPI taxonomy only |
| Provider from website with no license data + FSMB unavailable | Live NPI name lookup fills license states and number |
| Provider has no `licensed_in_target_state` (address-only mode) | Excluded from results |
| Website returns markdown-wrapped JSON | Fences stripped automatically |
| Very large website | Text truncated at 100,000 characters |

---

## 11. Sample Terminal Output

**Name + Address mode** (finds a specific provider, returns full license info):
```
=== Clinic Scraper ===
Target : Dr. Abner Fernandez, 121 S Crescent Dr Ste B, Pueblo, CO 81003
Mode   : Name + Address (full license lookup)

[1/5] Searching for clinic website...
      Found: https://www.uchealth.org/provider/abner-fernandez-md/
[2/5] Scraping website...
      Pages scraped: 2
        • https://www.uchealth.org/provider/abner-fernandez-md/
        • https://www.uchealth.org/provider/
[3/5] Extracting providers via Claude NER...
      Providers extracted: 1
      Tokens used: 2059 in / 186 out
[4/5] Verifying licenses...
      Licensed in CO: 1 / 1 provider(s)
      Checking FDA establishment registry for 'UCHealth Family Medicine - Parkview Pueblo West'...
      FDA status: Not found
[5/5] Saving results...
[output] Saved JSON   → /path/to/output/dr_abner_fernandez_20260421.json
[output] Saved Report → /path/to/output/dr_abner_fernandez_20260421.txt

Done. Results saved to /path/to/output
```

**Address-only mode** (returns all licensed providers at that location, with NPI fallback):
```
=== Clinic Scraper ===
Target : Unknown Clinic, 121 S Crescent Dr Ste B, Pueblo, CO 81003
Mode   : Address-only (license filter: CO only)

[1/5] Searching for clinic website...
      Found: https://www.southerncoloradoclinic.com/
[2/5] Scraping website...
      Pages scraped: 2
[3/5] Extracting providers via Claude NER...
      Providers extracted: 36
      Address mismatch (got: 3676 Parker Blvd, Pueblo, CO 81008) — falling back to NPI Registry.
[NPI] Looking up providers in CMS NPI Registry...
      4 provider(s) matched street '121 S Crescent Dr Ste B' out of 200 in ZIP 81003
      Providers found: 4
[4/5] Verifying licenses...
      Licensed in CO: 4 / 4 providers
[5/5] Saving results...
[output] Saved JSON   → /path/to/output/121_s_crescent_dr_..._20260421.json
[output] Saved Report → /path/to/output/121_s_crescent_dr_..._20260421.txt

Done. Results saved to /path/to/output
```

---

## 12. GitHub Repository

https://github.com/parekatakash/clinic-scraper-poc
