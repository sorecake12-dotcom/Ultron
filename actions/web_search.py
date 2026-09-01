#web_search.py
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR        = _get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"

def _safe_print(text: str):
    try:
        print(text)
    except Exception:
        try:
            print(text.encode("ascii", "replace").decode("ascii"))
        except Exception:
            pass

TIME_SENSITIVE_KEYWORDS = [
    "today", "right now", "this morning", "this evening", "latest", "this week",
    "current", "breaking news", "trending", "news", "happening", "what happened",
    "weather", "score", "scores", "stock price", "market today", "recently",
    "movie news", "latest movie", "movie", "film", "war", "conflict", "politics",
    "election", "technology", "tech", "science", "gaming", "game news",
    "entertainment", "company", "companies", "world events", "sports"
]

def is_realtime_query(query: str) -> bool:
    """Returns True if the query requires live real-time information."""
    if not query:
        return False
    q_lower = query.lower().strip()
    
    # Direct match for real-time keywords
    if any(kw in q_lower for kw in TIME_SENSITIVE_KEYWORDS):
        return True

    # Match common real-time question patterns
    patterns = [
        r"what'?s (?:the )?latest",
        r"tell me (?:the )?latest",
        r"give me (?:the )?latest",
        r"what happened",
        r"what'?s happening",
        r"current status of",
        r"who won"
    ]
    return any(re.search(pat, q_lower) for pat in patterns)


def _get_api_key() -> str:
    with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["gemini_api_key"]


def _extract_domain(url: str) -> str:
    try:
        parsed = urlparse(url)
        netloc = parsed.netloc.lower()
        if netloc.startswith("www."):
            netloc = netloc[4:]
        return netloc or url
    except Exception:
        return url


def _gemini_search(query: str) -> tuple[str, list[str]]:
    """
    Performs Gemini grounded web search.
    Returns (summary_text, sources_list).
    """
    from google import genai

    client   = genai.Client(api_key=_get_api_key())
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=query,
        config={"tools": [{"google_search": {}}]},
    )

    text = ""
    sources = []

    if response.candidates:
        cand = response.candidates[0]
        if hasattr(cand, "content") and cand.content:
            for part in cand.content.parts:
                if hasattr(part, "text") and part.text:
                    text += part.text

        # Extract grounding sources if present
        if hasattr(cand, "grounding_metadata") and cand.grounding_metadata:
            gm = cand.grounding_metadata
            if hasattr(gm, "grounding_chunks") and gm.grounding_chunks:
                for chunk in gm.grounding_chunks:
                    if hasattr(chunk, "web") and chunk.web:
                        title = getattr(chunk.web, "title", "")
                        uri   = getattr(chunk.web, "uri", "")
                        if title or uri:
                            sources.append(f"{title} ({_extract_domain(uri)})" if title else uri)

    text = text.strip()
    if not text:
        raise ValueError("Gemini returned empty search response.")
        
    return text, sources


def _ddg_search(query: str, max_results: int = 6) -> tuple[list[dict], list[str]]:
    try:
        from ddgs import DDGS
    except ImportError:
        from duckduckgo_search import DDGS

    results = []
    sources = []
    with DDGS() as ddgs:
        for r in ddgs.text(query, max_results=max_results):
            url = r.get("href", "")
            title = r.get("title", "")
            results.append({
                "title":   title,
                "snippet": r.get("body",   ""),
                "url":     url,
            })
            if title or url:
                sources.append(f"{title} ({_extract_domain(url)})" if title else url)

    return results, sources


def _ddg_news(query: str, max_results: int = 8) -> tuple[list[dict], list[str]]:
    """DDG news search — returns actual articles."""
    try:
        from ddgs import DDGS
    except ImportError:
        from duckduckgo_search import DDGS

    results = []
    sources = []
    try:
        with DDGS() as ddgs:
            for r in ddgs.news(query, max_results=max_results):
                title = r.get("title", "")
                url   = r.get("url", "")
                src   = r.get("source", "")
                results.append({
                    "title":   title,
                    "snippet": r.get("body", ""),
                    "url":     url,
                    "source":  src,
                })
                source_label = src or title or _extract_domain(url)
                if source_label and source_label not in sources:
                    sources.append(source_label)
    except Exception as e:
        _safe_print(f"[WebSearch Log] DDG news() failed ({e}) — falling back to text search")
        results, sources = _ddg_search(query, max_results=max_results)
        
    return results, sources


def _format_ddg(query: str, results: list[dict], sources: list[str]) -> str:
    if not results:
        return f"No results found for: {query}"

    lines = [f"Search results for: {query}\n"]
    for i, r in enumerate(results, 1):
        if r.get("title"):   lines.append(f"{i}. {r['title']}")
        if r.get("snippet"): lines.append(f"   {r['snippet']}")
        if r.get("url"):     lines.append(f"   Source: {r['url']}")
        lines.append("")

    if sources:
        lines.append("Sources used: " + ", ".join(sources[:5]))

    return "\n".join(lines).strip()


