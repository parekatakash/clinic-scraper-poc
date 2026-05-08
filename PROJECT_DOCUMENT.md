# Clinic Scraper POC — Presentation Document

---

## 1. What This Does (Executive Summary)

This tool automates **shipping compliance license verification** for healthcare providers. Given a clinic address, it:

1. Finds the clinic's official website automatically
2. Extracts provider names, titles, and specialties using AI
3. Verifies each provider's license status against official government and medical board databases
4. Returns a structured report showing who is licensed, their license numbers, DEA status, and where to verify manually

**Business value:** What would take a compliance analyst 30–60 minutes per clinic (manual web searches across 5+ databases) runs in under 2 minutes with a single command.

---

## 2. Technology Stack

| Layer | Tool / Service | Purpose | Link |
|---|---|---|---|
| **AI / NER** | Claude (Anthropic) | Extract provider names & details from unstructured website text | https://anthropic.com |
| **Search** | Serper.dev | Google Search API — find clinic website URL | https://serper.dev |
| **Search fallback** | DuckDuckGo Instant Answer API | Free fallback when Serper is unavailable | https://api.duckduckgo.com |
| **Provider registry** | CMS NPI Registry | Free federal database of all licensed healthcare providers | https://npiregistry.cms.hhs.gov |
| **Medical licenses** | FSMB MED API (DocInfo) | Federation of State Medical Boards — license status, board actions | https://developer.fsmb.org |
| **DEA registration** | DEA Diversion | Drug Enforcement Administration controlled substance registration | https://www.deadiversion.usdoj.gov |
| **Vet licenses (NJ)** | NJ MyLicense | NJ veterinary board — automated license lookup | https://newjersey.mylicense.com/verification/Search.aspx |
| **Vet licenses (NC)** | NC Vet Medical Board | NC veterinary board — automated license lookup | https://portal.ncvmb.org/verification/search.aspx |
| **Vet licenses (all states)** | AAVSB VetVerify | Manual fallback for all states | https://www.aavsb.org/public-tools/vet-verify/ |
| **Medicare enrollment** | CMS PECOS Open Data | Medicare enrollment status for providers | https://data.cms.gov/provider-data |
| **FDA registration** | FDA CDRH | Medical device establishment registration for clinics | https://www.accessdata.fda.gov/scripts/cdrh/cfdocs/cfRL/rl.cfm |
| **HTML parsing** | BeautifulSoup4 (Python) | Parse clinic websites and state board HTML | https://pypi.org/project/beautifulsoup4/ |
| **Language** | Python 3.11+ | Runtime | https://python.org |

---

## 3. AI Model Details

| Property | Value |
|---|---|
| **Provider** | Anthropic |
| **Model used** | `claude-sonnet-4-5` |
| **Task** | Named Entity Recognition (NER) — extract structured provider data from raw website text |
| **Input** | Combined text from homepage + up to 5 staff/provider sub-pages (max 100,000 characters) |
| **Output** | JSON: clinic name, address, phone, email, and list of providers with name/title/specialty/employer |
| **Typical token usage** | 1,500–10,000 input tokens / 150–300 output tokens per clinic |
| **Cost per query** | ~$0.005–$0.03 depending on website size |
| **Why this model** | Sonnet balances accuracy and cost. Opus would be more accurate for ambiguous cases but ~5× more expensive. Haiku would be faster/cheaper but misses edge cases in unstructured text. |

**Why AI instead of regex/rules?**
Clinic websites have no standard format. Some list staff in HTML tables, others in paragraphs, some bury names in blog posts or "About" pages. Claude reads natural language regardless of format and returns structured JSON consistently.

---

## 4. Step-by-Step Process

### Step 0 — Input Parsing & Mode Detection

**Input:** One or two command-line arguments.

```
"Dr. Abner Fernandez 121 S Crescent Dr Ste B, Pueblo, CO 81003"
```

