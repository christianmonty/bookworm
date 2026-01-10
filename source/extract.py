# source/extract.py
import base64
import json
from pathlib import Path
from typing import Any, Dict
from openai import OpenAI

client = OpenAI()

def _b64_data_url_jpg(image_bytes: bytes) -> str:
    b64 = base64.b64encode(image_bytes).decode("ascii")
    return f"data:image/jpeg;base64,{b64}"

def _read_bytes(path_str: str) -> bytes:
    return Path(path_str).read_bytes()

def extract_from_images(cover_path: str, copyright_path: str) -> Dict[str, Any]:
    cover_bytes = _read_bytes(cover_path)
    copy_bytes = _read_bytes(copyright_path)

    prompt_item = {
        "type": "input_text",   # <-- IMPORTANT: was "text", must be "input_text"
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
                {"type": "input_image", "image_url": _b64_data_url_jpg(copy_bytes)},
                {"type": "input_image", "image_url": _b64_data_url_jpg(cover_bytes)},
            ],
        }],
    )

    # The SDK gives you a combined text view here
    text = resp.output_text
    data = json.loads(text)
    data["_model"] = "gpt-4.1-mini"
    return data
