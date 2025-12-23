# all Google Cloud Storage stuff
# takes images from local storage, puts into GCS
# returns address of where uploaded image is in GCS

def upload_image(book_id, image_type, file_bytes) -> gcs_path:
    if file_bytes in None:
        raise ValueError("file_bytes cannot be None")

    # upload to GCP per following schema:
    # books/{book_id}/{image_type}.jpg # so see bookid, pagetype, jpg

    # do we have to use this schema to take book from local storage to GCS?
    # if so, create path to image from here and make sure that's uploaded to GCS

    # Implementation specific to GCP to go here

    pass
