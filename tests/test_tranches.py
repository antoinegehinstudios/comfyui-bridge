"""Le rendu par TRANCHES : la mémoire ne borne plus la durée d'un plan.

Un nœud qui tient toute une vidéo en mémoire dépasse celle du poste dès qu'un
plan s'allonge. La passerelle demande alors le rendu d'une même simulation en N
tranches successives, et les recolle par copie de flux. Éprouvé ici de bout en
bout, avec un vrai ffmpeg qui écrit les tranches, les recolle et mesure les
jonctions : un backend à blanc n'aurait prouvé que l'enchaînement de riens.
"""

import contextlib
import json
import math
import pathlib
import subprocess
import tempfile

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from comfyui_bridge.adapter import montage_video  # noqa: E402
from comfyui_bridge.adapter.chaines import (OCTETS_PAR_IMAGE, TRANCHES_MAX,  # noqa: E402
                                            fusionner_recits, noeud_de_tranches)
from comfyui_bridge.adapter.measure import measure  # noqa: E402
from comfyui_bridge.adapter.media import artifact_url, media_kind  # noqa: E402
from comfyui_bridge.api.main import create_app  # noqa: E402
from comfyui_bridge.config import Settings  # noqa: E402
from comfyui_bridge.core.plan import Artifact, BackendResult  # noqa: E402
from test_chaines_api import SANS_FFMPEG, BackendQuiLivre, _job  # noqa: E402

# Le nœud d'essai qui déclare savoir rendre une tranche.
NOEUD = "61"

# La taille et la cadence des essais, et la borne haute que le nœud déclare.
LARGEUR, HAUTEUR, CADENCE, DUREE_MAX = 160, 120, 25, 4.0

# Le budget qui donne exactement TROIS tranches : 4 s à 25 i/s = 100 images, à
# 160 × 120 × 12 = 230 400 octets l'image, soit 23 040 000 octets d'un seul
# tenant. 23 040 000 / 8 000 000 = 2,88 → 3.
BUDGET_POUR_TROIS = 8_000_000

# Un budget si petit qu'il faudrait 231 tranches — plus que les 64 qu'un nœud
# accepte : c'est la DEMANDE qui est hors de portée du poste, et il vaut mieux
# le dire que de tenter deux cent trente et un runs.
BUDGET_IMPOSSIBLE = 100_000

# Un graphe au format API de ComfyUI dont le nœud « 61 » déclare, EN LITTÉRAL,
# savoir rendre les images [n·i/N, n·(i+1)/N) d'une même simulation.
GRAPHE_TRANCHABLE = {
    NOEUD: {"class_type": "RenduDEssaiParTranches",
            "inputs": {"segment_index": 0, "segment_count": 1,
                       "duree_max_s": DUREE_MAX, "graine": 71}},
    "9": {"class_type": "SaveVideo",
          "inputs": {"filename_prefix": "cortex/tranche", "images": [NOEUD, 0]}},
}

# Le récit qu'écrit CHAQUE tranche : des mesures prises sur ses images à elle.
# Le minimum d'encre est sur la deuxième, la lumière la plus basse aussi, la
# plus haute également — de quoi voir que la fusion va chercher l'extrême là où
# il est, et non dans la première tranche venue.
RECITS_DE_TRANCHE = [
    {"encre_dans_le_cadre_min": 0.5, "encre_dans_le_cadre_min_s": 1.0,
     "encre_dans_le_cadre": [0.5], "duree_retenue_s": 2.0,
     "lumiere": {"intensite_min": 0.2, "intensite_max": 0.6}},
    {"encre_dans_le_cadre_min": 0.1, "encre_dans_le_cadre_min_s": 2.0,
     "encre_dans_le_cadre": [0.1], "duree_retenue_s": 2.0,
     "lumiere": {"intensite_min": 0.05, "intensite_max": 0.9}},
    {"encre_dans_le_cadre_min": 0.3, "encre_dans_le_cadre_min_s": 3.0,
     "encre_dans_le_cadre": [0.3], "duree_retenue_s": 2.0,
     "lumiere": {"intensite_min": 0.4, "intensite_max": 0.7}},
]

