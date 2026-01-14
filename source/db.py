# All DB related stuff
from pathlib import Path
import os, json
from contextlib import contextmanager # what is this for?
from typing import Iterator, Dict, Any, List # what is this for?

from sqlalchemy import (
    Boolean, Column, Integer, String, DateTime, ForeignKey, UniqueConstraint, func, Text, Float,)
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker, Session, joinedload, declarative_base, relationship


Base = declarative_base()

# books schema: books: id, status, timestamp_created, timestamp_updated
class Book(Base):
    __tablename__ = "books"

    id = Column(Integer, primary_key=True, autoincrement=True)
    status = Column(String, nullable=False, default="created")

    bin = Column(Integer, nullable=True)
    condition = Column(String, nullable=True)
    owner = Column(String, nullable=True)

    jacket_included = Column(Boolean, nullable=True)
    notes = Column(String(200), nullable=True)

    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(),)

    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now(),)

    #below permits doing book.images
    images = relationship(
        "BookImage",
        back_populates="book",
        cascade="all, delete-orphan",
    )

    extraction = relationship(
        "BookExtraction",
        back_populates="book",
        uselist=False,
        cascade="all, delete-orphan",
    )


# bookimages schema: book_id, image_type, gcs_path, timestamp_uploaded
class BookImage(Base):
    __tablename__ = "book_image"

    id = Column(Integer, primary_key=True, autoincrement=True)

    book_id = Column(Integer, ForeignKey("books.id", ondelete="CASCADE"), nullable=False)

    image_type = Column(String, nullable=False) #cover or copyright

    storage_path = Column(String, nullable=False) #path for file in storage

    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(),)

    book = relationship("Book", back_populates="images",)

    #enforcing no duplicates in Images table
    __table_args__ = (UniqueConstraint("book_id", "image_type", name="uq_book_image_type"),)

# This table for Book Extraction fields
class BookExtraction(Base):
    __tablename__ = "book_extraction"

    id = Column(Integer, primary_key=True, autoincrement=True)

    book_id = Column(Integer, ForeignKey("books.id", ondelete="CASCADE"), nullable=False, unique=True)

    status = Column(String, nullable=False, default="pending")  # pending|done|needs_review|error

    isbn10 = Column(String, nullable=True)
    isbn13 = Column(String, nullable=True)

    title = Column(String, nullable=True)
    author = Column(String, nullable=True)

    confidence = Column(Float, nullable=True)
    flags_json = Column(Text, nullable=True)   # store JSON string
    data_json = Column(Text, nullable=True)    # store full JSON string

    model = Column(String, nullable=True)
    error = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    book = relationship("Book", back_populates="extraction")


# change below to Postgres (Cloud SQL) later
BASE_DIR = Path(__file__).resolve().parents[1]   # repo root if db.py is in /source
LOCAL_STORE = BASE_DIR / "local_store"
LOCAL_STORE.mkdir(parents=True, exist_ok=True)

default_sqlite_url = f"sqlite:///{(LOCAL_STORE / 'bookworm.db').as_posix()}"


DATABASE_URL = os.environ.get("DATABASE_URL", default_sqlite_url)

#DB engine is connection factory SQLAlchemy uses under the hood
# VERIFY PURPOSE OF THIS BELOW
connect_args = {"check_same_thread" : False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)

#Session is unit of work for queries, inserts, and commits
# VERIFY PURPOSE OF THIS BELOW
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


#Create a Database connection/engine
# init_db to set up (create tables) if don't exist
def init_db() -> None:
    #run once at startup or manually for MVP
    Base.metadata.create_all(bind=engine) # what does this do?


@contextmanager
def get_session() -> Iterator[Session]:
    """
    Minimal session manager that guarantees closing the DB connection.
    """
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


CONDITIONS = {"new", "like_new", "very_good", "good", "acceptable"} # tbd update

def create_book(bin: int | None = None, status: str = "created") -> int:

    with get_session() as session:
        book = Book(bin=bin, status=status)
        session.add(book)
        session.flush() # pushes to DB
        return book.id

    # create initial book entry in books table with status 'created'
    # sql_query = """ INSERT INTO books VALUES (book_id, "created", CURRENT_TIMESTAMP, CURRENT_TIMESTAMP); """

def set_condition_once(book_id: int, condition: str) -> None:
    if condition not in CONDITIONS:
        raise ValueError(f"Invalid condition: {condition}")

    with get_session() as session:
        book = session.get(Book, book_id)
        if book is None:
            raise ValueError(f"Book {book_id} not found")

        if book.condition is None:
            book.condition = condition
            session.flush()

