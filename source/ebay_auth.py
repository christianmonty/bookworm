import os
import time
import base64
from typing import Optional, Tuple

import httpx

# In-process cache (good enough to start)
_cached_token: Optional[str] = None
_cached_expiry_epoch: float = 0.0  # seconds since epoch


def _ebay_base_url() -> str:
    env = os.getenv("EBAY_ENV", "production").lower()
    return "https://api.sandbox.ebay.com" if env == "sandbox" else "https://api.ebay.com"


def _basic_auth_header(client_id: str, client_secret: str) -> str:
    raw = f"{client_id}:{client_secret}".encode("utf-8")
    b64 = base64.b64encode(raw).decode("ascii")
    return f"Basic {b64}"


async def get_ebay_app_token() -> str:
    """
    Returns a valid eBay application access token (client_credentials).
    Caches token in memory until ~60 seconds before expiry.
    """
    global _cached_token, _cached_expiry_epoch

    now = time.time()
    if _cached_token and now < (_cached_expiry_epoch - 60):
        return _cached_token

    client_id = os.environ["EBAY_CLIENT_ID"]
    client_secret = os.environ["EBAY_CLIENT_SECRET"]
    scope = os.getenv("EBAY_SCOPE", "https://api.ebay.com/oauth/api_scope")

    url = f"{_ebay_base_url()}/identity/v1/oauth2/token"
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Authorization": _basic_auth_header(client_id, client_secret),
    }
    data = {
        "grant_type": "client_credentials",
        "scope": scope,
    }

    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(url, headers=headers, data=data)
        resp.raise_for_status()
        payload = resp.json()

    token = payload["access_token"]
    expires_in = int(payload.get("expires_in", 7200))  # seconds
    _cached_token = token
    _cached_expiry_epoch = now + expires_in

    return token


async def ebay_auth_headers() -> dict:
    token = await get_ebay_app_token()
    marketplace_id = os.getenv("EBAY_MARKETPLACE_ID", "EBAY_US")
    return {
        "Authorization": f"Bearer {token}",
        "X-EBAY-C-MARKETPLACE-ID": marketplace_id,
    }
