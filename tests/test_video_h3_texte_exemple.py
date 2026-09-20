"""Le montage livré « video-h3-texte » (copie de référence), monté comme la
passerelle le monte : deux phases par bloc, un run par phase, les relais entre
les runs, les images de référence commentées, et la continuité d'un bloc à
l'autre (enquête du 2026-09-18, tasks/enquete-continuite-2026-09-18.md) : le
premier bloc naît du texte (fl2va, 8 pas), chaque bloc suivant CONTINUE le
précédent par référence (ref2va, 4 pas : la queue du bloc précédent en vidéo de
référence, une image du milieu en image d'identité), la graine avance d'un bloc
à l'autre (même graine = même trajectoire de caméra rejouée), et la couture est
constatée (MeilleurRaccord) sans rien couper à la source — c'est le recollage
de la passerelle qui, depuis le 2026-09-20, cherche l'image de raccord de chaque
bloc et jette le rejeu (un bloc continué par référence rejoue ≈ 1,8 s de la fin
du précédent) ; le compte de blocs en tient compte (3,0 s par bloc suivant).

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
RELAIS = {"derniere_image": "d.png", "conditionnement": "c.pt", "latent": "l.latent",
          "queue": "q.pt", "identite": "i.png"}
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


def _noeud(g, type_):
    return next(v for v in g.values() if v["class_type"] == type_)


def _source(g, noeud, entree):
    return g[noeud["inputs"][entree][0]]


def test_la_boucle_demande_deux_runs_par_bloc_et_cinq_relais():
    brut = _brut()
    pour = noyau.boucle_par_run(_resolu())
    assert pour is not None and pour["phases"] == 2
    assert sorted(pour["relais"]) == ["conditionnement", "derniere_image", "identite", "latent", "queue"]
    # 15 s : l'amorce (5,1667 s) puis des blocs qui n'apportent que 3,0 s chacun
    # une fois leur rejeu jeté (2026-09-20) → 5 blocs, 10 runs ; 5 s → 1 bloc, 2 runs.
    assert noyau.tours_separes(_resolu(), {**brut["constantes"], **BASE}) == 10
    assert noyau.tours_separes(_resolu(), {**brut["constantes"], **BASE, "duration_s": 5}) == 2
    assert noyau.tours_separes(_resolu(), {**brut["constantes"], **BASE, "duration_s": 8}) == 4
    assert noyau.tours_separes(_resolu(), {**brut["constantes"], **BASE, "duration_s": 10}) == 6


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


def test_le_bloc_suivant_continue_le_precedent_par_sa_queue_et_son_identite():
    """R2 : le bloc suivant n'est plus « une image puis du neuf » (image-vers-
    vidéo, qui rejouait un plan déjà vu) mais une CONTINUATION par référence :
    les 22 dernières images natives du bloc précédent en vidéo de référence, une
    image de son milieu en image d'identité (même sujet, mêmes vêtements)."""
    encodage = _monter(BASE, tour=2, relais=RELAIS)
    assert "MiniMaxH3ImageToVideo" not in _types(encodage)
    ref = _noeud(encodage, "MiniMaxH3ReferenceToVideo")
    entrees = sorted(k for k in ref["inputs"] if k.startswith("ref_"))
    assert entrees == ["ref_image_size", "ref_images.ref_image_0", "ref_videos.ref_video_0"]
    identite = _source(encodage, ref, "ref_images.ref_image_0")
    assert identite["class_type"] == "RepeatImageBatch"
    assert _source(encodage, identite, "image")["class_type"] == "LoadImage"
    queue = _source(encodage, ref, "ref_videos.ref_video_0")
    assert queue["class_type"] == "RepeatImageBatch"
    assert _source(encodage, queue, "image")["class_type"] == "ChargerImages"
    consigne = _source(encodage, ref, "prompt")
    assert consigne["class_type"] == "StringConcatenate"
    assert consigne["inputs"]["string_a"].startswith("<Picture 1> is the same subject")
    assert "<Video 1> is the preceding shot" in consigne["inputs"]["string_a"]
    rangs = [v["inputs"]["rang"] for v in encodage.values() if v["class_type"] == "DirectionDuBloc"]
    assert rangs == [1]                                   # le bloc, pas le numéro de run
    # Ce run n'écrit que ce qu'il produit : le conditionnement et le latent.
    assert sorted(t for t in _types(encodage) if t in ("SaveImage", "SauverImages", "SauverConditionnement",
                                                       "SauverLatent")) == ["SauverConditionnement", "SauverLatent"]
    dernier = _monter(BASE, tour=9, relais=RELAIS)                  # le dernier des 10 runs
    assert not any(t in ("SaveImage", "SauverImages", "SauverConditionnement", "SauverLatent")
                   for t in _types(dernier))


