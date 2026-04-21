# Clinic Scraper POC — Project Document

---

## 1. Agenda / Purpose

The goal of this project is to automate the process of finding and extracting structured provider information from medical clinic websites. Given only a clinic address (and optionally a name), the tool:

1. Finds the clinic's official website automatically via search APIs
2. Scrapes the website for staff and provider pages
3. Uses an AI model (Claude) to intelligently extract structured data — provider names, titles, specialties, phone numbers, emails, and clinic contact details
4. Saves the results as a clean JSON file and a human-readable report

This eliminates the need to manually visit websites and copy-paste provider data, which is useful for healthcare directories, lead generation, research, or CRM population.

---

## 2. High-Level Flow

```
User Input (address / name)
        │
        ▼
 [Step 1] Search
  Find clinic website URL via Serper (Google) → fallback DuckDuckGo
        │
        ▼
 [Step 2] Scrape
  Fetch homepage + discover and fetch staff/provider sub-pages (up to 5)
        │
        ▼
 [Step 3] Extract
  Send combined page text to Claude AI for Named Entity Recognition (NER)
  → Returns structured JSON: clinic info + list of providers
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
│
├── steps/
│   ├── __init__.py         # Exports all step functions
│   ├── search.py           # Step 1 — find clinic website URL
│   ├── scraper.py          # Step 2 — scrape website pages
│   ├── extractor.py        # Step 3 — Claude AI NER extraction
│   └── output.py           # Step 4 — save JSON + generate report
│
└── output/                 # Generated results (created on first run)
    ├── <clinic>_<timestamp>.json
    └── <clinic>_<timestamp>.txt
```

---

## 4. How to Run

**Install dependencies (once):**
```bash
pip install -r requirements.txt
```

**Fill in `.env`:**
```
ANTHROPIC_API_KEY=sk-ant-api03-...
SERPER_API_KEY=...
```

**Run:**
```bash
# Address only (no clinic name known)
python clinic_scraper.py "815 W Randolph St, Chicago, IL 60607"

# With clinic name (gives better search results)
python clinic_scraper.py "Rush University Medical Center" "1653 W Congress Pkwy, Chicago, IL 60612"

# Custom output directory
python clinic_scraper.py "Clinic Name" "Street, City, ST 00000" --output-dir my_results
```

Address format must be: `"Street, City, ST 00000"` (two-letter state, 5-digit ZIP)

---

## 5. Code Walkthrough

### `clinic_scraper.py` — Entry Point

**Purpose:** Parses command-line input, validates the address format, and calls each step in sequence.

**Key logic:**
- Accepts 1 or 2 positional arguments: `[optional name]` `"address string"`
- Parses the address string using a regex into: street, city, state, postal
- Calls steps 1–4 in order, printing progress to the terminal
- If 0 pages are scraped (site blocked bots), it exits early with a clear error
- Output filenames are timestamped and slugified from the clinic name or address

---

### `steps/search.py` — Website Discovery

**Purpose:** Takes the clinic name + address and returns the URL of the clinic's official website.

**How it works:**
1. Builds a search query: `"<name> <address> <state> <postal> clinic official website"`
2. Tries **Serper.dev** first (Google Search API) — returns the first organic result that is NOT an aggregator site
3. Falls back to **DuckDuckGo Instant Answer API** if Serper is unavailable or fails

**Aggregator blocklist** (sites that are skipped):
`yelp.com`, `healthgrades.com`, `zocdoc.com`, `facebook.com`, `zoominfo.com`, `linkedin.com`, `vitals.com`, `webmd.com`, `yellowpages.com`, `mapquest.com`, `bbb.org`, `dnb.com`

---

### `steps/scraper.py` — Website Scraping

**Purpose:** Fetches and cleans text content from the clinic's website homepage and any staff/provider sub-pages.

**How it works:**
1. Opens a `requests.Session` with a real Chrome browser User-Agent to reduce bot detection
2. Fetches the homepage and strips all `<script>`, `<style>`, `<noscript>`, and `<head>` tags using **BeautifulSoup + lxml**
3. Scans all `<a>` links on the homepage looking for staff/provider pages using a keyword list
4. Fetches matching sub-pages (capped at 5) and combines all text into one block

**Staff page keywords used for link discovery:**
`staff`, `provider`, `physician`, `doctor`, `team`, `meet-our`, `our-team`, `directory`, `faculty`, `clinician`, `practitioner`, `specialist`, `about-us`, `about/team`

