"""LE MÊME SOCLE, TROIS TECHNIQUES QUI N'ONT RIEN EN COMMUN — la preuve sur pièces.

Antoine, 2026-09-17 : « prouve par des tests qui seraient drastiquement
différents mais prouvent que la base est la même, et vérifie ».

Ici tournent LA CHAÎNE DE RÉFÉRENCE (video-revelation, la copie versionnée du
plan) et LES TECHNIQUES DE RÉFÉRENCE (encre, brume, livre — et l'hybride
brume-et-encre), de bout en bout, sur un moteur d'essai qui honore les
tranches : même image, mêmes réglages du plan, même appel final. Le moteur
d'essai ne sait rien des techniques : il lit le nom du graphe qu'on lui donne,
les réglages et les entrées de nœud qu'on lui pousse, et note tout.

Ce qui doit être IDENTIQUE d'une technique à l'autre est mesuré identique :
les douze étapes et leur ordre, l'amont (analyse, culture, intention), les
entrées du plan qui atteignent le nœud de rôle, le découpage en tranches, les
contrôles du plan, le montage, un seul livrable, la durée livrée. Ce qui doit
DIFFÉRER est mesuré différent : les nœuds appelés, les réglages qui atteignent
le nœud, les grandeurs que la technique contrôle.

Le pendant côté nœuds (les mêmes trois techniques sur les mêmes images, le
même plan) est tests/test_meme_socle_trois_techniques.py de comfyui-ink-reveal.
"""

import contextlib
import gc
import json
import pathlib
import shutil
import subprocess
import tempfile
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from comfyui_bridge.adapter import montage_video  # noqa: E402
from comfyui_bridge.adapter.chaines import noeud_de_tranches  # noqa: E402
from comfyui_bridge.adapter.measure import measure  # noqa: E402
from comfyui_bridge.adapter.media import artifact_url, media_kind  # noqa: E402
from comfyui_bridge.api.main import create_app  # noqa: E402
from comfyui_bridge.config import Settings  # noqa: E402
from comfyui_bridge.core import chaine as noyau  # noqa: E402
from comfyui_bridge.core.plan import Artifact, BackendResult  # noqa: E402
from test_chaines_api import SANS_FFMPEG, BackendQuiLivre, _job  # noqa: E402
from test_socle_ink import (CONTROLES_PLAN_DANS_LA_DUREE, CONTROLES_PLAN_TENU_DU_PLAN,  # noqa: E402
                            CONTROLES_PLAN_VALIDE,
                            EXEMPLES, PLAN, TECHNIQUES)
from test_tranches import LARGE  # noqa: E402

RACINE = pathlib.Path(__file__).resolve().parents[1]

# Les trois techniques dont on exige qu'elles n'aient rien en commun ; les
# autres fichiers de référence (l'hybride) tournent aussi, sur le même socle.
TROIS = ("encre", "brume", "livre")

# L'AMONT : les trois graphes que le plan appelle avant de peindre, quelle que
# soit la technique.
AMONT = ("image-iconographe", "image-iconologue", "image-intention")

# LA DEMANDE, la même pour toutes : le plan, son format, son appel.
LARGEUR, HAUTEUR, CADENCE = 128, 128, 10
# DÉCISION ÉCRITE, 2026-09-19 : la durée demandée fait loi, et son plancher est
# passé de 5 à 12 s (test_socle_ink.BORNES_DU_PLAN) — la demande d'essai suit.
DUREE_S, CONTEMPLATION_S, CONCLUSION_S = 12.0, 3.0, 2.0
APPEL = "La suite, bientôt"
IMAGES_REPRISES = 5                      # ce que l'appel d'essai reprend à la conclusion
DEMANDE = {"workflow": "video-revelation", "image": "oeuvre.png",
           "duration_s": DUREE_S, "contemplation_s": CONTEMPLATION_S,
           "conclusion_s": CONCLUSION_S, "cta": APPEL,
           "width": LARGEUR, "height": HAUTEUR, "fps": CADENCE, "seed": 71}

# LE BUDGET QUI DONNE TROIS TRANCHES AU DÉROULEMENT, quelle que soit la
# technique : chaque nœud de déroulement déclare tenir 79 s au plus, soit 790
# images à 10 i/s, de 128 × 128 × 12 = 196 608 octets ; 60 000 000 octets
# logent 305 images par tranche, et 790 / 305 → 3. Une conclusion (2 s + 20 s
# d'allonge = 220 images) et un appel (20 s = 200 images) tiennent d'un tenant.
BUDGET_POUR_TROIS_TRANCHES = 60_000_000

# CE QUE L'AMONT ÉCRIT — le relevé et le plan que chaque technique reçoit tels
# quels, par les entrées que sa technique déclare.
MARKERS = json.dumps({"markers": [{"id": "oe", "label": "l'œuvre", "cx": 0.5, "cy": 0.5,
                                   "w": 1.0, "h": 1.0, "parties": [
    {"nom": "la lanterne", "cx": 0.3, "cy": 0.2, "w": 0.2, "h": 0.14},
    {"nom": "le pont", "cx": 0.5, "cy": 0.45, "w": 0.3, "h": 0.2},
    {"nom": "le visage", "cx": 0.6, "cy": 0.8, "w": 0.2, "h": 0.2}]}]}, ensure_ascii=False)
DIRECTION = json.dumps({
    "intention": "de la lanterne au visage",
    "beats": [{"id": "oe.p1", "label": "la lanterne", "cx": 0.3, "cy": 0.2, "w": 0.2, "h": 0.14,
               "hook": True},
              {"id": "oe.p2", "label": "le pont", "cx": 0.5, "cy": 0.45, "w": 0.3, "h": 0.2},
              {"id": "oe.p3", "label": "le visage", "cx": 0.6, "cy": 0.8, "w": 0.2, "h": 0.2}],
    "temps": {"hook": {"id": "oe.p1"}, "setup": {"ids": ["oe.p2"]},
              "corps": {"ids": ["oe.p3"]}, "conclusion": {"ids": ["oe"]}},
    "climax": "le visage", "climax_coeur": "oe.p3"}, ensure_ascii=False)