def test_le_premier_bloc_nait_du_texte_et_les_suivants_du_modele_de_reference_a_quatre_pas():
    """Mesuré (essais B2/C2 de l'enquête) : le poids ref2va + sa LoRA turbo à
    quatre pas continue vraiment le plan (SSIM 0,91–0,93 à la couture) en
    30 % de temps de moins que fl2va à huit pas, qui rejouait la trajectoire."""
    def modele(g):
        unet = _noeud(g, "UNETLoader")["inputs"]["unet_name"]
        lora = _noeud(g, "LoraLoaderModelOnly")["inputs"]["lora_name"]
        planning = _noeud(g, "BasicScheduler")
        return unet, lora, _source(g, planning, "steps")["inputs"]["value"]
    premier = modele(_monter(BASE, tour=1, relais=RELAIS))
    assert "fl2va" in premier[0] and "fl2v_turbo_8step" in premier[1] and premier[2] == 8
    for tour in (3, 5):
        suite = modele(_monter(BASE, tour=tour, relais=RELAIS))
        assert "ref2va" in suite[0] and "ref2v_turbo_4step" in suite[1] and suite[2] == 4


def test_la_graine_avance_d_un_bloc_a_l_autre():
    """R0 : même graine = même trajectoire de caméra (mesuré : deux blocs à la
    même graine rembobinent le même mouvement). Le bruit du bloc b est semé
    à graine + b, et la graine demandée reste celle que l'utilisateur voit."""
    for tour, bloc in ((1, 0), (3, 1), (5, 2)):
        rendu = _monter({**BASE, "seed": 71}, tour=tour, relais=RELAIS)
        somme = _source(rendu, _noeud(rendu, "RandomNoise"), "noise_seed")
        assert somme["class_type"] == "ComfyMathExpression" and somme["inputs"]["expression"] == "a + b"
        assert _source(rendu, somme, "values.a")["inputs"]["value"] == 71
        assert somme["inputs"]["values.b"] == bloc


def test_chaque_rendu_relaie_sa_queue_et_son_identite_et_constate_la_couture():
    for tour, bloc in ((1, 0), (3, 1)):
        rendu = _monter(BASE, tour=tour, relais=RELAIS)
        queue = _noeud(rendu, "SauverImages")
        assert queue["inputs"]["filename_prefix"] == "cortex/essai_relais_queue"
        prise = _source(rendu, queue, "images")
        assert prise["class_type"] == "RepeatImageBatch"        # le passe-plat de « livrer »
        prise = _source(rendu, prise, "image")
        assert prise["class_type"] == "GetImageRangeFromBatch"
        # -1 = « les num_frames DERNIÈRES images » : le contrat de GetImageRangeFromBatch
        # (KJNodes, min -1). Mesuré le 2026-09-19 : -22 était refusé à la validation
        # par le moteur, qui ignorait la sortie sans échouer (200 + node_errors), et la
        # queue n'était jamais écrite — le tour 3 échouait 11 min plus tard.
        assert (prise["inputs"]["start_index"], prise["inputs"]["num_frames"]) == (-1, 22)
        ecrits = {v["inputs"]["filename_prefix"] for v in rendu.values() if v["class_type"] == "SaveImage"}
        assert ecrits == {"cortex/essai_relais_derniere_image", "cortex/essai_relais_identite"}
        raccord = _noeud(rendu, "MeilleurRaccord")
        assert raccord["inputs"]["couper"] is False and raccord["inputs"]["bloc"] == bloc
        assert raccord["inputs"]["filename_prefix"] == f"cortex/recits/raccord_{bloc:03d}"
        if bloc == 0:
            assert "reference" not in raccord["inputs"]
        else:
            assert _source(rendu, raccord, "reference")["class_type"] == "LoadImage"
            # Ce que « livrer » découpe, c'est ce que le raccord a laissé passer.
            tier = next(v for v in rendu.values() if v["class_type"] == "GetImageRangeFromBatch"
                        and v["inputs"]["num_frames"] == 41 and v["inputs"]["start_index"] == 0)
            assert _source(rendu, tier, "images") is raccord


