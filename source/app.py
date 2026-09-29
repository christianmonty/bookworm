# FastAPI entrypoint
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

import os
import json, logging, hashlib
from contextlib import asynccontextmanager
from fastapi import FastAPI, Query, UploadFile, File, HTTPException, Request, Form, Response
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, Response
from starlette.middleware.sessions import SessionMiddleware

from source import db
from source import gcs
from source import extract

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

@app.get("/books/{book_id}/images/{image_type}")
async def serve_book_image(book_id: int, image_type: str):
    if image_type not in {"cover", "copyright"}:
        raise HTTPException(status_code=400, detail="image_type must be 'cover' or 'copyright'")

    storage_path = db.load_path(book_id, image_type)
    if not storage_path:
        raise HTTPException(status_code=404, detail="Image not found in DB")

    try:
        img_bytes = gcs.read_bytes(storage_path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load image: {e}")

    return Response(content=img_bytes, media_type="image/jpeg")

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
	storage_path = gcs.upload_image(book_id, image_type, file_bytes)

	try:
		db.save_image_record(book_id, image_type, storage_path)
	except ValueError as e:
		raise HTTPException(status_code=404, detail=str(e))

	if image_type == "cover":
		db.mark_status(book_id, "cover_uploaded")
	elif image_type == "copyright":
		db.mark_status(book_id, "images_uploaded")

	return {"book_id": book_id, "image_type": image_type, "storage_path": storage_path}


# Rendering the capture page in HTML
STYLE = """
<style>
  body { max-width: 520px; margin: 20px; font-family: system-ui; }
  h1 { margin: 0 0 12px 0; }
  h2 { margin: 0 0 14px 0; }
  .card { padding: 18px; border: 1px solid #ddd; border-radius: 18px; margin: 18px 0; }
  .big { font-size: 30px; padding: 18px; width: 100%; border-radius: 18px; }
  .med { font-size: 24px; padding: 16px; width: 100%; border-radius: 16px; }
  .label { font-size: 22px; display: block; margin-bottom: 10px; }
  .muted { color: #555; font-size: 18px; }
  .spacer { height: 26px; }
  .danger { margin-top: 34px; }
  select, input[type="number"] { font-size: 26px; padding: 16px; width: 100%; border-radius: 16px; }
  /* Big “Choose photo” button pattern */
  .file-input { position: absolute; left: -9999px; }
  .file-button { display: block; text-align: center; border: 2px solid #999; }
</style>
"""

def _render_capture_page(bin_value, owner_value, current_book_id, progress=None, message: str | None = None) -> str:
    cond_options = ["new", "like_new", "very_good", "good", "acceptable"]
    owner_value = owner_value or "Terry"
    sel_terry = "selected" if owner_value == "Terry" else ""
    sel_christian = "selected" if owner_value == "Christian" else ""
    sel_maria = "selected" if owner_value == "Maria" else ""


    def cond_select() -> str:
        opts = "\n".join([f'<option value="{c}">{c}</option>' for c in cond_options])
        return f"""
        <div class="card">
          <label class="label">Condition (locks once set)</label>
          <select name="condition" required>
            <option value="" selected disabled>Select…</option>
            {opts}
          </select>
          <div class="spacer"></div>
          <div class="muted">Pick once. After it’s set, you won’t be asked again.</div>
        </div>
        """

    def upload_form(image_type: str, condition_needed: bool, jacket_val, notes_val) -> str:
        title = "Step 1: Upload cover" if image_type == "cover" else "Step 2: Upload copyright"
        button_text = "Upload cover" if image_type == "cover" else "Upload copyright"

        # Checkbox: only editable if not set yet; otherwise display status
        jacket_block = ""
        if jacket_val is None:
            jacket_block = """
            <div class="card">
            <label class="label">
                <input type="checkbox" name="jacket_included" style="transform:scale(1.6); margin-right:12px;">
                Dust jacket included? (hardcover only)
            </label>
            <div class="muted">If unsure, leave unchecked.</div>
            </div>
            """
        else:
            jacket_text = "Yes" if jacket_val else "No"
            jacket_block = f"""
            <div class="card">
            <div style="font-size:22px;">Dust jacket included: <b>{jacket_text}</b></div>
            <div class="muted">Locked once set.</div>
            </div>
            """

        # Notes: editable during capture; capped at 200 via UI + server validation
        notes_prefill = (notes_val or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
        notes_block = f"""
        <div class="card">
        <label class="label">Notes (library markings, writing inside, any other defects)</label>
        <textarea name="notes" maxlength="200" rows="5"
            style="font-size:22px; padding:16px; width:100%; border-radius:16px;">{notes_prefill}</textarea>
        <div class="muted">Max 200 characters.</div>
        </div>
        """

        return f"""
        <div class="card">
        <h2>{title}</h2>
        <form method="post" action="/capture/upload/{image_type}" enctype="multipart/form-data">
            {cond_select() if condition_needed else ""}

            {jacket_block}
            {notes_block}

            <label class="label">Photo</label>
            <label for="file_{image_type}" class="med file-button" style="width:80%; margin:0 auto; display:block;">
            Choose photo (opens camera)
            </label>
            <input id="file_{image_type}" class="file-input"
                type="file" name="file"
                accept="image/*" capture="environment" required />

            <div class="spacer"></div>
            <div class="muted">After choosing the photo, press upload below.</div>

            <div class="spacer"></div>
            <button type="submit" class="big">{button_text}</button>
        </form>
        </div>
        """


    msg_html = f"<div class='card' style='border-color:#f1b0b7;'><b style='font-size:20px;'>{message}</b></div>" if message else ""

    # -------- State 1: no bin selected --------
    if bin_value is None:
        return f"""
        <html>
          <head>
            <meta name="viewport" content="width=device-width, initial-scale=1"/>
            {STYLE}
          </head>
          <body>
            <h1>Bookworm Capture</h1>
            {msg_html}

            <div class="card">=
              <form method="post" action="/capture/bin">
                <div style="margin:10px 0;">
                    <div style="font-size:20px; margin-bottom:6px;">Bin</div>
                    <input type="number" name="bin" inputmode="numeric" required
                        style="font-size:22px; padding:12px; width:100%;"/>
                </div>

                <div style="margin:10px 0;">
                    <div style="font-size:20px; margin-bottom:6px;">Owner</div>
                    <select name="owner" required style="font-size:22px; padding:12px; width:100%;">
                    <option value="Terry" {sel_terry}>Terry</option>
                    <option value="Christian" {sel_christian}>Christian</option>
                    <option value="Maria" {sel_maria}>Maria</option>
                    </select>
                </div>

                <button type="submit" style="font-size:22px; padding:12px 16px; width:100%;">Set bin</button>
              </form>
            </div>
          </body>
        </html>
        """

    # -------- State 2: bin selected, no current book --------
    if current_book_id is None:
        return f"""
        <html>
          <head>
            <meta name="viewport" content="width=device-width, initial-scale=1"/>
            {STYLE}
          </head>
          <body>
            <h1>Bookworm Capture</h1>
            {msg_html}

            <div class="card">
              <div style="font-size:22px;">Bin: <b>{bin_value}</b></div>
              <div class="spacer"></div>
              <form method="post" action="/capture/next">
                <button type="submit" class="big">Start next book</button>
              </form>
            </div>

            <div class="card">
              <form method="post" action="/capture/clear_bin">
                <button type="submit" class="med">Change bin</button>
              </form>
            </div>
          </body>
        </html>
        """

    # -------- State 3: current book in progress --------
    has_cover = progress["has_cover"]
    has_copyright = progress["has_copyright"]
    condition = progress["condition"]

    cover_line = "✅ cover uploaded" if has_cover else "❌ cover missing"
    copy_line = "✅ copyright uploaded" if has_copyright else "❌ copyright missing"
    cond_line = condition if condition else "not set"

    jacket_display = (
        "unknown" if progress["jacket_included"] is None
        else ("yes" if progress["jacket_included"] else "no")
    )
    notes_display = progress["notes"] or ""

    # Enforce order: cover first, then copyright, then finish
    condition_needed = (condition is None)
    jacket_val = progress.get("jacket_included")
    notes_val = progress.get("notes")

    if not has_cover:
        main_action_html = upload_form("cover", condition_needed, jacket_val, notes_val)
    elif not has_copyright:
        main_action_html = upload_form("copyright", condition_needed, jacket_val, notes_val)

    else:
        main_action_html = f"""
        <div class="card">
          <h2>All set</h2>
          <div class="muted">Both photos are uploaded. You can finish this book.</div>
          <div class="spacer"></div>
          <form method="post" action="/capture/finish">
            <button type="submit" class="big">Finish book</button>
          </form>
        </div>
        """

    return f"""
    <html>
      <head>
        <meta name="viewport" content="width=device-width, initial-scale=1"/>
        {STYLE}
      </head>
      <body>
        <h1>Bookworm Capture</h1>
        {msg_html}

        <div class="card">
          <div style="font-size:22px;">Bin: <b>{bin_value}</b></div>
          <div style="font-size:22px; margin-top:10px;">Current book: <b>{current_book_id}</b></div>
          <div class="spacer"></div>

          <div style="font-size:22px; margin:8px 0;">{cover_line}</div>
          <div style="font-size:22px; margin:8px 0;">{copy_line}</div>
          <div style="font-size:22px; margin:8px 0;">Condition: <b>{cond_line}</b></div>
          <div style="font-size:22px; margin:8px 0;">Jacket included: <b>{jacket_display}</b></div>
          <div style="font-size:22px; margin:8px 0;">Notes: <b>{notes_display if notes_display else "—"}</b></div>
        </div>

        {main_action_html}

        <div class="danger card">
          <form method="post" action="/capture/abandon">
            <button type="submit" class="med" style="border:2px solid #b00;">Abandon current book</button>
          </form>
          <div class="spacer"></div>
          <div class="muted">Use this only if you started a book by accident.</div>
        </div>
      </body>
    </html>
    """



#-----Capture Routes Detailed Below---------

@app.get("/capture", response_class=HTMLResponse)
async def capture(request: Request):
    bin_value = request.session.get("bin")
    owner_value = request.session.get("owner")
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

    html = _render_capture_page(bin_value, owner_value, current_book_id, progress=progress, message=message)
    return HTMLResponse(content=html)


@app.post("/capture/bin")
async def set_bin(request: Request, bin: int = Form(...), owner: str = Form(...)):
    request.session["bin"] = bin
    request.session["owner"] = owner
    return RedirectResponse(url="/capture", status_code=303)


@app.post("/capture/clear_bin")
async def clear_bin(request: Request):
    request.session.pop("bin", None)
    request.session.pop("owner", None)
    return RedirectResponse(url="/capture", status_code=303)


@app.post("/capture/next")
async def next_book(request: Request):
    bin_value = request.session.get("bin")
    owner_value = request.session.get("owner")

    if bin_value is None:
        request.session["capture_msg"] = "Select a bin first."
        return RedirectResponse(url="/capture", status_code=303)

    # If a book is already in progress, don't start a new one
    if request.session.get("current_book_id") is not None:
        request.session["capture_msg"] = "Finish or abandon the current book first."
        return RedirectResponse(url="/capture", status_code=303)

    book_id = db.create_book(bin=bin_value, status="in_progress")
    db.set_owner(book_id, owner_value or "Terry")

    request.session["current_book_id"] = book_id
    return RedirectResponse(url="/capture", status_code=303)


@app.post("/capture/upload/{image_type}")
async def capture_upload(
    request: Request,
    image_type: str,
    file: UploadFile = File(...),
    condition: str | None = Form(None),
    jacket_included: str | None = Form(None),
    notes: str | None = Form(None),
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


    # Jacket: lock once set (treat checkbox as True/False)
    if progress["jacket_included"] is None:
        jacket_bool = True if jacket_included == "on" else False
        db.set_jacket_once(book_id, jacket_bool)

    # Notes: allow updating during capture (up to 200 chars)
    try:
         db.set_notes(book_id, notes)
    except ValueError as e:
        request.session["capture_msg"] = str(e)
        return RedirectResponse(url="/capture", status_code=303)

    # Save photo to GCS and record in DB
    storage_path = gcs.upload_image(book_id, image_type, file_bytes)
    db.save_image_record(book_id, image_type, storage_path)

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

# Below are to get rid of the annoying Safari icon 404 spam (cosmetic)
@app.get("/favicon.ico")
async def favicon():
    return Response(status_code=204)

@app.get("/apple-touch-icon.png")
async def apple_touch_icon():
    return Response(status_code=204)

@app.get("/apple-touch-icon-precomposed.png")
async def apple_touch_icon_precomposed():
    return Response(status_code=204)


# this is to run extraction job
@app.post("/jobs/extract")
async def run_extract_job(limit: int | None = None, force: bool = False):
    book_ids = db.get_books_ready_for_extraction(limit=limit, force=force)
    results = []

    total = len(book_ids)
    for i, book_id in enumerate(book_ids, start=1):
        print(f"[extract] {i}/{total} book_id={book_id}", flush=True)
        cover_path = db.load_path(book_id, "cover")
        copy_path = db.load_path(book_id, "copyright")
        if not cover_path or not copy_path:
            db.upsert_extraction(
                book_id=book_id,
                status="error",
                isbn10=None, isbn13=None, title=None, author=None,
                confidence=None, flags=["missing_images"], data=None,
                model=None, error="Missing cover or copyright image path in DB",
            )
            continue

        try:
            cover_bytes = gcs.read_bytes(cover_path)
            copy_bytes = gcs.read_bytes(copy_path)
            data = extract.extract_from_images(cover_bytes, copy_bytes)

            flags = data.get("flags") or []
            confidence = data.get("confidence")

            # Decide status
            status = "done"
            if data.get("isbn13") is None and data.get("isbn10") is None:
                status = "needs_review"
            if data.get("title") is None or data.get("author") is None:
                status = "needs_review"

            db.upsert_extraction(
                book_id=book_id,
                status=status,
                isbn10=data.get("isbn10"),
                isbn13=data.get("isbn13"),
                title=data.get("title"),
                author=data.get("author"),
                confidence=confidence,
                flags=flags,
                data=data,
                model=data.get("_model"),
                error=None,
            )
            results.append({"book_id": book_id, "status": status})
        except Exception as e:
            db.upsert_extraction(
                book_id=book_id,
                status="error",
                isbn10=None, isbn13=None, title=None, author=None,
                confidence=None, flags=["exception"], data=None,
                model=None, error=str(e),
            )
            results.append({"book_id": book_id, "status": "error", "error": str(e)})

    return {"processed": results}


@app.get("/review/extractions", response_class=HTMLResponse)
async def review_extractions():
    rows = db.list_extractions()

    items = ""
    for r in rows:
        items += (
            f"<div style='border:1px solid #ddd; padding:12px; border-radius:12px; margin:10px 0;'>"
            f"<div><b>Book {r['book_id']}</b> — {r['status']} — conf={r['confidence']}</div>"
            f"<div>ISBN13: {r['isbn13'] or '—'}</div>"
            f"<div>Title: {r['title'] or '—'}</div>"
            f"<div>Author: {r['author'] or '—'}</div>"
            f"<div style='margin-top:8px;'><a href='/review/books/{r['book_id']}/extraction'>Open</a></div>"
            f"</div>"
        )

    return f"""
    <html><body style="max-width:820px; margin:20px; font-family:system-ui;">
      <h1>Extraction Review</h1>
      <p><a href="/capture">Back to capture</a></p>
      {items if items else "<p>No extractions yet.</p>"}
    </body></html>
    """



@app.get("/review/books/{book_id}/extraction", response_class=HTMLResponse)
async def review_extraction_detail(book_id: int):
    ex = db.read_extraction(book_id)
    cover_url = f"/books/{book_id}/images/cover"
    copy_url = f"/books/{book_id}/images/copyright"

    data_pretty = ex["data"]
    import json as _json
    data_str = _json.dumps(data_pretty, indent=2) if data_pretty else "—"

    flags = ex["flags"] or []
    flags_str = ", ".join(flags) if flags else "—"

    # Prefill values for manual override form
    isbn13_val = ex["isbn13"] or ""
    isbn10_val = ex["isbn10"] or ""
    title_val = ex["title"] or ""
    author_val = ex["author"] or ""

    return f"""
    <html><body style="max-width:980px; margin:20px; font-family:system-ui;">
      <h1>Book {book_id} — Extraction</h1>
      <p><a href="/review/extractions">Back</a></p>

      <div style="display:flex; gap:14px; flex-wrap:wrap;">
        <div style="flex:1; min-width:320px;">
          <h3>Cover</h3>
          <img src="{cover_url}" style="max-width:100%; border:1px solid #ddd; border-radius:12px;" />
        </div>
        <div style="flex:1; min-width:320px;">
          <h3>Copyright</h3>
          <img src="{copy_url}" style="max-width:100%; border:1px solid #ddd; border-radius:12px;" />
        </div>
      </div>

      <div style="border:1px solid #ddd; padding:14px; border-radius:12px; margin-top:14px;">
        <div><b>Status:</b> {ex["status"]}</div>
        <div><b>ISBN13:</b> {ex["isbn13"] or "—"}</div>
        <div><b>ISBN10:</b> {ex["isbn10"] or "—"}</div>
        <div><b>Title:</b> {ex["title"] or "—"}</div>
        <div><b>Author:</b> {ex["author"] or "—"}</div>
        <div><b>Confidence:</b> {ex["confidence"] if ex["confidence"] is not None else "—"}</div>
        <div><b>Flags:</b> {flags_str}</div>
      </div>

      <!-- Manual override block -->
      <div style="border:1px solid #ddd; padding:14px; border-radius:12px; margin-top:14px;">
        <h3 style="margin-top:0;">Manual override (mark done)</h3>

        <div style="display:flex; gap:12px; flex-wrap:wrap;">
          <div style="flex:1; min-width:220px;">
            <label>ISBN-13</label><br/>
            <input id="isbn13" value="{isbn13_val}" style="font-size:18px; padding:10px; width:100%;"/>
          </div>
          <div style="flex:1; min-width:220px;">
            <label>ISBN-10</label><br/>
            <input id="isbn10" value="{isbn10_val}" style="font-size:18px; padding:10px; width:100%;"/>
          </div>
        </div>

        <div style="display:flex; gap:12px; flex-wrap:wrap; margin-top:12px;">
          <div style="flex:1; min-width:220px;">
            <label>Title</label><br/>
            <input id="title" value="{title_val}" style="font-size:18px; padding:10px; width:100%;"/>
          </div>
          <div style="flex:1; min-width:220px;">
            <label>Author</label><br/>
            <input id="author" value="{author_val}" style="font-size:18px; padding:10px; width:100%;"/>
          </div>
        </div>

        <div style="margin-top:14px;">
          <button id="saveBtn" style="font-size:20px; padding:12px 16px;">Save override &amp; mark done</button>
          <span id="saveMsg" style="margin-left:12px; font-size:18px;"></span>
        </div>
      </div>

      <div style="border:1px solid #ddd; padding:14px; border-radius:12px; margin-top:14px;">
        <div style="margin-top:0;"><b>Raw JSON</b></div>
        <pre style="white-space:pre-wrap; font-size:14px; background:#f7f7f7; padding:12px; border-radius:12px;">{data_str}</pre>
      </div>

      <script>
        const bookId = {book_id};
        const saveBtn = document.getElementById("saveBtn");
        const saveMsg = document.getElementById("saveMsg");

        saveBtn.addEventListener("click", async () => {{
          saveMsg.textContent = "Saving...";
          const payload = {{
            isbn13: document.getElementById("isbn13").value,
            isbn10: document.getElementById("isbn10").value,
            title: document.getElementById("title").value,
            author: document.getElementById("author").value
          }};

          try {{
            const res = await fetch(`/review/books/${{bookId}}/override`, {{
              method: "POST",
              headers: {{"Content-Type": "application/json"}},
              body: JSON.stringify(payload)
            }});
            const data = await res.json();
            if (!data.ok) throw new Error(data.error || "Unknown error");
            saveMsg.textContent = "Saved ✅ Reloading...";
            setTimeout(() => location.reload(), 500);
          }} catch (err) {{
            saveMsg.textContent = "Error: " + err.message;
          }}
        }});
      </script>
    </body></html>
    """

@app.post("/review/books/{book_id}/override")
async def override_extraction_endpoint(book_id: int, request: Request):
    payload = await request.json()
    try:
        db.override_extraction(
            book_id=book_id,
            isbn10=payload.get("isbn10"),
            isbn13=payload.get("isbn13"),
            title=payload.get("title"),
            author=payload.get("author"),
        )
        return JSONResponse({"ok": True})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)



logger = logging.getLogger("ebay_webhook")

EBAY_VERIFICATION_TOKEN = "bookworm-dev-verify-token-2026-01-10"

def compute_challenge_response(challenge_code: str, endpoint_url: str) -> str:
    # MUST be: challengeCode + verificationToken + endpoint  (in that order)
    msg = (challenge_code + EBAY_VERIFICATION_TOKEN + endpoint_url).encode("utf-8")
    return hashlib.sha256(msg).hexdigest()

@app.get("/ebay/account-deletion")
async def ebay_validate(request: Request, challenge_code: str = Query(...)):
    # endpoint must be EXACT URL eBay is validating, with https scheme
    # Use Host + path; force https because eBay calls your ngrok URL via https
    host = request.headers.get("host")
    endpoint_url = f"https://{host}{request.url.path}"

    digest = compute_challenge_response(challenge_code, endpoint_url)
    return JSONResponse({"challengeResponse": digest})  # Content-Type: application/json

@app.post("/ebay/account-deletion")
async def ebay_account_deletion(request: Request):
    body = await request.body()
    logger.warning("eBay deletion event raw=%s", body.decode("utf-8", errors="replace"))
    try:
        logger.warning("eBay deletion event json=%s", json.loads(body))
    except Exception:
        pass
    return Response(status_code=200)

@app.get("/auth/ebay/callback")
async def ebay_oauth_callback(request: Request):
    return {"ok": True, "query_params": dict(request.query_params)}


@app.post("/jobs/extract_errors")
async def run_extract_errors(limit: int | None = None):
    book_ids = db.get_error_extraction_book_ids(limit=limit)
    return await _run_extract_for_book_ids(book_ids)

async def _run_extract_for_book_ids(book_ids: list[int]):
    results = []
    total = len(book_ids)

    for i, book_id in enumerate(book_ids, start=1):
        print(f"[extract] {i}/{total} book_id={book_id}", flush=True)

        cover_path = db.load_path(book_id, "cover")
        copy_path = db.load_path(book_id, "copyright")
        if not cover_path or not copy_path:
            db.upsert_extraction(
                book_id=book_id,
                status="error",
                isbn10=None, isbn13=None, title=None, author=None,
                confidence=None, flags=["missing_images"], data=None,
                model=None, error="Missing cover or copyright image path in DB",
            )
            continue

        try:
            cover_bytes = gcs.read_bytes(cover_path)
            copy_bytes = gcs.read_bytes(copy_path)
            data = extract.extract_from_images(cover_bytes, copy_bytes)

            flags = data.get("flags") or []
            confidence = data.get("confidence")

            status = "done"
            if data.get("isbn13") is None and data.get("isbn10") is None:
                status = "needs_review"
            if data.get("title") is None or data.get("author") is None:
                status = "needs_review"

            db.upsert_extraction(
                book_id=book_id,
                status=status,
                isbn10=data.get("isbn10"),
                isbn13=data.get("isbn13"),
                title=data.get("title"),
                author=data.get("author"),
                confidence=confidence,
                flags=flags,
                data=data,
                model=data.get("_model"),
                error=None,
            )
            results.append({"book_id": book_id, "status": status})
        except Exception as e:
            db.upsert_extraction(
                book_id=book_id,
                status="error",
                isbn10=None, isbn13=None, title=None, author=None,
                confidence=None, flags=["exception"], data=None,
                model=None, error=str(e),
            )
            results.append({"book_id": book_id, "status": "error", "error": str(e)})

    return {"processed": results}