# Une chaîne dont l'unique rendu est celui que la mémoire oblige à trancher.
# Les contrôles lisent le récit FUSIONNÉ : tous en « exists » sauf le premier,
# pour qu'ils tiennent aussi bien sur trois tranches que sur un seul run — c'est
# la MESURE de chacun que le test lit, pas son verdict.
CHAINE_TRANCHEE = {
    "version": 1, "chaine": "chaine-tranchee",
    "resume": "un rendu que la mémoire oblige à trancher",
    "expose": {"secondes": {"type": "FLOAT", "defaut": 2, "min": 1, "max": 4,
                            "libelle": "Durée", "unite": "s"}},
    "etapes": [
        {"id": "rendu", "rendre": {"workflow": "video-tranchable",
                                   "duration_s": "$secondes", "width": LARGEUR,
                                   "height": HAUTEUR, "fps": CADENCE}},
        {"id": "controle", "verifier": [
            {"id": "encre_vue", "valeur": "$rendu.recit.encre_dans_le_cadre_min",
             "op": "gte", "attendu": 0},
            {"id": "instant_du_minimum", "valeur": "$rendu.recit.encre_dans_le_cadre_min_s",
             "op": "exists"},
            {"id": "serie_par_tranche", "valeur": "$rendu.recit.encre_dans_le_cadre",
             "op": "exists"},
            {"id": "lumiere_la_plus_basse", "valeur": "$rendu.recit.lumiere.intensite_min",
             "op": "exists"},
            {"id": "lumiere_la_plus_haute", "valeur": "$rendu.recit.lumiere.intensite_max",
             "op": "exists"},
        ]},
    ],
    "livrable": "$rendu.livrable",
}

# La même demande, sur un graphe qui ne déclare RIEN : même durée, même taille,
# même cadence — seul le nœud manque.
CHAINE_NON_TRANCHEE = {
    "version": 1, "chaine": "chaine-non-tranchee",
    "resume": "le même rendu, sur un graphe qui ne sait pas trancher",
    "expose": {"secondes": {"type": "FLOAT", "defaut": 2, "min": 1, "max": 4,
                            "libelle": "Durée", "unite": "s"}},
    "etapes": [{"id": "rendu", "rendre": {"workflow": "video-entiere",
                                          "duration_s": "$secondes", "width": LARGEUR,
                                          "height": HAUTEUR, "fps": CADENCE}}],
    "livrable": "$rendu.livrable",
}


class BackendQuiTranche(BackendQuiLivre):
    """Un backend d'essai qui HONORE les tranches.

    Il lit la tranche qu'on lui demande dans les entrées de nœud (`segment_index`,
    `segment_count`), écrit la part de vidéo correspondante — le MÊME encodage
    pour toutes, sans quoi la copie de flux n'aurait rien à joindre — et, à côté,
    le récit de CETTE tranche.
    """

    def __init__(self, sortie: pathlib.Path) -> None:
        super().__init__(sortie)
        self.tranches_recues: list[tuple] = []

    def submit(self, plan, on_enqueued=None, on_progress=None, on_note=None, on_started=None):
        self.runs.append(plan.workflow)
        if on_enqueued:
            on_enqueued("essai-" + str(len(self.runs)), "attached")
        if on_started:
            on_started()
        if on_progress:
            on_progress(1, 1, "1")
        index = plan.overrides.get(f"{NOEUD}.segment_index")
        nombre = plan.overrides.get(f"{NOEUD}.segment_count")
        self.tranches_recues.append((index, nombre))
        if self.avant is not None:
            self.avant(plan)               # le crochet : ce qui arrive PENDANT un run
        dossier = self.sortie / "cortex"
        dossier.mkdir(parents=True, exist_ok=True)
        nom = str(plan.params.get("filename_prefix", "cortex/essai")).rsplit("/", 1)[-1]
        fichier = dossier / f"{nom}_{len(self.runs)}.mp4"
        secondes = float(plan.params.get("duration_s") or 1) / float(nombre or 1)
        subprocess.run([montage_video.outil(), "-y", "-v", "error", "-f", "lavfi",
                        "-i", f"testsrc=size={LARGEUR}x{HAUTEUR}:rate={CADENCE}:"
                              f"duration={secondes}",
                        "-pix_fmt", "yuv420p", str(fichier)], check=True)
        recit = fichier.with_suffix(".json")
        recit.write_text(json.dumps(RECITS_DE_TRANCHE[int(index or 0)], ensure_ascii=False),
                         encoding="utf-8")
        artefacts = [Artifact(kind=media_kind(f), path=str(f.resolve()),
                              url=artifact_url(self.sortie, f), bytes=f.stat().st_size,
                              measured=(measure(f) or None) if f is fichier else None)
                     for f in (fichier, recit)]
        return BackendResult(artifacts=artefacts, raw_stdout="essai", execution_s=0.5)


