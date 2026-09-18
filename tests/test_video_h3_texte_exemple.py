"""Le montage livré « video-h3-texte » (copie de référence), monté comme la
passerelle le monte : deux phases par bloc, un run par phase, les relais entre
les runs, et les images de référence commentées.

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
RELAIS = {"derniere_image": "d.png", "conditionnement": "c.pt", "latent": "l.latent"}
CHARGEURS = ("UNETLoader", "CLIPLoader", "VAELoader", "LoraLoaderModelOnly")


def _brut():
    return json.loads(MONTAGE.read_text(encoding="utf-8"))


def _resolu():
    return bibliotheque.resoudre(_brut()["montage"], bibliotheque.charger())


def _monter(params, tour=None, relais=None):
    brut = _brut()
    valeurs = dict(brut["constantes"])
    valeurs.update(brut.get("exemple") or {})
    valeurs.update(params)
    fragments = noyau.deplier(_resolu(), valeurs)
    return assembleur.assembler(fragments, valeurs, blocs=brut["blocs"], tour_seul=tour,
                                relais_fichiers=relais, prefixe_relais="cortex/essai")


def _types(g):
    return [v["class_type"] for v in g.values()]


def _chargeurs(g):
    return sorted(t for t in _types(g) if t in CHARGEURS)


def test_la_boucle_demande_deux_runs_par_bloc_et_trois_relais():
    brut = _brut()
    pour = noyau.boucle_par_run(_resolu())
    assert pour is not None and pour["phases"] == 2
    assert sorted(pour["relais"]) == ["conditionnement", "derniere_image", "latent"]
    assert noyau.tours_separes(_resolu(), {**brut["constantes"], **BASE}) == 6
    assert noyau.tours_separes(_resolu(), {**brut["constantes"], **BASE, "duration_s": 5}) == 2


def test_l_encodage_ne_charge_que_l_encodeur_et_le_rendu_que_le_modele():
    encodage = _monter(BASE, tour=0)
    assert _chargeurs(encodage) == ["CLIPLoader", "VAELoader"]
    assert "SamplerCustomAdvanced" not in _types(encodage)
    assert sorted(t for t in _types(encodage) if t in ("SauverConditionnement", "SauverLatent")) == [
        "SauverConditionnement", "SauverLatent"]
    rendu = _monter(BASE, tour=1, relais=RELAIS)
    assert _chargeurs(rendu) == ["LoraLoaderModelOnly", "UNETLoader", "VAELoader"]
    assert _types(rendu).count("SamplerCustomAdvanced") == 1 and "DirectionDuBloc" not in _types(rendu)
    assert sorted(t for t in _types(rendu) if t in ("ChargerConditionnement", "ChargerLatent")) == [
        "ChargerConditionnement", "ChargerLatent"]
    sampler = next(v for v in rendu.values() if v["class_type"] == "SamplerCustomAdvanced")
    assert rendu[sampler["inputs"]["latent_image"][0]]["class_type"] == "ChargerLatent"
    guider = rendu[sampler["inputs"]["guider"][0]]
    assert rendu[guider["inputs"]["conditioning"][0]]["class_type"] == "ChargerConditionnement"
    # Chaque tier de livraison est gardé, et le run écrit la dernière image pour le suivant.
    assert _types(rendu).count("GardeDePlace") == 3
    ecrit = next(v for v in rendu.values() if v["class_type"] == "SaveImage")
    assert ecrit["inputs"]["filename_prefix"] == "cortex/essai_relais_derniere_image"


def test_le_bloc_suivant_encode_depuis_la_derniere_image_relayee_et_la_passe():
    encodage = _monter(BASE, tour=2, relais=RELAIS)
    i2v = next(v for v in encodage.values() if v["class_type"] == "MiniMaxH3ImageToVideo")
    ancre = encodage[i2v["inputs"]["first_frame"][0]]
    assert ancre["class_type"] == "RepeatImageBatch"
    assert encodage[ancre["inputs"]["image"][0]]["class_type"] == "LoadImage"
    rangs = [v["inputs"]["rang"] for v in encodage.values() if v["class_type"] == "DirectionDuBloc"]
    assert rangs == [1]                                   # le bloc, pas le numéro de run
    assert sorted(t for t in _types(encodage) if t in ("SaveImage", "SauverConditionnement", "SauverLatent")) == [
        "SauverConditionnement", "SauverLatent", "SaveImage"]
    rendu = _monter(BASE, tour=3, relais=RELAIS)
    ancre_livraison = next(v for v in rendu.values() if v["class_type"] == "RepeatImageBatch"
                           and rendu[v["inputs"]["image"][0]]["class_type"] == "LoadImage")
    assert ancre_livraison is not None
    dernier = _monter(BASE, tour=5, relais=RELAIS)
    assert not any(t in ("SaveImage", "SauverConditionnement", "SauverLatent") for t in _types(dernier))


def test_sans_image_l_amorce_est_le_texte_seul():
    g = _monter(BASE, tour=0)
    assert "MiniMaxH3ReferenceToVideo" not in _types(g) and _types(g).count("MiniMaxH3ImageToVideo") == 1


def test_avec_des_images_l_encodage_du_premier_bloc_devient_texte_et_references():
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
        assert sum(1 for v in g.values() if v["class_type"] == "VAELoader"
                   and "audio" in v["inputs"]["vae_name"]) == 1
        prompt = g[ref["inputs"]["prompt"][0]]
        assert prompt["class_type"] == "StringConcatenate" and prompt["inputs"]["delimiter"] == "\n"
        etiquettes = [v["inputs"]["string_a"] for v in g.values()
                      if v["class_type"] == "StringConcatenate"
                      and isinstance(v["inputs"].get("string_a"), str)]
        assert sorted(etiquettes) == [f"<Picture {i + 1}> is " for i in range(n)]
        # Les blocs suivants ne relisent que le relais : pas de référence.
        suite = _monter({**BASE, **images}, tour=2, relais=RELAIS)
        assert "MiniMaxH3ReferenceToVideo" not in _types(suite) and _types(suite).count("LoadImage") == 1
