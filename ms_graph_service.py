"""
Microsoft Graph API service for calendar and Teams meeting operations.
Handles authentication and API calls using the client credentials flow.
"""

import os
import uuid
import asyncio
import logging
import httpx
from typing import Dict, List, Optional
from msal import ConfidentialClientApplication
from dotenv import load_dotenv

# -------------------------
# Logging — single configuration
# -------------------------
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s"
)
logger = logging.getLogger(__name__)
# Optionally reduce MSAL noise (uncomment if MSAL logs are too chatty)
# logging.getLogger("msal").setLevel(logging.INFO)

# -------------------------
# Load .env explicitly
# -------------------------
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
ENV_PATH = os.path.join(BASE_DIR, ".env")

logger.info(f"Loading .env from: {ENV_PATH}")
load_dotenv(dotenv_path=ENV_PATH, override=True, verbose=True)

# Debug environment variable values (hide secrets in logs)
logger.info(f"AZURE_TENANT_ID: {repr(os.environ.get('AZURE_TENANT_ID'))}")
logger.info(f"AZURE_CLIENT_ID: {repr(os.environ.get('AZURE_CLIENT_ID'))}")
logger.info(f"AZURE_CLIENT_SECRET: {'***hidden***' if os.environ.get('AZURE_CLIENT_SECRET') else None}")
logger.info(f"ORGANIZER_UPN: {repr(os.environ.get('ORGANIZER_UPN'))}")

# -------------------------
# Configuration from env
# -------------------------
TENANT = os.environ.get("AZURE_TENANT_ID")
CLIENT_ID = os.environ.get("AZURE_CLIENT_ID")
CLIENT_SECRET = os.environ.get("AZURE_CLIENT_SECRET")
ORGANIZER = os.environ.get("ORGANIZER_UPN")
DEFAULT_TZ = os.environ.get("DEFAULT_TZ", "Arabian Standard Time")
GRAPH_API_BASE = "https://graph.microsoft.com/v1.0"

_missing = [name for name, val in {
    "AZURE_TENANT_ID": TENANT,
    "AZURE_CLIENT_ID": CLIENT_ID,
    "AZURE_CLIENT_SECRET": CLIENT_SECRET,
    "ORGANIZER_UPN": ORGANIZER
}.items() if not val]

if _missing:
    err = f"Missing required environment variables: {', '.join(_missing)}"
    logger.error(err)
    raise ValueError(err)

# -------------------------
# MSAL client init
# -------------------------
SCOPES = ["https://graph.microsoft.com/.default"]

_msal_app = ConfidentialClientApplication(
    CLIENT_ID,
    authority=f"https://login.microsoftonline.com/{TENANT}",
    client_credential=CLIENT_SECRET,
)

def _get_access_token() -> str:
    """Acquire access token using client credentials flow."""
    token_response = _msal_app.acquire_token_for_client(scopes=SCOPES)
    if "access_token" not in token_response:
        error = token_response.get("error_description", token_response.get("error", token_response))
        logger.error(f"MSAL token acquisition failed: {error}")
        raise RuntimeError(f"MSAL token acquisition error: {error}")
    logger.debug("Successfully acquired MSAL access token")
    return token_response["access_token"]

def _build_headers(token: str, tz: Optional[str] = None) -> Dict[str, str]:
    hdrs = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    if tz:
        hdrs["Prefer"] = f'outlook.timezone="{tz}"'
    return hdrs

# -------------------------
# Graph API functions
# -------------------------
async def get_schedule(start_iso: str, end_iso: str, interval_min: int = 30, tz: str = DEFAULT_TZ) -> Dict:
    token = _get_access_token()
    body = {
        "schedules": [ORGANIZER],
        "startTime": {"dateTime": start_iso, "timeZone": tz},
        "endTime": {"dateTime": end_iso, "timeZone": tz},
        "availabilityViewInterval": max(5, min(60, interval_min)),
    }
    logger.debug("get_schedule body: %s", body)
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(f"{GRAPH_API_BASE}/users/{ORGANIZER}/calendar/getSchedule",
                                  headers=_build_headers(token, tz), json=body)
        resp.raise_for_status()
        return resp.json()

async def create_calendar_event(subject: str, start_iso: str, end_iso: str,
                                attendees: List[str], tz: str = DEFAULT_TZ) -> Dict:
    token = _get_access_token()
    transaction_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{subject}|{start_iso}|{','.join(sorted(attendees))}"))
    body = {
        "subject": subject,
        "body": {"contentType": "HTML", "content": f"<p>Booked via AI Assistant: {subject}</p>"},
        "start": {"dateTime": start_iso, "timeZone": tz},
        "end": {"dateTime": end_iso, "timeZone": tz},
        "attendees": [{"emailAddress": {"address": email}, "type": "required"} for email in attendees],
        "allowNewTimeProposals": True,
        "isOnlineMeeting": True,
        "onlineMeetingProvider": "teamsForBusiness",
        "transactionId": transaction_id,
    }
    logger.debug("create_calendar_event body: %s", body)
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(f"{GRAPH_API_BASE}/users/{ORGANIZER}/events",
                                 headers=_build_headers(token, tz), json=body)
        resp.raise_for_status()
        return resp.json()

async def create_online_meeting_standalone(subject: str, start_iso: str, end_iso: str) -> Dict:
    token = _get_access_token()
    body = {"subject": subject, "startDateTime": start_iso, "endDateTime": end_iso}
    logger.debug("create_online_meeting_standalone body: %s", body)
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(f"{GRAPH_API_BASE}/users/{ORGANIZER}/onlineMeetings",
                                 headers=_build_headers(token, None), json=body)
        resp.raise_for_status()
        return resp.json()

async def test_connection() -> Dict:
    """Verify connectivity to Microsoft Graph by fetching the organizer's profile."""
    token = _get_access_token()
    logger.debug("Testing Graph connectivity for organizer: %s", ORGANIZER)
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(f"{GRAPH_API_BASE}/users/{ORGANIZER}",
                                 headers=_build_headers(token))
        resp.raise_for_status()
        return resp.json()

# -------------------------
# Optional: run quick connectivity check if module executed directly
# -------------------------
if __name__ == "__main__":
    async def _main_check():
        try:
            logger.info("Running startup Graph connectivity test...")
            profile = await test_connection()
            display_name = profile.get("displayName") or profile.get("userPrincipalName")
            logger.info("✅ Connected to Microsoft Graph as: %s", display_name)
        except Exception as e:
            logger.exception("❌ Graph connectivity test failed: %s", e)

    asyncio.run(_main_check())