**What happens:**
1. Parse state (2-letter) and ZIP (5-digit) from the end of the string
2. If the street part starts with non-numeric words → auto-split into name + address
   - Street addresses always start with a number. Anything before the first digit = name/org.
3. Classify the name:
   - Contains `Dr.`, `MD`, `DVM`, `NP`, `PA` etc. → **Person mode** (specific provider lookup)
   - Contains `hospital`, `clinic`, `animal`, `vet` etc. → **Org mode** (clinic lookup)
   - No name → **Address mode** (auto-discover clinic from address)

**Three modes:**

| Mode | Input | What it returns |
|---|---|---|
| Address-only | `"121 Main St, Austin, TX 78701"` | All providers at that address — auto-discovers clinic |
| Org + Address | `"Austin Animal Clinic" "121 Main St..."` | All providers at that specific clinic |
| Person + Address | `"Dr. Jane Smith DVM" "121 Main St..."` | Full license info for that one provider |

---

### Step 1 — Website Discovery

**Goal:** Find the clinic's official website URL.

**How:**
1. Build a search query:
   - With name: `"All Pets and Paws 175 N Highway 113, Carrollton GA 30117 official website"`
   - Address-only: `"175 N Highway 113" GA 30117 clinic OR hospital OR veterinary OR medical`
2. Send to **Serper.dev** (Google Search API) — returns top 10 results
3. Skip known aggregator/directory sites (Yelp, Healthgrades, Facebook, ZoomInfo, 25+ others)
4. Return the first non-blocked URL

**Special cases:**
- If all results are blocked directories (e.g., Yelp, loc8nearme.com): extract the clinic name from the page title → re-search with just the clinic name → find real website
- If result is blocked but title contains clinic name: save the name for use in the re-search
- Google Knowledge Graph: Serper also returns the clinic's phone, address, and category from Google's business database — used to populate the report even when the website is inaccessible
- Fallback: DuckDuckGo Instant Answer API (free, no key needed)

**Output:** `(website_url, clinic_name, knowledge_graph_info)`

---

### Step 2 — Website Scraping

**Goal:** Extract text from the clinic's staff/provider pages.

**How:**
1. Fetch the homepage using a Chrome User-Agent (reduces bot detection)
2. Scan all `<a>` links on the homepage for staff-page keywords:
   `staff`, `team`, `doctors`, `providers`, `about`, `veterinarian`, `bio`, `directory`, `our-vets`, `meet-the`, and 20+ others
3. Fetch up to 5 matching sub-pages
4. Strip `<script>`, `<style>`, `<head>`, `<noscript>` tags
5. Combine all page text into one document

**Output:** Raw combined text (up to 100,000 characters)

**Why "about" is important:**
Many small clinics (especially veterinary practices) have no formal "Staff" page — all provider bios are on the "About" page. The keyword list includes `about` to catch `about.html`, `about.php`, etc.

**Fallback triggers** (if scraping fails, skip to NPI Registry):
- Website returns 403 / blocked by bot detection
- 0 pages successfully scraped
- Claude extracts 0 providers AND address doesn't match

---

### Step 3 — AI Extraction (Claude NER)

**Goal:** Extract structured provider data from raw website text.

**How:**
1. Send the combined page text to `claude-sonnet-4-5` via the Anthropic API
2. Claude identifies and extracts:
   - Clinic name, address, phone, email
   - For each provider: name, title (MD/DVM/NP etc.), specialty, employer, phone, email, license states
3. Response is JSON — parsed and validated
4. **Deduplication:** Same provider appearing on multiple pages (homepage + staff page) → keep the record with more complete data

**Prompt approach:** Unstructured NER — Claude reads the text like a human would and identifies healthcare providers regardless of how the website formats the information (tables, paragraphs, bios, testimonials).

**Token usage examples:**
| Clinic type | Approx. input tokens | Approx. cost |
|---|---|---|
| Small vet clinic (1 page) | 1,500 | ~$0.005 |
| Medium medical practice (3 pages) | 5,000 | ~$0.015 |
| Large health system (5+ pages) | 10,000+ | ~$0.030+ |

