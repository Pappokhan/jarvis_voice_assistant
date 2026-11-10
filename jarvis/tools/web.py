"""Open things in the user's browser. (Looking things up to answer questions uses Claude's web search.)"""
from __future__ import annotations

import webbrowser
from urllib.parse import quote_plus

from . import S, tool

_SITES = {
    "google": "https://www.google.com/search?q={}",
    "youtube": "https://www.youtube.com/results?search_query={}",
    "maps": "https://www.google.com/maps/search/{}",
    "wikipedia": "https://en.wikipedia.org/w/index.php?search={}",
    "amazon": "https://www.amazon.com/s?k={}",
    "github": "https://github.com/search?q={}",
}


@tool("Open a website in the user's default browser.", {"url": S("Address, e.g. 'youtube.com' or a full https:// URL")})
def open_url(url: str):
    url = url.strip()
    if not url.lower().startswith(("http://", "https://")):
        url = "https://" + url
    webbrowser.open(url)
    return f"Opened {url}"


@tool(
    "Show search results to the user in their browser (when they want to SEE results, a video, a map or "
    "a product page, rather than hear an answer).",
    {"query": S("What to search for"), "site": S("Where to search", enum=list(_SITES))},
    required=["query"],
)
def search_in_browser(query: str, site: str = "google"):
    webbrowser.open(_SITES[site].format(quote_plus(query)))
    return f"Showing {site} results for '{query}' in the browser."
