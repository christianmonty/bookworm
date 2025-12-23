# FastAPI entrypoint
from fastapi import FastAPI
from source import gcs
from source import db

app = FastAPI() # create a FastAPI instance


@app.get("/books")
async def get_entry(book_id: int): # path operation function, async means not block main thread
	# Need to get book entry from Postgres here and return it

	return db.read_entry(book_id)


# WHERE DO WE INCREMENT THE BOOK_ID IN THE CALL??

# POST /books to create a book row, returns book_id
# path operation function, to create initial book entry
@app.post("/books")
async def create_book(book_id: int):

	return db.create_book(book_id)

# POST /books/{book_id}/images/{image_type}
# accepts an upload, calls gcs.upload_image, writes to db for images
# DOUBLE CHECK FILE UPLOAD STUFF HERE IS CORRECT
@app.post("/books/{book_id}/images/{image_type}")
async def upload_book_image(book_id: int, image_type: str, file: UploadFile = File(...)):
	gcs_path = gcs.upload_image(book_id, image_type, file.file.read())

	return {"gcs_path": gcs_path}


# POST /jobs/extract?limit=10 point is to do the OpenAI calls in batches
# Calls pipeline.process_batch(limit)
