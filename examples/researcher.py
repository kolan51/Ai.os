"""
Research Agent — persistent knowledge builder using built-in tool mixins.

Demonstrates:
- WebSearchMixin for web search and URL fetching
- FilesystemMixin for saving reports to disk
- Crash recovery: kill the process mid-run, restart, it continues from where it stopped
- Cross-run memory: topics already researched are skipped on the next run

Usage:
    python examples/researcher.py

    # Or run in background and watch logs:
    aios run examples/researcher.py --detach
    aios logs researcher -f
"""

from aios import Agent, WebSearchMixin, FilesystemMixin, tool


TOPICS = [
    "persistent AI agent memory architectures",
    "autonomous AI agent frameworks comparison 2024",
    "LLM tool use and function calling best practices",
]


class ResearchAgent(Agent, WebSearchMixin, FilesystemMixin):
    name = "researcher"
    model = "claude-sonnet-4-6"
    version = "1.1.0"
    description = "Builds a persistent knowledge base from web research."
    system_prompt = (
        "You are a precise research assistant. Synthesize information clearly, "
        "focus on facts and practical insights, cite sources when available. "
        "When saving findings, be concise but complete."
    )

    @tool
    async def save_finding(self, topic: str, summary: str, sources: list) -> str:
        """
        Save a research finding to long-term memory.
        topic: The research topic identifier.
        summary: Concise summary of findings.
        sources: List of source URLs or references.
        """
        await self.memory.save(f"finding:{topic}", {"summary": summary, "sources": sources})
        return f"Saved finding for: {topic}"

    async def run(self) -> None:
        print(f"\n[{self.name}] starting research session")
        print(f"[{self.name}] agent id: {self.identity.short_id} · model: {self.model}\n")

        researched = []
        skipped = []

        for topic in TOPICS:
            existing = await self.memory.load(f"finding:{topic}")
            if existing:
                skipped.append(topic)
                print(f"  ✓ already know: {topic[:60]}")
                continue

            print(f"  → researching: {topic}")

            # Agentic loop — LLM decides when to search, what to fetch, when to save
            await self.think_with_tools(
                f"Research this topic thoroughly:\n\nTOPIC: {topic}\n\n"
                "Steps:\n"
                "1. Search the web for current information\n"
                "2. Fetch 1-2 relevant URLs for deeper context\n"
                "3. Synthesize the findings\n"
                "4. Save using save_finding with a clear summary and source list\n\n"
                "Be thorough but concise. Focus on practical insights.",
                max_iterations=8,
            )

            researched.append(topic)
            print(f"  ✓ done: {topic[:60]}\n")

        # Write a consolidated report to disk
        all_findings = await self.memory.all()
        topic_findings = {
            k.replace("finding:", ""): v
            for k, v in all_findings.items()
            if k.startswith("finding:")
        }

        if topic_findings:
            synthesis = await self.think(
                "Based on these research findings, write a concise executive summary "
                "highlighting the 3 most important insights and their implications:\n\n"
                + "\n\n".join(
                    f"TOPIC: {t}\n{d['summary']}"
                    for t, d in topic_findings.items()
                )
            )

            report_lines = [
                "# Research Report\n",
                f"Agent: {self.name} ({self.identity.short_id})\n\n",
                "## Executive Summary\n\n",
                synthesis,
                "\n\n---\n\n## Findings\n",
            ]
            for topic, data in topic_findings.items():
                report_lines.append(f"\n### {topic}\n\n")
                report_lines.append(data["summary"])
                if data.get("sources"):
                    report_lines.append("\n\n**Sources:**")
                    for src in data["sources"]:
                        report_lines.append(f"\n- {src}")
                report_lines.append("\n")

            await self.write_file("research_report.md", "".join(report_lines))
            await self.memory.save("synthesis", synthesis)
            print(f"\n[{self.name}] report written to research_report.md")

        print(f"\n[{self.name}] session complete")
        print(f"  researched: {len(researched)} topic(s)")
        print(f"  skipped (already known): {len(skipped)} topic(s)")
        print(f"  total in memory: {len(topic_findings)} finding(s)\n")


if __name__ == "__main__":
    ResearchAgent.launch()
