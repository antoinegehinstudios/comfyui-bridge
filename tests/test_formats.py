"""Le vocabulaire des FORMATS : orientation et résolution, déclarés et servis.

Le lanceur ne tient aucune liste : la passerelle déclare les cas généraux (leurs
libellés, leurs côtés), il les rend en deux listes et TRADUIT le choix en
largeur/hauteur. La règle de traduction est géométrique, pas métier — portrait ⇒
la largeur est le petit côté —, ce qui la garde vraie quand le vocabulaire
s'allonge.
"""

import json
import pathlib
import tempfile

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from comfyui_bridge.adapter import raccourcis  # noqa: E402
from comfyui_bridge.adapter.catalog import load_catalog  # noqa: E402
from comfyui_bridge.api.main import create_app  # noqa: E402
from comfyui_bridge.config import Settings  # noqa: E402
from comfyui_bridge.core.errors import WorkflowMappingError  # noqa: E402
from test_chaines_api import atelier  # noqa: E402,F401

# Le vocabulaire tel qu'il est déclaré au fichier de réconciliation : deux
# rubriques, et une clé de documentation que le catalogue n'a pas à servir.
FORMATS = {
    "_lire_moi": "Le vocabulaire des formats — documentation, pas une rubrique.",
    "orientations": [
        {"valeur": "portrait", "libelle": "Portrait (vertical)"},
        {"valeur": "paysage", "libelle": "Paysage (horizontal)"},
    ],
    "resolutions": [
        {"valeur": "720p", "libelle": "720p (HD)", "cote_court": 720, "cote_long": 1280},
        {"valeur": "1080p", "libelle": "1080p (Full HD)", "cote_court": 1080, "cote_long": 1920},
    ],
}

# Une chaîne d'essai qui expose largeur et hauteur, avec des défauts CARRÉS :
# ainsi tout couple choisi fait bouger les deux champs, et c'est le repli en
# format qu'on éprouve, pas le hasard d'un défaut resté en place.
CHAINE_A_COTES = {
    "version": 1, "chaine": "chaine-a-cotes",
    "resume": "une chaîne qui expose ses deux côtés",
    "expose": {
        "width": {"type": "INT", "defaut": 512, "min": 64, "max": 4096,
                  "libelle": "Largeur", "unite": "px"},
        "height": {"type": "INT", "defaut": 512, "min": 64, "max": 4096,
                   "libelle": "Hauteur", "unite": "px"},
    },
    "etapes": [{"id": "un", "rendre": {"workflow": "sd15-txt2img", "prompt": "un",
                                       "width": "$width", "height": "$height"}}],
    "livrable": "$un.livrable",
}


def _reconciliation(tmp: pathlib.Path, formats=FORMATS) -> dict:
    corps = {
        "categories": {"essais": {"titre": "Essais", "ordre": 1}},
        "workflows": {
            "chaine-a-cotes": {"kind": "image", "chaine": str(tmp / "chaine-a-cotes.json"),
                               "titre": "Chaîne à côtés", "categorie": "essais", "ordre": 1},
            "sd15-txt2img": {"titre": "Un graphe ordinaire", "categorie": "essais", "ordre": 2},
        },
    }
    if formats is not None:
        corps["formats"] = formats
    return corps


@pytest.fixture()
def banc():
    """Une passerelle dont le catalogue déclare un vocabulaire de formats."""
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="comfybridge_formats_"))
    (tmp / "chaine-a-cotes.json").write_text(json.dumps(CHAINE_A_COTES), encoding="utf-8")
    (tmp / "reconciliation.local.json").write_text(
        json.dumps(_reconciliation(tmp), ensure_ascii=False), encoding="utf-8")
    settings = Settings(comfy_backend="cli", dry_run=True,
                        comfyui_base_url="http://127.0.0.1:9", comfyui_request_timeout_s=1,
                        hermes_db=tmp / "hermes.sqlite3", comfy_output_dir=tmp / "out",
                        hermes_mode="local", workflows_dir=tmp / "workflows")
    with TestClient(create_app(settings)) as client:
        client.tmp = tmp
        yield client


def _raccourci(client, titre: str, **valeurs) -> dict:
    r = client.post("/v1/workflows/chaine-a-cotes/raccourcis",
                    json={"titre": titre, "valeurs": valeurs})
    assert r.status_code == 201, r.text
    return r.json()


# -- ce que la passerelle publie ----------------------------------------------


def test_le_catalogue_publie_le_vocabulaire_des_formats(banc):
    """Un lanceur qui tiendrait sa propre liste de résolutions la verrait
    vieillir au premier ajout — et il ne saurait pas les côtés."""
    formats = banc.get("/v1/workflows").json()["formats"]
    assert [o["valeur"] for o in formats["orientations"]] == ["portrait", "paysage"]
    assert formats["resolutions"][1] == {"valeur": "1080p", "libelle": "1080p (Full HD)",
                                         "cote_court": 1080, "cote_long": 1920}
    # La documentation du bloc n'est pas une rubrique : elle ne part pas au réseau.
    assert set(formats) == {"orientations", "resolutions"}


def test_sans_declaration_les_deux_listes_sont_vides(atelier):
    """Une passerelle qui ne déclare rien ne casse rien : le lanceur ne montre
    pas les listes, et largeur/hauteur restent des champs ordinaires."""
    assert atelier.get("/v1/workflows").json()["formats"] == {"orientations": [],
                                                              "resolutions": []}


