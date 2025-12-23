# Orchestrates steps of ingesting image and writing metadata into Postgres
from source import extraction


def process_book(book_id: int) -> int:
	#will do all the steps and then call extract.py

	# create initial entry in DB for book_id

	# pull 2 images for book_id from GCS
	image1 = None # query to GCS per bookid for cover image
	image2 = None # query to GCS per bookid for copyright image

	# function call  to return metadata (JSON/dict)
	book_data = extraction.process_images(book_id, image1, image2)

	# then write metadata into Postgres

	# then check written to Postgres correctly



	# make sure to return error message on failure or 0 for success

	return 0


# def process_batch(limit=...)