def save_image_record(book_id: int, image_type: str, storage_path: str) -> int:
    # save image record into book_images table
    # if pair (book_id, image_type) exists, update storage_path
    # otherwise, insert a new record to BookImage db
    if image_type not in {"cover", "copyright"}:
        raise ValueError("image_type must be 'cover' or 'copyright'")

    with get_session() as session:
        # Ensure the book exists
        book = session.get(Book, book_id)
        if book is None:
            raise ValueError(f"Book {book_id} not found")

        existing = session.execute(
            select(BookImage).where(
                BookImage.book_id == book_id,
                BookImage.image_type == image_type,
            )
        ).scalar_one_or_none()

        if existing:
            existing.storage_path = storage_path
            session.flush()
            return existing.id

        img = BookImage(
            book_id=book_id,
            image_type=image_type,
            storage_path=storage_path,
        )
        session.add(img)
        session.flush()
        return img.id

# sql_query = """ INSERT INTO book_images VALUES (book_id, image_type, gcs_path, CURRENT_TIMESTAMP) """

def mark_status(book_id: int, status: str) -> int:
    # updates the books status in Book DB
    # created, cover_uploaded, image_uploaded, extracted, on_eBay, SOLD, shipped, error
    with get_session() as session:
        book = session.get(Book, book_id)
        if book is None:
            raise ValueError(f"Book {book_id} not found")

        book.status = status
        session.flush()

# sql_query = """UPDATE books SET status = status, timestamp_updated = CURRENT_TIMESTAMP WHERE id = book_id; """

def load_path(book_id: int, image_type: str) -> str:
    # Returns storage_path for a given pair (book_id, image_type) or None
    with get_session() as session:
        img = session.execute(
            select(BookImage).where(
                BookImage.book_id == book_id,
                BookImage.image_type == image_type,
            )
        ).scalar_one_or_none()

        return None if img is None else img.storage_path
# sql_query = """SELECT gcs_path FROM book_images WHERE book_id = book_id AND image_type = image_type; """

def read_entry(book_id: int) -> Dict[str, Any]:
    #Returns a JSON-friendly dict for book + its images, use for debugging
    with get_session() as session:
        book = (session.execute(
            select(Book)
            .options(joinedload(Book.images))
            .where(Book.id == book_id)
        )
	.unique()
	.scalar_one_or_none()
	)

        if book is None:
            raise ValueError(f"Book {book_id} not found")

        images: List[Dict[str, Any]] = []
        for img in book.images:
            images.append(
                {
                    "id": img.id,
                    "image_type": img.image_type,
                    "storage_path": img.storage_path,
                    "created_at": img.created_at.isoformat() if img.created_at else None,
                }
            )

        return {
            "id": book.id,
            "status": book.status,
            "bin": book.bin,
            "owner": book.owner,
	    "condition": book.condition,
	    "jacket_included": book.jacket_included,
        "notes": book.notes,
            "created_at": book.created_at.isoformat() if book.created_at else None,
            "updated_at": book.updated_at.isoformat() if book.updated_at else None,
            "images": images,
        }

# sql_entry = """SELECT * FROM books WHERE book_id = book_id; """

def get_book_progress(book_id: int) -> dict:
    """Returns which images exist and current condition/status/bin."""
    with get_session() as session:
        book = (session.execute(
            select(Book).options(joinedload(Book.images)).where(Book.id == book_id)
        ).unique().scalar_one_or_none())

        if book is None:
            raise ValueError(f"Book {book_id} not found")

        types = {img.image_type for img in book.images}
        return {
            "id": book.id,
            "status": book.status,
            "bin": book.bin,
            "owner": book.owner,
            "condition": book.condition,
            "jacket_included": book.jacket_included,
            "notes": book.notes,
            "has_cover": "cover" in types,
            "has_copyright": "copyright" in types,
        }

def set_jacket_once(book_id: int, jacket_included: bool) -> None:
    with get_session() as session:
        book = session.get(Book, book_id)
        if book is None:
            raise ValueError(f"Book {book_id} not found")

        if book.jacket_included is None:
            book.jacket_included = jacket_included
            session.flush()


def set_notes(book_id: int, notes: str | None) -> None:
    if notes is None:
        return
    notes = notes.strip()
    if not notes:
        return
    if len(notes) > 200:
        raise ValueError("Notes must be 200 characters or fewer")

    with get_session() as session:
        book = session.get(Book, book_id)
        if book is None:
            raise ValueError(f"Book {book_id} not found")

        # Allow updating notes during capture (easy + forgiving)
        book.notes = notes
        session.flush()

def set_owner(book_id: int, owner: str) -> None:
    with get_session() as session:
        book = session.get(Book, book_id)
        if book is None:
            raise ValueError(f"Book {book_id} not found")
        book.owner = owner
        session.flush()


