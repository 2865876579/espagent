"""
Web search helper used by the cloud server.

It intentionally uses only Python standard library modules so the local
prototype does not need another API key or package install.
"""
from __future__ import annotations

import asyncio
import html
import json
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36"
)

WEATHER_TRANSLATIONS = {
    "Sunny": "晴",
    "Clear": "晴",
    "Partly cloudy": "局部多云",
    "Cloudy": "多云",
    "Overcast": "阴",
    "Mist": "薄雾",
    "Fog": "雾",
    "Haze": "霾",
    "Smoky haze": "烟霾",
    "Light rain": "小雨",
    "Moderate rain": "中雨",
    "Heavy rain": "大雨",
    "Patchy rain nearby": "附近有零星降雨",
    "Thundery outbreaks in nearby": "附近有雷雨",
}


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str


def _clean_text(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def _read_url(url: str, timeout: int = 12) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def _extract_weather_location(query: str) -> str | None:
    if "天气" not in query and "气温" not in query:
        return None

    location = query
    for word in ("天气预报", "天气", "气温", "今天", "今日", "现在", "最新", "查询", "查一下", "怎么样", "如何", "的"):
        location = location.replace(word, " ")
    location = re.sub(r"[，。！？、,.!?]", " ", location)
    location = re.sub(r"\s+", " ", location).strip()
    if not location:
        return None
    return location[:30]


def _weather_search(query: str) -> list[SearchResult]:
    location = _extract_weather_location(query)
    if not location:
        return []

    url_location = urllib.parse.quote(location)
    url = f"https://wttr.in/{url_location}?format=j1&lang=zh"
    data = json.loads(_read_url(url))
    current = data.get("current_condition", [{}])[0]
    weather = current.get("lang_zh") or current.get("weatherDesc") or [{}]
    weather_text = weather[0].get("value", "") if weather else ""
    weather_text = WEATHER_TRANSLATIONS.get(weather_text, weather_text)

    parts = [
        f"当前{current.get('temp_C', '?')}℃",
        f"体感{current.get('FeelsLikeC', '?')}℃",
        f"湿度{current.get('humidity', '?')}%",
        f"能见度{current.get('visibility', '?')}公里",
    ]
    if weather_text:
        parts.insert(0, weather_text)

    forecast = data.get("weather", [{}])[0]
    if forecast:
        parts.append(
            f"今日{forecast.get('mintempC', '?')}到{forecast.get('maxtempC', '?')}℃"
        )

    return [SearchResult(
        title=f"{location}当前天气",
        url=f"https://wttr.in/{url_location}",
        snippet="，".join(parts),
    )]


def _search_bing_rss(query: str, max_results: int) -> list[SearchResult]:
    params = urllib.parse.urlencode({"q": query, "format": "rss", "setlang": "zh-CN"})
    xml_text = _read_url(f"https://www.bing.com/search?{params}")
    root = ET.fromstring(xml_text)

    results: list[SearchResult] = []
    for item in root.findall(".//item"):
        title = _clean_text(item.findtext("title") or "")
        link = _clean_text(item.findtext("link") or "")
        snippet = _clean_text(item.findtext("description") or "")
        if title and link:
            results.append(SearchResult(title=title, url=link, snippet=snippet))
        if len(results) >= max_results:
            break
    return results


def _decode_duckduckgo_url(url: str) -> str:
    parsed = urllib.parse.urlparse(html.unescape(url))
    query = urllib.parse.parse_qs(parsed.query)
    uddg = query.get("uddg")
    if uddg:
        return urllib.parse.unquote(uddg[0])
    if url.startswith("//"):
        return "https:" + url
    return url


def _search_duckduckgo_lite(query: str, max_results: int) -> list[SearchResult]:
    params = urllib.parse.urlencode({"q": query})
    html_text = _read_url(f"https://lite.duckduckgo.com/lite/?{params}")

    link_matches = re.findall(
        r'<a[^>]+class="result-link"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
        html_text,
        flags=re.I | re.S,
    )
    snippets = re.findall(
        r'<td[^>]+class="result-snippet"[^>]*>(.*?)</td>',
        html_text,
        flags=re.I | re.S,
    )

    results: list[SearchResult] = []
    for index, (url, title) in enumerate(link_matches[:max_results]):
        snippet = snippets[index] if index < len(snippets) else ""
        results.append(SearchResult(
            title=_clean_text(title),
            url=_decode_duckduckgo_url(url),
            snippet=_clean_text(snippet),
        ))
    return [item for item in results if item.title and item.url]


def _search_sync(query: str, max_results: int) -> list[SearchResult]:
    errors: list[str] = []
    try:
        priority_results = _weather_search(query)
    except Exception as exc:
        priority_results = []
        errors.append(f"_weather_search: {exc}")

    for searcher in (_search_bing_rss, _search_duckduckgo_lite):
        try:
            results = searcher(query, max_results)
            if results:
                merged = priority_results + results
                return merged[:max_results]
        except Exception as exc:
            errors.append(f"{searcher.__name__}: {exc}")
    if priority_results:
        return priority_results[:max_results]
    print("[WebSearch] failed: " + " | ".join(errors))
    return []


async def search_web(query: str, max_results: int = 5) -> list[SearchResult]:
    """Run a web search without blocking the asyncio event loop."""
    return await asyncio.to_thread(_search_sync, query, max_results)


def format_search_results(query: str, results: list[SearchResult]) -> str:
    lines = [f"搜索词：{query}"]
    for index, item in enumerate(results, start=1):
        lines.append(f"{index}. {item.title}")
        if item.snippet:
            lines.append(f"摘要：{item.snippet}")
        lines.append(f"链接：{item.url}")
    return "\n".join(lines)