def _selection(rendu):
    """Par tier de livraison : (début, nombre, saut, pas, multiplicateur)."""
    tiers = []
    for choisi in (v for v in rendu.values() if v["class_type"] == "VHS_SelectEveryNthImage"):
        lisse = _source(rendu, choisi, "images")
        garde = _source(rendu, lisse, "images")
        lot = _source(rendu, garde, "images")                   # ImageBatch(ancre, tranche)
        tranche = _source(rendu, lot, "image2")
        if tranche["class_type"] == "VRAM_Debug":
            tranche = _source(rendu, tranche, "image_pass")
        # Le pas de sélection est calculé par le moteur (cadence × multiplicateur
        # // fps) : ici on le recalcule depuis les constantes.
        pas = _source(rendu, choisi, "select_every_nth")
        assert pas["class_type"] == "PrimitiveInt" and isinstance(pas["inputs"]["value"], list)
        assert _source(rendu, pas, "value")["class_type"] == "ComfyMathExpression"
        tiers.append((tranche["inputs"]["start_index"], tranche["inputs"]["num_frames"],
                      choisi["inputs"]["skip_first_images"],
                      _source(rendu, lisse, "multiplier")["inputs"]["value"]))
    return sorted(tiers)


def test_a_30_images_seconde_les_images_livrees_se_suivent_sans_trou_ni_doublon():
    """Le lissage tourne par tier (mémoire), mais la sélection d'une image sur
    quatre suit une grille GLOBALE : ce que chaque tier de chaque bloc garde
    est calculé depuis sa place dans le film, pour que bout à bout les
    morceaux fassent exactement une image sur quatre, sans trou ni doublon."""
    constantes = _brut()["constantes"]
    par_bloc = constantes["images_par_bloc"]                        # 124 natives par bloc
    pas = constantes["cadence"] * constantes["multiplicateur"] // BASE["fps"]
    gardees = []
    for bloc in range(3):
        rendu = _monter(BASE, tour=2 * bloc + 1, relais=RELAIS)
        # Bloc 0 : image 0 = ancre, 123 nouvelles ; ensuite 124 nouvelles, l'ancre
        # étant la dernière image du bloc précédent.
        premiere = 1 if bloc == 0 else par_bloc * bloc
        nouvelles = par_bloc - 1 if bloc == 0 else par_bloc
        couvertes = []
        for debut, nombre, saut, mult in _selection(rendu):
            ancre = premiere + debut - 1
            lissees = [mult * ancre + i for i in range(mult * nombre + 1)]
            gardees += lissees[saut::pas]
            couvertes += list(range(debut, debut + nombre))
        assert couvertes == list(range(nouvelles)), (bloc, couvertes)
    assert gardees == sorted(gardees) and len(set(gardees)) == len(gardees)
    assert pas == 4 and gardees == list(range(0, gardees[-1] + 1, pas))


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
        # Les blocs suivants ne relisent pas les images de l'utilisateur : leur
        # référence est le bloc précédent (sa queue, son image d'identité).
        suite = _monter({**BASE, **images}, tour=2, relais=RELAIS)
        assert _types(suite).count("LoadImage") == 1 and _types(suite).count("ChargerImages") == 1
        ref = _noeud(suite, "MiniMaxH3ReferenceToVideo")
        assert sorted(k for k in ref["inputs"] if k.startswith("ref_images.")) == ["ref_images.ref_image_0"]