@pytest.fixture()
def banc():
    """Fabrique de passerelles d'essai : `banc(budget)` en rend une.

    Le budget d'une tranche est ce qu'on fait varier d'un cas à l'autre — le
    reste du catalogue ne bouge pas, pour que la seule différence éprouvée soit
    celle-là.
    """
    pile = contextlib.ExitStack()

    def batir(tranche_octets: int = BUDGET_POUR_TROIS) -> TestClient:
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="comfybridge_tranches_"))
        (tmp / "video-tranchable.json").write_text(json.dumps(GRAPHE_TRANCHABLE),
                                                   encoding="utf-8")
        (tmp / "chaine-tranchee.json").write_text(json.dumps(CHAINE_TRANCHEE), encoding="utf-8")
        (tmp / "chaine-non-tranchee.json").write_text(json.dumps(CHAINE_NON_TRANCHEE),
                                                      encoding="utf-8")
        (tmp / "reconciliation.local.json").write_text(json.dumps({
            "categories": {"essais": {"titre": "Essais", "ordre": 1}},
            "workflows": {
                "video-tranchable": {
                    "kind": "video", "workflow": str(tmp / "video-tranchable.json"),
                    "bindings": {"filename_prefix": {"node": "9", "input": "filename_prefix"}},
                    "defaults": {"width": LARGEUR, "height": HAUTEUR, "fps": CADENCE}},
                # Le même rendu sans le nœud qui déclare les tranches : le
                # gabarit livré suffit, il n'a pas d'entrée « segment_index ».
                "video-entiere": {
                    "kind": "video", "workflow": "workflow_template.json",
                    "bindings": {"filename_prefix": {"node": "9", "input": "filename_prefix"}},
                    "defaults": {"width": LARGEUR, "height": HAUTEUR, "fps": CADENCE}},
                "chaine-tranchee": {"kind": "video", "chaine": str(tmp / "chaine-tranchee.json"),
                                    "titre": "Chaîne tranchée", "categorie": "essais",
                                    "ordre": 1},
                "chaine-non-tranchee": {"kind": "video",
                                        "chaine": str(tmp / "chaine-non-tranchee.json"),
                                        "titre": "Chaîne d'un seul tenant",
                                        "categorie": "essais", "ordre": 2},
            },
        }, ensure_ascii=False), encoding="utf-8")
        settings = Settings(comfy_backend="cli", dry_run=True,
                            comfyui_base_url="http://127.0.0.1:9", comfyui_request_timeout_s=1,
                            hermes_db=tmp / "hermes.sqlite3", comfy_output_dir=tmp / "out",
                            hermes_mode="local", workflows_dir=tmp / "workflows",
                            tranche_octets=tranche_octets)
        app = create_app(settings)
        faux = BackendQuiTranche(settings.comfy_output_dir)
        app.state.container.orchestrator._backend = faux
        client = pile.enter_context(TestClient(app))
        client.faux = faux
        client.tmp = tmp
        return client

    yield batir
    pile.close()


def _etape(job: dict, ident: str) -> dict:
    return [e for e in job["etapes"] if e["id"] == ident][0]


# -- de bout en bout -----------------------------------------------------------


@SANS_FFMPEG
def test_un_rendu_trop_lourd_est_demande_en_tranches(banc):
    """Ce n'est pas au demandeur de raccourcir sa vidéo : la passerelle découpe
    la MÊME simulation et dit au nœud quelle part rendre."""
    atelier = banc(BUDGET_POUR_TROIS)
    job = _job(atelier, atelier.post("/v1/render", json={"workflow": "chaine-tranchee",
                                                         "secondes": 2, "label": "tranche"}))
    assert job["status"] == "succeeded", job.get("problem")
    rendu = _etape(job, "rendu")
    assert rendu["tranches"] == 3 and len(rendu["job_ids"]) == 3
    assert rendu["note"] == "3 tranches"          # ce que la fiche montre une fois fini
    assert rendu["resultat"]["tranches"] == 3
    assert rendu["resultat"]["job_ids"] == rendu["job_ids"]
    # Chaque tranche a reçu SON rang et le compte, dans l'ordre.
    assert atelier.faux.tranches_recues == [(0, 3), (1, 3), (2, 3)]
    # Ce sont trois runs ordinaires, visibles comme les autres, et qui disent
    # de qui ils sont.
    for sous_id in rendu["job_ids"]:
        sous = atelier.get(f"/v1/jobs/{sous_id}").json()
        assert sous["parent"] == job["id"] and sous["status"] == "succeeded"
    assert rendu["job_id"] == rendu["job_ids"][-1]


