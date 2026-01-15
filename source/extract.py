# source/extract.py
import base64
import json
from typing import Any, Dict
from openai import OpenAI

client = OpenAI()


def _b64_data_url_jpg(image_bytes: bytes) -> str:
    b64 = base64.b64encode(image_bytes).decode("ascii")
    return f"data:image/jpeg;base64,{b64}"


def extract_from_images(cover_bytes: bytes, copyright_bytes: bytes) -> Dict[str, Any]:
    """
    GCS-first: callers pass image bytes (not file paths).
    """
    if not cover_bytes or not copyright_bytes:
        raise ValueError("cover_bytes and copyright_bytes must be non-empty")

    prompt_item = {
        "type": "input_text",
        "text": (
            "You are extracting book metadata from two images.\n"
            "Rules:\n"
            "1) Prefer ISBN extraction from the COPYRIGHT page image.\n"
            "2) Prefer title/author extraction from the COVER image.\n"
            "3) If a field cannot be confidently determined, return null.\n"
            "4) Return STRICT JSON only, matching this schema:\n"
            "{"
            '"isbn13": string|null,'
            '"isbn10": string|null,'
            '"title": string|null,'
            '"author": string|null,'
            '"publisher": string|null,'
            '"publication_year": integer|null,'
            '"edition": string|null,'
            '"language": string|null,'
            '"binding": "paperback"|"hardcover"|"unknown",'
            '"confidence": number,'
            '"flags": array of strings,'
            '"sources": {"isbn":"copyright|cover|none","title_author":"cover|copyright|none"}'
            "}\n"
            "Flags examples: no_isbn_found, invalid_isbn_format, missing_title_author, blurry_text, multiple_isbns.\n"
        ),
    }

    resp = client.responses.create(
        model="gpt-4.1-mini",
        input=[{
            "role": "user",
            "content": [
                prompt_item,
                {"type": "input_image", "image_url": _b64_data_url_jpg(copyright_bytes)},
                {"type": "input_image", "image_url": _b64_data_url_jpg(cover_bytes)},
            ],
        }],
    )

    text = (resp.output_text or "").strip()

    # If the model wrapped JSON in ```...```, strip it
    if text.startswith("```"):
        text = text.strip("`")
        # sometimes it becomes "json\n{...}"
        if "\n" in text:
            text = text.split("\n", 1)[1].strip()

    if not text:
        raise ValueError("Model returned empty output_text (no JSON)")

    try:
        data = json.loads(text)
    except Exception as e:
        # include first 200 chars to debug quickly
        raise ValueError(f"Invalid JSON from model: {e}. First200={text[:200]!r}")

    data["_model"] = "gpt-4.1-mini"
    return data
