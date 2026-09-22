"""Une pièce jointe que le moteur ne sait pas ouvrir n'entre pas.

Le 2026-09-22, Antoine a déposé un dessin vectoriel (`retarus_2025_RGB.svg`)
comme image de référence. Le nœud qui charge une image ne sait pas l'ouvrir et
se rabat sur le chemin VIDÉO ; la bibliothèque de démultiplexage meurt alors
d'une violation d'accès (`Windows fatal exception: access violation`,
`av/stream.pyd`) et emporte le MOTEUR ENTIER — pas une erreur de run, un
processus qui disparaît. Vingt et un des vingt-quatre échecs de la journée
citent ce fichier ; entre eux, le moteur était mort et tout ce qui partait
échouait « moteur injoignable », « run perdu », ou après une heure d'attente.

Deux gardes, ici : le DÉPÔT (le contenu décide) et le LANCEMENT (le nom décide
— c'est tout ce qu'on a d'un fichier déjà chez le moteur, rejeu compris).
"""

import io

import pytest

from comfyui_bridge.core.pieces_jointes import PAS_DES_IMAGES, refus_par_le_nom  # noqa: E402

SVG = (b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 392.9 73.2">'
       b'<path d="M30.9 29.5c-2.5-2.5-4.5-3.8-8.4-3.8"/></svg>')


def _png(taille=(4, 4)) -> bytes:
    Image = pytest.importorskip("PIL.Image")
    tampon = io.BytesIO()
    Image.new("RGB", taille, (10, 20, 30)).save(tampon, format="PNG")
    return tampon.getvalue()


# -- la règle, sans rien ouvrir ------------------------------------------------


def test_un_dessin_vectoriel_est_refuse_par_son_nom_et_on_dit_quoi_faire():
    dit = refus_par_le_nom("retarus_2025_RGB.svg", "image")
    assert dit and "dessin vectoriel" in dit and "exporter en PNG" in dit
    assert "retarus_2025_RGB.svg" in dit          # l'utilisateur voit QUEL fichier
    for ext in PAS_DES_IMAGES:
        assert refus_par_le_nom(f"a{ext}", "image"), ext
        assert refus_par_le_nom(f"A{ext.upper()}", "image"), ext     # la casse ne sauve pas
    # Ce qui s'ouvre vraiment passe, et les autres catégories ne sont pas jugées
    # sur cette liste (une vidéo n'est pas une image, et c'est normal).
    assert refus_par_le_nom("photo.png", "image") is None
    assert refus_par_le_nom("plan.jpeg", "image") is None
    assert refus_par_le_nom("un.svg", "video") is None
    assert refus_par_le_nom("", "image") is None


# -- le dépôt : le contenu décide ----------------------------------------------


def test_le_contenu_decide_meme_sous_un_nom_deguise():
    juger = pytest.importorskip("comfyui_bridge.adapter.juger_media")
    pytest.importorskip("PIL")
    assert juger.refus(_png(), "vrai.png", "image") is None
    dit = juger.refus(SVG, "deguise.png", "image")        # nom d'image, contenu vectoriel
    assert dit and "ne s'ouvre pas comme une image" in dit and "exporter en PNG" in dit
    assert "<svg" in dit                                   # le début du fichier est montré
    # Par son nom, le refus est plus court et plus clair : il n'a rien à ouvrir.
    assert "dessin vectoriel" in (juger.refus(SVG, "logo.svg", "image") or "")
    # Une vidéo n'est pas jugée comme une image, et rien n'est refusé sans raison.
    assert juger.refus(SVG, "film.mp4", "video") is None
    assert juger.refus(b"", "vide.png", "image") is None   # le vide est refusé ailleurs


# -- par l'API : au dépôt, et au lancement ------------------------------------


@pytest.fixture()
def atelier():
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from test_chaines_api import atelier as _atelier             # noqa: F401
    yield from _atelier.__wrapped__()


def test_le_depot_refuse_ce_qui_ferait_tomber_le_moteur(atelier):
    r = atelier.post("/v1/inputs/image", files={"file": ("retarus_2025_RGB.svg", SVG, "image/svg+xml")},
                     data={"param": "image"})
    assert r.status_code == 422, r.text
    dit = r.json()
    assert "dessin vectoriel" in dit["detail"] and dit["field"] == "image"
    assert dit["fichier"] == "retarus_2025_RGB.svg"
    # …et une vraie image PASSE la porte : sur cet atelier, elle échoue ensuite
    # faute de moteur (c'est lui qui range les fichiers) — ce qui compte est
    # que l'échec ne vienne pas d'ici.
    try:
        bonne = atelier.post("/v1/inputs/image", files={"file": ("photo.png", _png(), "image/png")},
                             data={"param": "image"})
        assert bonne.status_code != 422 or "vectoriel" not in bonne.text
    except Exception as exc:                       # noqa: BLE001 — le moteur d'essai n'écoute pas
        assert "vectoriel" not in str(exc) and "ne s'ouvre pas" not in str(exc)


def test_le_lancement_refuse_un_fichier_deja_chez_le_moteur(atelier):
    """Le dépôt d'hier, un rejeu, un raccourci : le fichier est déjà là-bas et
    on n'a que son nom. Il suffit — et le refus arrive AVANT que le moteur y
    touche."""
    refus = atelier.post("/v1/render", json={"workflow": "chaine-piece-au-repos",
                                             "image_2": "retarus_2025_RGB.svg"})
    assert refus.status_code == 422, refus.text
    assert "dessin vectoriel" in refus.json()["detail"]
    # Le même lancement avec une vraie image n'est pas gêné.
    ok = atelier.post("/v1/render", json={"workflow": "chaine-piece-au-repos",
                                          "image_2": "ma-piece.png"})
    assert ok.status_code == 202, ok.text
    from test_chaines_api import _job
    _job(atelier, ok)
