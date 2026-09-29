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

def clean_isbn(s: str | None) -> str | None:
    """
    Normalize ISBN/GTIN for eBay 'gtin' param.
    - Removes hyphens/spaces/etc
    - Keeps digits
    - Allows X only as last char for ISBN-10
    Returns digits-only ISBN-13 or ISBN-10 (possibly ending in X), else None.
    """
    if not s:
        return None

    s = s.strip().upper()
    # keep only digits and X
    filtered = "".join(ch for ch in s if ch.isdigit() or ch == "X")

    # ISBN-13 must be exactly 13 digits
    if len(filtered) == 13 and filtered.isdigit():
        return filtered

    # ISBN-10: first 9 digits numeric; last char numeric or X
    if len(filtered) == 10 and filtered[:9].isdigit() and (filtered[9].isdigit() or filtered[9] == "X"):
        return filtered

    return None




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
    requested_condition_id: str | None = None
    if requested_condition in COND_MAP:
        requested_condition_id = COND_MAP[requested_condition][0]

    # --- Normalize inputs ---
    isbn_clean = clean_isbn(isbn)

    def build_q() -> str:
        parts: list[str] = []
        t = (title or "").strip()
        a = (author or "").strip()
        if t and t != "—":
            parts.append(t)
        if a and a != "—":
            parts.append(a)
        if year:
            parts.append(str(year))
        return " ".join(parts).strip()

    q_text = build_q()

    # Search helper: tries exact condition (if given) then any condition.
    async def run_search(*, gtin: str | None, q: str | None) -> list[dict]:
        if not gtin and not q:
            return []

        # 1) Exact condition attempt
        if requested_condition_id:
            data = await _browse_search(
                client,
                gtin=gtin,
                q=q,
                condition_id=requested_condition_id,
                limit=10,
            )
            items = data.get("itemSummaries") or []
            if items:
                return items

        # 2) Any condition
        data_any = await _browse_search(
            client,
            gtin=gtin,
            q=q,
            condition_id=None,
            limit=20,
        )
        all_items = data_any.get("itemSummaries") or []
        if not all_items:
            return []

        # If we requested a condition, pick the nearest available conditionId
        if requested_condition_id:
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
                filtered = [it for it in all_items if str(it.get("conditionId")) == best]
                return filtered if filtered else all_items

        return all_items

    # --- Primary attempt: GTIN if available, else Q ---
    query_type: str
    query_text: str

    items: list[dict] = []

    if isbn_clean:
        query_type = "gtin"
        query_text = isbn_clean
        items = await run_search(gtin=isbn_clean, q=None)

        # NEW: if GTIN yields nothing, fall back to q search
        if not items and q_text:
            query_type = "q_fallback"
            query_text = q_text
            items = await run_search(gtin=None, q=q_text)
    else:
        query_type = "q"
        query_text = q_text
        items = await run_search(gtin=None, q=q_text) if q_text else []

    # Compute estimator
    excl, incl_fixed, debug = _extract_prices(items)

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

    used_condition_id = None
    used_condition = None
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
