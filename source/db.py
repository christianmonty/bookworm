# All DB related stuff
from pathlib import Path
import os
from contextlib import contextmanager # what is this for?
from typing import Iterator, Dict, Any, List # what is this for?

from sqlalchemy import (
    Column, Integer, String, DateTime, ForeignKey, UniqueConstraint, func,)
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker, Session, joinedload, declarative_base, relationship


Base = declarative_base()

# books schema: books: id, status, timestamp_created, timestamp_updated
class Book(Base):
    __tablename__ = "books"

    id = Column(Integer, primary_key=True, autoincrement=True)

    status = Column(String, nullable=False, default="created")

    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(),)

    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now(),)

    #below permits doing book.images
    images = relationship(
        "BookImage",
        back_populates="book",
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



def create_book() -> int:

    # include some sort of error check if book_id already in table!
    with get_session() as session:
        book = Book() #tbd if initializer needed since default
        session.add(book)
        session.flush() # pusehs to DB
        return book.id

    # create initial book entry in books table with status 'created'
    # sql_query = """ INSERT INTO books VALUES (book_id, "created", CURRENT_TIMESTAMP, CURRENT_TIMESTAMP); """


def save_image_record(book_id: int, image_type: str, gcs_path: str) -> int:
    # save image record into book_images table
    # if pair (book_id, image_type) exists, update storage_path
    # otherwise, insert a new record to BookImage db
    if image_type not in {"cover", "copywright"}:
        raise ValueError("image_type must be 'cover' or 'copywright'")

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
            existing.storage_path = gcs_path
            session.flush()
            return existing.id

        img = BookImage(
            book_id=book_id,
            image_type=image_type,
            storage_path=gcs_path,
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
            "created_at": book.created_at.isoformat() if book.created_at else None,
            "updated_at": book.updated_at.isoformat() if book.updated_at else None,
            "images": images,
        }

# sql_entry = """SELECT * FROM books WHERE book_id = book_id; """

def save_extraction(book_id: int, metadata: dict) -> int:
    # save extracted metadata into books table

    # TBD implemented
    raise NotImplementedError("Add extraction columns/table before implementing this.")

# sql_query = """UPDATE books
# Need to add for-loop here for metadata into database, match up fields #
# manually I guess