def test_une_resolution_sans_ses_cotes_est_refusee_a_la_lecture(tmp_path):
    """Servie tronquée, elle aurait donné une liste où un choix n'écrit rien, et
    le lanceur aurait eu l'air en panne. Refusée ici, et nommée."""
    (tmp_path / "chaine-a-cotes.json").write_text(json.dumps(CHAINE_A_COTES), encoding="utf-8")
    boiteux = {"orientations": [{"valeur": "portrait", "libelle": "Portrait (vertical)"}],
               "resolutions": [{"valeur": "540p", "libelle": "540p", "cote_court": 540}]}
    (tmp_path / "reconciliation.local.json").write_text(
        json.dumps(_reconciliation(tmp_path, boiteux), ensure_ascii=False), encoding="utf-8")
    livre = pathlib.Path("comfyui_bridge/adapter/resources/reconciliation.json").resolve()
    with pytest.raises(WorkflowMappingError) as refus:
        load_catalog(livre, tmp_path / "workflows", data_dir=tmp_path)
    assert "540p" in str(refus.value) and "cote_long" in str(refus.value)

    # …et une entrée sans « valeur » non plus : c'est elle que le lanceur renvoie.
    muette = {"orientations": [{"libelle": "Portrait (vertical)"}], "resolutions": []}
    (tmp_path / "reconciliation.local.json").write_text(
        json.dumps(_reconciliation(tmp_path, muette), ensure_ascii=False), encoding="utf-8")
    with pytest.raises(WorkflowMappingError) as sans_valeur:
        load_catalog(livre, tmp_path / "workflows", data_dir=tmp_path)
    assert "orientations" in str(sans_valeur.value) and "valeur" in str(sans_valeur.value)


# -- l'écart d'un raccourci ----------------------------------------------------


def test_un_couple_declare_se_lit_comme_UN_format(banc):
    """L'utilisateur a choisi « 1080p, Paysage » — pas « 1920 px » puis
    « 1080 px ». Les laisser séparés obligeait à recomposer le format de tête,
    carte après carte."""
    vue = _raccourci(banc, "Grand paysage", width=1920, height=1080)
    assert vue["ecarts"] == [{"champ": "format", "libelle": "Format",
                              "valeur": "1080p paysage",
                              "libelle_valeur": "1080p (Full HD), Paysage (horizontal)"}]
    # Le portrait suit la même règle géométrique : la largeur est le petit côté.
    debout = _raccourci(banc, "Debout", width=720, height=1280)
    assert debout["ecarts"] == [{"champ": "format", "libelle": "Format",
                                 "valeur": "720p portrait",
                                 "libelle_valeur": "720p (HD), Portrait (vertical)"}]
    # Les VALEURS enregistrées, elles, restent la largeur et la hauteur : c'est
    # ce qu'un formulaire repose, et « format » n'est pas un champ du mode.
    assert debout["valeurs"] == {"width": 720, "height": 1280}


def test_un_couple_hors_vocabulaire_reste_une_largeur_et_une_hauteur(banc):
    """704 n'est le côté court d'aucune résolution déclarée : inventer un format
    pour lui aurait dit à l'utilisateur qu'il a choisi ce qu'il n'a pas choisi."""
    vue = _raccourci(banc, "Sur mesure", width=704, height=1280)
    assert [(e["champ"], e["libelle"], e["libelle_valeur"]) for e in vue["ecarts"]] == [
        ("width", "Largeur", "704 px"), ("height", "Hauteur", "1280 px")]


# -- la traduction, sans app ---------------------------------------------------


def test_la_regle_du_format_est_geometrique():
    """Portrait ⇒ la largeur est le petit côté ; paysage ⇒ le grand. Un carré ne
    désigne aucune orientation, et un couple hors du vocabulaire aucun format."""
    assert raccourcis.format_du_couple(720, 1280, FORMATS)["valeur"] == "720p portrait"
    assert raccourcis.format_du_couple(1280, 720, FORMATS)["valeur"] == "720p paysage"
    assert raccourcis.format_du_couple(1024, 1024, FORMATS) is None      # carré
    assert raccourcis.format_du_couple(704, 1280, FORMATS) is None       # hors vocabulaire
    assert raccourcis.format_du_couple(720, 1280, None) is None          # rien de déclaré


def test_le_repli_ne_derange_pas_l_ordre_des_autres_ecarts():
    """Le format prend la place du PREMIER des deux côtés ; ce qui l'entoure ne
    bouge pas. Un seul côté changé n'est pas un format : les deux restent."""
    decrire = {"width": {"libelle": "Largeur", "unite": "px"},
               "height": {"libelle": "Hauteur", "unite": "px"},
               "fond": {"libelle": "Fond"}}
    lignes = raccourcis.ecarts({"fond": "sepia", "width": 1920, "height": 1080, "fps": 30},
                               {"fond": "washi", "width": 512, "height": 512, "fps": 25},
                               lambda champ: decrire.get(champ, {}), FORMATS)
    assert [e["champ"] for e in lignes] == ["fond", "format", "fps"]

    seul = raccourcis.ecarts({"width": 1920}, {"width": 512},
                             lambda champ: decrire.get(champ, {}), FORMATS)
    assert [e["champ"] for e in seul] == ["width"]