@SANS_FFMPEG
def test_les_tranches_sont_recollees_sans_reencodage_et_les_jonctions_mesurees(banc):
    """Le livrable est UNE vidéo de la durée demandée, et aucune image n'a été
    ré-encodée : le recollage ne coûte aucune génération de perte de plus que
    le rendu d'un seul tenant. Les jonctions sont mesurées et dites."""
    atelier = banc(BUDGET_POUR_TROIS)
    job = _job(atelier, atelier.post("/v1/render", json={"workflow": "chaine-tranchee",
                                                         "secondes": 2, "label": "joint"}))
    assert job["status"] == "succeeded", job.get("problem")
    livrable = pathlib.Path(job["artifacts"][0]["path"])
    assert livrable.is_file() and livrable.suffix == ".mp4"
    assert 1.7 <= montage_video.mesurer(livrable)["duration_s"] <= 2.3
    journal = "\n".join(job["logs"])
    assert "3 tranches recollées sans ré-encodage" in journal
    assert "jonctions mesurées" in journal
    rendu = _etape(job, "rendu")
    assert rendu["resultat"]["jonctions"]["nombre"] == 2       # trois parts, deux frontières
    assert 0.0 <= rendu["resultat"]["jonctions"]["pire"] <= 1.0


@SANS_FFMPEG
def test_les_recits_des_tranches_sont_fusionnes_en_un_seul(banc):
    """Une étape « verifier » contrôle le récit sans savoir qu'il a été rendu en
    trois fois : l'extrême est allé le chercher dans la tranche qui le porte, et
    la série des tranches est dans l'ordre."""
    atelier = banc(BUDGET_POUR_TROIS)
    job = _job(atelier, atelier.post("/v1/render", json={"workflow": "chaine-tranchee",
                                                         "secondes": 2}))
    assert job["status"] == "succeeded", job.get("problem")
    # Les scalaires, dans la fiche du job.
    fiche = _etape(job, "rendu")["resultat"]["recit"]
    assert fiche["encre_dans_le_cadre_min"] == 0.1              # le minimum des trois
    assert fiche["encre_dans_le_cadre_min_s"] == 2.0            # l'instant de CETTE tranche
    assert fiche["duree_retenue_s"] == 2.0                      # identique : gardé tel quel
    assert fiche["tranches"] == 3
    # Les listes et les objets, par ce que les contrôles ont mesuré.
    mesures = {c["id"]: c["mesure"] for c in _etape(job, "controle")["resultat"]["controles"]}
    assert mesures["serie_par_tranche"] == [0.5, 0.1, 0.3]      # une par tranche, dans l'ordre
    assert mesures["lumiere_la_plus_basse"] == 0.05             # l'objet fusionné de même
    assert mesures["lumiere_la_plus_haute"] == 0.9
    assert all(c["ok"] for c in _etape(job, "controle")["resultat"]["controles"])


@SANS_FFMPEG
def test_des_tranches_que_la_copie_refuse_sont_recollees_en_le_disant(banc, monkeypatch):
    """Des parts que la copie de flux ne sait pas joindre (un encodeur ou des
    réglages qui diffèrent) sont recollées en ré-encodant — une génération de
    perte de plus, jamais silencieuse : le journal la dit."""
    from comfyui_bridge.core.errors import MediaAssemblyError

    def refuse(parts, sortie):
        raise MediaAssemblyError("essai : deux parts n'ont pas le même encodage")

    monkeypatch.setattr(montage_video, "concatener", refuse)
    atelier = banc(BUDGET_POUR_TROIS)
    job = _job(atelier, atelier.post("/v1/render", json={"workflow": "chaine-tranchee",
                                                         "secondes": 2}))
    assert job["status"] == "succeeded", job.get("problem")
    journal = "\n".join(job["logs"])
    assert "copie de flux impossible" in journal and "recollage ré-encodé" in journal
    assert "3 tranches recollées en ré-encodant" in journal
    livrable = pathlib.Path(job["artifacts"][0]["path"])
    assert livrable.is_file()
    assert 1.7 <= montage_video.mesurer(livrable)["duration_s"] <= 2.3