---

### Step 4 — NPI Registry Fallback

**Goal:** Find providers when the website path fails.

**Used when:**
- No website URL found
- Website blocked or returns 0 pages
- Claude extracts 0 providers

**CMS NPI Registry** (free, no key):
- **Person mode:** Search by first + last name → state-scoped first, then national retry
- **Address mode:** Fetch up to 200 providers in the ZIP code → filter to those whose practice address matches the input street number + street name
- **Org mode:** NPI-2 (organization) lookup by clinic name → returns org NPI, address, phone, authorized official

**Limitation:** Veterinarians, dentists, and other non-Medicare providers frequently have no NPI record. NPI is most complete for MDs, DOs, NPs, and PAs who bill Medicare/Medicaid.

---

### Step 5 — License Verification

**Goal:** For each found provider, verify their license status.

Different checks run based on provider type:

#### 5a. State Medical License — FSMB MED API
- **Who:** Physicians (MD/DO), Nurse Practitioners, Physician Assistants
- **Source:** Federation of State Medical Boards — the same backend as https://www.docinfo.org
- **What it returns:** License number, license status (Active/Inactive/Expired), all licensed states, board disciplinary actions
- **API:** Two-step — search by name → get Federation ID → fetch full license summary
- **Cost:** Free with registration at https://developer.fsmb.org
- **Fallback:** NPI taxonomy fields (license state + number without status or board actions)

#### 5b. DEA Registration
- **Who:** Physicians, NPs, PAs, Veterinarians, Pharmacists (anyone who prescribes controlled substances)
- **Two methods:**
  1. **Checksum validation** (offline, always runs) — DEA numbers have a mathematical checksum; confirms structural validity instantly
  2. **Web lookup** (best-effort) — queries `apps.deadiversion.usdoj.gov` for full registration details
- **Returns:** DEA number, valid/invalid, registrant type (Practitioner, Mid-Level, Pharmacist), schedules, expiry

#### 5c. Veterinary License — State Vet Boards
- **Who:** Veterinarians (DVM/VMD)
- **Automated (NJ, NC):** POST form to state board website → parse license number, status, expiry from HTML response
- **All other states:** Manual URL provided — compliance analyst clicks through to verify
- **AAVSB VetVerify:** Blocked by Cloudflare — manual link only

#### 5d. Medicare Enrollment
- **Who:** All providers with a known NPI number
- **Source:** CMS PECOS Open Data API (free)
- **Returns:** Enrolled yes/no, provider type, state

#### 5e. FDA CDRH Establishment Registry
- **Who:** The clinic itself (not individual providers)
- **Checks:** Whether the clinic is registered as a medical device establishment
- **Relevant for:** Clinics that sell, lease, or service medical devices

---

### Step 6 — Output

**Two files generated per run:**

**`<name>_<timestamp>.json`** — Machine-readable, all raw data:
```json
{
  "clinic_name": "All Pets & Paws Animal Hospital",
  "address": "175 N. Hwy. 113 Carrollton, GA 30117",
  "phone": "(770) 834-7044",
  "providers": [
    {
      "name": "Dr. Taffy Shields Rhyne",
      "title": "Doctor of Veterinary Medicine",
      "specialty": "Small Animal Medicine",
      "provider_category": "Veterinarian",
      "licensed_in_target_state": false,
      "veterinary_license": {
        "state_vet_board_url": "https://gcmb.mylicense.com/verification/Search.aspx"
      }
    }
  ]
}
```

**`<name>_<timestamp>.txt`** — Human-readable report:
```
============================================================
CLINIC SCRAPER REPORT
Generated : April 29, 2026 10:11 AM
============================================================
CLINIC INFORMATION
Name    : All Pets & Paws Animal Hospital
Address : 175 N. Hwy. 113 Carrollton, GA 30117
Phone   : (770) 834-7044

PROVIDERS  (1 found)
1. Dr. Taffy Shields Rhyne  [NOT VERIFIED]
   Title           : Doctor of Veterinary Medicine
   Specialty       : Small Animal Medicine
   State Board URL : https://gcmb.mylicense.com/verification/Search.aspx
============================================================
```

