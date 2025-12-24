# Orchestrates steps of ingesting image and writing metadata into Postgres
from source import extraction
from source import db

# 12/23 TOMORROW: CONCPETUALLY THIS SEEMS OK, BUT VERIFY ORDER/CALLS

def process_book(book_id: int) -> int:
	# load image paths from DB book_images table
	path1 = db.load_paths(book_id, "cover")
	path2 = db.load_paths(book_id, "copywrite")

	# pull 2 images for book_id from GCS
	image1 = None # query to GCS per bookid for cover image
	image2 = None # query to GCS per bookid for copyright image

	# function call  to return metadata (JSON/dict)
	book_data = extraction.process_images(book_id, image1, image2)

	# then write metadata into Postgres
	res = db.save_extraction(book_id, book_data)
	if res:
		return -1
	# then check written to Postgres correctly
	entry = db.read_entry(book_id)

	#then print out the values in hashmap elegantly (for now)
	#or add some assertion to check that certain fields or not None


	# make sure to return error message on failure or 0 for success

	return 0


# def process_batch(limit=...)