**Output:** `{ "pages": [...urls], "text": "combined plain text from all pages" }`

---

### `steps/extractor.py` — AI Extraction (Claude NER)

**Purpose:** Sends the scraped website text to Claude AI and returns structured provider data.

**Model used:** `claude-sonnet-4-5`

**How it works:**
1. Truncates the input text to 100,000 characters (~25k tokens) to stay within cost and context limits
2. Sends a structured prompt to Claude with a system role and a user prompt containing the scraped text
3. Claude is instructed to return a specific JSON schema with clinic info and a providers array
4. The response is cleaned (markdown fences stripped if present) and parsed as JSON
5. Token usage is recorded alongside the data for cost tracking

**Output JSON schema:**
```json
{
  "clinic_name": "string",
  "address": "string",
  "phone": "string",
  "email": "string",
  "website": "string",
  "providers": [
    {
      "name": "string",
      "title": "string",
      "specialty": "string",
      "phone": "string",
      "email": "string"
    }
  ],
  "_source_url": "URL that was scraped",
  "_usage": { "input_tokens": 0, "output_tokens": 0 }
}
```

---

### `steps/output.py` — Save Results

**Purpose:** Writes the extracted data to disk in two formats.

**`save_json(data, path)`**
- Writes the raw Claude output as a pretty-printed JSON file
- Useful for programmatic processing, importing into databases, or further automation

**`save_report(data, path)`**
- Generates a clean plain-text report with sections for clinic info and a numbered provider list
- Includes generation timestamp, source URL, and token usage at the bottom
- Useful for human review, sharing, or record-keeping

---

## 6. Dependencies

| Package | Version | Purpose |
|---|---|---|
| `anthropic` | ≥ 0.49.0 | Claude AI API client |
| `beautifulsoup4` | ≥ 4.12.0 | HTML parsing |
| `lxml` | ≥ 5.1.0 | Fast HTML parser backend for BeautifulSoup |
| `requests` | ≥ 2.31.0 | HTTP requests to websites and search APIs |
| `python-dotenv` | ≥ 1.0.0 | Loads `.env` file into environment variables |

---

## 7. Environment Variables (`.env`)

| Variable | Required | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | API key for Claude AI |
| `SERPER_API_KEY` | No | API key for Serper (Google Search). Falls back to DuckDuckGo if missing |

---

## 8. Known Limitations & Edge Cases

| Situation | Behaviour |
|---|---|
| Site blocks scraping (403/bot detection) | Exits with clear error after step 2 |
| No website found via search | Exits with error after step 1 |
| Claude returns markdown-wrapped JSON | Fences are stripped automatically before parsing |
| No clinic name provided | Search query uses address only; filename derived from address |
| Website has no staff/provider pages | Only the homepage is scraped; extraction still attempted |
| Very large websites | Text truncated at 100,000 characters before sending to Claude |

---

## 9. Sample Output

**Terminal:**
```
=== Clinic Scraper ===
Target : TeleSlim Clinic, 815 W Randolph St, Chicago, IL 60607

[1/4] Searching for clinic website...
      Found: https://www.teleslimclinic.com/
[2/4] Scraping website...
      Pages scraped: 2
        • https://www.teleslimclinic.com/
        • https://www.teleslimclinic.com/our-team
[3/4] Extracting providers via Claude NER...
      Providers extracted: 8
      Tokens used: 9380 in / 591 out
[4/4] Saving results...
[output] Saved JSON   → /path/to/output/teleslim_clinic_20260421_005326.json
[output] Saved Report → /path/to/output/teleslim_clinic_20260421_005326.txt

Done. Results saved to /path/to/output
```

**Report file (`.txt`):**
```
============================================================
CLINIC SCRAPER REPORT
Generated : April 21, 2026 12:53 AM
============================================================

CLINIC INFORMATION
----------------------------------------
Name    : TeleSlim Clinic
Address : 875 North Michigan Ave. 31st Floor Chicago, IL 60611
Phone   : (872) 666-9699
Email   : info@TeleSlimClinic.com
Website : https://www.teleslimclinic.com/

PROVIDERS  (8 found)
----------------------------------------
1. Jihad Kudsi
   Title     : M.D. MBA MSF DABOM FASMBS FACS CEO & Founder
   Specialty : General Surgery and Obesity Medicine
...
```
