from __future__ import annotations

from ..registry import tool


class WebSearchMixin:
    """
    Adds web_search and fetch_url tools to an agent.

    Uses DuckDuckGo Instant Answers (no API key required) for search.
    Uses httpx for URL fetching with a 15s timeout and HTML stripping.
    """

    @tool
    async def web_search(self, query: str, max_results: int = 5) -> str:
        """
        Search the web using DuckDuckGo and return a text summary.
        query: The search query.
        max_results: Maximum number of results to include.
        """
        import httpx

        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(
                "https://api.duckduckgo.com/",
                params={"q": query, "format": "json", "no_html": "1", "skip_disambig": "1"},
                headers={"User-Agent": "aios-agent/0.1"},
            )
            data = resp.json()

        parts: list[str] = []

        abstract = data.get("AbstractText", "").strip()
        if abstract:
            source = data.get("AbstractSource", "")
            parts.append(f"[{source}] {abstract}" if source else abstract)

        for topic in data.get("RelatedTopics", [])[:max_results]:
            if isinstance(topic, dict) and "Text" in topic:
                parts.append(topic["Text"])
            elif isinstance(topic, dict) and "Topics" in topic:
                for sub in topic["Topics"][:2]:
                    if "Text" in sub:
                        parts.append(sub["Text"])

        if not parts:
            return f"No results found for: {query}"
        return "\n\n".join(parts)

    @tool
    async def fetch_url(self, url: str, extract_text: bool = True) -> str:
        """
        Fetch the content of a URL and return it as text.
        url: The URL to fetch.
        extract_text: Strip HTML tags and return plain text only.
        """
        import httpx

        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(
                url,
                headers={"User-Agent": "aios-agent/0.1 (https://github.com/aios-runtime/aios)"},
            )
            resp.raise_for_status()
            content = resp.text

        if extract_text:
            content = _strip_html(content)

        # Trim to avoid blowing up context windows
        if len(content) > 8000:
            content = content[:8000] + "\n\n[... truncated at 8000 chars ...]"

        return content


def _strip_html(html: str) -> str:
    """Minimal HTML stripper — removes tags, collapses whitespace."""
    import re
    text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
