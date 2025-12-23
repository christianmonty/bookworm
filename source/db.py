# All DB related stuff
import os
from contextlib import contextmanager # what is this for?
from typing import Optional, Dict, Any, List # what is this for?

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
class BookImages(Base):
    __tablename__ = "book_image"

    id = Column(Integer, primary_key=True, autoincrement=True)

    book_id = Column(Integer, ForeignKey("books.id", ondelete="CASCADE"), nullable=False)

    image_type = Column(String, nullable=False) #cover or copywright

    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(),)

    #below permits doing book.images
    images = relationship("Book", back_populates="images",)

    #enforcing no duplicates in Images table
    __table_args__ = (UniqueConstraint("book_id", "image_type", name="uq_book_image_type"),)



# change below to Postgres (Cloud SQL) later
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./local_store/bookworm.db")

#DB engine is connection factory SQLAlchemy uses under the hood
# VERIFY PURPOSE OF THIS BELOW
connect_args = {"check_same_thread" : False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)

#Session is unit of work for queries, inserts, and commits
# VERIFY PURPOSE OF THIS BELOW
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


#Create a Database connection/engine
# init_db to set up (create tables) if don't exist
def init_db():
    #run once at startup or manually for MVP
    Base.metadata.create_all(bind=engine) # what does this do?


@contextmanager
def get_session() -> Session:
    # Minimal session manager which closes DB connection after
    session = SessionLocal()
    try:
	yield session
	session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# Helper functions: Do we just use SQL to implement these?
def create_book(book_id: int) -> int:

    # include some sort of error check if book_id already in table!


    # create initial book entry in books table with status 'created'
    sql_query = """
    INSERT INTO books
    VALUES (book_id, "created", CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);
    """
    pass
    return book_id

def save_image_record(book_id: int, image_type: str, gcs_path: str) -> int:
    # save image record into book_images table

	#include some sort of error check if book_id & image_type present!
    sql_query = """
    INSERT INTO book_images
    VALUES (book_id, image_type, gcs_path, CURRENT_TIMESTAMP)
    """
    pass

def save_extraction(book_id: int, metadata: dict) -> int:
    # save extracted metadata into books table

    sql_query = """
    UPDATE books
    # Need to add for-loop here for metadata into database, match up fields #
    # manually I guess
    """
    pass

def mark_status(book_id: int, status: str) -> int:
    sql_query = """
    UPDATE books
    SET status = status, timestamp_updated = CURRENT_TIMESTAMP
    WHERE id = book_id;
    """
    # Double check this all makes sense, including CURRENT_TIMESTAMP
    # created, images_uploaded, extracted, on_eBay, SOLD, shipped, error
    pass

def load_path(book_id: int, image_type: str) -> str:
    sql_query = """
    SELECT gcs_path
    FROM book_images
    WHERE book_id = book_id AND image_type = image_type;
    """
    pass

def read_entry(book_id: int) -> dict:
    sql_entry = """
    SELECT *
    FROM books
    WHERE book_id = book_id;
    """

    return {}