def _format_news(query: str, results: list[dict], sources: list[str]) -> str:
    if not results:
        return f"No news found for: {query}"

    lines = [f"Latest news: {query}\n"]
    for i, r in enumerate(results, 1):
        title = r.get("title", "")
        if not title:
            continue
        src = f"  [{r['source']}]" if r.get("source") else ""
        lines.append(f"{i}. {title}{src}")
        if r.get("snippet"):
            lines.append(f"   {r['snippet'][:140]}")
        if r.get("url"):
            lines.append(f"   {r['url']}")
        lines.append("")

    if sources:
        lines.append("Sources: " + ", ".join(sources[:6]))

    return "\n".join(lines).strip()


# ── Search Modes ──────────────────────────────────────────────────────────────

def _search(query: str) -> tuple[str, list[str]]:
    """Default search — Gemini grounded, DDG fallback."""
    try:
        text, sources = _gemini_search(query)
        return text, sources
    except Exception as e:
        _safe_print(f"[WebSearch Log] Gemini search failed ({e}) — trying DDG...")
        results, sources = _ddg_search(query)
        return _format_ddg(query, results, sources), sources


def _news(query: str = "") -> tuple[str, list[str]]:
    """
    Runs Gemini grounded search AND DDG news.
    Returns result and sources.
    """
    gemini_query = f"latest news today: {query}" if query else "top world news today"
    try:
        text, sources = _gemini_search(gemini_query)
        return text, sources
    except Exception as e:
        _safe_print(f"[WebSearch Log] Gemini news search failed ({e}) — trying DDG news...")
        ddg_query = query if query else "world news today"
        results, sources = _ddg_news(ddg_query, max_results=8)
        return _format_news(ddg_query, results, sources), sources


def _research(query: str) -> tuple[str, list[str]]:
    research_query = (
        f"Comprehensive, detailed explanation of: {query}. "
        "Include background context, key facts, current state, and important nuances."
    )
    try:
        return _gemini_search(research_query)
    except Exception as e:
        _safe_print(f"[WebSearch Log] Research Gemini failed ({e}) — DDG fallback...")
        results, sources = _ddg_search(query, max_results=10)
        return _format_ddg(query, results, sources), sources


def _price(query: str) -> tuple[str, list[str]]:
    price_query = f"current price of {query} — how much does it cost today"
    try:
        return _gemini_search(price_query)
    except Exception as e:
        _safe_print(f"[WebSearch Log] Price Gemini failed ({e}) — DDG fallback...")
        results, sources = _ddg_search(f"{query} price buy", max_results=6)
        return _format_ddg(query, results, sources), sources


# ── Public entry point ─────────────────────────────────────────────────────────

def web_search(
    parameters:     dict,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    params = parameters or {}
    query  = params.get("query", "").strip()
    mode   = params.get("mode",  "search").lower().strip()
    items  = params.get("items", [])

    if not query and items:
        query = ", ".join(items)

    if not query:
        return "Please provide a search query."

    # Incorporate user memory preferences if search query is broad
    from memory.memory_manager import load_memory
    mem = load_memory()
    prefs = mem.get("preferences", {})
    if prefs and any(w in query.lower() for w in ["news", "today", "trending", "latest"]):
        user_pref_summary = ", ".join([f"{k}: {v.get('value') if isinstance(v, dict) else v}" for k, v in list(prefs.items())[:3]])
        if user_pref_summary:
            _safe_print(f"[WebSearch Log] Factoring user preferences into search: {user_pref_summary}")

    t_start = time.perf_counter()
    _safe_print(f"[WebSearch Log] Search Started | Query: '{query}' | Mode: '{mode}'")
    if player and hasattr(player, "write_log"):
        player.write_log(f"[WebSearch] Checking the latest information for '{query}'...")

    try:
        if mode == "news" or "news" in query.lower():
            text, sources = _news(query)
        elif mode == "research":
            text, sources = _research(query)
        elif mode == "price":
            text, sources = _price(query)
        else:
            text, sources = _search(query)

        elapsed_ms = (time.perf_counter() - t_start) * 1000.0
        sources_str = ", ".join(sources[:6]) if sources else "Web search index"

        _safe_print(f"[WebSearch Log] Search Completed | Results Count: {len(text)} chars | Time: {elapsed_ms:.1f}ms")
        _safe_print(f"[WebSearch Log] Sources Used: {sources_str}")

        if player and hasattr(player, "write_log"):
            player.write_log(f"[WebSearch] Retrieved results from: {sources_str}")

        return text

    except Exception as e:
        elapsed_ms = (time.perf_counter() - t_start) * 1000.0
        _safe_print(f"[WebSearch Log] Search Failure | Query: '{query}' | Error: {e} | Time: {elapsed_ms:.1f}ms")
        if player and hasattr(player, "write_log"):
            player.write_log(f"[WebSearch] Live search failed: {e}")
        return f"I could not retrieve current information from the web at this time. (Error: {e})"