RECIT_ANALYSE = {"markers_json": MARKERS, "anchors_json": json.dumps({"ancre": True}),
                 "empreinte": "sha256:essai"}
RECIT_CULTURE = {"culture_json": json.dumps({"identite": "oeuvre-inedite"}),
                 "anchors_json": json.dumps({"ancre": True})}
# Le plan tient les dix contrôles de « plan_valide » : ce sont eux, pas la
# peinture, qui arrêteraient la chaîne avant de dépenser une seconde de rendu.
RECIT_INTENTION = {"hook": "la lanterne", "climax": "le visage", "hook_recouvre_climax": False,
                   "nb_temps": 3, "hook_aire": 0.028, "hook_est_vide": False,
                   "climax_est_central": True, "climax_coeur": "oe.p3", "trajet_retours": 0,
                   "temps_dans_l_approche": True, "part_des_traces": 0.5,
                   "accroche_couverte_par_le_suivant": 0.0, "direction_json": DIRECTION,
                   # Le plan a reçu le budget et s'y est taillé (2026-09-19) : son
                   # minimum tient dans la durée demandée — « le_plan_tient_dans_la_duree ».
                   "duree_s": DUREE_S, "duree_fixe_s": 12.5, "duree_minimale_s": 11.3,
                   "duree_prevue_s": DUREE_S, "temps_retires_pour_la_duree": 0,
                   "duree_detail": "3 temps : fixe 12,5 s + trajets 0 s"}
RECIT_APPEL = {"images_reprises": IMAGES_REPRISES, "images_ecrites": 10}


# -- les graphes de référence, réduits à leurs nœuds et leurs littéraux ----------


def _amont(class_type, inputs):
    return {"1": {"class_type": class_type, "inputs": {"image": "example.png", **inputs}}}


def _deroulement(class_type, propres):
    """Un graphe de déroulement comme les trois de référence : l'image, la
    durée, la cadence, le format, la graine, et le nœud « 61 » qui déclare la
    convention des tranches et sa borne — plus les réglages propres à SA
    technique, en littéral."""
    return {
        "1": {"class_type": "LoadImage", "inputs": {"image": "example.png"}},
        "2": {"class_type": "PrimitiveFloat", "inputs": {"value": 24.0}},
        "3": {"class_type": "PrimitiveInt", "inputs": {"value": 25}},
        "4": {"class_type": "FramesFromDuration", "inputs": {"duration_s": ["2", 0],
                                                            "fps": ["3", 0]}},
        "5": {"class_type": "PrimitiveInt", "inputs": {"value": 704}},
        "6": {"class_type": "PrimitiveInt", "inputs": {"value": 1280}},
        "16": {"class_type": "PrimitiveInt", "inputs": {"value": 11}},
        "61": {"class_type": class_type, "inputs": {
            "markers_json": "", "direction_json": "", "contemplation_s": 4.0,
            "duree_max_s": 79.0, "segment_index": 0, "segment_count": 1, **propres,
            "image": ["1", 0], "frames": ["4", 0], "fps": ["3", 0],
            "out_width": ["5", 0], "out_height": ["6", 0], "seed": ["16", 0]}},
        "17": {"class_type": "CreateVideo", "inputs": {"images": ["61", 0], "fps": ["4", 0]}},
        "18": {"class_type": "SaveVideo", "inputs": {"filename_prefix": "video/essai",
                                                   "video": ["17", 0]}},
    }


def _conclusion(class_type, propres, duree_s):
    return {
        "1": {"class_type": "LoadVideo", "inputs": {"file": "example.mp4"}},
        "2": {"class_type": "GetVideoComponents", "inputs": {"video": ["1", 0]}},
        "3": {"class_type": "PrimitiveFloat", "inputs": {"value": duree_s}},
        "4": {"class_type": "PrimitiveInt", "inputs": {"value": 25}},
        "5": {"class_type": "FramesFromDuration", "inputs": {"duration_s": ["3", 0],
                                                            "fps": ["4", 0]}},
        "16": {"class_type": "PrimitiveInt", "inputs": {"value": 7}},
        "6": {"class_type": class_type, "inputs": {
            "hold_s": 0.5, "depart_s": 0.0, "appel_texte": "", "fermeture_json": "",
            "prolongation_s": 0.0, "allonge_max_s": 20.0, "segment_index": 0,
            "segment_count": 1, **propres,
            "image": ["2", 0], "frames": ["5", 0], "fps": ["4", 0], "seed": ["16", 0]}},
        "17": {"class_type": "CreateVideo", "inputs": {"images": ["6", 0], "fps": ["5", 0]}},
        "18": {"class_type": "SaveVideo", "inputs": {"filename_prefix": "video/essai",
                                                   "video": ["17", 0]}},
    }


