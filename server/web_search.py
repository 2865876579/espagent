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

GOLD_KEYWORDS = ("金价", "黄金", "金店", "足金", "支付宝金", "积存金", "现货金", "Au99")


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


def _read_sina_quote(symbol: str) -> str:
    url = f"https://hq.sinajs.cn/list={symbol}"
    request = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Referer": "https://finance.sina.com.cn/",
    })
    with urllib.request.urlopen(request, timeout=10) as response:
        return response.read().decode("gbk", errors="replace")


def _parse_sina_assignment(text: str) -> list[str]:
    match = re.search(r'="(.*)";', text)
    if not match:
        return []
    value = match.group(1).strip()
    if not value:
        return []
    return [part.strip() for part in value.split(",")]


def _fmt_number(value: str, suffix: str = "") -> str:
    try:
        return f"{float(value):.2f}{suffix}"
    except Exception:
        return value + suffix if value else "未知"


def _gold_search(query: str) -> list[SearchResult]:
    if not any(keyword.lower() in query.lower() for keyword in GOLD_KEYWORDS):
        return []

    results: list[SearchResult] = []

    try:
        parts = _parse_sina_assignment(_read_sina_quote("SGE_AU9999"))
        if len(parts) >= 18:
            results.append(SearchResult(
                title="上海黄金交易所 Au99.99",
                url="https://finance.sina.com.cn/futures/quotes/SGE_AU9999.shtml",
                snippet=(
                    f"最新{_fmt_number(parts[3], '元/克')}，"
                    f"涨跌幅{parts[17]}，时间{parts[16]}。"
                ),
            ))
    except Exception as exc:
        print(f"[GoldSearch] SGE_AU9999 failed: {exc}")

    try:
        parts = _parse_sina_assignment(_read_sina_quote("SGE_AUTD"))
        if len(parts) >= 18:
            results.append(SearchResult(
                title="上海黄金交易所 Au(T+D)",
                url="https://finance.sina.com.cn/futures/quotes/SGE_AUTD.shtml",
                snippet=(
                    f"最新{_fmt_number(parts[3], '元/克')}，"
                    f"涨跌幅{parts[17]}，时间{parts[16]}。"
                ),
            ))
    except Exception as exc:
        print(f"[GoldSearch] SGE_AUTD failed: {exc}")

    try:
        parts = _parse_sina_assignment(_read_sina_quote("hf_XAU"))
        if len(parts) >= 15:
            results.append(SearchResult(
                title=parts[14] or "伦敦金（现货黄金）",
                url="https://finance.sina.com.cn/futures/quotes/hf_XAU.shtml",
                snippet=(
                    f"最新{_fmt_number(parts[0], '美元/盎司')}，"
                    f"日内高点{_fmt_number(parts[4])}，低点{_fmt_number(parts[5])}，"
                    f"时间{parts[13]} {parts[6]}。"
                ),
            ))
    except Exception as exc:
        print(f"[GoldSearch] hf_XAU failed: {exc}")

    if "支付宝" in query and results:
        results.insert(0, SearchResult(
            title="支付宝金价说明",
            url="https://www.alipay.com/",
            snippet=(
                "支付宝内的黄金价格通常和具体黄金基金、积存金产品、买卖差价或服务费有关，"
                "未必等同于上海金实时盘面价。下面行情可作为基准参考，最终以支付宝 App 内页面为准。"
            ),
        ))

    return results


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
        priority_results = _gold_search(query)
    except Exception as exc:
        priority_results = []
        errors.append(f"_gold_search: {exc}")

    try:
        priority_results += _weather_search(query)
    except Exception as exc:
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


def direct_answer_from_results(query: str, results: list[SearchResult]) -> str | None:
    """Return a deterministic spoken answer for structured data sources."""
    if not results:
        return None

    if any(keyword.lower() in query.lower() for keyword in GOLD_KEYWORDS):
        au9999 = next((item for item in results if "Au99.99" in item.title), None)
        autd = next((item for item in results if "Au(T+D)" in item.title), None)
        alipay = "支付宝" in query

        parts: list[str] = []
        if au9999:
            parts.append(f"上海金 Au99.99：{au9999.snippet.rstrip('。')}")
        if autd:
            parts.append(f"黄金 T+D：{autd.snippet.rstrip('。')}")
        if not parts:
            return None

        answer = "；".join(parts)
        if alipay:
            answer += "。支付宝里的金价还会受具体产品和买卖差价影响，最终以支付宝 App 页面为准。"
        else:
            answer += "。这是交易所行情，不等同于金店零售价。"
        return answer

    weather = next((item for item in results if item.title.endswith("当前天气")), None)
    if weather:
        city = weather.title.removesuffix("当前天气")
        return f"{city}现在{weather.snippet}。"

    return None
