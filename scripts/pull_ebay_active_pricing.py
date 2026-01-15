# scripts/pull_ebay_active_pricing.py
import asyncio
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv

from source import db
from source.ebay_pricing import fetch_price_estimate_for_book, fetch_with_retries

# Load .env the same way as your app.py
load_dotenv(Path(__file__).resolve().parents[1] / ".env")


async def main():
    db.init_db()

    books = db.list_books_for_pricing()
    total = len(books)
    print(f"[pricing] books_to_process={total}")

    # Gentle throttling: 1 request every ~0.25s avg, plus retries on 429
    throttle_seconds = float(os.getenv("EBAY_THROTTLE_SECONDS", "0.25"))

    async with httpx.AsyncClient(timeout=20.0) as client:
        for i, b in enumerate(books, start=1):
            book_id = b["book_id"]
            condition = b.get("condition")

            isbn = b.get("isbn")
            title = b.get("title")
            author = b.get("author")
            year = b.get("year")

            async def run_one():
                return await fetch_price_estimate_for_book(
                    client,
                    isbn=isbn,
                    title=title,
                    author=author,
                    year=year,
                    requested_condition=condition,
                )

            try:
                result = await fetch_with_retries(run_one)
                db.upsert_ebay_pricing(
                    book_id=book_id,
                    query_type=result["query_type"],
                    query_text=result["query_text"],
                    requested_condition=condition,
                    requested_condition_id=result["requested_condition_id"],
                    used_condition=result["used_condition"],
                    used_condition_id=result["used_condition_id"],
                    sample_size=result["sample_size"],
                    median_excl=result["median_excl"],
                    median_incl_fixed=result["median_incl_fixed"],
                    items_json={"items": result["debug_items"]},
                )

                title_disp = (title or "—").replace("\n", " ").strip()
                author_disp = (author or "").replace("\n", " ").strip()
                name = f"{title_disp}" + (f" — {author_disp}" if author_disp else "")

                print(
                    f"[pricing] {i}/{total} book_id={book_id} | {name} | "
                    f"book_cond={condition} | used_cond={result['used_condition']} | "
                    f"excl=${result['median_excl']} | incl_fixed=${result['median_incl_fixed']} | query={result['query_type']} | "
                    f"n={result['sample_size']}"
                )

            except Exception as e:
                # store a row with null price so you can see failures later
                db.upsert_ebay_pricing(
                    book_id=book_id,
                    query_type="gtin" if isbn else "q",
                    query_text=isbn or f"{title or ''} {author or ''} {year or ''}".strip(),
                    requested_condition=condition,
                    requested_condition_id=None,
                    used_condition=None,
                    used_condition_id=None,
                    sample_size=0,
                    median_excl=None,
                    median_incl_fixed=None,
                    items_json={"error": str(e)},
                )
                print(f"[pricing] {i}/{total} book_id={book_id} ERROR: {e}")

            await asyncio.sleep(throttle_seconds)

    print("[pricing] done")


if __name__ == "__main__":
    asyncio.run(main())