GRAPHES = {
    "image-iconographe": _amont("IconographeDocumentation", {"profil": "illustration"}),
    "image-iconologue": _amont("IconologueCulture", {"markers_json": "", "anchors_json": "",
                                                    "empreinte_iconographe": ""}),
    "image-intention": {
        "1": {"class_type": "LoadImage", "inputs": {"image": "example.png"}},
        "70": {"class_type": "DirectionDeStyle", "inputs": {"style_narratif": "reseau-social",
                                                           "style_approche": "peinture-calme"}},
        "62": {"class_type": "RevealDirection", "inputs": {
            "markers_json": "", "culture_json": "", "anchors_json": "",
            "image": ["1", 0], "structure_json": ["70", 0], "approche_json": ["70", 0]}},
    },
    "video-reveal-cinematic-dirige": _deroulement("RevealCinematic", {
        "fond": "washi", "encre": "lavis", "rendu": "ink-bleed", "conduite": "le plan",
        "ambiance": "lanterne", "negatif": "non", "bords": "fondus"}),
    "video-reveal-brume-dirige": _deroulement("RevealBrume", {
        "fond": "brume-blanche", "conduite": "le plan", "bords": "fondus"}),
    "video-reveal-livre-dirige": _deroulement("RevealLivre", {"papier": "ivoire"}),
    "video-reveal-closing": _conclusion("InkClosing", {"fond": "washi", "ambiance": "lanterne"},
                                        11.0),
    "video-reveal-brume-closing": _conclusion("BrumeClosing", {"fond": "brume-blanche"}, 8.0),
    "video-reveal-livre-closing": _conclusion("LivreClosing", {"papier": "ivoire"}, 8.0),
    "video-appel-final": {
        "1": {"class_type": "LoadVideo", "inputs": {"file": "example.mp4"}},
        "3": {"class_type": "PrimitiveFloat", "inputs": {"value": 3.5}},
        "4": {"class_type": "PrimitiveInt", "inputs": {"value": 25}},
        "5": {"class_type": "FramesFromDuration", "inputs": {"duration_s": ["3", 0],
                                                            "fps": ["4", 0]}},
        "16": {"class_type": "PrimitiveInt", "inputs": {"value": 7}},
        "7": {"class_type": "InkCaption", "inputs": {
            "texte": "", "police": "", "duree_max_s": 20.0, "segment_index": 0,
            "segment_count": 1, "duree_s": ["3", 0], "fps": ["4", 0], "seed": ["16", 0],
            "video": ["1", 0]}},
        "17": {"class_type": "CreateVideo", "inputs": {"images": ["7", 0], "fps": ["5", 0]}},
        "18": {"class_type": "SaveVideo", "inputs": {"filename_prefix": "video/essai",
                                                   "video": ["17", 0]}},
    },
}
LIAISONS_VIDEO = {"duration_s": {"node": "2", "input": "value"},
                  "fps": {"node": "3", "input": "value"},
                  "width": {"node": "5", "input": "value"},
                  "height": {"node": "6", "input": "value"},
                  "seed": {"node": "16", "input": "value"},
                  "filename_prefix": {"node": "18", "input": "filename_prefix"}}
LIAISONS_FIN = {"video": {"node": "1", "input": "file"},
                "duration_s": {"node": "3", "input": "value"},
                "fps": {"node": "4", "input": "value"},
                "seed": {"node": "16", "input": "value"},
                "filename_prefix": {"node": "18", "input": "filename_prefix"}}


def _reconciliation(tmp: pathlib.Path) -> dict:
    """Le catalogue de ce poste d'essai : la chaîne de référence, ses menus,
    et les dix graphes déclarés comme le poste réel les déclare."""
    def graphe(nom, **plus):
        return {"kind": "video", "workflow": str(tmp / f"{nom}.json"), **plus}
    entrees = {
        "video-revelation": {"kind": "video", "chaine": str(tmp / "video-revelation.json"),
                             "titre": "Révéler une image", "categorie": "reveler-une-image",
                             "ordre": 1},
        "image-iconographe": {"kind": "image", "workflow": str(tmp / "image-iconographe.json"),
                              "bindings": {"image": {"node": "1", "input": "image"}}},
        "image-iconologue": {"kind": "image", "workflow": str(tmp / "image-iconologue.json"),
                             "bindings": {"image": {"node": "1", "input": "image"}}},
        "image-intention": {"kind": "image", "workflow": str(tmp / "image-intention.json"),
                            "bindings": {"image": {"node": "1", "input": "image"},
                                         "style_narratif": {"node": "70",
                                                            "input": "style_narratif"}}},
        "video-appel-final": graphe("video-appel-final", defaults={"fps": 25},
                                    bindings=LIAISONS_FIN),
    }
    for nom in ("video-reveal-cinematic-dirige", "video-reveal-brume-dirige",
                "video-reveal-livre-dirige"):
        entrees[nom] = graphe(nom, defaults={"duration_s": 24.0, "fps": 25, "width": 704,
                                             "height": 1280},
                              bindings={"image": {"node": "1", "input": "image"},
                                        **LIAISONS_VIDEO})
    for nom in ("video-reveal-closing", "video-reveal-brume-closing",
                "video-reveal-livre-closing"):
        entrees[nom] = graphe(nom, defaults={"duration_s": 8.0, "fps": 25},
                              bindings=LIAISONS_FIN)
    return {
        "categories": {"reveler-une-image": {"titre": "Révéler une image", "ordre": 1}},
        "menus": {
            "style_narratif": {"libelle": "Structure du récit", "source_fichier": {
                "chemin": str(tmp / "narratifs.json"), "table": "styles", "libelle": "libelle"}},
            "style_approche": {"libelle": "Approche", "source_fichier": {
                "chemin": str(tmp / "approches.json"), "table": "styles", "libelle": "libelle"}},
        },
        "workflows": entrees,
    }


# -- le moteur d'essai -------------------------------------------------------


def _artefact(sortie, fichier, mesure=False):
    return Artifact(kind=media_kind(fichier), path=str(fichier.resolve()),
                    url=artifact_url(sortie, fichier), bytes=fichier.stat().st_size,
                    measured=(measure(fichier) or None) if mesure else None)


