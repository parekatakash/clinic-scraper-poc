"""
State veterinary board license lookup.

Scrapers are implemented only for boards with accessible HTML endpoints.
Most states use JavaScript-heavy portals (Thentia, Salesforce, eLicense)
that cannot be queried without a browser — those fall back to manual URLs.

Implemented:
  NJ  — newjersey.mylicense.com (ASP.NET MyLicense, HTML table)
  NC  — portal.ncvmb.org        (ASP.NET dl-horizontal, HTML)

Framework ready for: GA, IN, MD, VA, PA (all MyLicense variants — add
profession codes and test when boards are accessible).
"""

import re
import requests
from bs4 import BeautifulSoup

_TIMEOUT = 14

# ── NJ license number pattern: 29VI01234500 ──────────────────────────────────
_NJ_LICENSE_RE = re.compile(r"^\d{2}[A-Z]{2}\d{6,8}$")


# ─────────────────────────────────────────────────────────────────────────────
# Public dispatcher
# ─────────────────────────────────────────────────────────────────────────────

_STATE_FUNCS = {
    "NJ": "_lookup_nj",
    "NC": "_lookup_nc",
}

_STATE_BOARD_URLS = {
    "NJ": "https://newjersey.mylicense.com/verification/Search.aspx",
    "NC": "https://portal.ncvmb.org/verification/search.aspx",
    "MA": "https://checkalicense.dpl.state.ma.us/",
    "CA": "https://search.dca.ca.gov/",
    "TX": "https://apps.veterinary.texas.gov/s/licenseelookup",
    "FL": "https://ens.fdacs.gov/MnMApplication/Faces/CheckALicense",
    "NY": "https://eservices.op.nysed.gov/professions/public-license-search",
    "IL": "https://online-dfpr.micropact.com/lookup/licenselookup.aspx",
    "CO": "https://apps.colorado.gov/dora/licensing/Lookup/LicenseLookup.aspx",
    "WA": "https://fortress.wa.gov/doh/providercredentialsearch/",
    "GA": "https://gcmb.mylicense.com/verification/Search.aspx",
    "OH": "https://elicense.ohio.gov/oh_verifylicense/",
    "PA": "https://www.pals.pa.gov/",
    "AZ": "https://vetboard.az.gov/licensee-directory-1",
    "VA": "https://dhp.virginiainteractive.org/lookup/index",
    "MI": "https://www.michigan.gov/lara/i-need-to/find-or-verify-a-licensed-professional-or-business",
    "MN": "https://vet.hlb.state.mn.us/",
    "OR": "https://ovmeb.us.thentiacloud.net/webs/ovmeb/register/",
    "CT": "https://www.elicense.ct.gov/Lookup/LicenseLookup.aspx",
}


def lookup_vet_license(first: str, last: str, state: str) -> dict:
    """
    Try to retrieve a vet's state license number from the state vet board.
    Returns a dict with license_number, license_status, board_url.
    Falls back to manual board URL if automated lookup is not available.
    """
    board_url = _STATE_BOARD_URLS.get(state, "https://www.aavsb.org/public-tools/vet-verify/")
    base = {"state_vet_board_url": board_url}

    func_name = _STATE_FUNCS.get(state)
    if not func_name or not first or not last:
        return base

    try:
        func = globals()[func_name]
        result = func(first, last)
        if result:
            return {**base, **result}
    except Exception as e:
        print(f"[vet_license] {state} lookup error for '{first} {last}': {e}")

    return base


# ─────────────────────────────────────────────────────────────────────────────
# NJ — newjersey.mylicense.com (MyLicense ASP.NET)
# ─────────────────────────────────────────────────────────────────────────────

