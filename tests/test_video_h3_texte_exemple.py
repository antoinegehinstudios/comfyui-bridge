"""Le montage livré « video-h3-texte » (copie de référence), monté comme la
passerelle le monte : un run par tour, et les images de référence commentées.

C'est la recette elle-même qui est éprouvée ici — pas un montage d'essai —
pour que la copie livrée avec le paquet reste montable le jour où le mécanisme
change. Les blocs viennent de la bibliothèque livrée.
"""

import json
import pathlib

from comfyui_bridge.adapter import assembleur, bibliotheque
from comfyui_bridge.core import blocs as noyau

RACINE = pathlib.Path(__file__).resolve().parents[1] / "comfyui_bridge" / "adapter" / "resources"
MONTAGE = RACINE / "montages-exemples" / "video-h3-texte.json"

BASE = {"duration_s": 15, "fps": 30, "width": 1080, "height": 1920, "prompt": "x",
        "filename_prefix": "cortex/essai"}


def _brut():
    return json.loads(MONTAGE.read_text(encoding="utf-8"))


def _monter(params, tour=None, relais=None):
    brut = _brut()
    valeurs = dict(brut["constantes"])
    valeurs.update(brut.get("exemple") or {})
    valeurs.update(params)
    montage = bibliotheque.resoudre(brut["montage"], bibliotheque.charger())
    fragments = noyau.deplier(montage, valeurs)
    return assembleur.assembler(fragments, valeurs, blocs=brut["blocs"], tour_seul=tour,
                                relais_fichiers=relais, prefixe_relais="cortex/essai")


def _types(g):
    return [v["class_type"] for v in g.values()]


def test_la_boucle_demande_un_run_par_tour_et_relaie_la_derniere_image():
    brut = _brut()
    pour = noyau.boucle_par_run(bibliotheque.resoudre(brut["montage"], bibliotheque.charger()))
    assert pour is not None and list(pour["relais"]) == ["derniere_image"]
    assert noyau.tours_separes(bibliotheque.resoudre(brut["montage"], bibliotheque.charger()),
                               {**brut["constantes"], **BASE}) == 3


def test_chaque_run_porte_un_bloc_et_le_relais_au_bon_endroit():
    premier = _monter(BASE, tour=0)
    assert _types(premier).count("SamplerCustomAdvanced") == 1
    assert _types(premier).count("MiniMaxH3ImageToVideo") == 1 and "LoadImage" not in _types(premier)
    ecrit = next(v for v in premier.values() if v["class_type"] == "SaveImage")
    assert ecrit["inputs"]["filename_prefix"] == "cortex/essai_relais_derniere_image"
    milieu = _monter(BASE, tour=1, relais={"derniere_image": "ancre.png"})
    lu = next(k for k, v in milieu.items() if v["class_type"] == "LoadImage")
    segment = next(v for v in milieu.values() if v["class_type"] == "MiniMaxH3ImageToVideo")
    assert segment["inputs"]["first_frame"] == [lu, 0]
    assert milieu[lu]["inputs"]["image"] == "ancre.png"
    rangs = sorted(v["inputs"]["rang"] for v in milieu.values() if v["class_type"] == "DirectionDuBloc")
    assert rangs == [1]
    dernier = _monter(BASE, tour=2, relais={"derniere_image": "b.png"})
    assert "SaveImage" not in _types(dernier)


def test_sans_image_l_amorce_est_le_texte_seul():
    g = _monter(BASE, tour=0)
    assert "MiniMaxH3ReferenceToVideo" not in _types(g)


def test_avec_des_images_l_amorce_devient_texte_et_references():
    for n, images in ((1, {"image": "a.png"}),
                      (2, {"image": "a.png", "image_2": "b.png"}),
                      (3, {"image": "a.png", "image_2": "b.png", "image_3": "c.png"})):
        g = _monter({**BASE, **images}, tour=0)
        types = _types(g)
        assert types.count("MiniMaxH3ReferenceToVideo") == 1 and "MiniMaxH3ImageToVideo" not in types
        assert types.count("LoadImage") == n
        ref = next(v for v in g.values() if v["class_type"] == "MiniMaxH3ReferenceToVideo")
        assert sorted(k for k in ref["inputs"] if k.startswith("ref_images.")) == [
            f"ref_images.ref_image_{i}" for i in range(n)]
        # Le VAE audio que le nœud exige n'est chargé que dans cette variante.
        assert sum(1 for v in g.values() if v["class_type"] == "VAELoader"
                   and "audio" in v["inputs"]["vae_name"]) == 1
        # La consigne du bloc, puis une phrase « <Picture i> is <rôle> » par image.
        prompt = g[ref["inputs"]["prompt"][0]]
        assert prompt["class_type"] == "StringConcatenate" and prompt["inputs"]["delimiter"] == "\n"
        etiquettes = [v["inputs"]["string_a"] for v in g.values()
                      if v["class_type"] == "StringConcatenate"
                      and isinstance(v["inputs"].get("string_a"), str)]
        assert sorted(etiquettes) == [f"<Picture {i + 1}> is " for i in range(n)]
        # Les tours suivants ne relisent que le relais : pas de référence.
        suite = _monter({**BASE, **images}, tour=1, relais={"derniere_image": "r.png"})
        assert "MiniMaxH3ReferenceToVideo" not in _types(suite)
        assert _types(suite).count("LoadImage") == 1
