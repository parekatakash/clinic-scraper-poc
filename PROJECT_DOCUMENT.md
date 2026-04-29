# Clinic Scraper POC — Project Document

---

## 1. Agenda / Purpose

This tool automates the process of finding and extracting structured provider information from medical and veterinary clinic websites. Given a clinic address (and optionally a clinic name or provider name), it:

1. Finds the clinic's official website via search APIs
2. Scrapes the website for staff and provider pages
3. Uses Claude AI to extract structured data (names, titles, specialties, contact info)
4. Validates the extracted address against the input to confirm the right clinic was found
5. Falls back to the official CMS NPI Registry if the website fails or address doesn't match
6. Verifies each provider's license using the FSMB MED API (DocInfo), NPI taxonomy data, and state veterinary board scrapers
7. Checks the FDA CDRH establishment database for clinic device registration
8. Saves results as a timestamped JSON file and a plain-text report

---

## 2. Three Operating Modes

| Mode | How to invoke | Behaviour |
|---|---|---|
| **Address-only** | `python3 clinic_scraper.py "Street, City, ST ZIP"` | Auto-discovers clinic from address. Returns only providers licensed in the input state. |
| **Org + Address** | `python3 clinic_scraper.py "Clinic Name" "Street, City, ST ZIP"` | Targeted search for that specific clinic. Skips address mismatch check when org confirmed. |
| **Person + Address** | `python3 clinic_scraper.py "Dr. Name DVM" "Street, City, ST ZIP"` | Finds that specific provider. Returns full license info across all states. |

**Auto-detection rules:**
- First argument is a **person** if it contains credentials (`Dr.`, `MD`, `DO`, `DVM`, `VMD`, `NP`, `PA`, `DDS`, `APRN`, `RN`) — triggers person mode
- First argument is an **org** if it contains clinic/hospital keywords — triggers org mode
- Otherwise → address-only mode (single argument) or person mode (two arguments)

**Single-string auto-split:**
Name and address can also be passed as one string — the tool detects a name/org prefix before the first digit and splits automatically:
```bash
# These two are equivalent:
python3 clinic_scraper.py "Dr. Abner Fernandez 121 S Crescent Dr Ste B, Pueblo, CO 81003"
python3 clinic_scraper.py "Dr. Abner Fernandez" "121 S Crescent Dr Ste B, Pueblo, CO 81003"
```

---

## 3. High-Level Flow

```
User Input
  [optional name/org]  "Street, City, ST ZIP"
        │
        ▼
 [Mode Detection]
  1 arg  → address-only
  2 args → person mode (credentials found) or org mode (org keywords found)
        │
        ▼
 [1/5] Search
  Serper.dev (Google) → fallback DuckDuckGo
  Returns (URL, discovered_name) — name extracted from page title even if URL is blocked
        │
        ├─ address-only: if discovered_name is a healthcare org → use as org_name
        │                if not healthcare → skip ("Skipped non-healthcare result")
        │                if no URL but name found → re-search with org name
        ▼
 [2/5] Scrape
  Fetch homepage + up to 5 staff/provider sub-pages
        │
        ▼ 0 pages or address mismatch → skip to NPI
 [3/5] Extract (Claude AI)
  Send combined page text to claude-sonnet-4-5
  Returns: clinic info + provider list (deduped by normalized name)
        │
        ▼ 0 providers or address mismatch → skip to NPI
 [NPI Fallback] CMS NPI Registry
  Name mode   → search by first/last name (state-scoped, then national)
  Address mode → fetch ZIP results, filter to providers at exact street
        │
        ▼
 [4/5] License Verification
  For each provider:
    → Physician/NP/PA: FSMB MED API (DocInfo) + NPI fallback + DEA + Medicare
    → Veterinarian: State vet board scraper + NPI + DEA
    → Pharmacist: NPI + pharmacy board URL + Medicare
  Address-only: filter to providers licensed in target state
  Vet inclusive: always include even without confirmed license (vet specialty)
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
│   ├── extractor.py        # Step 3 — Claude AI NER extraction + deduplication
│   ├── npi.py              # NPI Registry lookup + provider category detection
│   ├── dea.py              # DEA registration: checksum validation + web lookup
│   ├── license.py          # All license types: FSMB, DEA, State Vet Board, Pharmacy, Medicare, FDA
│   ├── vet_license.py      # State veterinary board scrapers (NJ, NC; others fallback to manual URL)
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
# Address only — auto-discovers clinic and returns all licensed providers
venv/bin/python3 clinic_scraper.py "65 Mill St #1, Agawam, MA 01001"

# Org + address — targets specific clinic, bypasses address mismatch check
venv/bin/python3 clinic_scraper.py "Agawam Animal Hospital" "65 Mill St #1, Agawam, MA 01001"

# Person + address — finds that specific provider with full license info
venv/bin/python3 clinic_scraper.py "Dr. Abner Fernandez MD" "121 S Crescent Dr Ste B, Pueblo, CO 81003"

# Custom output directory
venv/bin/python3 clinic_scraper.py "Rush University" "1653 W Congress Pkwy, Chicago, IL 60612" --output-dir my_results
```

