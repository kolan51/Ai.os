"""Multi-agent pipeline using the message bus.

Two agents collaborate via pub/sub:
  - ScraperAgent: fetches URLs, publishes raw content to the "raw_pages" topic
  - SummarizerAgent: reads from "raw_pages", summarises each page, saves to memory

Run both in parallel:
  aios run examples/pipeline.py -d    # starts ScraperAgent
  # In a second terminal:
  # aios run examples/pipeline.py:SummarizerAgent -d
"""

import asyncio
from aios import Agent, tool
from aios.tools.builtin.web import WebSearchMixin


class ScraperAgent(Agent, WebSearchMixin):
    name = "scraper"
    model = "claude-sonnet-4-6"
    system_prompt = "You are a web research assistant. Find and publish relevant pages."

    topics_to_research = [
        "persistent AI agent architectures",
        "LLM memory systems",
        "autonomous agent frameworks",
    ]

    async def run(self) -> None:
        for query in self.topics_to_research:
            seen_key = f"scraped:{query}"
            if await self.memory.load(seen_key):
                self.logger.info("Already scraped: %s", query)
                continue

            self.logger.info("Searching: %s", query)
            results = await self.web_search(query)

            # Publish raw results for the Summarizer to consume
            await self.publish("raw_pages", {
                "query": query,
                "results": results,
            })

            await self.memory.save(seen_key, "done")
            self.logger.info("Published results for: %s", query)

        self.logger.info("Scraper done. Published %d topics.", len(self.topics_to_research))


class SummarizerAgent(Agent):
    name = "summarizer"
    model = "claude-sonnet-4-6"
    system_prompt = "You are a concise summariser. Extract key insights from web search results."

    async def run(self) -> None:
        # Load cursor from memory to pick up where we left off
        cursor = int(await self.memory.load("bus_cursor") or 0)
        self.logger.info("Polling raw_pages (since id=%d)…", cursor)

        msgs, new_cursor = await self.subscribe("raw_pages", since=cursor)
        if not msgs:
            self.logger.info("No new pages to summarise.")
            return

        for msg in msgs:
            query = msg["payload"].get("query", "")
            results = msg["payload"].get("results", "")

            summary = await self.think(
                f"Summarise these search results for the query '{query}' in 3 bullet points:\n\n{results}"
            )

            key = f"summary:{query}"
            await self.memory.save(key, summary)
            self.logger.info("Saved summary for: %s", query)

        # Persist cursor so we skip these messages next run
        await self.memory.save("bus_cursor", str(new_cursor))
        self.logger.info("Summarised %d page(s). Cursor now %d.", len(msgs), new_cursor)


if __name__ == "__main__":
    # Default: run the scraper. Pass --name summarizer to run the other.
    import sys
    if "--summarizer" in sys.argv:
        SummarizerAgent.launch()
    else:
        ScraperAgent.launch()