# To prepare books for extraction
def get_books_ready_for_extraction(limit: int | None = None, force: bool = False) -> List[int]:
    """
    Books that have BOTH images.
    - If force=False: exclude books whose extraction status is 'done'
    - If force=True: include them anyway (re-run)
    - If limit is None: return all
    """
    with get_session() as session:
        subq_cover = select(BookImage.book_id).where(BookImage.image_type == "cover").subquery()
        subq_copy = select(BookImage.book_id).where(BookImage.image_type == "copyright").subquery()

        q = (
            select(Book.id)
            .where(Book.id.in_(select(subq_cover.c.book_id)))
            .where(Book.id.in_(select(subq_copy.c.book_id)))
            .order_by(Book.id.asc())
        )

        book_ids = [row[0] for row in session.execute(q).all()]

        out: List[int] = []
        for bid in book_ids:
            ex = session.execute(
                select(BookExtraction).where(BookExtraction.book_id == bid)
            ).scalar_one_or_none()

            if force or ex is None or ex.status != "done":
                out.append(bid)
                if limit is not None and len(out) >= limit:
                    break

        return out



def upsert_extraction(
    book_id: int,
    status: str,
    isbn10: str | None,
    isbn13: str | None,
    title: str | None,
    author: str | None,
    confidence: float | None,
    flags: list[str] | None,
    data: dict | None,
    model: str | None = None,
    error: str | None = None,
) -> None:
    with get_session() as session:
        book = session.get(Book, book_id)
        if book is None:
            raise ValueError(f"Book {book_id} not found")

        existing = session.execute(
            select(BookExtraction).where(BookExtraction.book_id == book_id)
        ).scalar_one_or_none()

        flags_json = None if flags is None else json.dumps(flags)
        data_json = None if data is None else json.dumps(data)

        if existing:
            existing.status = status
            existing.isbn10 = isbn10
            existing.isbn13 = isbn13
            existing.title = title
            existing.author = author
            existing.confidence = confidence
            existing.flags_json = flags_json
            existing.data_json = data_json
            existing.model = model
            existing.error = error
            session.flush()
            return

        ex = BookExtraction(
            book_id=book_id,
            status=status,
            isbn10=isbn10,
            isbn13=isbn13,
            title=title,
            author=author,
            confidence=confidence,
            flags_json=flags_json,
            data_json=data_json,
            model=model,
            error=error,
        )
        session.add(ex)
        session.flush()


def list_extractions() -> List[Dict[str, Any]]:
    with get_session() as session:
        rows = session.execute(
            select(BookExtraction).order_by(BookExtraction.updated_at.desc())
        ).scalars().all()

        out: List[Dict[str, Any]] = []
        for r in rows:
            out.append({
                "book_id": r.book_id,
                "status": r.status,
                "isbn13": r.isbn13,
                "title": r.title,
                "author": r.author,
                "confidence": r.confidence,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
                "error": r.error,
            })
        return out


def read_extraction(book_id: int) -> Dict[str, Any]:
    import json as _json
    with get_session() as session:
        ex = session.execute(
            select(BookExtraction).where(BookExtraction.book_id == book_id)
        ).scalar_one_or_none()
        if ex is None:
            raise ValueError(f"No extraction for book {book_id}")

        flags = None
        data = None
        try:
            flags = _json.loads(ex.flags_json) if ex.flags_json else None
        except Exception:
            flags = None
        try:
            data = _json.loads(ex.data_json) if ex.data_json else None
        except Exception:
            data = None

        return {
            "book_id": ex.book_id,
            "status": ex.status,
            "isbn10": ex.isbn10,
            "isbn13": ex.isbn13,
            "title": ex.title,
            "author": ex.author,
            "confidence": ex.confidence,
            "flags": flags,
            "data": data,
            "model": ex.model,
            "error": ex.error,
            "updated_at": ex.updated_at.isoformat() if ex.updated_at else None,
        }


def override_extraction(book_id: int, isbn10: str | None, isbn13: str | None, title: str | None, author: str | None) -> None:
    """Manual override: set fields + mark status done."""
    def clean(s: str | None) -> str | None:
        if s is None:
            return None
        s = s.strip()
        return s if s else None

    isbn10 = clean(isbn10)
    isbn13 = clean(isbn13)
    title = clean(title)
    author = clean(author)

    with get_session() as session:
        ex = session.execute(
            select(BookExtraction).where(BookExtraction.book_id == book_id)
        ).scalar_one_or_none()

        if ex is None:
            # create a new extraction record if missing
            ex = BookExtraction(book_id=book_id)
            session.add(ex)
            session.flush()

        # Update fields
        ex.isbn10 = isbn10
        ex.isbn13 = isbn13
        ex.title = title
        ex.author = author

        # Mark done + clear error
        ex.status = "done"
        ex.error = None

        # Add a manual flag (preserve existing flags if any)
        flags = []
        try:
            flags = json.loads(ex.flags_json) if ex.flags_json else []
        except Exception:
            flags = []
        if "manual_override" not in flags:
            flags.append("manual_override")
        ex.flags_json = json.dumps(flags)

        session.flush()