Address format required: `"Street, City, ST 00000"` — two-letter state, 5-digit ZIP.

---

## 6. Code Walkthrough

### `clinic_scraper.py` — Entry Point

Parses arguments, detects the operating mode, and orchestrates all 5 steps.

**Three modes:**
- `mode = "address"` — 1 arg or 2 args with neither person credentials nor org keywords
- `mode = "org"` — 2 args where arg1 contains clinic/hospital keywords
- `mode = "person"` — 2 args where arg1 contains medical credentials

**Address-only re-search flow:**
1. Serper returns a blocked directory URL (e.g., loc8nearme.com, yelp.com)
2. Name is still extracted from the page title (e.g., "Agawam Animal Hospital")
3. `_is_healthcare_name()` validates it's a medical/vet org (not a restaurant or pub)
4. If valid, name is used as `org_name` and a targeted re-search is triggered
5. The re-search finds the actual clinic website

**Mismatch check bypass:**
- When `org_name` is confirmed (user-provided OR auto-discovered), address mismatch check is skipped
- This handles clinics whose website shows only city/state without a street number

**Provider filtering (address-only mode):**
- Always includes providers with `licensed_in_target_state: True`
- Always includes veterinarians (even without confirmed license) so they appear for manual review
- All others excluded if no license in target state confirmed

---

### `steps/search.py` — Website Discovery

Builds a search query and returns `(url, discovered_name)` tuple.

**Name extraction from all results (including blocked):**
- Collects `fallback_name` from the first result title even if its URL is blocked
- Returns the first non-blocked URL with its title-derived name
- If all URLs are blocked, returns `(None, fallback_name)` so the name is still available for re-search

**Knowledge graph fallback:**
Serper returns a `knowledgeGraph` with the clinic's phone, address, and website from Google's business database. These fields are used to populate clinic info in the report even when website scraping and NPI both return nothing.

**Blocked aggregator sites (extended list):**
yelp.com, healthgrades.com, zocdoc.com, facebook.com, zoominfo.com, linkedin.com, vitals.com, webmd.com, yellowpages.com, mapquest.com, bbb.org, dnb.com, loc8nearme.com, cylex.us, manta.com, n49.com, chamberofcommerce.com, brownbook.net, hotfrog.com, showmelocal.com, storeboard.com, iplanet.com, local.com, citysearch.com, merchantcircle.com

---

### `steps/scraper.py` — Website Scraping

Fetches and cleans text from the homepage and up to 5 staff/provider sub-pages.

