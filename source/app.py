# FastAPI entrypoint

# POST /books to create a book row, returns book_id

# POST /books/{book_id}/images/{image_type}
# accepts an upload, calls gcs.upload_image, writes to databaase

# POST /jobs/extract?limit=10
# Calls pipeline.process_batch(limit)