class BackendDesTechniques(BackendQuiLivre):
    """Un moteur d'essai qui tient TOUS les graphes du plan, sans rien savoir
    des techniques. Les graphes d'amont ne livrent que des nombres (le relevé,
    la culture, l'intention) ; un graphe de rôle livre une vidéo au format et
    à la cadence demandés — la part qu'on lui demande quand on le tranche, le
    même encodage pour toutes — et, à côté, le récit dicté pour CE graphe. Il
    note ce que chaque run a reçu : le graphe, ses réglages, ses entrées de
    nœud."""

    def __init__(self, sortie: pathlib.Path, recits: dict) -> None:
        super().__init__(sortie)
        self.recits = recits                       # graphe → récit
        self.recus: list[tuple[str, dict, dict]] = []

    def submit(self, plan, on_enqueued=None, on_progress=None, on_note=None, on_started=None):
        self.runs.append(plan.workflow)
        self.recus.append((plan.workflow, dict(plan.params), dict(plan.overrides)))
        if on_enqueued:
            on_enqueued("essai-" + str(len(self.runs)), "attached")
        if on_started:
            on_started()
        if on_progress:
            on_progress(1, 1, "1")
        if self.avant is not None:
            self.avant(plan)
        dossier = self.sortie / "cortex"
        dossier.mkdir(parents=True, exist_ok=True)
        nom = str(plan.params.get("filename_prefix", "cortex/essai")).rsplit("/", 1)[-1]
        recit = self.recits.get(plan.workflow)
        if plan.kind != "video":
            fichier = dossier / f"{nom}_{len(self.runs)}.json"
            fichier.write_text(json.dumps(recit or {}, ensure_ascii=False), encoding="utf-8")
            return BackendResult(artifacts=[_artefact(self.sortie, fichier)],
                                 raw_stdout="essai", execution_s=0.5)
        nombre = next((int(v) for k, v in plan.overrides.items()
                       if k.endswith(".segment_count")), 1)
        secondes = float(plan.params.get("duration_s") or 1.0) / max(1, nombre)
        largeur = int(plan.params.get("width") or LARGEUR)
        hauteur = int(plan.params.get("height") or HAUTEUR)
        cadence = int(plan.params.get("fps") or CADENCE)
        fichier = dossier / f"{nom}_{len(self.runs)}.mp4"
        # Du bruit sur la mire : de quoi PESER, le contrôle final l'exige.
        subprocess.run([montage_video.outil(), "-y", "-v", "error", "-f", "lavfi",
                        "-i", f"testsrc2=size={largeur}x{hauteur}:rate={cadence}:"
                              f"duration={secondes}",
                        "-vf", "noise=alls=40:allf=t+u", "-c:v", "libx264",
                        "-preset", "ultrafast", "-crf", "18", "-pix_fmt", "yuv420p",
                        str(fichier)], check=True)
        artefacts = [_artefact(self.sortie, fichier, mesure=True)]
        if recit is not None:
            trace = fichier.with_suffix(".json")
            trace.write_text(json.dumps(recit, ensure_ascii=False), encoding="utf-8")
            artefacts.append(_artefact(self.sortie, trace))
        return BackendResult(artifacts=artefacts, raw_stdout="essai", execution_s=0.5)


def _recit_qui_tient(controles: list[dict], recit: dict) -> dict:
    """Le récit d'un déroulement qui tient chaque contrôle de SA technique, à
    la valeur attendue : ce n'est pas la peinture qu'on juge ici, c'est que la
    chaîne applique à chaque technique la liste qu'ELLE déclare."""
    for c in controles:
        chemin = str(c["valeur"]).split(".")[2:]          # $deroulement.recit.a.b : a, b
        attendu = c.get("attendu")
        if c["op"] == "exists":
            valeur = 1
        elif c["op"] == "between":
            valeur = list(attendu)[0]
        else:                                             # gte, lte, eq : à la valeur attendue
            valeur = attendu
        cible = recit
        for cle in chemin[:-1]:
            cible = cible.setdefault(cle, {})
        cible[chemin[-1]] = valeur
    return recit


def _techniques_de_reference() -> dict:
    """Les techniques DE CE PLAN parmi les copies de référence : celles qui
    tiennent le rôle « deroulement » (depuis le 2026-09-24, le dossier porte
    aussi celles d'un autre plan — créer une image, rôle « image »)."""
    lues = {f.stem: noyau.lire_technique(json.loads(f.read_text(encoding="utf-8")), f.stem)
            for f in sorted(TECHNIQUES.glob("*.json"))}
    return {nom: t for nom, t in lues.items() if "deroulement" in t.roles}


def _recits(techniques: dict) -> dict:
    """Ce que chaque graphe écrit à côté de son rendu."""
    recits = {"image-iconographe": dict(RECIT_ANALYSE), "image-iconologue": dict(RECIT_CULTURE),
              "image-intention": dict(RECIT_INTENTION), "video-appel-final": dict(RECIT_APPEL)}
    for technique in techniques.values():
        graphe = technique.roles["deroulement"].workflow
        recit = recits.setdefault(graphe, {
            "duree_retenue_s": DUREE_S,
            # Ce que tout nœud de déroulement écrit de la durée (2026-09-19) : la
            # demande RÉELLE, la prévue, la retenue, et si elle est tenue — le
            # constat commun « la_duree_est_tenue » le lit, quelle que soit la
            # technique.
            "duree_demandee_s": DUREE_S, "duree_prevue_s": DUREE_S,
            "duree_minimale_s": 11.3, "duree_tenue": True,
            "fermeture_json": json.dumps({"technique": technique.nom, "cadre": [0, 0, LARGEUR, HAUTEUR],
                                          "duree_retenue_s": DUREE_S})})
        _recit_qui_tient(technique.controles["plan_tenu"], recit)
    return recits


def _demonter(tmp: pathlib.Path) -> None:
    """Le poste d'essai disparaît avec la session. Sous Windows, la mémoire
    Hermes (sqlite) reste ouverte tant que sa connexion n'est pas ramassée :
    on ramasse, puis on réessaie — un dossier d'essai qui survit à son test
    est un socle d'essai de plus sur le poste."""
    for _ in range(5):
        gc.collect()
        try:
            shutil.rmtree(tmp)
            return
        except OSError:
            time.sleep(0.5)
    shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture(scope="module")