---

## 5. API Keys Required

| API | Required? | Cost | How to Get | Link |
|---|---|---|---|---|
| **Anthropic (Claude)** | **YES** | Pay-per-token (~$0.003–$0.015/query) | Sign up → API keys | https://console.anthropic.com |
| **Serper.dev** | No (but strongly recommended) | Free tier: 2,500 queries/month. Paid: $50/month for 50k queries | Sign up → API key | https://serper.dev |
| **FSMB MED API** | No (but enables license status) | Free with registration | Register as developer | https://developer.fsmb.org |
| **CMS NPI Registry** | No | Always free | No key needed | https://npiregistry.cms.hhs.gov/api/ |
| **CMS PECOS** | No | Always free | No key needed | https://data.cms.gov/provider-data |
| **DEA Diversion** | No | Always free | No key needed | https://www.deadiversion.usdoj.gov |
| **FDA CDRH** | No | Always free | No key needed | https://www.accessdata.fda.gov |

**Minimum viable setup:** Only `ANTHROPIC_API_KEY` is required. The tool still works without Serper or FSMB — it falls back to DuckDuckGo search and NPI taxonomy for license data.

---

## 6. Setup Instructions (Step by Step)

### Prerequisites
- Python 3.11 or newer: https://python.org/downloads
- Git: https://git-scm.com/downloads

### Install

```bash
# 1. Clone the repository
git clone https://github.com/parekatakash/clinic-scraper-poc.git
cd clinic-scraper-poc

# 2. Create a Python virtual environment
python3 -m venv venv

# 3. Activate it
source venv/bin/activate          # Mac / Linux
venv\Scripts\activate             # Windows

# 4. Install dependencies (4 packages)
pip install -r requirements.txt
```

### Configure API Keys

Create a file named `.env` in the project folder:

```
ANTHROPIC_API_KEY=sk-ant-api03-...
SERPER_API_KEY=...
FSMB_CLIENT_ID=...
FSMB_CLIENT_SECRET=...
```

- Get Anthropic key: https://console.anthropic.com → API Keys
- Get Serper key: https://serper.dev → Dashboard → API Key
- Get FSMB credentials: https://developer.fsmb.org → Register → Create Application

### Run

```bash
# Address only
venv/bin/python3 clinic_scraper.py "175 N Highway 113, Carrollton, GA 30117"

# Org + address (or combined in one string)
venv/bin/python3 clinic_scraper.py "All Pets and Paws 175 N Highway 113, Carrollton, GA 30117"

# Person + address
venv/bin/python3 clinic_scraper.py "Dr. Abner Fernandez 121 S Crescent Dr Ste B, Pueblo, CO 81003"
```

Results saved to `output/` folder as `.json` and `.txt`.

---

## 7. Feasibility Assessment — What Works vs What Needs Work

### ✅ Fully Automated (Works Today)

| Scenario | Data Source | Coverage |
|---|---|---|
| Find MD/DO physician's license | FSMB MED API | All 50 states |
| Find NP / PA license | FSMB MED API | All 50 states |
| Verify DEA registration (checksum) | Offline algorithm | All providers |
| Look up provider's NPI number | CMS NPI Registry | All MDs, DOs, NPs, PAs |
| Check Medicare enrollment | CMS PECOS | All Medicare-enrolled providers |
| Find vet license in NJ | NJ MyLicense | New Jersey |
| Find vet license in NC | NC Vet Board | North Carolina |
| Scrape clinic website for provider names | BeautifulSoup + Claude | Any clinic with a public website |
| Extract names from any page format | Claude AI | Tables, bios, paragraphs, About pages |

### ⚠️ Partial / Needs Manual Follow-up

