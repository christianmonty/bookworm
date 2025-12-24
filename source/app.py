# FastAPI entrypoint
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, UploadFile, File, HTTPException, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware

from source import db
from source import gcs

@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()     # runs once on startup
    yield            # app is running
    # can optionally add db shutdown cleanup

app = FastAPI(lifespan=lifespan) # create a FastAPI instance

app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ.get("BOOKWORM_SECRET_KEY", "dev-only-change-me"),
)


# WHERE DO WE INCREMENT THE BOOK_ID IN THE CALL??
# POST /books to create a book row, returns book_id
# path operation function, to create initial book entry
@app.post("/books")
async def create_book():
	book_id = db.create_book()
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
async def upload_book_image(book_id: int, image_type: str, file: UploadFile = File(...)): # Fully understand what syntax means
	if image_type not in {"cover", "copyright"}:
		raise HTTPException(status_code=400, detail="image_type must be 'cover' or 'copyright'")

	file_bytes = await file.read()
	if not file_bytes:
		raise HTTPException(status_code=400, detail="Uploaded file was empty")

	# Upload the photo locally
	gcs_path = gcs.upload_image(book_id, image_type, file_bytes)

	try:
		db.save_image_record(book_id, image_type, gcs_path)
	except ValueError as e:
		raise HTTPException(status_code=404, detail=str(e))

	if image_type == "cover":
		db.mark_status(book_id, "cover_uploaded")
	elif image_type == "copyright":
		db.mark_status(book_id, "images_uploaded")

	return {"book_id": book_id, "image_type": image_type, "gcs_path": gcs_path}


# POST /jobs/extract?limit=10 point is to do the OpenAI calls in batches
# Calls pipeline.process_batch(limit)


# Rendering the capture page in HTML

def _render_capture_page(bin_value, current_book_id, progress=None, message: str | None = None) -> str:
    # minimal inline HTML (no templates needed)
    # keep it simple and big-button friendly
    cond_options = ["new", "like_new", "very_good", "good", "acceptable"]

    def cond_select():
        opts = "\n".join([f'<option value="{c}">{c}</option>' for c in cond_options])
        return f"""
        <label style="font-size:18px;">Condition (locks once set)</label><br/>
        <select name="condition" required style="font-size:20px; padding:10px; width:100%;">
          <option value="" selected disabled>Select…</option>
          {opts}
        </select><br/><br/>
        """

    msg_html = f"<p style='color:#b00; font-size:18px;'><b>{message}</b></p>" if message else ""

    # State 1: no bin
    if bin_value is None:
        return f"""
        <html><body style="max-width:520px; margin:20px; font-family:system-ui;">
          <h1>Bookworm Capture</h1>
          {msg_html}
          <form method="post" action="/capture/bin">
            <label style="font-size:18px;">Select bin (numeric)</label><br/>
            <input name="bin" type="number" min="1" required style="font-size:24px; padding:12px; width:100%;"/><br/><br/>
            <button type="submit" style="font-size:26px; padding:16px; width:100%;">Set bin</button>
          </form>
        </body></html>
        """

    # State 2: have bin, no current book
    if current_book_id is None:
        return f"""
        <html><body style="max-width:520px; margin:20px; font-family:system-ui;">
          <h1>Bookworm Capture</h1>
          {msg_html}
          <p style="font-size:18px;">Bin: <b>{bin_value}</b></p>
          <form method="post" action="/capture/next">
            <button type="submit" style="font-size:28px; padding:18px; width:100%;">Start next book</button>
          </form>
          <hr/>
          <form method="post" action="/capture/clear_bin">
            <button type="submit" style="font-size:18px; padding:12px; width:100%;">Change bin</button>
          </form>
        </body></html>
        """

    # State 3: in-progress book
    has_cover = progress["has_cover"]
    has_copyright = progress["has_copyright"]
    condition = progress["condition"]

    cover_line = "✅ cover uploaded" if has_cover else "❌ cover missing"
    copy_line = "✅ copyright uploaded" if has_copyright else "❌ copyright missing"
    cond_line = condition if condition else "not set"

    # Decide which upload form to show (enforce order: cover then copyright)
    upload_html = ""
    if not has_cover:
        upload_html = f"""
        <h2>Step 1: Upload cover</h2>
        <form method="post" action="/capture/upload/cover" enctype="multipart/form-data">
          {cond_select() if condition is None else ""}
          <input type="file" name="file" accept="image/*" capture="environment"
                 required style="font-size:18px; width:100%;"/><br/><br/>
          <button type="submit" style="font-size:26px; padding:16px; width:100%;">Upload cover</button>
        </form>
        """
    elif not has_copyright:
        upload_html = f"""
        <h2>Step 2: Upload copyright</h2>
        <form method="post" action="/capture/upload/copyright" enctype="multipart/form-data">
          {cond_select() if condition is None else ""}
          <input type="file" name="file" accept="image/*" capture="environment"
                 required style="font-size:18px; width:100%;"/><br/><br/>
          <button type="submit" style="font-size:26px; padding:16px; width:100%;">Upload copyright</button>
        </form>
        """
    else:
        upload_html = f"""
        <h2>All set</h2>
        <form method="post" action="/capture/finish">
          <button type="submit" style="font-size:28px; padding:18px; width:100%;">Finish book</button>
        </form>
        """

    return f"""
    <html><body style="max-width:520px; margin:20px; font-family:system-ui;">
      <h1>Bookworm Capture</h1>
      {msg_html}
      <p style="font-size:18px;">Bin: <b>{bin_value}</b></p>
      <p style="font-size:18px;">Current book: <b>{current_book_id}</b></p>

      <div style="padding:12px; border:1px solid #ddd; border-radius:12px;">
        <p style="font-size:18px; margin:6px 0;">{cover_line}</p>
        <p style="font-size:18px; margin:6px 0;">{copy_line}</p>
        <p style="font-size:18px; margin:6px 0;">Condition: <b>{cond_line}</b></p>
      </div>

      <hr/>
      {upload_html}

      <hr/>
      <form method="post" action="/capture/abandon">
        <button type="submit" style="font-size:18px; padding:12px; width:100%;">Abandon current book</button>
      </form>
    </body></html>
    """