def atelier():
    """Une passerelle d'essai qui porte la chaîne de référence, les techniques
    de référence telles quelles, et les dix graphes du poste."""
    pile = contextlib.ExitStack()
    patches = pytest.MonkeyPatch()
    pile.callback(patches.undo)
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="comfybridge_meme_socle_"))
    # Un environnement d'essai se démonte tout seul : rien de ce poste d'essai
    # (catalogue, techniques, vidéos de bruit, mémoire Hermes) ne reste après
    # la session.
    pile.callback(_demonter, tmp)
    shutil.copy(EXEMPLES / "video-revelation.json", tmp / "video-revelation.json")
    (tmp / "techniques").mkdir()
    for fichier in TECHNIQUES.glob("*.json"):
        shutil.copy(fichier, tmp / "techniques" / fichier.name)
    for nom, graphe in GRAPHES.items():
        (tmp / f"{nom}.json").write_text(json.dumps(graphe), encoding="utf-8")
    # La structure porte ses temps : le plan ne liste que celles qui ont une
    # accroche (« requiert » du champ), et une structure sans accroche, à côté,
    # ne doit pas apparaître.
    (tmp / "narratifs.json").write_text(json.dumps({"styles": {
        "reseau-social": {"libelle": "Réseau social",
                          "temps": [{"nom": "hook"}, {"nom": "setup"}, {"nom": "corps"}]},
        "continu": {"libelle": "Continu", "temps": [{"nom": "continu"}]}}},
        ensure_ascii=False), encoding="utf-8")
    (tmp / "approches.json").write_text(json.dumps({"styles": {
        "peinture-calme": {"libelle": "Peinture calme"}}}, ensure_ascii=False), encoding="utf-8")
    (tmp / "reconciliation.local.json").write_text(
        json.dumps(_reconciliation(tmp), ensure_ascii=False), encoding="utf-8")
    settings = Settings(comfy_backend="cli", dry_run=True,
                        comfyui_base_url="http://127.0.0.1:9", comfyui_request_timeout_s=1,
                        hermes_db=tmp / "hermes.sqlite3", comfy_output_dir=tmp / "out",
                        hermes_mode="local", workflows_dir=tmp / "workflows",
                        tranche_octets=BUDGET_POUR_TROIS_TRANCHES,
                        attente_place_s=0, attente_place_pas_s=0)
    app = create_app(settings)
    techniques = _techniques_de_reference()
    faux = BackendDesTechniques(settings.comfy_output_dir, _recits(techniques))
    app.state.container.orchestrator._backend = faux
    from comfyui_bridge.adapter import chaines as module
    from comfyui_bridge.adapter import neutral
    # Le poste a de la place (le découpage ne dépend que du budget déclaré), et
    # il n'y a pas de moteur où déposer la queue : le dépôt rend le nom.
    patches.setattr(module.materiel, "mesure_du_poste", lambda: LARGE)
    patches.setattr(neutral, "upload_image", lambda base, nom, contenu, *reste, **autres: nom)
    client = pile.enter_context(TestClient(app))
    client.faux = faux
    client.techniques = techniques
    client.chaine = noyau.lire(json.loads((EXEMPLES / "video-revelation.json")
                                          .read_text(encoding="utf-8")), "video-revelation")
    yield client
    pile.close()


def _produire(atelier, technique: str, cta: str = APPEL):
    """Une production entière sous une technique : le job fini, et ce que le
    moteur a reçu pour elle, run par run."""
    debut = len(atelier.faux.recus)
    r = atelier.post("/v1/render", json={**DEMANDE, "technique": technique, "label": technique,
                                         "cta": cta})
    assert r.status_code == 202, r.text
    job = _job(atelier, r)
    return job, atelier.faux.recus[debut:]


@pytest.fixture(scope="module")
def productions(atelier):
    """La même demande, sous chaque technique de référence."""
    if not montage_video.disponible():
        pytest.skip("ffmpeg/ffprobe absents de ce poste : le recollage ne peut pas être éprouvé")
    faits = {}
    for nom in atelier.techniques:
        job, recus = _produire(atelier, nom)
        assert job["status"] == "succeeded", (nom, job.get("problem"), job["logs"][-8:])
        faits[nom] = (job, recus)
    return faits


def _etape(job, ident):
    return [e for e in job["etapes"] if e["id"] == ident][0]


def _runs(recus, graphe):
    return [(params, overrides) for nom, params, overrides in recus if nom == graphe]


def _sans_segments(overrides):
    return {k: v for k, v in overrides.items() if ".segment_" not in k}


# -- ce qui est identique --------------------------------------------------------


def test_les_douze_etapes_sont_les_memes_et_toutes_tenues(atelier, productions):
    """Le plan est le même fichier pour toutes : les douze étapes, dans cet
    ordre, avec leurs genres — et sous chaque technique, toutes sont tenues."""
    for nom, (job, _) in productions.items():
        assert [(e["id"], e["genre"]) for e in job["etapes"]] == PLAN, nom
        assert [e["statut"] for e in job["etapes"]] == ["done"] * len(PLAN), nom
        assert job["workflow"] == "video-revelation" and job["params"]["technique"] == nom
    # Les réglages du PLAN sont les mêmes valeurs chez toutes ; seuls ceux de la
    # technique s'ajoutent.
    communs = [k for k in atelier.chaine.champs if k != "technique"]
    reglages = {nom: {k: job["params"].get(k) for k in communs}
                for nom, (job, _) in productions.items()}
    assert len({json.dumps(r, sort_keys=True) for r in reglages.values()}) == 1, reglages
    for nom, (job, _) in productions.items():
        propres = set(job["params"]) - set(communs) - {"technique"}
        assert propres == set(atelier.techniques[nom].champs), (nom, propres)


