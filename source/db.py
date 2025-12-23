# All DB related stuff

#Create a Database connection/engine
# init_db to set up (create tables)
def init_db():
    pass
# can either do raw SQL here or SQLAlchemy metadata.create_all

# Going to have two tables initially with following schema:
# books table:
# schema: books: id, status, timestamp_created, timestamp_updated

# book_images:
# schema: book_id, image_type, gcs_path, timestamp_uploaded

# TBD: remember to set all future columns as NULL, or do implicitly


# Helper functions: Do we just use SQL to implement these?
def create_book(book_id: int) -> int:

    # include some sort of error check if book_id already in table!


    # create initial book entry in books table with status 'created'
    sql_query = """
    INSERT INTO books
    VALUES (book_id, "created", CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);
    """
    pass

def save_image_record(book_id: int, image_type:str, gcs_path: str) -> int:
    # save image record into book_images table
    pass

def save_extraction(book_id: int, metadata: dict) -> int:
    # save extracted metadata into books table
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
