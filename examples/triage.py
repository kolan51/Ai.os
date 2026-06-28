"""
Triage agent — runs every 15 minutes, reads new Linear issues, classifies
priority and assigns labels using an LLM, then posts a Slack summary.

Usage:
    cp .env.example .env   # add LINEAR_API_KEY and SLACK_BOT_TOKEN
    aios run examples/triage.py

Required env vars:
    LINEAR_API_KEY      — lin_api_... from linear.app/settings/api
    SLACK_BOT_TOKEN     — xoxb-... from api.slack.com/apps
    ANTHROPIC_API_KEY   — or any model key you prefer
"""
from __future__ import annotations

import json
from aios import Agent, schedule, tool
from aios import LinearMixin, SlackMixin


SLACK_CHANNEL = "#eng-triage"
TEAM_ID = ""  # set to your Linear team ID, or leave empty to use the first team


class TriageAgent(Agent, LinearMixin, SlackMixin):
    name = "triage"
    model = "claude-sonnet-4-6"
    description = "Auto-triages new Linear issues and posts a Slack summary every 15 minutes"
    system_prompt = (
        "You are an engineering triage assistant. "
        "When given a list of Linear issues, classify each one by urgency (critical/high/medium/low) "
        "and suggest which team should own it. Be concise and direct."
    )

    @schedule("every 15m")
    async def run(self) -> None:
        team_id = await self._resolve_team()
        if not team_id:
            self.logger.warning("No Linear team found — set TEAM_ID or create a team")
            return

        seen: set[str] = set(await self.memory.load("seen_issue_ids", default=[]))
        issues = await self.linear_get_issues(team=team_id, state="Triage", limit=50)
        new_issues = [i for i in issues if i["id"] not in seen]

        if not new_issues:
            self.logger.info("No new issues in Triage state")
            return

        self.logger.info(f"Triaging {len(new_issues)} new issue(s)")
        summary = await self._classify_and_summarise(new_issues)

        await self.slack_send_message(SLACK_CHANNEL, summary)
        await self.memory.log_event("triage_run", {"new_issues": len(new_issues)})

        seen.update(i["id"] for i in new_issues)
        await self.memory.save("seen_issue_ids", list(seen))

    async def _resolve_team(self) -> str:
        global TEAM_ID
        if TEAM_ID:
            return TEAM_ID
        cached = await self.memory.load("team_id")
        if cached:
            return cached
        teams = await self.linear_list_teams()
        if teams:
            TEAM_ID = teams[0]["id"]
            await self.memory.save("team_id", TEAM_ID)
            return TEAM_ID
        return ""

    async def _classify_and_summarise(self, issues: list[dict]) -> str:
        bullet_list = "\n".join(
            f"- [{i['identifier']}] {i['title']} (current priority: {i.get('priority', 'none')})"
            for i in issues
        )
        prompt = (
            f"Classify each issue below by urgency and ownership. "
            f"Then write a Slack-formatted summary (use *bold* for urgent items).\n\n{bullet_list}"
        )
        return await self.think(prompt)

    @tool
    async def mark_as_seen(self, issue_id: str) -> str:
        """Mark a Linear issue as seen so it won't be triaged again. issue_id: the Linear issue ID."""
        seen: list = await self.memory.load("seen_issue_ids", default=[])
        if issue_id not in seen:
            seen.append(issue_id)
            await self.memory.save("seen_issue_ids", seen)
        return f"Marked {issue_id} as seen"

    @tool
    async def clear_seen(self) -> str:
        """Reset the seen-issues list so all Triage issues will be re-triaged next run."""
        await self.memory.delete("seen_issue_ids")
        return "Cleared seen issues — all Triage issues will be re-triaged on next run"


if __name__ == "__main__":
    TriageAgent.launch()