#-----Capture Routes Detailed Below---------

@app.get("/capture", response_class=HTMLResponse)
async def capture(request: Request):
    bin_value = request.session.get("bin")
    current_book_id = request.session.get("current_book_id")
    message = request.session.pop("capture_msg", None)

    progress = None
    if current_book_id is not None:
        try:
            progress = db.get_book_progress(current_book_id)
        except ValueError:
            # stale session -> clear
            request.session.pop("current_book_id", None)
            current_book_id = None

    html = _render_capture_page(bin_value, current_book_id, progress=progress, message=message)
    return HTMLResponse(content=html)


@app.post("/capture/bin")
async def set_bin(request: Request, bin: int = Form(...)):
    request.session["bin"] = bin
    return RedirectResponse(url="/capture", status_code=303)


@app.post("/capture/clear_bin")
async def clear_bin(request: Request):
    request.session.pop("bin", None)
    return RedirectResponse(url="/capture", status_code=303)


@app.post("/capture/next")
async def next_book(request: Request):
    bin_value = request.session.get("bin")
    if bin_value is None:
        request.session["capture_msg"] = "Select a bin first."
        return RedirectResponse(url="/capture", status_code=303)

    # If a book is already in progress, don't start a new one
    if request.session.get("current_book_id") is not None:
        request.session["capture_msg"] = "Finish or abandon the current book first."
        return RedirectResponse(url="/capture", status_code=303)

    book_id = db.create_book(bin=bin_value, status="in_progress")
    request.session["current_book_id"] = book_id
    return RedirectResponse(url="/capture", status_code=303)


@app.post("/capture/upload/{image_type}")
async def capture_upload(
    request: Request,
    image_type: str,
    file: UploadFile = File(...),
    condition: str | None = Form(None),
):
    if image_type not in {"cover", "copyright"}:
        request.session["capture_msg"] = "Invalid image type."
        return RedirectResponse(url="/capture", status_code=303)

    book_id = request.session.get("current_book_id")
    if book_id is None:
        request.session["capture_msg"] = "No active book. Start next book first."
        return RedirectResponse(url="/capture", status_code=303)

    file_bytes = await file.read()
    if not file_bytes:
        request.session["capture_msg"] = "Uploaded file was empty."
        return RedirectResponse(url="/capture", status_code=303)

    # If condition isn't set yet, require it (locked once set)
    progress = db.get_book_progress(book_id)
    if progress["condition"] is None:
        if not condition:
            request.session["capture_msg"] = "Pick a condition (it locks once set)."
            return RedirectResponse(url="/capture", status_code=303)
        try:
            db.set_condition_once(book_id, condition)
        except ValueError as e:
            request.session["capture_msg"] = str(e)
            return RedirectResponse(url="/capture", status_code=303)

    # Save locally and record in DB (reuse your existing pattern)
    gcs_path = gcs.upload_image(book_id, image_type, file_bytes)
    db.save_image_record(book_id, image_type, gcs_path)

    # Optional: keep your status semantics
    if image_type == "cover":
        db.mark_status(book_id, "cover_uploaded")
    else:
        db.mark_status(book_id, "images_uploaded")

    return RedirectResponse(url="/capture", status_code=303)


@app.post("/capture/finish")
async def capture_finish(request: Request):
    book_id = request.session.get("current_book_id")
    if book_id is None:
        request.session["capture_msg"] = "No active book."
        return RedirectResponse(url="/capture", status_code=303)

    progress = db.get_book_progress(book_id)
    if not (progress["has_cover"] and progress["has_copyright"]):
        request.session["capture_msg"] = "Need both cover + copyright before finishing."
        return RedirectResponse(url="/capture", status_code=303)

    db.mark_status(book_id, "complete")
    request.session.pop("current_book_id", None)
    return RedirectResponse(url="/capture", status_code=303)


@app.post("/capture/abandon")
async def capture_abandon(request: Request):
    book_id = request.session.get("current_book_id")
    if book_id is not None:
        db.mark_status(book_id, "abandoned")
    request.session.pop("current_book_id", None)
    return RedirectResponse(url="/capture", status_code=303)
