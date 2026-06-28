from __future__ import annotations

import os
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from ..registry import tool


class EmailMixin:
    """
    Adds email sending tools to an agent.

    Uses Python's built-in smtplib — no extra dependencies needed.
    Supports Gmail, Outlook, SendGrid SMTP, and any standard SMTP server.

    Required environment variables::

        EMAIL_SMTP_HOST=smtp.gmail.com
        EMAIL_SMTP_PORT=587
        EMAIL_ADDRESS=you@gmail.com
        EMAIL_PASSWORD=your-app-password

    For Gmail: use an App Password (Google Account → Security → 2FA → App passwords).
    For SendGrid: host=smtp.sendgrid.net, user=apikey, password=<API key>.

    Usage::

        from aios import Agent
        from aios.tools.builtin import EmailMixin

        class AlertAgent(Agent, EmailMixin):
            name = "alerter"
            model = "claude-haiku-4-5-20251001"

            async def run(self):
                await self.send_email(
                    to="team@example.com",
                    subject="Daily Report",
                    body="Everything looks good.",
                )
    """

    def _email_cfg(self) -> tuple[str, int, str, str]:
        host = os.environ.get("EMAIL_SMTP_HOST", "")
        port_str = os.environ.get("EMAIL_SMTP_PORT", "587")
        address = os.environ.get("EMAIL_ADDRESS", "")
        password = os.environ.get("EMAIL_PASSWORD", "")

        missing = [k for k, v in {
            "EMAIL_SMTP_HOST": host,
            "EMAIL_ADDRESS": address,
            "EMAIL_PASSWORD": password,
        }.items() if not v]

        if missing:
            raise OSError(
                f"Missing email configuration: {', '.join(missing)}. "
                "Set EMAIL_SMTP_HOST, EMAIL_SMTP_PORT (default 587), "
                "EMAIL_ADDRESS, and EMAIL_PASSWORD in your .env file."
            )

        try:
            port = int(port_str)
        except ValueError:
            port = 587

        return host, port, address, password

    @tool
    async def send_email(
        self,
        to: str,
        subject: str,
        body: str,
        html: bool = False,
        cc: str = "",
        reply_to: str = "",
    ) -> str:
        """
        Send an email via SMTP.
        to: Recipient email address (or comma-separated list).
        subject: Email subject line.
        body: Email body — plain text, or HTML if html=True.
        html: Set to true to send body as HTML (default: false).
        cc: Optional CC recipients (comma-separated).
        reply_to: Optional Reply-To address.
        """
        import asyncio

        return await asyncio.get_event_loop().run_in_executor(
            None, self._send_smtp, to, subject, body, html, cc, reply_to
        )

    def _send_smtp(
        self,
        to: str,
        subject: str,
        body: str,
        html: bool,
        cc: str,
        reply_to: str,
    ) -> str:
        host, port, address, password = self._email_cfg()

        msg = MIMEMultipart("alternative")
        msg["From"] = address
        msg["To"] = to
        msg["Subject"] = subject
        if cc:
            msg["Cc"] = cc
        if reply_to:
            msg["Reply-To"] = reply_to

        mime_type = "html" if html else "plain"
        msg.attach(MIMEText(body, mime_type, "utf-8"))

        recipients = [r.strip() for r in to.split(",")]
        if cc:
            recipients += [r.strip() for r in cc.split(",")]

        ctx = ssl.create_default_context()
        if port == 465:
            with smtplib.SMTP_SSL(host, port, context=ctx) as smtp:
                smtp.login(address, password)
                smtp.sendmail(address, recipients, msg.as_string())
        else:
            with smtplib.SMTP(host, port) as smtp:
                smtp.ehlo()
                smtp.starttls(context=ctx)
                smtp.login(address, password)
                smtp.sendmail(address, recipients, msg.as_string())

        return f"Email sent to {to} (subject: {subject!r})"

    @tool
    async def send_html_email(self, to: str, subject: str, html_body: str) -> str:
        """
        Send an HTML-formatted email.
        to: Recipient email address.
        subject: Email subject line.
        html_body: Full HTML content for the email body.
        """
        return await self.send_email(to=to, subject=subject, body=html_body, html=True)