@SANS_FFMPEG
def test_sans_budget_le_rendu_part_d_un_seul_tenant(banc):
    """`COMFY_TRANCHE_GO=0` : jamais de tranche. Le rendu part entier, comme
    avant — la mécanique ne s'invite pas là où on n'en veut pas."""
    atelier = banc(0)
    job = _job(atelier, atelier.post("/v1/render", json={"workflow": "chaine-tranchee",
                                                         "secondes": 2}))
    assert job["status"] == "succeeded", job.get("problem")
    rendu = _etape(job, "rendu")
    assert "tranches" not in rendu and "job_ids" not in rendu
    assert atelier.faux.tranches_recues == [(None, None)]       # aucune entrée forcée
    assert "tranches" not in "\n".join(job["logs"])


@SANS_FFMPEG
def test_un_graphe_qui_ne_declare_rien_n_est_jamais_tranche(banc):
    """Le découpage est une CONVENTION que le nœud déclare (`segment_index`,
    `segment_count`) : sans elle, trancher rendrait trois fois la même vidéo
    entière, et le recollage en ferait une vidéo trois fois trop longue."""
    atelier = banc(BUDGET_POUR_TROIS)
    job = _job(atelier, atelier.post("/v1/render", json={"workflow": "chaine-non-tranchee",
                                                         "secondes": 2}))
    assert job["status"] == "succeeded", job.get("problem")
    rendu = _etape(job, "rendu")
    assert "tranches" not in rendu
    assert atelier.faux.tranches_recues == [(None, None)]


@SANS_FFMPEG
def test_un_arret_demande_est_honore_entre_deux_tranches(banc):
    """Une tranche n'est pas un détail d'exécution qu'on ne pourrait plus
    arrêter : l'arrêt est pris entre deux tranches comme il l'est entre deux
    étapes, et rien n'est retenu contre la chaîne."""
    atelier = banc(BUDGET_POUR_TROIS)
    conteneur = atelier.app.state.container

    def arreter(_plan):
        for j in conteneur.store.list(10):
            if j.etapes and j.status.value == "running":
                conteneur.store.request_cancel(j.id)

    atelier.faux.avant = arreter
    job = _job(atelier, atelier.post("/v1/render", json={"workflow": "chaine-tranchee"}))
    assert job["status"] == "cancelled"
    assert job["problem"]["problem_kind"] == "cancelled"
    assert len(atelier.faux.tranches_recues) == 1      # la deuxième n'a pas été lancée
    assert _etape(job, "rendu")["statut"] != "done"


def test_une_demande_hors_de_portee_du_poste_est_refusee_en_le_disant(banc):
    """Deux cent trente et une tranches, ce n'est plus un découpage : c'est une
    demande que ce poste ne peut pas tenir. Le dire vaut mieux que d'essayer."""
    atelier = banc(BUDGET_IMPOSSIBLE)
    job = _job(atelier, atelier.post("/v1/render", json={"workflow": "chaine-tranchee",
                                                         "secondes": 2}))
    assert job["status"] == "failed"
    assert "tranches" in job["problem"]["detail"]
    assert str(TRANCHES_MAX) in job["problem"]["detail"]
    assert atelier.faux.tranches_recues == []                   # rien n'a été lancé


# -- ce qu'un nœud déclare, et la fusion des récits ----------------------------


def test_un_noeud_declare_savoir_trancher_par_deux_entrees_litterales():
    """Une entrée LIÉE à un autre nœud n'est pas un réglage qu'on puisse écrire :
    la forcer n'écrirait rien, et le rendu partirait en trois fois entier."""
    assert noeud_de_tranches(GRAPHE_TRANCHABLE) == NOEUD
    lie = {"61": {"class_type": "X", "inputs": {"segment_index": ["12", 0],
                                                "segment_count": 3}}}
    assert noeud_de_tranches(lie) is None
    assert noeud_de_tranches({"9": {"class_type": "SaveVideo", "inputs": {"images": ["8", 0]}}}) \
        is None
    assert noeud_de_tranches({}) is None
    # Un seul des deux ne suffit pas : le nœud ne saurait pas de combien de
    # tranches il fait partie.
    assert noeud_de_tranches({"61": {"inputs": {"segment_index": 0}}}) is None


