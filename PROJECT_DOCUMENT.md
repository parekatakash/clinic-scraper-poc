# Clinic Scraper POC — Project Document

---

## 1. Agenda / Purpose

The goal of this project is to automate the process of finding and extracting structured provider information from medical clinic websites. Given only a clinic address (and optionally a name), the tool:

1. Finds the clinic's official website automatically via search APIs
2. Scrapes the website for staff and provider pages
3. Uses an AI model (Claude) to intelligently extract structured data — provider names, titles, specialties, current employer, phone, email, address, and license states
4. Validates the extracted address against the input address to ensure the right clinic was found
5. Falls back to the official CMS NPI Registry if no website is found, scraping fails, or the address doesn't match
6. Saves the results as a clean JSON file and a human-readable report

---

## 2. High-Level Flow

```
User Input (address / optional name)
        │
        ▼
 [Step 1] Search
  Find clinic website URL via Serper (Google) → fallback DuckDuckGo
        │
        ▼ (no URL found → skip to NPI)
 [Step 2] Scrape
  Fetch homepage + discover and fetch staff/provider sub-pages (up to 5)
        │
        ▼ (site blocked / 0 pages → skip to NPI)
 [Step 3] Extract
  Send combined page text to Claude AI for Named Entity Recognition (NER)
  → Returns structured JSON: clinic info + list of providers
        │
        ▼
 [Step 3b] Address Validation
  Check extracted address against input postal code + street
  → Mismatch → fall back to NPI
        │
        ▼ (0 providers or address mismatch → NPI fallback)
 [NPI Fallback] CMS NPI Registry
  Query official government registry by name/address/postal code
  → Returns verified provider data with NPI numbers and license states
        │
        ▼
 [Step 4] Output
  Save results as:
    • output/<name>_<timestamp>.json   (machine-readable)
    • output/<name>_<timestamp>.txt    (human-readable report)
```

---

## 3. Project Structure

```
clinic-scraper-poc/
│
├── clinic_scraper.py       # Entry point — CLI, argument parsing, orchestration
├── requirements.txt        # Python dependencies
├── .env                    # API keys and config (not committed to git)
├── .gitignore              # Excludes .env, output/, venv/, __pycache__
├── PROJECT_DOCUMENT.md     # This document
│
├── steps/
│   ├── __init__.py         # Exports all step functions
│   ├── search.py           # Step 1 — find clinic website URL
│   ├── scraper.py          # Step 2 — scrape website pages
│   ├── extractor.py        # Step 3 — Claude AI NER extraction
│   ├── npi.py              # NPI Registry fallback lookup
│   └── output.py           # Step 4 — save JSON + generate report
│
└── output/                 # Generated results (created on first run, gitignored)
    ├── <clinic>_<timestamp>.json
    └── <clinic>_<timestamp>.txt
```

---

## 4. How to Run

**Install dependencies (once):**
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

**Each new terminal session:**
```bash
source venv/bin/activate
```

**Run:**
```bash
# Address only (no clinic name known)
python3 clinic_scraper.py "815 W Randolph St, Chicago, IL 60607"

# With clinic name (gives more accurate search results)
python3 clinic_scraper.py "Rush University Medical Center" "1653 W Congress Pkwy, Chicago, IL 60612"
```

Address format must be: `"Street, City, ST 00000"` (two-letter state, 5-digit ZIP)

---

## 5. Code Walkthrough

### `clinic_scraper.py` — Entry Point

**Purpose:** Parses command-line input, validates the address format, and calls each step in sequence.

**Key logic:**
- Accepts 1 or 2 positional arguments: `[optional name]` `"address string"`
- Parses the address string using a regex into: street, city, state, postal
- Calls steps 1–4 in order with automatic fallback at each stage
- **Address validation:** after Claude extraction, checks that the extracted clinic address matches the input postal code or street number — if not, discards the result and uses NPI Registry instead
- Output filenames are timestamped and slugified from the clinic name or address

**Fallback triggers:**
| Situation | Action |
|---|---|
| No website found by search | Skip to NPI Registry |
| Website blocked scraping (403 etc.) | Skip to NPI Registry |
| Claude extracts 0 providers | Skip to NPI Registry |
| Extracted address doesn't match input | Skip to NPI Registry |

---

### `steps/search.py` — Website Discovery

**Purpose:** Takes the clinic name + address and returns the URL of the clinic's official website.

**How it works:**
1. With name: `"<name> <address> <state> <postal> official website"`
2. Without name: `"<address>" <state> <postal> clinic"` (address in quotes for exact matching)
3. Tries **Serper.dev** (Google Search API) first, falls back to **DuckDuckGo**