- Uses a Chrome browser User-Agent to reduce bot detection
- Parses HTML with **BeautifulSoup + html.parser** (built-in, no extra install)
- Strips `<script>`, `<style>`, `<noscript>`, `<head>` tags
- Discovers staff/provider pages by scanning `<a>` links for keywords:
  `staff`, `provider`, `physician`, `doctor`, `team`, `meet-our`, `our-team`, `directory`, `faculty`, `clinician`, `practitioner`, `specialist`, `about-us`, `about/team`, `about`, `veterinarian`, `veterinary`, `our-vets`, `our-doctors`, `meet-the`, `doctors`, `vets`, `associates`, `professionals`, `bio`, `bios`
- **`about` is included** because many small clinics put all provider bios on a single "About" page (e.g., `about.html`) with no separate staff directory

---

### `steps/extractor.py` — Claude AI NER + Deduplication

Sends combined page text to Claude and returns deduplicated, structured provider data.

- **Model:** `claude-sonnet-4-5`
- **Max input:** 100,000 characters (~25k tokens)
- Strips markdown fences from response before JSON parsing
- Records token usage for cost tracking

**Provider deduplication:**
- Strips titles (`Dr.`, `DVM`, `MD`, `NP`, `PA`, etc.) and middle initials from names
- Normalizes to `(first, last)` key
- When the same person appears multiple times (homepage + staff page), keeps the record with more non-null fields

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
3. Returns empty if no providers match — never dumps the whole ZIP

**Data extracted from NPI taxonomy:**
- License state and license number per taxonomy record
- Primary specialty
- Practice address, phone, employer

---

### `steps/dea.py` — DEA Registration Verification

Verifies DEA registration for providers who handle controlled substances.

**Two methods:**
1. **Checksum validation** (offline, always runs) — DEA numbers have a defined checksum algorithm
2. **DEA web lookup** (best-effort) — attempts `apps.deadiversion.usdoj.gov` registrant search

**Fields returned:** `dea_number`, `dea_valid_format`, `dea_valid_checksum`, `dea_registrant_type`, `dea_registered`, `dea_schedules`, `dea_expiry`, `dea_lookup_url`

---

### `steps/vet_license.py` — State Veterinary Board Scrapers

Attempts to retrieve a veterinarian's state license number directly from state vet board websites.

**Implemented scrapers (automated lookup):**

| State | Board | Method |
|---|---|---|
| NJ | newjersey.mylicense.com | ASP.NET ViewState POST → HTML table (`datagrid_results`) |
| NC | portal.ncvmb.org | ASP.NET ViewState POST → `dl-horizontal` element pairs |

**NJ license number format:** `^\d{2}[A-Z]{2}\d{6,8}$` (e.g., `29VI00653000`)

**NC result structure:**
- Two `dl.dl-horizontal` per result: first = `{Name, License Status, Issued Date}`, second = `{License Number, License Type, Expiration Date}`

**Fallback states (manual URL only):** MA, CA, TX, FL, NY, IL, CO, WA, GA, OH, PA, AZ, VA, MI, MN, OR, CT (all use JS-heavy platforms — Thentia Cloud, Salesforce, eLicense, Angular — that cannot be queried without a real browser)

**Fields returned:**
- `state_vet_board_url` — always included (manual fallback link)
- `vet_license_found` — `True` if automated lookup succeeded
- `license_number`, `license_status`, `license_expiry` — from automated lookup
- `vetverify_lookup_url` — AAVSB VetVerify link (Cloudflare-protected, manual only)

**Adding a new state scraper:**
1. Add the state code to `_STATE_FUNCS` mapping: `"GA": "_lookup_ga"`
2. Implement `_lookup_ga(first, last) -> dict | None` following the NJ/NC pattern:
   - GET the search page, extract `__VIEWSTATE` and `__EVENTVALIDATION`
   - POST with first name, last name, and profession filter
   - Parse the result HTML and return `{license_number, license_status, vet_license_found: True}`
3. Test with a known licensed vet in that state