def test_l_amont_est_le_meme_et_seuls_les_roles_changent_de_graphe(atelier, productions):
    """Analyse, culture, intention : trois graphes que la technique ne touche
    pas. Puis le déroulement (en trois tranches), la conclusion et l'appel
    prennent le graphe que la technique déclare pour chaque rôle — et ces
    graphes, chez les trois, ne portent pas les mêmes nœuds."""
    for nom, (job, recus) in productions.items():
        roles = atelier.techniques[nom].roles
        graphes = [g for g, _, _ in recus]
        assert graphes[:3] == list(AMONT), (nom, graphes)
        assert graphes[3:] == [roles["deroulement"].workflow] * 3 + [
            roles["conclusion"].workflow, roles["appel"].workflow], (nom, graphes)
        journal = "\n".join(job["logs"])
        for role in ("deroulement", "conclusion", "appel"):
            assert f"étape {role} : technique {nom} → {roles[role].workflow}" in journal, role
    # Chez les trois : trois nœuds de déroulement, trois nœuds de conclusion,
    # sans un en commun.
    noeuds = {nom: (GRAPHES[atelier.techniques[nom].roles["deroulement"].workflow]["61"]["class_type"],
                    GRAPHES[atelier.techniques[nom].roles["conclusion"].workflow]["6"]["class_type"])
              for nom in TROIS}
    assert len({d for d, _ in noeuds.values()}) == 3 and len({c for _, c in noeuds.values()}) == 3, noeuds
    # L'amont ne reçoit RIEN de la technique : mêmes réglages, mêmes entrées —
    # à UNE entrée près, depuis le 2026-09-19 : la FIN FIXE que le nœud de la
    # technique choisie impose (« $technique.budget.queue_s »), que le plan
    # retranche de la durée pour se tailler dedans. C'est la technique qui dit
    # ce que son nœud fait, comme elle déclare ses entrées ; et chez les trois,
    # ce nombre n'est pas le même.
    queue = "62.queue_s"
    amont = {nom: [(params.get("image"), {k: v for k, v in _sans_segments(overrides).items()
                                          if k != queue})
                   for g, params, overrides in recus if g in AMONT]
             for nom, (_, recus) in productions.items()}
    assert len({json.dumps(a, sort_keys=True) for a in amont.values()}) == 1
    queues = {}
    for nom, (_, recus) in productions.items():
        (_, intention), = [(p, o) for g, p, o in recus if g == "image-intention"]
        attendu = atelier.techniques[nom].donnees["budget"]["queue_s"]
        assert intention[queue] == attendu, (nom, intention[queue], attendu)
        # …et le plan a reçu la durée, la contemplation et la graine de la DEMANDE.
        assert intention["62.duree_s"] == DUREE_S and intention["62.contemplation_s"] == CONTEMPLATION_S
        assert intention["62.seed"] == DEMANDE["seed"]
        queues[nom] = intention[queue]
    assert len({queues[n] for n in TROIS}) == 3, queues


def test_le_plan_atteint_chaque_noeud_de_role_tel_quel_et_le_reste_est_a_la_technique(
        atelier, productions):
    """Ce que le PLAN écrit atteint chaque nœud de déroulement à l'identique :
    l'image, la durée, la cadence, le format, la graine (réglages du run), le
    relevé de l'analyse, le plan de l'intention et la contemplation (entrées
    de nœud). Tout le reste vient de la TECHNIQUE — ses réglages, à ses
    défauts — et chez les trois, ce reste n'a pas une clé en commun."""
    du_plan = {"61.markers_json": MARKERS, "61.direction_json": DIRECTION,
               "61.contemplation_s": CONTEMPLATION_S}
    propres_par_technique = {}
    reglages_par_technique = {}
    for nom, (job, recus) in productions.items():
        technique = atelier.techniques[nom]
        runs = _runs(recus, technique.roles["deroulement"].workflow)
        assert len(runs) == 3, nom
        # Les trois tranches reçoivent les mêmes réglages et les mêmes entrées.
        reglages = [{k: v for k, v in p.items() if k != "filename_prefix"} for p, _ in runs]
        assert reglages[0] == reglages[1] == reglages[2], nom
        entrees = [_sans_segments(o) for _, o in runs]
        assert entrees[0] == entrees[1] == entrees[2], nom
        for cle, valeur in du_plan.items():
            assert entrees[0][cle] == valeur, (nom, cle)
        for cle in ("image", "duration_s", "fps", "width", "height", "seed"):
            assert reglages[0][cle] == DEMANDE[cle], (nom, cle)
        reglages_par_technique[nom] = reglages[0]
        propres = {k: v for k, v in entrees[0].items() if k not in du_plan}
        # …et le reste est exactement ce que la technique déclare, à ses défauts.
        attendu = {k: technique.champs[v[1:]].defaut
                   for k, v in technique.roles["deroulement"].inputs.items()
                   if isinstance(v, str) and v.startswith("$") and v[1:] in technique.champs}
        assert propres == attendu, (nom, propres, attendu)
        propres_par_technique[nom] = propres
    assert len({json.dumps(r, sort_keys=True) for r in reglages_par_technique.values()}) == 1
    # Chez les trois, ces réglages ne sont jamais les mêmes : le livre n'a pas
    # une clé en commun avec les deux autres ; l'encre et la brume visent la
    # même ENTRÉE de nœud (« 61.fond ») depuis deux champs de noms différents
    # (« fond », le papier ; « brume », la teinte de la nappe) et avec d'autres
    # valeurs — et l'encre en porte QUATRE de plus (trois, puis « bords »
    # revenu chez elle le 2026-09-19 : il pèse, un raccourci le nomme).
    propres = propres_par_technique
    assert set(propres["livre"]).isdisjoint(set(propres["encre"]) | set(propres["brume"]))
    assert propres["encre"]["61.fond"] != propres["brume"]["61.fond"]
    assert set(propres["brume"]) < set(propres["encre"])
    assert len(propres["encre"]) - len(propres["brume"]) == 4
    assert propres["encre"]["61.bords"] == "fondus"
    # Les conclusions, de même : le contrat du plan (l'instant de reprise, le
    # texte de l'appel, la fermeture reçue du déroulement) est le même chez
    # toutes ; le reste est à la technique.
    for nom, (job, recus) in productions.items():
        technique = atelier.techniques[nom]
        (_, fin), = _runs(recus, technique.roles["conclusion"].workflow)
        assert fin["6.depart_s"] == DUREE_S and fin["6.appel_texte"] == APPEL, nom
        assert json.loads(fin["6.fermeture_json"])["duree_retenue_s"] == DUREE_S, nom
        (_, appel), = _runs(recus, technique.roles["appel"].workflow)
        assert _sans_segments(appel) == {"7.texte": APPEL, "7.police": ""}, nom
    fins = {nom: {k: v for k, v in _runs(recus, atelier.techniques[nom].roles["conclusion"].workflow)[0][1].items()
                  if k not in ("6.depart_s", "6.appel_texte", "6.fermeture_json")}
            for nom, (_, recus) in productions.items()}
    assert fins["encre"] == {"6.fond": "washi", "6.ambiance": "lanterne"}
    assert fins["brume"] == {"6.fond": "brume-blanche"}
    assert fins["livre"] == {"6.papier": "ivoire"}


