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

# One stable name per CATEGORY: uploading again simply overwrites it. Un
# workflow peut avoir plusieurs entrées de la même catégorie (image, image_2…) :
# elles partagent le même élément neutre, c'est la même catégorie.
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
                 overwrite: bool = True, timeout: float = 60.0,
                 subfolder: str = "") -> str:
    """Hand a file to ComfyUI through its own ``/api/upload/image``.

    Returns the name ComfyUI knows it by — the only name a graph can reference.
    One implementation for the neutral asset and for a file a user brings.

    Le moteur RÉPOND où il l'a rangé, et c'est cette réponse qui fait la
    référence : une image reste à la racine du dossier d'entrée et se cite par
    son nom, tandis qu'un modèle 3D vit dans « 3d/ » et se cite « 3d/<nom> »
    (c'est exactement ce que Load3D liste). Ignorer le sous-dossier rendait un
    nom que le graphe ne pouvait pas résoudre.
    """
    champs = {"type": "input", "overwrite": "true" if overwrite else "false"}
    if subfolder:
        champs["subfolder"] = subfolder
    body, content_type = _multipart(champs, filename, payload)
    req = urllib.request.Request(
        base_url.rstrip("/") + "/api/upload/image",
        data=body, headers={"Content-Type": content_type}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        try:
            import json
            reponse = json.loads(resp.read().decode("utf-8"))
        except Exception:
            return filename
    nom = reponse.get("name") or filename
    range_dans = reponse.get("subfolder") or ""
    return f"{range_dans}/{nom}" if range_dans else nom


def ensure_neutral_image(base_url: str, timeout: float = 30.0) -> str:
    """Upload the neutral white image to ComfyUI and return the name it knows."""
    return upload_image(base_url, NEUTRAL_NAMES["image"], build_white_png(), timeout=timeout)


# Comment fabriquer l'élément neutre de chaque catégorie. Une catégorie absente
# d'ici n'en a pas : le workflow tournera sur son propre contenu, et le run le
# DIT plutôt que de laisser croire à un neutre qui n'existe pas.
_FABRIQUES = {"image": build_white_png}


def has_neutral(param: str) -> bool:
    from ..core.intention import media_category
    return (media_category(param) or "") in _FABRIQUES


def ensure_neutral(base_url: str, param: str, timeout: float = 30.0) -> str | None:
    """L'élément neutre de la catégorie de ``param``, déposé chez ComfyUI.

    Rend le nom sous lequel le moteur le connaît — le seul qu'un graphe peut
    citer — ou None quand la catégorie n'a pas de neutre.
    """
    from ..core.intention import media_category
    categorie = media_category(param) or ""
    fabrique = _FABRIQUES.get(categorie)
    nom = NEUTRAL_NAMES.get(categorie)
    if fabrique is None or not nom:
        return None
    return upload_image(base_url, nom, fabrique(), timeout=timeout)