---

### `steps/license.py` — License Verification

Enriches each provider with **all applicable license types** based on their provider category:

| Provider Category | License Sources Used |
|---|---|
| Physician (MD/DO) | FSMB + NPI + DEA + Medicare |
| Nurse Practitioner / PA | FSMB + NPI + DEA + Medicare |
| Veterinarian | State vet board scraper + NPI + DEA |
| Pharmacist | NPI + Pharmacy board URL + Medicare |
| All others | NPI + Medicare |

**Veterinarian flow:**
1. Call `lookup_vet_license(first, last, state)` from `vet_license.py`
2. If `vet_license_found: True` → set `licensed_in_target_state: True`, copy `license_number` and `license_status`
3. Always run NPI name lookup for vets (regardless of whether FSMB state data exists)
4. DEA lookup runs for vets (they may prescribe controlled substances)

#### FSMB MED API / DocInfo (primary source for physicians/NPs/PAs)
- Official API of the Federation of State Medical Boards
- OAuth2 client credentials (`FSMB_CLIENT_ID` + `FSMB_CLIENT_SECRET`)
- **Register free at:** https://developer.fsmb.org
- Gracefully skipped if credentials are not set

#### NPI Registry — license data fallback (three-tier)
- **Tier 1:** NPI discovery path — license states/numbers come from taxonomy fields
- **Tier 2:** Website scraping path — live NPI name search fills in `license_states`, `license_number`, `npi`
- **Tier 3:** National retry — if state-scoped search returns nothing, retries without state filter

#### DEA Registration
- Called for physicians, NPs, PAs, vets, pharmacists (`requires_dea: true`)
- Checksum validation always runs; web lookup is best-effort

#### Pharmacy License Reference
- Returns state-specific pharmacy board URL (CO, CA, TX, FL, NY, IL, MA, GA, WA)
- Falls back to NABP e-Profile lookup URL for other states

#### Medicare Enrollment — CMS PECOS Open Data
- Called for all providers with a known NPI number
- Returns `medicare_enrolled`, `medicare_provider_type`

#### FDA CDRH Establishment Registry
- Checks if the clinic is registered as a medical device establishment
- Returns `fda_registered: true/false`

**Fields added per provider:**
- `provider_category` — detected type (Physician MD/DO, Nurse Practitioner, Veterinarian, etc.)
- `requires_dea` — true/false
- `license_number` — state medical or vet license number
- `license_status` — Active / Inactive / Expired
- `license_states` — all states where licensed
- `licensed_in_target_state` — true/false
- `board_actions` — list of disciplinary actions (FSMB)
- `state_board_url` — manual verification link
- `dea` — DEA validation result
- `veterinary_license` — state vet board result (vets only)
- `pharmacy_license_url` — state pharmacy board URL (pharmacists only)
- `medicare_enrollment` — Medicare enrollment status

---

### `steps/output.py` — Save Results

**`save_json`** — pretty-printed JSON with all raw data

**`save_report`** — plain-text report showing per provider:
- `[LICENSED]` or `[NOT VERIFIED]` badge
- NPI number, title, specialty, current employer
- License number and states, license status
- Board actions
- DEA number with checksum status
- Vet license number, status, expiry (if found); manual vet board URL
- Pharmacy board URL
- Medicare enrollment status
- Phone and email
- FDA registration status for the clinic at the bottom

---

## 7. Dependencies

| Package | Purpose |
|---|---|
| `anthropic` | Claude AI API client |
| `beautifulsoup4` | HTML parsing (website scraping + vet board scrapers) |
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
| **State Vet Boards (NJ, NC)** | Yes | Yes | No | Free | None |
| **State Vet Boards (other states)** | Manual only | Manual only | No | Free | None |
| **FDA CDRH** | N/A (clinic only) | Device registration | No | Free | None |

---

## 10. Edge Cases & Behaviour

