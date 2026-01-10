#!/usr/bin/env python3
"""
One-time migration: local_store/raw/{book_id}/{image_type}.jpg -> GCS raw/{book_id}/{image_type}.jpg
and update SQLite table book_image.storage_path to gs://BUCKET/raw/{book_id}/{image_type}.jpg

Uses environment variables by default:
  - DATABASE_URL (e.g. sqlite:///./local_store/bookworm.db)
  - GOOGLE_CLOUD_PROJECT (optional)
  - GCS_BUCKET (bucket name, no gs:// prefix)

Safe behaviors:
- Reminds you to back up DB
- Optional --backup to create a timestamped DB copy (SQLite only)
- --dry-run mode: no uploads, no DB writes
- Logs + summary report (and optional JSON report)
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union
from urllib.parse import urlparse, unquote

from google.cloud import storage
from sqlalchemy import MetaData, Table, create_engine, select, update


ALLOWED_IMAGE_TYPES = {"cover", "copyright"}


def setup_logger(verbose: bool) -> logging.Logger:
    logger = logging.getLogger("migrate_images_to_gcs")
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(logging.DEBUG if verbose else logging.INFO)

    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
    handler.setFormatter(fmt)

    if not logger.handlers:
        logger.addHandler(handler)

    return logger


def repo_root_from_script() -> Path:
    # scripts/migrate_images_to_gcs.py -> repo root is parents[1]
    return Path(__file__).resolve().parents[1]


def normalize_book_id(raw: str) -> Union[int, str]:
    s = raw.strip()
    if s.isdigit():
        try:
            return int(s)
        except ValueError:
            return s
    return s


def to_gs_uri(bucket: str, object_key: str) -> str:
    return f"gs://{bucket}/{object_key}"


@dataclass
class LocalImage:
    path: Path
    book_id: Union[int, str]
    image_type: str
    object_key: str  # raw/{book_id}/{image_type}.jpg


def find_local_images(raw_dir: Path, logger: logging.Logger) -> List[LocalImage]:
    images: List[LocalImage] = []
    if not raw_dir.exists():
        logger.error(f"Raw image directory not found: {raw_dir}")
        return images

    for p in raw_dir.glob("*/*.jpg"):
        # Expect: raw/{book_id}/{image_type}.jpg
        try:
            book_id_folder = p.parent.name
            image_type = p.stem  # cover / copyright
            book_id = normalize_book_id(book_id_folder)
            object_key = f"raw/{book_id_folder}/{image_type}.jpg"  # MUST use folder name verbatim
            images.append(LocalImage(path=p, book_id=book_id, image_type=image_type, object_key=object_key))
        except Exception as e:
            logger.warning(f"Skipping unexpected path {p}: {e}")

    return images


def parse_sqlite_file_path(database_url: str, repo_root: Path) -> Optional[Path]:
    """
    If DATABASE_URL is sqlite and points to a local file, return the Path to the .db file.
    Supports:
      - sqlite:///relative/path.db
      - sqlite:////absolute/path.db
      - sqlite:///C:/absolute/windows/path.db  (often seen on Windows)
    Returns None for in-memory or non-sqlite.
    """
    if not database_url.startswith("sqlite"):
        return None

    # Common in-memory forms
    if database_url in ("sqlite://", "sqlite:///:memory:", "sqlite:///:memory"):
        return None

    parsed = urlparse(database_url)
    # For sqlite, parsed.path holds the filesystem-ish portion (possibly URL-encoded)
    raw_path = unquote(parsed.path or "")

    # Examples:
    # sqlite:///./local_store/bookworm.db  -> path "/./local_store/bookworm.db"
    # sqlite:////Users/me/bookworm.db      -> path "//Users/me/bookworm.db"
    # sqlite:///C:/repo/local_store/x.db   -> path "/C:/repo/local_store/x.db"
    # sqlite:///C:\repo\local_store\x.db   -> rare / odd; backslashes won't appear in URL path usually

    if not raw_path:
        return None

    # Strip leading slash for relative paths like "/./local_store/bookworm.db"
    # But keep UNC/absolute Linux forms. Heuristic:
    # - If it looks like "/C:/" (Windows drive), strip the first slash.
    # - If it looks like "/./" or "/../" (relative), strip the first slash and resolve relative to repo root.
    # - If it looks like "//" (posix absolute due to sqlite:////), keep as absolute after collapsing.
    if raw_path.startswith("/C:/") or raw_path.startswith("/c:/"):
        raw_path = raw_path[1:]  # "C:/..."
        return Path(raw_path).resolve()

    if raw_path.startswith("/./") or raw_path.startswith("/../"):
        rel = raw_path[1:]  # "./..." or "../..."
        return (repo_root / rel).resolve()

    # If it's a single leading slash and does NOT look like a posix absolute you want to keep?
    # On Windows, many sqlite URLs still look like "/./relative". We handled that above.
    # For posix absolute paths, keep it.
    p = Path(raw_path)

    # If it's not absolute, resolve relative to repo root (rare due to urlparse behavior).
    if not p.is_absolute():
        return (repo_root / raw_path).resolve()

    return p.resolve()


def sqlite_connect_args(database_url: str) -> dict:
    # Needed for SQLite if app might use threads; harmless for our script too
    return {"check_same_thread": False} if database_url.startswith("sqlite") else {}


def load_book_image_table(database_url: str):
    """
    Reflect the book_image table from the provided DATABASE_URL.
    (Works for sqlite and other DBs later too.)
    """
    engine = create_engine(database_url, connect_args=sqlite_connect_args(database_url))
    metadata = MetaData()
    table = Table("book_image", metadata, autoload_with=engine)
    return engine, table


def backup_sqlite_db(sqlite_db_path: Path, logger: logging.Logger) -> Path:
    ts = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = sqlite_db_path.with_suffix(sqlite_db_path.suffix + f".bak-{ts}")
    shutil.copy2(sqlite_db_path, backup_path)
    logger.info(f"SQLite DB backup created: {backup_path}")
    return backup_path


def upload_one(
    client: storage.Client,
    bucket_name: str,
    local_path: Path,
    object_key: str,
    overwrite: bool,
    logger: logging.Logger,
) -> Tuple[bool, Optional[str]]:
    """
    Returns (success, error_message).
    """
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(object_key)

    try:
        if not overwrite:
            if blob.exists():
                logger.info(f"Skip upload (exists, --no-overwrite): gs://{bucket_name}/{object_key}")
                return True, None

        blob.upload_from_filename(str(local_path))
        return True, None
    except Exception as e:
        return False, str(e)


def bool_optional_action_available() -> bool:
    return hasattr(argparse, "BooleanOptionalAction")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Migrate Bookworm local images to GCS and update SQLite storage_path."
    )

    # Use env defaults, allow overrides
    parser.add_argument(
        "--bucket",
        default=None,
        help="GCS bucket name (no gs:// prefix). Defaults to env GCS_BUCKET.",
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="SQLAlchemy DB URL. Defaults to env DATABASE_URL, else sqlite:///./local_store/bookworm.db",
    )
    parser.add_argument(
        "--raw-dir",
        default=None,
        help="Path to raw images dir (default: repo_root/local_store/raw).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="No uploads and no DB writes. Still prints what would happen.",
    )

    if bool_optional_action_available():
        parser.add_argument(
            "--overwrite",
            action=argparse.BooleanOptionalAction,
            default=True,
            help="Overwrite objects in GCS if they already exist (default: overwrite). Use --no-overwrite to disable.",
        )
    else:
        # Fallback for older Python: --overwrite / --no-overwrite
        parser.add_argument(
            "--overwrite",
            dest="overwrite",
            action="store_true",
            default=True,
            help="Overwrite objects in GCS if they already exist (default: overwrite).",
        )
        parser.add_argument(
            "--no-overwrite",
            dest="overwrite",
            action="store_false",
            help="Do not overwrite objects in GCS if they already exist.",
        )

    parser.add_argument(
        "--backup",
        action="store_true",
        help="Create a timestamped backup copy of the SQLite DB before writing (SQLite only).",
    )
    parser.add_argument(
        "--report-json",
        default=None,
        help="Optional path to write a JSON report (e.g., reports/gcs_migration_report.json).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Verbose logging.",
    )

    args = parser.parse_args()
    logger = setup_logger(args.verbose)

    root = repo_root_from_script()

    bucket = args.bucket or os.environ.get("GCS_BUCKET")
    if not bucket:
        logger.error("Bucket not provided. Use --bucket or set env var GCS_BUCKET.")
        return 2

    default_db_url = "sqlite:///./local_store/bookworm.db"
    database_url = args.database_url or os.environ.get("DATABASE_URL") or default_db_url

    raw_dir = Path(args.raw_dir) if args.raw_dir else (root / "local_store" / "raw")

    logger.info("=== Bookworm GCS Image Migration ===")
    logger.info(f"Repo root: {root}")
    logger.info(f"Raw images dir: {raw_dir}")
    logger.info(f"Bucket: {bucket}")
    logger.info(f"Database URL: {database_url}")
    logger.info(f"Dry run: {args.dry_run}")
    logger.info(f"Overwrite: {args.overwrite}")

    if not raw_dir.exists():
        logger.error(f"Raw images dir not found: {raw_dir}")
        return 2

    logger.info("Reminder: stop your FastAPI app while migrating (avoid SQLite locks).")
    logger.info("Reminder: keep your original DB + images until you verify uploads and DB updates are correct.")
    if not args.dry_run and not args.backup:
        logger.info("Tip: re-run with --backup to create a timestamped copy of your SQLite DB before writing (SQLite only).")

    # Reflect DB table
    try:
        engine, book_image = load_book_image_table(database_url)
    except Exception as e:
        logger.error(f"Failed to reflect 'book_image' table using DATABASE_URL: {e}")
        logger.error("Make sure the table name is exactly 'book_image' and DATABASE_URL points to the right DB.")
        return 3

    # Optional SQLite backup
    sqlite_db_path = parse_sqlite_file_path(database_url, root)
    if args.backup and not args.dry_run:
        if sqlite_db_path is None:
            logger.warning("--backup was requested, but DATABASE_URL is not a file-based sqlite DB. Skipping backup.")
        else:
            if not sqlite_db_path.exists():
                logger.error(f"SQLite DB file not found at: {sqlite_db_path} (from DATABASE_URL)")
                return 2
            backup_sqlite_db(sqlite_db_path, logger)

    # Preload DB rows (for reporting + matching)
    db_rows: Dict[Tuple[Union[int, str], str], dict] = {}
    missing_local_for_db_rows: List[dict] = []

    with engine.connect() as conn:
        rows = conn.execute(select(book_image)).mappings().all()
        for r in rows:
            if "book_id" not in r or "image_type" not in r:
                logger.error("book_image table must have columns: book_id, image_type, storage_path")
                return 3

            book_id = r["book_id"]
            image_type = r["image_type"]
            db_rows[(book_id, image_type)] = dict(r)

            expected_local = raw_dir / str(book_id) / f"{image_type}.jpg"
            if not expected_local.exists():
                missing_local_for_db_rows.append(
                    {
                        "book_id": book_id,
                        "image_type": image_type,
                        "expected_local_path": str(expected_local),
                        "current_storage_path": r.get("storage_path"),
                    }
                )

    # Find local images
    local_images = find_local_images(raw_dir, logger)
    num_found = len(local_images)

    # Match local -> DB
    local_no_db_match: List[dict] = []
    candidates: List[LocalImage] = []

    def find_db_match(book_id_folder_name: str, parsed_book_id: Union[int, str], image_type: str):
        if (parsed_book_id, image_type) in db_rows:
            return (parsed_book_id, image_type)

        if book_id_folder_name.isdigit():
            try:
                as_int = int(book_id_folder_name)
                if (as_int, image_type) in db_rows:
                    return (as_int, image_type)
            except ValueError:
                pass

        if (book_id_folder_name, image_type) in db_rows:
            return (book_id_folder_name, image_type)

        return None

    for img in local_images:
        folder_name = img.path.parent.name
        key = find_db_match(folder_name, img.book_id, img.image_type)
        if key is None:
            local_no_db_match.append(
                {
                    "local_path": str(img.path),
                    "book_id_folder": folder_name,
                    "parsed_book_id": img.book_id,
                    "image_type": img.image_type,
                }
            )
            continue
        candidates.append(img)

    # Prepare GCS client (ADC or GOOGLE_APPLICATION_CREDENTIALS)
    project = os.environ.get("GOOGLE_CLOUD_PROJECT")
    gcs_client = storage.Client(project=project) if project else storage.Client()

    uploaded_ok = 0
    uploaded_failed: List[dict] = []
    db_rows_updated = 0
    db_update_failed: List[dict] = []
    skipped_due_to_image_type: List[dict] = []

    # Process uploads + DB updates
    with engine.begin() as conn:  # transaction
        for img in candidates:
            if img.image_type not in ALLOWED_IMAGE_TYPES:
                skipped_due_to_image_type.append(
                    {"local_path": str(img.path), "image_type": img.image_type, "reason": "unexpected image_type"}
                )
                logger.warning(f"Skipping unexpected image_type '{img.image_type}' at {img.path}")
                continue

            gs_uri = to_gs_uri(bucket, img.object_key)

            if args.dry_run:
                logger.info(f"[DRY RUN] Would upload: {img.path} -> {gs_uri}")
                logger.info(f"[DRY RUN] Would update DB storage_path where (book_id={img.book_id}, image_type={img.image_type})")
                uploaded_ok += 1
                db_rows_updated += 1
                continue

            ok, err = upload_one(
                client=gcs_client,
                bucket_name=bucket,
                local_path=img.path,
                object_key=img.object_key,
                overwrite=args.overwrite,
                logger=logger,
            )
            if not ok:
                logger.error(f"Upload failed: {img.path} -> {gs_uri} | {err}")
                uploaded_failed.append({"local_path": str(img.path), "gs_uri": gs_uri, "error": err})
                continue

            uploaded_ok += 1

            # Update DB storage_path
            try:
                stmt = (
                    update(book_image)
                    .where(book_image.c.book_id == img.book_id)
                    .where(book_image.c.image_type == img.image_type)
                    .values(storage_path=gs_uri)
                )
                res = conn.execute(stmt)
                if res.rowcount and res.rowcount > 0:
                    db_rows_updated += int(res.rowcount)
                else:
                    # Fallback if folder name is digits but img.book_id typing mismatched
                    folder_name = img.path.parent.name
                    if folder_name.isdigit():
                        try:
                            as_int = int(folder_name)
                            stmt2 = (
                                update(book_image)
                                .where(book_image.c.book_id == as_int)
                                .where(book_image.c.image_type == img.image_type)
                                .values(storage_path=gs_uri)
                            )
                            res2 = conn.execute(stmt2)
                            if res2.rowcount and res2.rowcount > 0:
                                db_rows_updated += int(res2.rowcount)
                            else:
                                db_update_failed.append(
                                    {
                                        "book_id": img.book_id,
                                        "image_type": img.image_type,
                                        "gs_uri": gs_uri,
                                        "error": "No matching DB row to update (unexpected; should have matched earlier).",
                                    }
                                )
                        except ValueError:
                            db_update_failed.append(
                                {"book_id": img.book_id, "image_type": img.image_type, "gs_uri": gs_uri, "error": "No matching DB row to update."}
                            )
                    else:
                        db_update_failed.append(
                            {
                                "book_id": img.book_id,
                                "image_type": img.image_type,
                                "gs_uri": gs_uri,
                                "error": "No matching DB row to update (unexpected; should have matched earlier).",
                            }
                        )

            except Exception as e:
                logger.error(f"DB update failed for (book_id={img.book_id}, image_type={img.image_type}): {e}")
                db_update_failed.append(
                    {"book_id": img.book_id, "image_type": img.image_type, "gs_uri": gs_uri, "error": str(e)}
                )

    report = {
        "timestamp": dt.datetime.now().isoformat(),
        "repo_root": str(root),
        "raw_dir": str(raw_dir),
        "bucket": bucket,
        "database_url": database_url,
        "google_cloud_project": project,
        "dry_run": args.dry_run,
        "overwrite": args.overwrite,
        "summary": {
            "local_files_found": num_found,
            "uploaded_successfully" if not args.dry_run else "would_upload_count": uploaded_ok,
            "db_rows_updated" if not args.dry_run else "would_update_count": db_rows_updated,
            "local_files_with_no_matching_db_row": len(local_no_db_match),
            "db_rows_pointing_to_missing_local_files": len(missing_local_for_db_rows),
            "upload_failures": len(uploaded_failed),
            "db_update_failures": len(db_update_failed),
            "skipped_unexpected_image_type": len(skipped_due_to_image_type),
        },
        "details": {
            "local_no_db_match": local_no_db_match,
            "db_rows_missing_local_files": missing_local_for_db_rows,
            "upload_failed": uploaded_failed,
            "db_update_failed": db_update_failed,
            "skipped_unexpected_image_type": skipped_due_to_image_type,
        },
    }

    logger.info("=== Migration Summary ===")
    logger.info(f"Local files found: {report['summary']['local_files_found']}")
    if args.dry_run:
        logger.info(f"Would upload: {report['summary']['would_upload_count']}")
        logger.info(f"Would update DB rows: {report['summary']['would_update_count']}")
    else:
        logger.info(f"Uploaded successfully: {report['summary']['uploaded_successfully']}")
        logger.info(f"DB rows updated: {report['summary']['db_rows_updated']}")
    logger.info(f"Local files with NO matching DB row: {report['summary']['local_files_with_no_matching_db_row']}")
    logger.info(f"DB rows pointing to missing local files: {report['summary']['db_rows_pointing_to_missing_local_files']}")
    logger.info(f"Upload failures: {report['summary']['upload_failures']}")
    logger.info(f"DB update failures: {report['summary']['db_update_failures']}")
    logger.info(f"Skipped unexpected image_type: {report['summary']['skipped_unexpected_image_type']}")

    if args.report_json:
        out_path = Path(args.report_json)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        logger.info(f"Wrote JSON report: {out_path}")

    if uploaded_failed or db_update_failed:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
