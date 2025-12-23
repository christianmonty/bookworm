# all Google Cloud Storage stuff
# takes images from local storage, puts into GCS
# returns address of where uploaded image is in GCS

#12/23 TOMORROW: figure out what local path to save (or Google photos lol w/naming...
# GET connected to GCS, figure out how to send an image there, named correctly?

# think of automated way to name every other at some point...

def upload_image(book_id, image_type, file_bytes) -> gcs_path:
    if file_bytes in None:
        raise ValueError("file_bytes cannot be None")

    # upload to GCP per following schema:
    # books/{book_id}/{image_type}.jpg # so see bookid, pagetype, jpg

    # do we have to use this schema to take book from local storage to GCS?
    # if so, create path to image from here and make sure that's uploaded to GCS

    # Implementation specific to GCP to go here

    pass
