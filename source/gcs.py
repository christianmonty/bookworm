# all Google Cloud Storage stuff
# takes images from local storage, puts into GCS
# returns address path of where uploaded image is in GCS


# GET connected to GCS, figure out how to send an image there, named correctly?
# think of automated way to name every other at some point...little applet!

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
LOCAL_STORE = BASE_DIR / "local_store"

#Goal is take bytes from FastAPI upload, write to disk, return "path" string
def upload_image(book_id: int, image_type: str, file_bytes: bytes) -> str:
    if file_bytes in None:
        raise ValueError("file_bytes cannot be None")

    if image_type not in {"cover", "copyright"}:
        raise ValueError("image_type must be 'cover' or 'copywright'")

    #Now need to create path from directory and type signifiers
    out_dir = LOCAL_STORE / "raw" / str(book_id)
    out_dir.mkdir(parents=True, exist_ok=True)

    out_path = out_dir / f"{image_type}.jpg"
    out_path.write_bytes(file_bytes)

    return str(out_path)


    # upload to GCP per following schema:
    # books/{book_id}/{image_type}.jpg # so see bookid, pagetype, jpg

    # do we have to use this schema to take book from local storage to GCS?
    # if so, create path to image from here and make sure that's uploaded to GCS