def test_le_decoupage_en_tranches_est_le_meme_quelle_que_soit_la_technique(atelier, productions):
    """Le mécanisme ne voit qu'un graphe résolu et son budget : trois tranches
    pour le déroulement de chaque technique, la conclusion et l'appel d'un
    seul tenant — et la technique n'y est pour rien."""
    for nom, (job, recus) in productions.items():
        deroulement = _etape(job, "deroulement")
        assert deroulement["tranches"] == 3 and len(deroulement["job_ids"]) == 3, nom
        technique = atelier.techniques[nom]
        recues = [(o.get("61.segment_index"), o.get("61.segment_count"))
                  for _, o in _runs(recus, technique.roles["deroulement"].workflow)]
        assert recues == [(0, 3), (1, 3), (2, 3)], nom
        for role in ("conclusion", "appel"):
            (_, o), = _runs(recus, technique.roles[role].workflow)
            assert not [k for k in o if ".segment_" in k], (nom, role)
            assert "tranches" not in _etape(job, role), (nom, role)
        journal = "\n".join(job["logs"])
        assert "3 tranches recollées sans ré-encodage" in journal, nom
        assert deroulement["resultat"]["jonctions"]["nombre"] == 2, nom
        # Le récit fusionné des tranches est ce que « plan_tenu » a jugé.
        assert deroulement["resultat"]["recit"]["tranches"] == 3, nom
        assert deroulement["resultat"]["recit"]["duree_retenue_s"] == DUREE_S, nom


def test_les_controles_du_plan_sont_les_memes_et_ceux_de_la_peinture_sont_a_la_technique(
        atelier, productions):
    """Le plan se juge avant de peindre et le livrable après le montage : ces
    deux listes sont celles de la chaîne, identiques chez toutes. La peinture
    se juge sur la liste de SA technique — et chez les trois, ces listes ne
    sont pas les mêmes."""
    tenus = {}
    for nom, (job, _) in productions.items():
        # Le plan se juge sur les dix contrôles de la chaîne PUIS sur ce que la
        # technique exige de lui — l'encre, un quart de temps tracés ; les deux
        # autres, rien (2026-09-17).
        du_plan = ["le_plan_porte_des_traits",
                   "l_accroche_n_est_pas_dans_le_temps_suivant"] if nom == "encre" else []
        for ident, attendu in (("plan_valide", CONTROLES_PLAN_VALIDE + du_plan),
                               ("controle", ["le_montage_a_ses_parts", "livrable_pese"])):
            lignes = _etape(job, ident)["resultat"]["controles"]
            assert [c["id"] for c in lignes] == attendu and all(c["ok"] for c in lignes), (nom, ident)
        # Avant de peindre, la durée se CONSTATE (2026-09-20 : « mentionner une
        # erreur ne doit pas suicider la livraison ») — ici le plan tient.
        dans_la_duree = _etape(job, "plan_dans_la_duree")["resultat"]
        assert dans_la_duree["constat"] is True and dans_la_duree["non_tenus"] == [], nom
        assert [c["id"] for c in dans_la_duree["controles"]] == CONTROLES_PLAN_DANS_LA_DUREE, nom
        # La peinture se CONSTATE sur le constat commun du plan (la durée
        # tenue, 2026-09-19) PUIS sur la liste de sa technique.
        lignes = _etape(job, "plan_tenu")["resultat"]["controles"]
        assert [c["id"] for c in lignes] == CONTROLES_PLAN_TENU_DU_PLAN + [
            c["id"] for c in atelier.techniques[nom].controles["plan_tenu"]], nom
        assert all(c["ok"] for c in lignes), nom
        tenus[nom] = [c["id"] for c in lignes]
    assert len({tuple(t) for t in (tenus[n] for n in TROIS)}) == 3, tenus
    assert "chaque_temps_a_sa_page" in tenus["livre"] and "chaque_temps_a_sa_page" not in tenus["encre"]
    assert "des_traits_et_pas_que_des_blocs" in tenus["encre"] and \
        "des_traits_et_pas_que_des_blocs" not in tenus["brume"]


def test_le_montage_livre_une_seule_video_de_la_meme_duree(productions):
    """Un livrable, trois parts, la conclusion sans ce que l'appel a repris, et
    la même durée à l'image près chez toutes : le montage et le contrôle final
    ne savent pas quelle technique a peint."""
    attendu = DUREE_S + CONCLUSION_S - IMAGES_REPRISES / CADENCE + 1.0     # + 1 s d'appel
    durees = {}
    for nom, (job, _) in productions.items():
        assert len(job["artifacts"]) == 1, nom
        montage = _etape(job, "montage")
        assert montage["resultat"]["parts"] == 3, nom
        livrable = pathlib.Path(job["artifacts"][0]["path"])
        assert livrable.is_file(), nom
        assert livrable.name.startswith(f"{nom}_video-revelation-montage_"), livrable.name
        assert livrable.stat().st_size >= 100_000, nom          # « livrable_pese »
        durees[nom] = montage_video.mesurer(livrable)["duration_s"]
        assert abs(durees[nom] - attendu) <= 0.3, (nom, durees[nom], attendu)
    assert max(durees.values()) - min(durees.values()) <= 0.15, durees


@SANS_FFMPEG
def test_sans_appel_chaque_technique_retombe_a_deux_parts(atelier):
    """L'étape facultative est celle du plan : sans texte, l'appel est sauté et
    le montage retombe à deux parts, quelle que soit la technique."""
    for nom in TROIS:
        job, recus = _produire(atelier, nom, cta="")
        assert job["status"] == "succeeded", (nom, job.get("problem"))
        assert _etape(job, "appel")["statut"] == "skipped", nom
        assert _etape(job, "montage")["resultat"]["parts"] == 2, nom
        assert [g for g, _, _ in recus][-1] == atelier.techniques[nom].roles["conclusion"].workflow
        assert len(job["artifacts"]) == 1, nom


# -- mentionner ne tue pas la livraison ------------------------------------------