# -- aucune entrée paresseuse : la branche se décide au montage --------------
#
# Mesuré le 2026-09-18 (tasks/enquete-double-echantillonnage-2026-09-18.md) :
# un commutateur paresseux du moteur (ComfySwitchNode) sur le chemin des
# images, avec un consommateur non paresseux du décodage (le relais
# derniere_image) et un moteur en --cache-none, faisait ré-exécuter modèle,
# échantillonneur et décodage à chaque bloc du milieu. Un run n'envoie donc
# jamais d'entrée paresseuse au moteur : la décision est prise ici.

PARESSEUX = ("ComfySwitchNode", "ComfySoftSwitchNode", "Switch", "ImpactSwitch")


def _amont(g, numero, vus=None):
    """Tout ce qui alimente un nœud, de proche en proche (les numéros)."""
    vus = set() if vus is None else vus
    for v in g[numero]["inputs"].values():
        if isinstance(v, list) and v[0] not in vus:
            vus.add(v[0])
            _amont(g, v[0], vus)
    return vus


def _atteint(g):
    """Ce que le moteur exécutera : les nœuds atteignables depuis une sortie."""
    sorties = [k for k, v in g.items() if v["class_type"] in ("VHS_VideoCombine", "SaveImage")]
    dedans = set(sorties)
    for s in sorties:
        dedans |= _amont(g, s)
    return dedans


def test_aucun_run_ne_porte_d_entree_paresseuse():
    for tour in range(6):
        g = _monter(BASE, tour=tour, relais=RELAIS if tour else None)
        assert not [t for t in _types(g) if t in PARESSEUX], (tour, _types(g))


def test_a_30_images_seconde_chaque_tier_lisse_puis_agrandit_avant_d_ecrire():
    rendu = _monter(BASE, tour=1, relais=RELAIS)
    dedans = _atteint(rendu)
    for combine in (v for v in rendu.values() if v["class_type"] == "VHS_VideoCombine"):
        echelle = rendu[combine["inputs"]["images"][0]]
        assert echelle["class_type"] == "ImageScale"
        agrandi = rendu[echelle["inputs"]["image"][0]]
        assert agrandi["class_type"] == "ImageUpscaleWithModelBatched"
        choisi = rendu[agrandi["inputs"]["images"][0]]
        assert choisi["class_type"] == "VHS_SelectEveryNthImage"
        lisse = rendu[choisi["inputs"]["images"][0]]
        assert lisse["class_type"] == "FrameInterpolate"
        assert rendu[lisse["inputs"]["images"][0]]["class_type"] == "GardeDePlace"
    assert sum(1 for k in dedans if rendu[k]["class_type"] == "FrameInterpolate") == 3
    assert sum(1 for k in dedans if rendu[k]["class_type"] == "ImageUpscaleWithModelBatched") == 3


def test_a_24_images_seconde_les_images_natives_partent_telles_quelles():
    rendu = _monter({**BASE, "fps": 24}, tour=1, relais=RELAIS)
    dedans = _atteint(rendu)
    # Ni FILM ni la garde qui le précède ne sont sur le chemin d'une sortie.
    assert not [k for k in dedans if rendu[k]["class_type"] in ("FrameInterpolate", "GardeDePlace",
                                                                  "FrameInterpolationModelLoader")]
    for combine in (v for v in rendu.values() if v["class_type"] == "VHS_VideoCombine"):
        agrandi = rendu[rendu[combine["inputs"]["images"][0]]["inputs"]["image"][0]]
        assert agrandi["class_type"] == "ImageUpscaleWithModelBatched"
        source = rendu[agrandi["inputs"]["images"][0]]
        assert source["class_type"] in ("GetImageRangeFromBatch", "VRAM_Debug")


def test_sans_agrandisseur_il_n_est_jamais_charge():
    rendu = _monter({**BASE, "agrandir": False}, tour=1, relais=RELAIS)
    dedans = _atteint(rendu)
    assert not [k for k in dedans if rendu[k]["class_type"] in ("ImageUpscaleWithModelBatched",
                                                                  "UpscaleModelLoader")]
    for combine in (v for v in rendu.values() if v["class_type"] == "VHS_VideoCombine"):
        echelle = rendu[combine["inputs"]["images"][0]]
        assert rendu[echelle["inputs"]["image"][0]]["class_type"] == "VHS_SelectEveryNthImage"
