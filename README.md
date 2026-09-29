# Bookworm

Turn two phone photos of a book into a reviewed catalog record with an eBay price estimate.

## Overview

I built Bookworm to sell a personal collection of about 400 books on eBay. Researching and writing up each book by hand took about 5 minutes. Bookworm cuts that to about 30 seconds.

For each book, I photograph the cover and the copyright page from a mobile capture page. The photos go to a FastAPI service, which stores them in Google Cloud Storage. An OpenAI vision model reads both images and returns strict JSON with the ISBN, title, author, publisher, edition, a confidence score, and flags for anything it couldn't read. It's instructed to return `null` rather than guess. Any record with a missing ISBN, title, or author goes to a review page, where I check it against the photos and correct it, because a wrong ISBN or edition leads to a bad listing. Reviewed books are stored in Postgres on Cloud SQL. A pricing job then queries the eBay Browse API for comparable listings. It searches by ISBN first and falls back to a title and author search, prefers comps in the same condition, and stores a median price estimate for each book along with the comps behind it.

Along the way I designed the relational schema, wired up OAuth and rate-limit handling for eBay, and migrated the live data from SQLite and local files to Cloud SQL and GCS without losing any records.

## How it works

```
Phone (capture UI)
   │  cover + copyright-page photos
   ▼
FastAPI service ──► Google Cloud Storage   (raw images: raw/{book_id}/{type}.jpg)
   │
   ├─► Postgres on Cloud SQL               (books, images, extractions)
   │
   ├─► Extraction job ──► OpenAI vision (gpt-4.1-mini, strict JSON)
   │        │
   │        └─ missing ISBN / title / author ──► needs_review
   ▼
Review UI  ──► confirm or override fields ──► done
   │
   ▼
Pricing job ──► eBay Browse API (OAuth, by ISBN → title/author fallback)
                 └─ condition-matched comps ──► median price per book
```

1. **Capture.** A mobile-friendly web page walks through each book: choose a storage bin and owner, then upload a cover photo and a copyright-page photo, and record condition, dust jacket, and notes. Images go straight to GCS, and their paths are saved in Postgres.
2. **Extract.** `POST /jobs/extract` processes books in batches. Both images go to an OpenAI vision model with a strict JSON schema covering ISBN-10/13, title, author, publisher, year, edition, binding, a confidence score, and flags such as `blurry_text` or `multiple_isbns`. The prompt tells the model to take the ISBN from the copyright page, take the title and author from the cover, and return `null` rather than guess.
3. **Triage.** Any book with a missing ISBN, title, or author is marked `needs_review` automatically. Errors are stored alongside the record, so one bad image doesn't stop the batch.
4. **Review.** A review page shows each extraction next to its source images. I can confirm it or fix fields manually before the record is marked `done`.
5. **Price.** `scripts/pull_ebay_active_pricing.py` looks up comparable listings for every reviewed book through the eBay Browse API. It searches by normalized ISBN first and falls back to a title and author search. It tries to match the book's condition exactly, and if nothing matches it uses the closest condition available. It then takes the median of the cheapest comps, both with and without fixed shipping, and stores the result along with the comps it used.

## Design decisions

- **Human in the loop by design.** A wrong ISBN or edition produces a wrong listing and an unhappy buyer. The model is told to return `null` rather than guess, and anything uncertain goes to review instead of being auto-accepted.
- **Two photos per book.** The cover identifies the book; the copyright page carries the ISBN and edition. Asking the model to take each field from the best source reduced errors.
- **Images in object storage, metadata in Postgres.** The database stores only GCS paths, so rows stay small and the images can be reprocessed later with a different model.
- **Pricing that stays accurate when data is thin.** Many books return few or no comps for their ISBN, so the pricing job widens the search step by step: exact condition, then nearest condition, then a title and author search. It records which strategy it used and how many comps it found, so thinly supported prices are easy to spot.
- **Resilient batch jobs.** eBay OAuth tokens are cached until just before they expire. Rate limits (429s) and timeouts are retried with exponential backoff, and requests are throttled. A book that fails is saved with its error instead of stopping the run, and failed extractions can be rerun on their own (`/jobs/extract_errors`).
- **Started local, then moved to the cloud.** The first version used SQLite and local files. I migrated to GCS and Cloud SQL with one-off scripts (`scripts/`) that support `--dry-run`, backups, and a summary report, so the live data could move safely.

## Tech stack

Python · FastAPI · SQLAlchemy · PostgreSQL (Cloud SQL) · Google Cloud Storage · OpenAI API · eBay Browse API (OAuth2) · httpx/asyncio

## Data model

| Table | Purpose |
|---|---|
| `books` | One row per physical book: status, bin, owner, condition, jacket, notes |
| `book_image` | GCS path for each image; unique on `(book_id, image_type)` |
| `book_extraction` | Extracted fields, confidence, flags, full model JSON, review status |
| `book_ebay_pricing` | Query strategy, requested and matched condition, sample size, median prices, comps used |

## Running locally

```bash
pip install -r requirements.txt
cp .env.example .env            # fill in database, GCS, OpenAI, and eBay credentials
./scripts/start_proxy.sh        # Cloud SQL Auth Proxy (or point DATABASE_URL at local Postgres/SQLite)
uvicorn source.app:app --reload
```

Then open `/capture` on your phone (same network) to start cataloging, and `/review/extractions` to review results. Run the pricing job with:

```bash
python scripts/pull_ebay_active_pricing.py
```

## Status and roadmap

- [x] Mobile capture flow with GCS image storage
- [x] LLM metadata extraction with confidence and flags
- [x] Review UI with manual overrides
- [x] Migration from SQLite to Cloud SQL Postgres
- [x] eBay pricing comps from active listings (condition-aware, with fallbacks)
- [ ] Pricing from sold listings (needs eBay Marketplace Insights access)
- [ ] Automated eBay listing creation (Inventory API)
- [ ] Sales tracking and inventory updates
- [ ] Alembic migrations