| Situation | Behaviour |
|---|---|
| Name + address passed as one string | Auto-split at first digit; name/org detected from prefix |
| No website found by search | Falls back to NPI Registry; clinic info populated from Google Knowledge Graph |
| Website blocked (403 / bot detection) | Falls back to NPI Registry |
| All search results are blocked directories | Name extracted from title → re-search with org name |
| Discovered org name is a restaurant / pub | Rejected by `_is_healthcare_name()`, no re-search |
| Extracted address postal code doesn't match input | Falls back to NPI Registry |
| Org name confirmed (user or auto-discovered) | Address mismatch check bypassed |
| Claude extracts 0 providers | Falls back to NPI Registry |
| Same provider extracted from multiple pages | Deduplicated by normalized `(first, last)` key |
| NPI name search returns nothing | Retries without state restriction |
| NPI address search finds no street match | Returns empty — no ZIP dump |
| FSMB credentials not set | License states from NPI taxonomy only |
| Provider from website with no NPI + FSMB unavailable | Live NPI name lookup fills license states and number |
| Veterinarian in address-only mode with no confirmed license | Included in results (manual vet board URL provided) |
| Vet license found on state board | `licensed_in_target_state: True`, license number/status copied |
| State vet board uses JS platform | Automated lookup unavailable; manual URL provided |
| Website returns markdown-wrapped JSON | Fences stripped automatically |
| Very large website | Text truncated at 100,000 characters |

---

## 11. Sample Terminal Output

**Address-only mode — veterinary clinic** (Agawam Animal Hospital, MA):
```
=== Clinic Scraper ===
Target : 65 Mill St #1, Agawam, MA 01001
Mode   : Address-only (license filter: MA only)

[1/5] Searching for clinic website...
      Skipped blocked result: loc8nearme.com
      Discovered: Agawam Animal Hospital
      Re-searching with org name...
      Found: https://www.agawamanimalhospital.com/
[2/5] Scraping website...
      Pages scraped: 3
[3/5] Extracting providers via Claude NER...
      Providers extracted: 2
      Tokens used: 1842 in / 201 out
[4/5] Verifying licenses...
      [vet_license] MA: automated lookup not available — manual URL provided
      Licensed in MA: 2 / 2 provider(s)
[5/5] Saving results...
[output] Saved JSON   → /path/to/output/65_mill_st_...json
[output] Saved Report → /path/to/output/65_mill_st_...txt
```

**Org + Address mode** (targeted clinic search):
```
=== Clinic Scraper ===
Target : Agawam Animal Hospital, 65 Mill St #1, Agawam, MA 01001
Mode   : Org + Address (targeted clinic search)

[1/5] Searching for clinic website...
      Found: https://www.agawamanimalhospital.com/
[2/5] Scraping website...
      Pages scraped: 3
[3/5] Extracting providers via Claude NER...
      Providers extracted: 2
[4/5] Verifying licenses...
      Licensed in MA: 2 / 2 providers
[5/5] Saving results...
```

**Name + Address mode** (specific provider, full license info):
```
=== Clinic Scraper ===
Target : Dr. Abner Fernandez MD, 121 S Crescent Dr Ste B, Pueblo, CO 81003
Mode   : Person + Address (full license lookup)

[1/5] Searching for clinic website...
      Found: https://www.uchealth.org/provider/abner-fernandez-md/
[2/5] Scraping website...
      Pages scraped: 2
[3/5] Extracting providers via Claude NER...
      Providers extracted: 1
      Tokens used: 2059 in / 186 out
[4/5] Verifying licenses...
      Licensed in CO: 1 / 1 provider(s)
[5/5] Saving results...
[output] Saved JSON   → /path/to/output/dr_abner_fernandez_20260422.json
[output] Saved Report → /path/to/output/dr_abner_fernandez_20260422.txt
```

---

## 12. GitHub Repository

https://github.com/parekatakash/clinic-scraper-poc
