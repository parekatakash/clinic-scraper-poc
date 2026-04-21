from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

# Keywords that suggest a staff/provider listing page
_STAFF_KEYWORDS = [
    "staff", "provider", "physician", "doctor", "team", "meet-our",
    "our-team", "directory", "faculty", "clinician", "practitioner",
    "specialist", "about-us", "about/team",
]

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}


def scrape_clinic(base_url: str) -> dict:
    """
    Fetches the homepage and any staff/provider sub-pages.
    Returns a dict with the combined text and the pages visited.
    """
    session = requests.Session()
    session.headers.update(_HEADERS)

    pages: dict[str, str] = {}

    homepage_text = _fetch_text(session, base_url)
    if homepage_text:
        pages[base_url] = homepage_text

    staff_urls = _find_staff_links(session, base_url)
    for url in staff_urls[:5]:  # cap at 5 sub-pages
        text = _fetch_text(session, url)
        if text:
            pages[url] = text

    combined = "\n\n---\n\n".join(
        f"[PAGE: {url}]\n{text}" for url, text in pages.items()
    )
    return {"pages": list(pages.keys()), "text": combined}


def _fetch_text(session: requests.Session, url: str) -> str | None:
    try:
        resp = session.get(url, timeout=15, allow_redirects=True)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        for tag in soup(["script", "style", "noscript", "head"]):
            tag.decompose()
        return soup.get_text(separator=" ", strip=True)
    except Exception as e:
        print(f"[scraper] Failed to fetch {url}: {e}")
        return None


def _find_staff_links(session: requests.Session, base_url: str) -> list[str]:
    """Crawl the homepage for links whose href/text match staff-page keywords."""
    base_domain = urlparse(base_url).netloc
    found: list[str] = []
    try:
        resp = session.get(base_url, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        for a in soup.find_all("a", href=True):
            href: str = a["href"].lower()
            text: str = a.get_text(strip=True).lower()
            if any(kw in href or kw in text for kw in _STAFF_KEYWORDS):
                full = urljoin(base_url, a["href"])
                parsed = urlparse(full)
                if parsed.netloc == base_domain and full not in found:
                    found.append(full)
    except Exception as e:
        print(f"[scraper] Link discovery error: {e}")
    return found