**Aggregator blocklist** (skipped):
`yelp.com`, `healthgrades.com`, `zocdoc.com`, `facebook.com`, `zoominfo.com`, `linkedin.com`, `vitals.com`, `webmd.com`, `yellowpages.com`, `mapquest.com`, `bbb.org`, `dnb.com`

---

### `steps/scraper.py` — Website Scraping

**Purpose:** Fetches and cleans text from the clinic homepage and staff/provider sub-pages.

**How it works:**
1. Opens a `requests.Session` with a Chrome browser User-Agent
2. Fetches the homepage and strips `<script>`, `<style>`, `<noscript>`, `<head>` tags via **BeautifulSoup**
3. Scans all `<a>` links for staff/provider page keywords, fetches up to 5 sub-pages
4. Combines all page text into one block separated by page URL labels

**Staff page keywords:**
`staff`, `provider`, `physician`, `doctor`, `team`, `meet-our`, `our-team`, `directory`, `faculty`, `clinician`, `practitioner`, `specialist`, `about-us`, `about/team`

---

### `steps/extractor.py` — Claude AI NER Extraction

**Purpose:** Sends scraped website text to Claude AI and returns structured provider data.

**Model:** `claude-sonnet-4-5`

**Extracted fields per provider:**
- `name`, `title`, `specialty`
- `current_employer` — clinic/hospital/org they work at
- `phone`, `email`
- `license_states` — list of US state abbreviations where a license is mentioned

**How it works:**
1. Truncates input to 100,000 characters to control token cost
2. Sends system + user prompt to Claude with the scraped text
3. Strips any markdown fences from the response before JSON parsing
4. Records token usage for cost tracking

---

### `steps/npi.py` — NPI Registry Fallback

**Purpose:** Queries the CMS National Provider Identifier (NPI) Registry — the official US government database of all licensed healthcare providers.

**No API key required.** Uses the public CMS API at `npiregistry.cms.hhs.gov`.

**How it works:**
1. If a name is given, searches by first/last name + state
2. Falls back to postal code + state location search
3. Returns the same data shape as `extractor.py` so the rest of the pipeline is unchanged

**NPI data includes:**
- NPI number (unique government ID for each provider)
- Full name + credential
- Primary specialty/taxonomy
- Practice address + phone
- Current employer (organization name at practice location)
- License states (derived from all registered addresses)

---

### `steps/output.py` — Save Results

**`save_json(data, path)`** — pretty-printed JSON file

**`save_report(data, path)`** — plain-text report including:
- Clinic info (name, address, phone, email, website)
- Numbered provider list with title, specialty, current employer, license states, phone, email
- Source URL or NPI Registry attribution
- Token usage (if Claude was used)

---

## 6. Dependencies

| Package | Version | Purpose |
|---|---|---|
| `anthropic` | ≥ 0.49.0 | Claude AI API client |
| `beautifulsoup4` | ≥ 4.12.0 | HTML parsing |
| `requests` | ≥ 2.31.0 | HTTP requests to websites and search APIs |
| `python-dotenv` | ≥ 1.0.0 | Loads `.env` file into environment variables |

*Note: `lxml` removed from requirements — `html.parser` (Python built-in) is used instead for reliability across environments.*

---

## 7. Environment Variables (`.env`)

| Variable | Required | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | API key for Claude AI (`sk-ant-api03-...`) |
| `SERPER_API_KEY` | No | API key for Serper.dev (Google Search). Falls back to DuckDuckGo if missing |

---

## 8. Known Limitations & Edge Cases

| Situation | Behaviour |
|---|---|
| Site blocks scraping (403/bot detection) | Falls back to NPI Registry |
| No website found via search | Falls back to NPI Registry |
| Extracted address doesn't match input | Falls back to NPI Registry |
| Claude returns markdown-wrapped JSON | Fences stripped automatically |
| No clinic name provided | Search uses quoted address; filename derived from address |
| NPI Registry returns no results | Empty provider list saved with a warning |
| Very large websites | Text truncated at 100,000 characters |

---

## 9. Sample Terminal Output

```
=== Clinic Scraper ===
Target : Unknown Clinic, 121 S Crescent Dr Ste B, Pueblo, CO 81003

[1/4] Searching for clinic website...
      Found: https://www.southerncoloradoclinic.com/
[2/4] Scraping website...
      Pages scraped: 2
[3/4] Extracting providers via Claude NER...
      Providers extracted: 36
      Address mismatch (got: 3676 Parker Blvd, Pueblo, CO 81008) — falling back to NPI Registry.
[NPI] Looking up providers in CMS NPI Registry...
      Providers found: 12
      Source: NPI Registry (cms.hhs.gov)
[4/4] Saving results...
[output] Saved JSON   → /path/to/output/121_s_crescent_dr_..._20260421.json
[output] Saved Report → /path/to/output/121_s_crescent_dr_..._20260421.txt

Done. Results saved to /path/to/output
```
