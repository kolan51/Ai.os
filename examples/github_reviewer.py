"""
GitHub PR review agent — wakes on webhook events, reviews pull requests with AI.

Setup:
  1. Copy .env.example → .env, add ANTHROPIC_API_KEY and GITHUB_WEBHOOK_SECRET
  2. aios run examples/github_reviewer.py
  3. Point your GitHub webhook to http://your-host:8080/github
     (Settings → Webhooks → Add webhook, content type: application/json)

The agent:
  - Receives pull_request events from GitHub
  - Summarises the PR title, description, and changed files
  - Saves the review to long-term memory
  - Posts a log entry to the timeline
"""
from __future__ import annotations

from aios import Agent, tool, trigger
from aios import GitHubMixin


class GitHubReviewAgent(Agent, GitHubMixin):
    name = "gh_reviewer"
    model = "claude-sonnet-4-6"
    description = "Reviews GitHub pull requests via webhook"
    system_prompt = (
        "You are a senior software engineer doing a pull request review. "
        "Be concise, constructive, and specific. Focus on correctness, "
        "security, and maintainability. Flag potential issues clearly."
    )

    @trigger("webhook", path="/github", port=8080, secret="env:GITHUB_WEBHOOK_SECRET")
    async def run(self, payload: dict) -> None:
        action = payload.get("action", "")
        pr = payload.get("pull_request")

        # Only review newly opened PRs
        if action not in ("opened", "synchronize") or not pr:
            self.logger.info("Skipping event: action=%s", action)
            return

        pr_number = pr["number"]
        title = pr["title"]
        body = pr.get("body") or "(no description)"
        repo = payload.get("repository", {}).get("full_name", "unknown")
        author = pr.get("user", {}).get("login", "unknown")

        self.logger.info("Reviewing PR #%d: %s by @%s", pr_number, title, author)

        prompt = (
            f"Pull request #{pr_number} in {repo}\n"
            f"Author: @{author}\n"
            f"Title: {title}\n\n"
            f"Description:\n{body}\n\n"
            f"Write a concise code review (3-6 bullet points). "
            f"Flag any obvious bugs, security issues, or missing tests."
        )
        review = await self.think(prompt)

        await self.memory.save(f"review_pr_{pr_number}", {
            "repo": repo,
            "title": title,
            "author": author,
            "review": review,
        })
        await self.memory.log_event("pr_reviewed", {
            "pr": pr_number,
            "repo": repo,
            "title": title,
        })

        self.logger.info("Review saved for PR #%d:\n%s", pr_number, review)

    @tool
    async def get_review(self, pr_number: int) -> str:
        """Retrieve a saved review for a pull request. pr_number: the PR number."""
        data = await self.memory.load(f"review_pr_{pr_number}")
        if not data:
            return f"No review found for PR #{pr_number}"
        return f"PR #{pr_number} — {data.get('title')}\n\n{data.get('review', '')}"


if __name__ == "__main__":
    GitHubReviewAgent.launch()
