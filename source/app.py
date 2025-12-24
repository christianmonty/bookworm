# FastAPI entrypoint
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from contextlib import asynccontextmanager
from fastapi import FastAPI, UploadFile, File, HTTPException

from source import db
from source import gcs

@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()     # runs once on startup
    yield            # app is running
    # can optionally add db shutdown cleanup

app = FastAPI(lifespan=lifespan) # create a FastAPI instance


# WHERE DO WE INCREMENT THE BOOK_ID IN THE CALL??
# POST /books to create a book row, returns book_id
# path operation function, to create initial book entry
@app.post("/books")
async def create_book():
	book_id = db.create_book(book_id)
	return {"book_id": book_id}

@app.get("/books/{book_id}")
async def get_entry(book_id: int): # path operation function, async means not block main thread
	# Need to get book entry from Postgres here and return it
	try:
		return db.read_entry(book_id)
	except ValueError as e:
		raise HTTPException(status_code=404, detail=str(e))




# POST /books/{book_id}/images/{image_type}
# accepts an upload, calls gcs.upload_image, writes to db for images
@app.post("/books/{book_id}/images/{image_type}")
async def upload_book_image(book_id: int, image_type: str, file: UploadFile = File(...)):
	gcs_path = gcs.upload_image(book_id, image_type, file.file.read())

	ret = db.save_image_record(int, str, gcs_path)
	if not ret:
		raise ValueError(f"Image for {book_id} not saved correctly")

	if image_type == "cover":
		db.mark_status(book_id, "cover_uploaded")
	elif image_type = "copywright"
		db.mark_status(book_id, "images_uploaded")

	return {"gcs_path": gcs_path}


# POST /jobs/extract?limit=10 point is to do the OpenAI calls in batches
# Calls pipeline.process_batch(limit)
