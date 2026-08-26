"""Neutral content, per media category.

When a caller supplies nothing for a media input, the workflow would otherwise
run on whatever it happens to carry — someone else's picture leaking into the
result. So the bridge supplies a NEUTRAL element instead: a plain white image
for the image category. Deliberate and boring beats leftover and surprising.

The asset is generated once and uploaded to ComfyUI through its own
``/api/upload/image`` endpoint, so no assumption is made about where ComfyUI
keeps its input folder.
"""

from __future__ import annotations

import io
import mimetypes
import urllib.request
import uuid

# One stable name per category: uploading again simply overwrites it.
NEUTRAL_NAMES = {"image": "cortex-neutral-white.png"}

_WHITE_SIZE = (1024, 1024)


def build_white_png(size: tuple[int, int] = _WHITE_SIZE) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", size, (255, 255, 255)).save(buf, format="PNG")
    return buf.getvalue()


def _multipart(fields: dict[str, str], filename: str, payload: bytes) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    parts: list[bytes] = []
    for key, value in fields.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode()
        )
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="{filename}"\r\n'
        f"Content-Type: {mime}\r\n\r\n".encode()
    )
    parts.append(payload)
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def upload_image(base_url: str, filename: str, payload: bytes,
                 overwrite: bool = True, timeout: float = 60.0) -> str:
    """Hand an image to ComfyUI through its own ``/api/upload/image``.

    Returns the name ComfyUI knows it by — the only name a graph can reference.
    One implementation for the neutral asset and for a file a user brings.
    """
    body, content_type = _multipart(
        {"type": "input", "overwrite": "true" if overwrite else "false"}, filename, payload)
    req = urllib.request.Request(
        base_url.rstrip("/") + "/api/upload/image",
        data=body, headers={"Content-Type": content_type}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        try:
            import json
            return json.loads(resp.read().decode("utf-8")).get("name") or filename
        except Exception:
            return filename


def ensure_neutral_image(base_url: str, timeout: float = 30.0) -> str:
    """Upload the neutral white image to ComfyUI and return the name it knows."""
    return upload_image(base_url, NEUTRAL_NAMES["image"], build_white_png(), timeout=timeout)
