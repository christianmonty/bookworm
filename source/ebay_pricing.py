# source/ebay_pricing.py
import asyncio
import math
from typing import Any, Optional

import httpx

from source.ebay_auth import ebay_auth_headers, _ebay_base_url


COND_MAP = {
    "new": ("1000", "New"),
    "like_new": ("2750", "Like New"),
    "very_good": ("4000", "Very Good"),
    "good": ("5000", "Good"),
    "acceptable": ("6000", "Acceptable"),
}

ORDERED_COND_IDS = ["1000", "2750", "4000", "5000", "6000"]


def _cond_distance(a: str, b: str) -> int:
    """Distance in the fixed ordering above."""
    try:
        ia = ORDERED_COND_IDS.index(a)
        ib = ORDERED_COND_IDS.index(b)
        return abs(ia - ib)
    except ValueError:
        return 999


def _median(vals: list[float]) -> Optional[float]:
    if not vals:
        return None
    vals = sorted(vals)
    n = len(vals)
    mid = n // 2
    if n % 2 == 1:
        return vals[mid]
    return (vals[mid - 1] + vals[mid]) / 2.0


def _extract_prices(items: list[dict]) -> tuple[list[float], list[float], list[dict]]:
    """
    Returns:
      excl_list: [price]
      incl_fixed_list: [price + fixed shipping] (only when fixed shipping exists)
      cheap_items_debug: small list of cheapest items used for debugging
    """
    rows = []
    for it in items:
        try:
            price = float(it["price"]["value"])
        except Exception:
            continue

        ship_fixed = None
        # shippingOptions may have FIXED shippingCost
        for opt in it.get("shippingOptions", []) or []:
            if opt.get("shippingCostType") == "FIXED":
                sc = opt.get("shippingCost", {})
                if sc and sc.get("currency") == "USD":
                    try:
                        ship_fixed = float(sc["value"])
                    except Exception:
                        ship_fixed = None
                break

        total_fixed = (price + ship_fixed) if ship_fixed is not None else None

        rows.append({
            "itemId": it.get("itemId"),
            "title": it.get("title"),
            "condition": it.get("condition"),
            "conditionId": it.get("conditionId"),
            "price": price,
            "shipping_fixed": ship_fixed,
            "total_fixed": total_fixed,
            "itemWebUrl": it.get("itemWebUrl"),
        })

    # Sort by total_fixed when available else by price
    rows.sort(key=lambda r: (r["total_fixed"] if r["total_fixed"] is not None else r["price"]))

    excl = [r["price"] for r in rows]
    incl_fixed = [r["total_fixed"] for r in rows if r["total_fixed"] is not None]

    # keep small debug
    debug = rows[:5]
    return excl, incl_fixed, debug


async def _browse_search(client: httpx.AsyncClient, *, gtin: str | None, q: str | None, condition_id: str | None, limit: int = 10) -> dict:
    base = _ebay_base_url()
    url = f"{base}/buy/browse/v1/item_summary/search"

    params = {"limit": str(limit)}
    if gtin:
        params["gtin"] = gtin
    if q:
        params["q"] = q
    if condition_id:
        # conditionIds:{5000} but URL-encoding is handled by httpx
        params["filter"] = f"conditionIds:{{{condition_id}}}"

    headers = await ebay_auth_headers()
    resp = await client.get(url, params=params, headers=headers)
    if resp.status_code == 429:
        raise httpx.HTTPStatusError("rate_limited", request=resp.request, response=resp)
    if resp.status_code >= 400:
        raise RuntimeError(f"Browse search failed {resp.status_code}: {resp.text}")
    return resp.json()


async def fetch_price_estimate_for_book(
    client: httpx.AsyncClient,
    *,
    isbn: str | None,
    title: str | None,
    author: str | None,
    year: int | None,
    requested_condition: str | None,
) -> dict:
    """
    Returns dict with:
      query_type, query_text
      requested_condition_id
      used_condition_id/used_condition
      sample_size
      median_excl, median_incl_fixed
      debug_items (<=5)
    """
    requested_condition_id = None
    if requested_condition in COND_MAP:
        requested_condition_id = COND_MAP[requested_condition][0]

    # Build query
    query_type = "gtin" if isbn else "q"
    if isbn:
        query_text = isbn
    else:
        parts = []
        if title:
            parts.append(title)
        if author:
            parts.append(author)
        if year:
            parts.append(str(year))
        query_text = " ".join(parts).strip()

    # 1) Exact condition attempt (if we have one)
    items = []
    used_condition_id = None
    used_condition = None

    if query_text:
        if requested_condition_id:
            data = await _browse_search(client, gtin=isbn if isbn else None, q=None if isbn else query_text, condition_id=requested_condition_id, limit=10)
            items = data.get("itemSummaries") or []

        # 2) Fallback: any condition (single extra call)
        if not items:
            data_any = await _browse_search(client, gtin=isbn if isbn else None, q=None if isbn else query_text, condition_id=None, limit=20)
            all_items = data_any.get("itemSummaries") or []

            # Choose nearest condition among available items
            if requested_condition_id:
                # group by conditionId
                best = None
                best_dist = 999
                for it in all_items:
                    cid = it.get("conditionId")
                    if not cid:
                        continue
                    d = _cond_distance(str(cid), requested_condition_id)
                    if d < best_dist:
                        best_dist = d
                        best = str(cid)
                if best is not None:
                    items = [it for it in all_items if str(it.get("conditionId")) == best]
                else:
                    items = all_items
            else:
                items = all_items

    # Compute estimator
    excl, incl_fixed, debug = _extract_prices(items)

    # Choose lowest N logic on excl and incl_fixed separately
    def pick_vals(vals: list[float]) -> list[float]:
        vals = sorted(vals)
        if len(vals) >= 5:
            return vals[:5]
        if len(vals) >= 3:
            return vals[:3]
        return vals[:1]

    picked_excl = pick_vals(excl)
    picked_incl = pick_vals(incl_fixed)

    median_excl = _median(picked_excl)
    median_incl = _median(picked_incl)

    # used condition (from the items we ended up using)
    if debug:
        used_condition_id = debug[0].get("conditionId")
        used_condition = debug[0].get("condition")

    return {
        "query_type": query_type,
        "query_text": query_text,
        "requested_condition_id": requested_condition_id,
        "used_condition_id": str(used_condition_id) if used_condition_id is not None else None,
        "used_condition": used_condition,
        "sample_size": len(items),
        "median_excl": median_excl,
        "median_incl_fixed": median_incl,
        "debug_items": debug,
    }


async def fetch_with_retries(fn, *, max_attempts: int = 5, base_sleep: float = 0.6):
    attempt = 0
    while True:
        attempt += 1
        try:
            return await fn()
        except httpx.HTTPStatusError as e:
            # 429 backoff
            if e.response is not None and e.response.status_code == 429 and attempt < max_attempts:
                await asyncio.sleep(base_sleep * (2 ** (attempt - 1)))
                continue
            raise
        except (httpx.TimeoutException, httpx.TransportError) as e:
            if attempt < max_attempts:
                await asyncio.sleep(base_sleep * (2 ** (attempt - 1)))
                continue
            raise