| Scenario | Current State | What's Missing |
|---|---|---|
| Vet license in GA, CA, TX, FL, NY, IL, etc. | Manual URL provided — analyst clicks through | State vet boards use JavaScript platforms (can't be scraped without a browser) |
| DEA web lookup | Best-effort HTTP request | DEA site is unreliable; checksum validation always works |
| Provider has no website listing | 0 providers found — report shows clinic info only | Clinic doesn't publish staff online |
| Pharmacy license verification | State board URL provided | No automated lookup implemented |

### ❌ Not Automated (Manual Only Today)

| Scenario | Why | Feasibility to Automate |
|---|---|---|
| Most state vet boards (OR, TX, WA, AZ, PA, VA, MI, OH, CT...) | All use JavaScript-heavy portals (Thentia Cloud, Salesforce, eLicense, Angular) that require a real browser | Possible with Playwright/Selenium — medium effort per state |
| AAVSB VetVerify | Blocked by Cloudflare | Would need browser automation + CAPTCHA handling |
| State medical board lookups (non-FSMB) | FSMB covers most states; some boards are not in FSMB | State-by-state scraper effort |
| Dental board licenses | Not implemented | Similar effort to vet boards |
| Optometry / Chiropractic / PT licenses | Not implemented | State-by-state effort |

---

## 8. Cost Estimate Per Query

| Component | Cost Per Run | Notes |
|---|---|---|
| Claude API | ~$0.005–$0.03 | Depends on website size. Small vet clinic ≈ $0.005. Large hospital ≈ $0.03 |
| Serper.dev | ~$0.001 | 1 search query per run ($50/month = 50k queries) |
| All other APIs | $0.00 | NPI, FSMB, CMS, DEA, FDA — all free |
| **Total per clinic** | **~$0.006–$0.031** | |

**Volume estimate:**
| Clinics/month | Est. Claude cost | Serper cost | Total |
|---|---|---|---|
| 100 | $0.50–$3.00 | $0.10 | ~$3–$4/month |
| 1,000 | $5–$30 | $1.00 | ~$6–$31/month |
| 10,000 | $50–$300 | $10 | ~$60–$310/month |

---

## 9. Data Sources — What Each Returns

| Source | URL | Returns | Coverage |
|---|---|---|---|
| **CMS NPI Registry** | https://npiregistry.cms.hhs.gov/api/ | NPI number, name, address, specialty, license state+number | MDs, DOs, NPs, PAs, Pharmacists, some vets |
| **FSMB MED API** | https://services-med.fsmb.org | License number, status, all states licensed, board actions | Physicians, NPs, PAs — all 50 states |
| **DEA Diversion** | https://www.deadiversion.usdoj.gov | DEA number, registrant type, schedules, expiry | All DEA-registered providers |
| **CMS PECOS** | https://data.cms.gov/provider-data | Medicare enrollment status, provider type | Medicare-enrolled providers |
| **NJ MyLicense** | https://newjersey.mylicense.com/verification | Vet license number, status | NJ veterinarians |
| **NC Vet Board** | https://portal.ncvmb.org/verification | Vet license number, status, expiry | NC veterinarians |
| **AAVSB VetVerify** | https://www.aavsb.org/public-tools/vet-verify/ | Manual vet license lookup (all states) | All states — manual only |
| **FDA CDRH** | https://www.accessdata.fda.gov/scripts/cdrh | Medical device establishment registration | Clinics with device registrations |
| **Serper (Google KG)** | https://serper.dev | Clinic name, phone, address, website from Google | Any Google-indexed business |

---

## 10. What Would Be Needed to Scale This

### For Production Use

| Requirement | Current State | What's Needed |
|---|---|---|
| **Input source** | Single CLI command | API endpoint or CSV batch processor |
| **State vet board coverage** | NJ + NC automated, rest manual | Playwright/Selenium browser automation per state (~2–3 days per state) |
| **Rate limiting** | None — single-threaded | Respect API rate limits; add retry/backoff |
| **Error handling** | Print + continue | Structured logging, alerting on failures |
| **Storage** | Local JSON/TXT files | Database (Postgres/MongoDB) or S3 |
| **Monitoring** | None | Track per-source success rates |
| **FSMB credentials** | Single shared credentials | Production-tier account |
| **Serper usage** | 50k/month plan | Scale plan based on volume |

### Immediate Next Steps (Low Effort)

1. **Add GA vet board scraper** — GA uses the same MyLicense platform as NJ. Estimated 4 hours.
2. **Add more MyLicense states** — IN, MD, VA use the same ASP.NET platform. Each takes ~2 hours.
3. **Batch CSV input** — accept a CSV of clinic addresses and run all in sequence. ~1 day.
4. **Add pharmacy license lookups** — NABP API or state board scrapers. ~3 days.

---

## 11. Project Structure

```
clinic-scraper-poc/
│
├── clinic_scraper.py       # Entry point — CLI, orchestration, mode auto-detection
├── requirements.txt        # 4 Python dependencies
├── .env                    # API keys (never committed to git)
│
├── steps/
│   ├── search.py           # Step 1 — Google/DuckDuckGo search, knowledge graph
│   ├── scraper.py          # Step 2 — fetch homepage + staff pages
│   ├── extractor.py        # Step 3 — Claude AI NER + provider deduplication
│   ├── npi.py              # NPI Registry (CMS) — individual + org lookup
│   ├── dea.py              # DEA checksum + web lookup
│   ├── license.py          # FSMB + NPI + DEA + Medicare + FDA orchestration
│   ├── vet_license.py      # State vet board scrapers (NJ, NC; 20 states with manual URLs)
│   └── output.py           # JSON + plain-text report generation
│
└── output/                 # Generated results (gitignored)
```

---

## 12. Dependencies

```
anthropic          # Claude AI API — pip install anthropic
beautifulsoup4     # HTML parsing — pip install beautifulsoup4
requests           # HTTP — pip install requests
python-dotenv      # .env file loader — pip install python-dotenv
```

Python's built-in `html.parser` is used — no additional parser needed.

---

## 13. GitHub Repository

https://github.com/parekatakash/clinic-scraper-poc

---

## 14. Key External Links (All Referenced APIs & Boards)

| Resource | Link |
|---|---|
| Anthropic Console (get API key) | https://console.anthropic.com |
| Anthropic Model Docs | https://docs.anthropic.com/en/docs/about-claude/models |
| Serper.dev (Google Search API) | https://serper.dev |
| CMS NPI Registry API | https://npiregistry.cms.hhs.gov/api/ |
| CMS NPI Registry — manual lookup | https://npiregistry.cms.hhs.gov |
| FSMB Developer Portal | https://developer.fsmb.org |
| FSMB DocInfo (manual lookup) | https://www.docinfo.org |
| DEA Diversion — registrant search | https://www.deadiversion.usdoj.gov/webforms/deaRegistrationSearch.jsp |
| CMS PECOS Open Data | https://data.cms.gov/provider-data |
| FDA CDRH Establishment Registry | https://www.accessdata.fda.gov/scripts/cdrh/cfdocs/cfRL/rl.cfm |
| AAVSB VetVerify | https://www.aavsb.org/public-tools/vet-verify/ |
| NJ Vet Board (MyLicense) | https://newjersey.mylicense.com/verification/Search.aspx |
| NC Vet Board | https://portal.ncvmb.org/verification/search.aspx |
| GA Vet Board (MyLicense) | https://gcmb.mylicense.com/verification/Search.aspx |
| MA License Check | https://checkalicense.dpl.state.ma.us/ |
| CA DCA License Search | https://search.dca.ca.gov/ |
| TX Vet Board | https://apps.veterinary.texas.gov/s/licenseelookup |
| FL Vet (FDACS) | https://ens.fdacs.gov/MnMApplication/Faces/CheckALicense |
| NY License Lookup | https://eservices.op.nysed.gov/professions/public-license-search |
| NABP Pharmacy e-Profile | https://nabp.pharmacy/programs/pharmacies/nabp-e-profile-id/ |