def _lookup_nj(first: str, last: str) -> dict | None:
    s = requests.Session()
    r = s.get(
        "https://newjersey.mylicense.com/verification/Search.aspx",
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=_TIMEOUT,
    )
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    vs = soup.find("input", {"id": "__VIEWSTATE"})
    evt = soup.find("input", {"id": "__EVENTVALIDATION"})
    vsg = soup.find("input", {"id": "__VIEWSTATEGENERATOR"})
    if not vs:
        return None

    post = {
        "__VIEWSTATE": vs["value"],
        "__EVENTVALIDATION": evt["value"] if evt else "",
        "__VIEWSTATEGENERATOR": vsg["value"] if vsg else "",
        "t_web_lookup__first_name": first,
        "t_web_lookup__last_name": last,
        "t_web_lookup__profession_name": "Veterinary Medical Examiners",
        "t_web_lookup__license_type_name": "",
        "t_web_lookup__license_no": "",
        "t_web_lookup__addr_city": "",
        "sch_button": "Search",
    }
    r2 = s.post(
        "https://newjersey.mylicense.com/verification/Search.aspx",
        data=post,
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=_TIMEOUT,
    )
    r2.raise_for_status()
    return _parse_nj(r2.text, first, last)


def _parse_nj(html: str, first: str, last: str) -> dict | None:
    soup = BeautifulSoup(html, "html.parser")
    grid = soup.find("table", {"id": "datagrid_results"})
    if not grid:
        return None

    first_u = first.upper()
    last_u = last.upper()

    for row in grid.find_all("tr"):
        cells = [c.get_text(strip=True) for c in row.find_all("td")]
        if len(cells) < 5:
            continue
        # Name is in cells[0]; license number is the first cell matching NJ pattern
        name_cell = cells[0].upper()
        if first_u not in name_cell and last_u not in name_cell:
            continue
        # Find the license number among all cells
        lic_num = next((c for c in cells if _NJ_LICENSE_RE.match(c)), None)
        # Status is typically the cell after profession/type columns
        status = _extract_nj_status(cells)
        if lic_num or status:
            return {
                "license_number": lic_num,
                "license_status": status or "Unknown",
                "vet_license_found": True,
            }

    return None


def _extract_nj_status(cells: list[str]) -> str | None:
    status_words = {"active", "inactive", "suspended", "revoked", "expired", "renewed"}
    for c in cells:
        if c.lower() in status_words:
            return c.title()
    return None


# ─────────────────────────────────────────────────────────────────────────────
# NC — portal.ncvmb.org (ASP.NET dl-horizontal pairs)
# ─────────────────────────────────────────────────────────────────────────────

def _lookup_nc(first: str, last: str) -> dict | None:
    s = requests.Session()
    r = s.get(
        "https://portal.ncvmb.org/verification/search.aspx",
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=_TIMEOUT,
    )
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    vs = soup.find("input", {"id": "__VIEWSTATE"})
    evt = soup.find("input", {"id": "__EVENTVALIDATION"})
    if not vs:
        return None

    post = {
        "__VIEWSTATE": vs["value"],
        "__EVENTVALIDATION": evt["value"] if evt else "",
        "ctl00$Content$txtFirstName": first,
        "ctl00$Content$txtLastName": last,
        "ctl00$Content$btnEnter": "Search",
    }
    r2 = s.post(
        "https://portal.ncvmb.org/verification/search.aspx",
        data=post,
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=_TIMEOUT,
    )
    r2.raise_for_status()
    return _parse_nc(r2.text, first, last)


def _parse_nc(html: str, first: str, last: str) -> dict | None:
    soup = BeautifulSoup(html, "html.parser")
    dls = soup.find_all("dl", {"class": "dl-horizontal"})

    first_u = first.upper()
    last_u = last.upper()

    # DL elements come in pairs: [name+status, license_number+type]
    for i in range(0, len(dls) - 1, 2):
        info_dl = dls[i]
        detail_dl = dls[i + 1]

        info = _dl_to_dict(info_dl)
        detail = _dl_to_dict(detail_dl)

        name = (info.get("Name") or "").upper()
        if first_u not in name and last_u not in name:
            continue

        lic_num = detail.get("License Number")
        lic_type = detail.get("License Type")
        status = info.get("License Status")
        issued = info.get("Issued Date")
        expiry = detail.get("Expiration Date") or detail.get("Revoke Date")

        return {
            "license_number": lic_num,
            "license_type": lic_type,
            "license_status": status,
            "license_issued": issued,
            "license_expiry": expiry,
            "vet_license_found": True,
        }

    return None


def _dl_to_dict(dl) -> dict:
    result = {}
    dts = dl.find_all("dt")
    dds = dl.find_all("dd")
    for dt, dd in zip(dts, dds):
        key = dt.get_text(strip=True).rstrip(":")
        val = dd.get_text(strip=True)
        result[key] = val
    return result
