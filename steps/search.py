import os
import requests


def search_clinic_website(clinic_name: str | None, address: str, state: str, postal_code: str) -> str | None:
    """Search for the clinic's official website. Tries Serper first, falls back to DuckDuckGo."""
    name_part = f"{clinic_name} " if clinic_name else ""
    query = f"{name_part}{address} {state} {postal_code} clinic official website"
    url = _serper_search(query) or _duckduckgo_search(query)
    return url


def _serper_search(query: str) -> str | None:
    api_key = os.getenv("SERPER_API_KEY")
    if not api_key:
        return None
    try:
        resp = requests.post(
            "https://google.serper.dev/search",
            headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
            json={"q": query, "num": 5},
            timeout=10,
        )
        resp.raise_for_status()
        results = resp.json().get("organic", [])
        for result in results:
            link = result.get("link", "")
            if link and not any(skip in link for skip in [
                "yelp.com", "healthgrades.com", "zocdoc.com", "facebook.com",
                "zoominfo.com", "linkedin.com", "vitals.com", "webmd.com",
                "yellowpages.com", "mapquest.com", "bbb.org", "dnb.com",
            ]):
                return link
    except Exception as e:
        print(f"[search] Serper error: {e}")
    return None


def _duckduckgo_search(query: str) -> str | None:
    try:
        resp = requests.get(
            "https://api.duckduckgo.com/",
            params={"q": query, "format": "json", "no_redirect": 1, "no_html": 1},
            timeout=10,
            headers={"User-Agent": "clinic-scraper/1.0"},
        )
        resp.raise_for_status()
        data = resp.json()
        # Instant answer redirect
        if data.get("Redirect"):
            return data["Redirect"]
        # Related topics
        for topic in data.get("RelatedTopics", []):
            url = topic.get("FirstURL", "")
            if url and "duckduckgo.com" not in url:
                return url
    except Exception as e:
        print(f"[search] DuckDuckGo error: {e}")
    return None
