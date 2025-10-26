"""
Email client wrapper using SMTP with graceful no-op when credentials are missing.

Environment variables:
- SMTP_HOST: SMTP server host
- SMTP_PORT: SMTP server port (integer)
- SMTP_USER: Username for SMTP AUTH (optional)
- SMTP_PASS: Password for SMTP AUTH (optional)
- SMTP_FROM: Default "from" email address for outgoing messages (optional)
"""

import os
import smtplib
from email.message import EmailMessage
from typing import Optional, Tuple


class EmailClient:
    """A thin SMTP wrapper that sends emails if credentials are available, otherwise no-op."""

    def __init__(self) -> None:
        self.smtp_host: Optional[str] = os.getenv("SMTP_HOST")
        self.smtp_port: Optional[int] = None
        port_str = os.getenv("SMTP_PORT")
        if port_str and port_str.isdigit():
            try:
                self.smtp_port = int(port_str)
            except Exception:
                self.smtp_port = None
        self.smtp_user: Optional[str] = os.getenv("SMTP_USER")
        self.smtp_pass: Optional[str] = os.getenv("SMTP_PASS")
        self.smtp_from: Optional[str] = os.getenv("SMTP_FROM")

    def _enabled(self) -> bool:
        # Host and port are minimally required.
        return bool(self.smtp_host and self.smtp_port)

    # PUBLIC_INTERFACE
    def send_email(
        self,
        to_email: str,
        subject: str,
        body: str,
        from_email: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """
        Send an email via SMTP. Returns (success, message).
        If SMTP credentials are missing or invalid, returns (False, reason) but does not raise.
        """
        if not self._enabled():
            return (
                False,
                "SMTP disabled: missing SMTP_HOST/SMTP_PORT; "
                "returning no-op.",
            )

        msg = EmailMessage()
        msg["From"] = from_email or self.smtp_from or (self.smtp_user or "no-reply@example.com")
        msg["To"] = to_email
        msg["Subject"] = subject
        msg.set_content(body)

        try:
            # Use STARTTLS if 587 typical; if 465, often use SMTP_SSL. Here we try plain SMTP
            # with starttls if available. Keep it minimal and broadly compatible; STARTTLS will
            # be attempted when authentication is provided.
            with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=15) as server:
                server.ehlo()
                try:
                    server.starttls()
                    server.ehlo()
                except Exception:
                    # If server does not support TLS, continue without it.
                    pass
                if self.smtp_user and self.smtp_pass:
                    server.login(self.smtp_user, self.smtp_pass)
                server.send_message(msg)
            return True, "Email sent"
        except Exception as exc:
            # Graceful failure
            return False, f"SMTP send failed: {exc!s}"


# Singleton-ish accessor
_email_client_instance: Optional[EmailClient] = None


# PUBLIC_INTERFACE
def get_email_client() -> EmailClient:
    """Return a cached EmailClient instance."""
    global _email_client_instance
    if _email_client_instance is None:
        _email_client_instance = EmailClient()
    return _email_client_instance
