"""Free web search (DuckDuckGo, no API key). Used by the local brain; Claude has its own search."""
from __future__ import annotations

import re

import requests

from . import S, tool


@tool(
    "Search the web and get the top results (title, snippet, link). Use for current events, facts you "
    "are unsure of, prices, weather, definitions. Answer the user from the snippets.",
    {"query": S("Search query")},
)
def web_search(query: str):
    from ddgs import DDGS

    results = []
    for backend in ("auto", "google", "yahoo", "bing"):  # any one engine often comes back empty; try the next
        try:
            results = DDGS().text(query, max_results=5, backend=backend)
        except Exception:
            results = []
        if results:
            break
    if not results:
        return "No results. Try different words."
    return "\n\n".join(f"{r.get('title', '')}\n{r.get('body', '')}\n{r.get('href', '')}" for r in results)


WEATHER_CODES = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "freezing fog",
    51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 61: "light rain", 63: "rain", 65: "heavy rain",
    66: "freezing rain", 67: "heavy freezing rain", 71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains",
    80: "light showers", 81: "showers", 82: "violent showers", 85: "snow showers", 86: "heavy snow showers",
    95: "thunderstorm", 96: "thunderstorm with hail", 99: "thunderstorm with heavy hail",
}


@tool(
    "Current weather and the forecast for today and the next two days in a city. Always use this for weather questions.",
    {"city": S("City name, e.g. Dhaka")},
)
def get_weather(city: str):
    geo = requests.get("https://geocoding-api.open-meteo.com/v1/search", params={"name": city, "count": 1}, timeout=15).json()
    if not geo.get("results"):
        return f"No place called {city!r} was found."
    place = geo["results"][0]
    w = requests.get("https://api.open-meteo.com/v1/forecast", timeout=15, params={
        "latitude": place["latitude"], "longitude": place["longitude"], "timezone": "auto", "forecast_days": 3,
        "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
    }).json()
    cur, day = w["current"], w["daily"]
    sky = lambda code: WEATHER_CODES.get(code, "unsettled")
    lines = [
        f"{place['name']}, {place.get('country', '')} now: {sky(cur['weather_code'])}, {cur['temperature_2m']:.0f} C "
        f"(feels like {cur['apparent_temperature']:.0f} C), humidity {cur['relative_humidity_2m']}%, wind {cur['wind_speed_10m']:.0f} km/h."
    ]
    for i, label in enumerate(("Today", "Tomorrow", "The day after")):
        lines.append(
            f"{label}: {sky(day['weather_code'][i])}, {day['temperature_2m_min'][i]:.0f} to {day['temperature_2m_max'][i]:.0f} C, "
            f"{day['precipitation_probability_max'][i]}% chance of rain."
        )
    return "\n".join(lines)


@tool(
    "Fetch a web page and return its readable text (use after web_search when a snippet is not enough).",
    {"url": S("Page address from a search result")},
)
def read_web_page(url: str):
    r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    html = re.sub(r"(?is)<(script|style|nav|footer|header|noscript)\b.*?</\1>", " ", r.text)
    text = re.sub(r"\s+", " ", re.sub(r"(?s)<[^>]+>", " ", html)).strip()
    return text[:3500]