def test_la_fusion_garde_le_fait_et_va_chercher_la_mesure():
    """Ce qui est identique d'une tranche à l'autre est un fait du plan — la même
    simulation l'a écrit. Ce qui diffère est une mesure prise sur les images de
    la tranche : l'extrême est celui de toutes, avec SON instant."""
    fusionne = fusionner_recits([
        {"plan": "le même", "encre_min": 0.5, "encre_min_s": 1.0, "chaleur_max": 3,
         "serie": [1, 2], "lumiere": {"intensite_min": 0.2}},
        {"plan": "le même", "encre_min": 0.1, "encre_min_s": 2.0, "chaleur_max": 9,
         "serie": [3], "lumiere": {"intensite_min": 0.05}},
    ])
    assert fusionne["plan"] == "le même"                 # identique : gardé tel quel
    assert fusionne["encre_min"] == 0.1                  # le minimum des deux…
    assert fusionne["encre_min_s"] == 2.0                # …et l'instant de SA tranche
    assert fusionne["chaleur_max"] == 9                  # le maximum, de même
    assert fusionne["serie"] == [1, 2, 3]                # les listes se suivent
    assert fusionne["lumiere"] == {"intensite_min": 0.05}   # les objets, récursivement
    assert fusionne["tranches"] == 2
    assert "tranches_divergentes" not in fusionne


def test_un_scalaire_qui_diverge_sans_le_dire_est_nomme():
    """Un nombre qui change d'une tranche à l'autre sans dire s'il est un
    minimum ou un maximum ne se fusionne pas : garder la première valeur en
    silence aurait laissé un contrôle juger sur un tiers de la vidéo."""
    fusionne = fusionner_recits([
        {"hook": "la lanterne", "tenue_s": 2.4, "cadre": {"haut": 1, "bas": 2}},
        {"hook": "le seuil", "tenue_s": 9.9, "cadre": {"haut": 1, "bas": 7}},
    ])
    assert fusionne["hook"] == "la lanterne" and fusionne["tenue_s"] == 2.4
    assert fusionne["cadre"] == {"haut": 1, "bas": 2}
    assert sorted(fusionne["tranches_divergentes"]) == ["cadre.bas", "hook", "tenue_s"]
    # Un seul récit n'est pas une fusion : il est rendu tel quel, sans compte.
    seul = fusionner_recits([{"hook": "la lanterne"}])
    assert seul == {"hook": "la lanterne"}
    assert fusionner_recits([]) == {}


def test_un_extreme_identique_garde_l_instant_de_la_premiere_et_le_dit():
    """Le même minimum atteint à deux instants différents : l'extrême ne
    départage rien, la première tranche fait foi — et l'écart se nomme, sans
    quoi un contrôle aurait lu une seconde prise sur un tiers de la vidéo en la
    croyant prise sur tout."""
    fusionne = fusionner_recits([{"encre_min": 0.2, "encre_min_s": 1.0},
                                 {"encre_min": 0.2, "encre_min_s": 9.0}])
    assert fusionne["encre_min"] == 0.2 and fusionne["encre_min_s"] == 1.0
    assert fusionne["tranches_divergentes"] == ["encre_min_s"]


def test_une_cle_qu_une_seule_tranche_porte_est_nommee_des_deux_cotes():
    """Une clé que la première tranche est seule à porter se nommait déjà ; une
    clé qu'une tranche SUIVANTE est seule à porter se perdait en silence, et un
    contrôle cherchait une clé qu'un nœud avait pourtant écrite. Les deux
    manques se disent maintenant de la même façon."""
    devant = fusionner_recits([{"encre": 1, "climax": "la lanterne"}, {"encre": 1}])
    assert devant["tranches_divergentes"] == ["climax"]
    derriere = fusionner_recits([{"encre": 1}, {"encre": 1, "climax": "la lanterne"}])
    assert derriere["tranches_divergentes"] == ["climax"]
    # La valeur n'est pas inventée pour autant : la première tranche ne l'a pas
    # mesurée, et le récit fusionné ne peut pas dire ce qu'elle vaut sur tout.
    assert "climax" not in derriere


def test_le_budget_se_lit_comme_la_memoire_qu_une_tranche_demande():
    """La formule que le poste a mesurée : une image RVB en float32, fois le
    nombre d'images de la durée BORNÉE par ce que le nœud déclare tenir."""
    assert OCTETS_PAR_IMAGE == 12
    images = int(math.ceil(DUREE_MAX * CADENCE))
    demande = images * LARGEUR * HAUTEUR * OCTETS_PAR_IMAGE
    assert demande == 23_040_000
    assert int(math.ceil(demande / BUDGET_POUR_TROIS)) == 3
    assert int(math.ceil(demande / BUDGET_IMPOSSIBLE)) > TRANCHES_MAX
