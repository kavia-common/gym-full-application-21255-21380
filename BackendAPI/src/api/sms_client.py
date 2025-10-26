"""
SMS client wrapper for Twilio with graceful no-op when credentials are missing.

Environment variables:
- TWILIO_ACCOUNT_SID
- TWILIO_AUTH_TOKEN
- TWILIO_FROM  (E.164 format, e.g., +12345550123)

Implementation note:
- To avoid adding external dependencies, this client uses requests via httpx to call Twilio's API.
- If credentials are missing, calls are no-ops and return a stubbed response.
"""

import base64
import os
from typing import Optional, Tuple

import httpx


class SMSClient:
    """A simple Twilio SMS sender using httpx. No-op when credentials are missing."""

    def __init__(self) -> None:
        self.account_sid: Optional[str] = os.getenv("TWILIO_ACCOUNT_SID")
        self.auth_token: Optional[str] = os.getenv("TWILIO_AUTH_TOKEN")
        self.from_number: Optional[str] = os.getenv("TWILIO_FROM")

    def _enabled(self) -> bool:
        return bool(self.account_sid and self.auth_token and self.from_number)

    # PUBLIC_INTERFACE
    def send_sms(self, to_number: str, body: str) -> Tuple[bool, str]:
        """
        Send an SMS via Twilio. Returns (success, message/response_summary).
        If credentials are missing, returns (False, reason) without raising.
        """
        if not self._enabled():
            return (
                False,
                "Twilio disabled: missing TWILIO_ACCOUNT_SID/TWILIO_AUTH_TOKEN/"
                "TWILIO_FROM; returning no-op.",
            )

        url = f"https://api.twilio.com/2010-04-01/Accounts/{self.account_sid}/Messages.json"
        auth_bytes = f"{self.account_sid}:{self.auth_token}".encode("utf-8")
        basic_auth = base64.b64encode(auth_bytes).decode("ascii")

        data = {
            "From": self.from_number,
            "To": to_number,
            "Body": body,
        }

        try:
            with httpx.Client(timeout=15.0) as client:
                resp = client.post(
                    url,
                    data=data,
                    headers={"Authorization": f"Basic {basic_auth}"},
                )
            if 200 <= resp.status_code < 300:
                return True, "SMS sent"
            return False, f"Twilio API error {resp.status_code}: {resp.text}"
        except Exception as exc:
            return False, f"Twilio send failed: {exc!s}"


_sms_client_instance: Optional[SMSClient] = None


# PUBLIC_INTERFACE
def get_sms_client() -> SMSClient:
    """Return a cached SMSClient instance."""
    global _sms_client_instance
    if _sms_client_instance is None:
        _sms_client_instance = SMSClient()
    return _sms_client_instance
