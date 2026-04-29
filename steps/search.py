import os
import requests

# Sites that aggregate or review providers but are not official clinic websites.
# Results from these domains are skipped so the scraper hits the real clinic site.
_BLOCKED_DOMAINS = {
    "yelp.com", "healthgrades.com", "zocdoc.com", "facebook.com",
    "zoominfo.com", "linkedin.com", "vitals.com", "webmd.com",
    "yellowpages.com", "mapquest.com", "bbb.org", "dnb.com",
    "petinsurancereview.com", "petmd.com", "vetstreet.com", "vetary.com",
    "topvet.net", "vetsource.com", "pawlicy.com", "wagwalking.com", "petsapp.com",
    "findatopdoc.com", "ratemds.com", "usnews.com", "castleconnolly.com",
    "wellness.com", "angieslist.com", "thumbtack.com", "bing.com",
    "google.com", "wikipedia.org", "tripadvisor.com",
    "wanderboat.ai", "restaurantji.com", "foursquare.com", "grubhub.com",
    "doordash.com", "opentable.com", "menuism.com", "allmenus.com",
    "loc8nearme.com", "cylex.us", "manta.com", "n49.com", "chamberofcommerce.com",
    "brownbook.net", "hotfrog.com", "showmelocal.com", "storeboard.com",
    "iplanet.com", "local.com", "citysearch.com", "merchantcircle.com",
}


def search_clinic_website(
    clinic_name: str | None,
    address: str,
    state: str,
    postal_code: str,
) -> tuple[str | None, str | None, dict]:
    """
    Search for the clinic's official website.
    Returns (url, discovered_name, kg_info) where:
      - discovered_name is the business name from the search result title
      - kg_info is a dict with phone/address/title from the Google Knowledge Graph
    Tries Serper first, falls back to DuckDuckGo.
    """
    if clinic_name:
        query = f"{clinic_name} {address} {state} {postal_code} official website"
    else:
        # Quoted address + healthcare terms so we find medical/vet/dental sites, not restaurants
        query = f'"{address}" {state} {postal_code} clinic OR hospital OR doctor OR veterinary OR dental OR medical'
    return _serper_search(query) or _duckduckgo_search(query)


def _is_blocked(url: str) -> bool:
    return any(domain in url for domain in _BLOCKED_DOMAINS)


def _serper_search(query: str) -> tuple[str | None, str | None, dict] | None:
    api_key = os.getenv("SERPER_API_KEY")
    if not api_key:
        return None
    try:
        resp = requests.post(
            "https://google.serper.dev/search",
            headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
            json={"q": query, "num": 10},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        results = data.get("organic", [])

        # Pull knowledge graph info (phone, address, title) — useful when scraping fails
        kg = data.get("knowledgeGraph", {})
        kg_info: dict = {}
        if kg.get("title"):
            kg_info["name"] = kg["title"]
        if kg.get("address"):
            kg_info["address"] = kg["address"]
        if kg.get("phone"):
            kg_info["phone"] = kg["phone"]
        if kg.get("website"):
            kg_info["website"] = kg["website"]

        # Collect the best available business name from any result (even blocked ones)
        fallback_name: str | None = kg_info.get("name")
        for result in results:
            title = result.get("title", "")
            n = _clean_title(title) if title else None
            if n and not fallback_name:
                fallback_name = n

        for result in results:
            link = result.get("link", "")
            if link and not _is_blocked(link):
                title = result.get("title", "")
                name = _clean_title(title) if title else fallback_name
                return link, name, kg_info

        # No non-blocked URL — return name only
        name = fallback_name
        if name or kg_info:
            return None, name, kg_info
    except Exception as e:
        print(f"[search] Serper error: {e}")
    return None


def _duckduckgo_search(query: str) -> tuple[str | None, str | None, dict] | None:
    try:
        resp = requests.get(
            "https://api.duckduckgo.com/",
            params={"q": query, "format": "json", "no_redirect": 1, "no_html": 1},
            timeout=10,
            headers={"User-Agent": "clinic-scraper/1.0"},
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("Redirect") and not _is_blocked(data["Redirect"]):
            return data["Redirect"], data.get("Heading") or None, {}
        for topic in data.get("RelatedTopics", []):
            url = topic.get("FirstURL", "")
            if url and "duckduckgo.com" not in url and not _is_blocked(url):
                return url, None, {}
    except Exception as e:
        print(f"[search] DuckDuckGo error: {e}")
    return None


def _clean_title(title: str) -> str | None:
    """Strip boilerplate suffixes from a page title to get the business name."""
    import re
    # Remove everything after common separators used for site taglines
    cleaned = re.split(r"\s*[|–—-]\s*", title)[0].strip()
    # Remove trailing generic words
    noise = {"official site", "official website", "home", "welcome", "homepage"}
    if cleaned.lower() in noise or len(cleaned) < 3:
        return None
    return cleaned
