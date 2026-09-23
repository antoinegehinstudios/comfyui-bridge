"""Un dessin vectoriel déposé : peint pour le moteur, gardé pour le flux.

Antoine, 2026-09-23 : « ajoute à l'outil d'import la possibilité d'importer un
SVG ». Le moteur ne sait ouvrir que des images matricielles (un SVG l'a fait
tomber vingt et une fois la veille) : le dépôt PEINT donc le document et envoie
l'image — et il dépose AUSSI le document, sous son nom, pour le nœud qui le
repeindra à la taille du plan (`ChargerSVG`, paquet `comfyui-vectoriel`).
"""

import io

import pytest

from comfyui_bridge.adapter import svg_rendu as S  # noqa: E402

SVG = (b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 100">'
       b'<rect width="400" height="100" fill="#2b6cb0"/></svg>')
SVG_HAUT = (b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg" width="50" height="200">'
            b'<rect width="50" height="200"/></svg>')

sans_peintre = pytest.mark.skipif(S.peintre() is None,
                                  reason="aucun peintre de vectoriel sur ce poste")


# -- reconnaître, mesurer ------------------------------------------------------


def test_un_dessin_se_reconnait_a_son_contenu_pas_a_son_nom():
    """Un `.png` peut être un SVG (mesuré) : c'est le contenu qui décide, sans
    quoi le document part chez le moteur sous un nom d'image — et le tue."""
    assert S.est_svg(SVG) and S.est_svg(SVG_HAUT)
    assert S.est_svg(b"   \n<svg/>") and S.est_svg(b'<?xml version="1.0"?>\n<SVG />')
    assert not S.est_svg(b"\x89PNG\r\n\x1a\n") and not S.est_svg(b"du texte")


def test_la_taille_se_lit_a_la_racine_du_document():
    assert S.taille_declaree(SVG) == (400.0, 100.0)
    assert S.taille_declaree(SVG_HAUT) == (50.0, 200.0)
    # Le `width` d'un trait n'est pas celui du document (mesuré le 2026-09-23).
    assert S.taille_declaree(b'<svg xmlns="x"><rect width="10" height="10"/></svg>') is None
    assert S.taille_declaree(b"pas du svg") is None


def test_le_cote_long_garde_le_rapport():
    assert S.dimensions_voulues(SVG, cote_long=800) == (800, 200)
    assert S.dimensions_voulues(SVG_HAUT, cote_long=800) == (200, 800)
    assert S.dimensions_voulues(SVG, largeur=200) == (200, 50)
    assert S.dimensions_voulues(SVG, hauteur=50) == (200, 50)
    assert S.dimensions_voulues(SVG, largeur=7, hauteur=9) == (7, 9)
    assert max(S.dimensions_voulues(SVG, cote_long=999_999)) == S.COTE_LONG_MAX


# -- peindre -------------------------------------------------------------------


@sans_peintre
def test_le_dessin_devient_une_image_a_fond_transparent():
    pytest.importorskip("PIL")
    from PIL import Image
    png, fait = S.peindre(SVG, cote_long=200)
    with Image.open(io.BytesIO(png)) as vue:
        assert vue.size == (200, 50) and vue.mode in ("RGBA", "LA")
        assert vue.convert("RGBA").getpixel((100, 25))[3] == 255      # le dessin est là
    assert fait["largeur"] == 200 and fait["hauteur"] == 50 and fait["cote_long"] == 200
    assert "inkscape" in fait["peintre"].lower()


def test_sans_peintre_le_depot_dit_ce_qui_manque(monkeypatch):
    monkeypatch.setattr(S, "peintre", lambda: None)
    with pytest.raises(S.SansPeintre) as e:
        S.peindre(SVG)
    assert "Inkscape" in str(e.value) and S.VARIABLE in str(e.value)


def test_un_dessin_demesure_est_refuse_avant_d_etre_peint():
    with pytest.raises(RuntimeError, match="Mio"):
        S.peindre(b"<svg>" + b"x" * (S.OCTETS_MAX + 1))


# -- par l'API : l'image part, le document reste ------------------------------


@pytest.fixture()
def atelier():
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from test_chaines_api import atelier as _atelier             # noqa: F401
    yield from _atelier.__wrapped__()


@sans_peintre
def test_le_depot_peint_le_dessin_et_garde_le_document(atelier, monkeypatch):
    pytest.importorskip("PIL")
    from PIL import Image

    from comfyui_bridge.adapter import neutral
    deposes = []

    def faux_depot(base_url, nom, octets, overwrite=True, timeout=60.0, subfolder=""):
        deposes.append({"nom": nom, "octets": octets, "subfolder": subfolder})
        return nom

    monkeypatch.setattr(neutral, "upload_image", faux_depot)
    r = atelier.post("/v1/inputs/image",
                     files={"file": ("logo maison.svg", SVG, "image/svg+xml")},
                     data={"param": "image", "cote_long": "256"})
    assert r.status_code == 201, r.text
    dit = r.json()
    # Ce qui part chez le moteur est l'IMAGE, sous le nom du document en .png.
    assert dit["name"] == "logo maison.png"
    assert dit["converti"]["de"] == "svg" and dit["converti"]["largeur"] == 256
    assert dit["converti"]["hauteur"] == 64
    assert dit["converti"]["document_garde"] == "logo maison.svg"
    # Deux dépôts : l'image peinte, puis le document — et l'image EST une image.
    assert [d["nom"] for d in deposes] == ["logo maison.svg", "logo maison.png"]
    with Image.open(io.BytesIO(deposes[1]["octets"])) as vue:
        assert vue.size == (256, 64)
    assert deposes[0]["octets"] == SVG, "le document est déposé tel quel, sans retouche"


@sans_peintre
def test_un_dessin_deguise_en_png_est_peint_lui_aussi(atelier, monkeypatch):
    """Le nom ne décide de rien : un `.png` qui contient un SVG serait mortel
    pour le moteur. Le contenu décide, et ce qui part est une vraie image."""
    from comfyui_bridge.adapter import neutral
    deposes = []
    monkeypatch.setattr(neutral, "upload_image",
                        lambda base, nom, octets, *a, **k: deposes.append(nom) or nom)
    r = atelier.post("/v1/inputs/image", files={"file": ("deguise.png", SVG, "image/png")},
                     data={"param": "image"})
    assert r.status_code == 201, r.text
    assert r.json()["name"] == "deguise.png" and r.json()["converti"]["de"] == "svg"
    assert deposes == ["deguise.svg", "deguise.png"]


def test_un_dessin_sans_peintre_est_refuse_en_le_disant(atelier, monkeypatch):
    monkeypatch.setattr(S, "peintre", lambda: None)
    r = atelier.post("/v1/inputs/image", files={"file": ("logo.svg", SVG, "image/svg+xml")},
                     data={"param": "image"})
    assert r.status_code == 422, r.text
    assert "Inkscape" in r.json()["detail"] and r.json()["fichier"] == "logo.svg"


def test_un_dessin_designe_comme_image_reste_refuse_au_lancement(atelier):
    """Le document lui-même n'est pas une image : le désigner ferait tomber le
    moteur. Le refus dit maintenant quoi faire — le redéposer, ou prendre
    l'image déjà peinte."""
    refus = atelier.post("/v1/render", json={"workflow": "chaine-piece-au-repos",
                                             "image_2": "logo.svg"})
    assert refus.status_code == 422
    detail = refus.json()["detail"]
    assert "logo.png" in detail and "redéposer" in detail and "nœud" in detail