def test_un_plan_qui_ne_tient_pas_dans_la_duree_se_constate_et_la_chaine_livre(atelier):
    """DÉCISION ÉCRITE, 2026-09-20 au matin. « Sépia au trait sec, à la
    chandelle » demandé à 12 s sous Peinture calme : le plan taillé à trois
    temps demandait 31,58 s, et « plan_valide » a REFUSÉ — Antoine : « mentionner
    une erreur ne doit pas suicider la livraison ! Les erreurs mentionnées ne
    tuent pas la livraison, elles émettent seulement. » Depuis : l'écart se
    CONSTATE avant de peindre (étape plan_dans_la_duree, chiffrée, avec son
    aide, au journal et à la fiche), le déroulement reçoit la durée que le plan
    demande, et la chaîne livre."""
    if not montage_video.disponible():
        pytest.skip("ffmpeg/ffprobe absents de ce poste : le recollage ne peut pas être éprouvé")
    faux = atelier.faux
    garde = faux.recits["image-intention"]
    # Le plan a reçu 12 s, s'est taillé à trois temps, et il lui en faut 31,58.
    faux.recits["image-intention"] = {**garde, "duree_s": DUREE_S, "duree_minimale_s": 31.58,
                                      "duree_prevue_s": 31.58, "temps_retires_pour_la_duree": 2,
                                      "duree_detail": "3 temps : accroche 2,8 s + tenues 8,6 s "
                                                      "+ trajets 9,2 s + fin 11,0 s"}
    try:
        debut = len(faux.recus)
        # Une autre graine que « productions » : l'intention est gardée par clé,
        # et la même clé reprendrait le plan qui tenait.
        r = atelier.post("/v1/render", json={**DEMANDE, "technique": "encre", "seed": 72,
                                             "label": "sepia-12s"})
        assert r.status_code == 202, r.text
        job = _job(atelier, r)
        recus = faux.recus[debut:]
    finally:
        faux.recits["image-intention"] = garde
    # LA CHAÎNE LIVRE.
    assert job["status"] == "succeeded", (job.get("problem"), job["logs"][-8:])
    assert job["problem"] is None and len(job["artifacts"]) == 1
    assert [e["statut"] for e in job["etapes"]] == ["done"] * len(PLAN)
    # …et l'écart est ÉMIS : le plan se juge tenu (plan_valide, tous verts),
    # puis la durée se constate non tenue, chiffrée, avec son aide.
    assert all(c["ok"] for c in _etape(job, "plan_valide")["resultat"]["controles"])
    constat = _etape(job, "plan_dans_la_duree")["resultat"]
    assert constat["constat"] is True and constat["non_tenus"] == ["le_plan_tient_dans_la_duree"]
    ligne = constat["controles"][0]
    assert (ligne["ok"], ligne["op"], ligne["mesure"], ligne["attendu"]) == (False, "lte", 31.58, DUREE_S)
    assert "constat" in ligne["aide"].lower() and "Visite guidée" in ligne["aide"]
    journal = "\n".join(job["logs"])
    assert ("étape plan_dans_la_duree : constaté, non tenu — le_plan_tient_dans_la_duree : "
            "mesuré 31.58, attendu lte 12.0 — Le plan ne tient pas") in journal
    # Le déroulement a reçu la durée que le PLAN demande (ses trois tranches,
    # toutes), pas la demande brute — et la fiche garde la demande de
    # l'utilisateur telle qu'elle est.
    runs = _runs(recus, atelier.techniques["encre"].roles["deroulement"].workflow)
    assert len(runs) == 3 and all(p["duration_s"] == 31.58 for p, _ in runs), runs
    assert job["demande"]["duration_s"] == DUREE_S and job["params"]["duration_s"] == DUREE_S
    assert _etape(job, "deroulement")["resultat"]["mesure"]["duration_s"] == pytest.approx(31.58, abs=0.4)


# -- les graphes de ce poste ---------------------------------------------------


def test_sur_ce_poste_chaque_role_de_chaque_technique_declare_les_tranches():
    """Ce qui rend le mécanisme agnostique, c'est une CONVENTION que chaque
    nœud de rôle déclare (segment_index, segment_count, et sa borne). Sur ce
    poste, les graphes réels des trois rôles de chaque technique la déclarent
    — sans quoi un rendu lourd repartirait entier sous cette technique-là."""
    donnees = RACINE / "_data"
    if not (donnees / "techniques").is_dir() or not (donnees / "reconciliation.local.json").is_file():
        pytest.skip("pas de _data sur ce poste : rien à vérifier")
    catalogue = json.loads((donnees / "reconciliation.local.json").read_text(encoding="utf-8"))
    noeuds = {}
    for fichier in sorted((donnees / "techniques").glob("*.json")):
        technique = noyau.lire_technique(json.loads(fichier.read_text(encoding="utf-8")), fichier.stem)
        if "deroulement" not in technique.roles:
            continue                      # une technique d'un autre plan (créer une image)
        for role in ("deroulement", "conclusion", "appel"):
            nom = technique.roles[role].workflow
            chemin = pathlib.Path(catalogue["workflows"][nom]["workflow"])
            assert chemin.is_file(), (technique.nom, role, nom)
            graphe = json.loads(chemin.read_text(encoding="utf-8"))
            noeud = noeud_de_tranches(graphe)
            assert noeud is not None, (technique.nom, role, nom)
            entrees = graphe[noeud]["inputs"]
            assert float(entrees.get("duree_max_s") or 0) > 0 or \
                float(entrees.get("allonge_max_s") or 0) > 0, (technique.nom, role, nom)
            noeuds[(technique.nom, role)] = graphe[noeud]["class_type"]
    # Trois techniques, trois nœuds de déroulement et trois de conclusion.
    assert len({noeuds[(t, "deroulement")] for t in TROIS if (t, "deroulement") in noeuds}) == \
        len([t for t in TROIS if (t, "deroulement") in noeuds])
    assert len({noeuds[(t, "conclusion")] for t in TROIS if (t, "conclusion") in noeuds}) == \
        len([t for t in TROIS if (t, "conclusion") in noeuds])
