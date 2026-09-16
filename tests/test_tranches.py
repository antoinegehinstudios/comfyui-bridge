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
from comfyui_bridge.core.errors import BackendExecutionError  # noqa: E402
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

# Le même nœud, qui déclare ALLONGER au-delà de la durée demandée plutôt
# qu'une borne absolue : une conclusion qui garde la page vivante pour l'appel.
ALLONGE_MAX = 2.0
GRAPHE_QUI_ALLONGE = {
    NOEUD: {"class_type": "RenduDEssaiParTranches",
            "inputs": {"segment_index": 0, "segment_count": 1,
                       "allonge_max_s": ALLONGE_MAX, "graine": 71}},
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
        recit.write_text(json.dumps(RECITS_DE_TRANCHE[int(index or 0) % len(RECITS_DE_TRANCHE)],
                                    ensure_ascii=False),
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

    def batir(tranche_octets: int = BUDGET_POUR_TROIS, **plus) -> TestClient:
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="comfybridge_tranches_"))
        (tmp / "video-tranchable.json").write_text(json.dumps(GRAPHE_TRANCHABLE),
                                                   encoding="utf-8")
        (tmp / "video-qui-allonge.json").write_text(json.dumps(GRAPHE_QUI_ALLONGE),
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
                # Le nœud qui allonge la durée demandée d'au plus ALLONGE_MAX.
                "video-qui-allonge": {
                    "kind": "video", "workflow": str(tmp / "video-qui-allonge.json"),
                    "bindings": {"filename_prefix": {"node": "9", "input": "filename_prefix"}},
                    "defaults": {"width": LARGEUR, "height": HAUTEUR, "fps": CADENCE}},
                # Le graphe tranchable SANS taille déclarée : celle d'un graphe
                # qui la prend d'une vidéo d'entrée.
                "video-sans-taille": {
                    "kind": "video", "workflow": str(tmp / "video-tranchable.json"),
                    "bindings": {"filename_prefix": {"node": "9", "input": "filename_prefix"}},
                    "defaults": {}},
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
                            tranche_octets=tranche_octets, **plus)
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
    # UNE TRANCHE N'EST JAMAIS UNE LIVRAISON : le job réussi ne porte que le
    # recollé, jamais les trois parts qui l'ont fait.
    assert len(job["artifacts"]) == 1
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


# -- n'importe quel appelant ---------------------------------------------------


@SANS_FFMPEG
def test_un_graphe_appele_directement_est_tranche_comme_une_etape(banc):
    """Le mécanisme ne regarde ni le mode, ni sa catégorie, ni qui appelle :
    seulement le graphe et le budget. Sans cela, le même nœud tenait en mémoire
    dans une chaîne et débordait appelé seul."""
    atelier = banc(BUDGET_POUR_TROIS)
    r = atelier.post("/v1/render", json={"workflow": "video-tranchable", "duration_s": 2,
                                         "width": LARGEUR, "height": HAUTEUR, "fps": CADENCE,
                                         "label": "direct"})
    assert r.status_code == 202, r.text
    # Le job accepté dit DÉJÀ ce qu'il va faire, comme une chaîne.
    assert [e["id"] for e in r.json()["etapes"]] == ["rendu"]
    job = _job(atelier, r)
    assert job["status"] == "succeeded", job.get("problem")
    rendu = _etape(job, "rendu")
    assert rendu["tranches"] == 3 and len(rendu["job_ids"]) == 3
    assert atelier.faux.tranches_recues == [(0, 3), (1, 3), (2, 3)]
    for sous_id in rendu["job_ids"]:
        assert atelier.get(f"/v1/jobs/{sous_id}").json()["parent"] == job["id"]
    assert "rendu par tranches" in "\n".join(job["logs"])
    # UN livrable, pas trois : les tranches ne sont pas des livraisons.
    assert len(job["artifacts"]) == 1
    livrable = pathlib.Path(job["artifacts"][0]["path"])
    assert livrable.is_file() and 1.7 <= montage_video.mesurer(livrable)["duration_s"] <= 2.3


@SANS_FFMPEG
def test_un_rendu_direct_qui_tient_reste_un_run_ordinaire(banc):
    """La mécanique ne s'invite pas là où on n'en veut pas : sans budget, ou sur
    un graphe qui ne déclare rien, le run part entier et le job n'a pas
    d'étapes — un appelant ne voit rien changer."""
    sans_budget = banc(0)
    job = _job(sans_budget, sans_budget.post("/v1/render", json={
        "workflow": "video-tranchable", "duration_s": 2, "width": LARGEUR,
        "height": HAUTEUR, "fps": CADENCE}))
    assert job["status"] == "succeeded", job.get("problem")
    assert not job["etapes"]
    assert sans_budget.faux.tranches_recues == [(None, None)]

    muet = banc(BUDGET_POUR_TROIS)
    entier = _job(muet, muet.post("/v1/render", json={
        "workflow": "video-entiere", "duration_s": 2, "width": LARGEUR,
        "height": HAUTEUR, "fps": CADENCE}))
    assert entier["status"] == "succeeded", entier.get("problem")
    assert not entier["etapes"] and muet.faux.tranches_recues == [(None, None)]


@SANS_FFMPEG
def test_le_rejeu_d_un_rendu_tranche_est_tranche_aussi(banc):
    """Rejouer, c'est renvoyer la demande gardée : elle repasse par le même
    chemin et retrouve le même découpage. Le rejeu n'est pas une seconde façon
    de lancer un run."""
    atelier = banc(BUDGET_POUR_TROIS)
    premier = _job(atelier, atelier.post("/v1/render", json={
        "workflow": "video-tranchable", "duration_s": 2, "width": LARGEUR,
        "height": HAUTEUR, "fps": CADENCE, "label": "origine"}))
    assert _etape(premier, "rendu")["tranches"] == 3

    r = atelier.post(f"/v1/jobs/{premier['id']}/rejouer", json={"reglages": {}})
    assert r.status_code == 202, r.text
    rejoue = _job(atelier, r)
    assert rejoue["id"] != premier["id"] and rejoue["status"] == "succeeded"
    assert _etape(rejoue, "rendu")["tranches"] == 3
    assert rejoue["demande"]["label"] == "origine"          # la demande ne bouge pas
    assert atelier.faux.tranches_recues == [(0, 3), (1, 3), (2, 3)] * 2


def test_un_probleme_connu_refuse_avant_la_premiere_tranche(banc):
    """Hermes est consulté AVANT, comme pour un run entier : un souvenir qui
    tient encore refuse ici, et non à la troisième tranche — trois runs dépensés
    pour redécouvrir ce que la mémoire savait déjà."""
    from comfyui_bridge.core.problems import OOM
    atelier = banc(BUDGET_POUR_TROIS)
    c = atelier.app.state.container
    # La configuration telle que le plan la signe : 160×120, et les 50 images
    # que 2 s à 25 i/s demandent (`latent_batch`).
    c.registry.record(c.settings.host_id, "video-tranchable",
                      {"width": LARGEUR, "height": HAUTEUR, "latent_batch": 50},
                      status="failed", problem=OOM, detail="CUDA out of memory")
    r = atelier.post("/v1/render", json={"workflow": "video-tranchable", "duration_s": 2,
                                         "width": LARGEUR, "height": HAUTEUR, "fps": CADENCE})
    assert r.status_code == 422
    assert r.headers["content-type"].startswith("application/problem+json")
    assert r.json()["type"].endswith("/reconciliation-refused")
    assert r.json()["problem"] == OOM
    assert atelier.faux.tranches_recues == []               # rien n'a été lancé
    assert not atelier.get("/v1/jobs").json()["jobs"]       # …et aucun job créé


def test_une_demande_hors_de_portee_est_refusee_a_l_appelant_pas_au_service(banc):
    """Appelé directement, un rendu qui demanderait 231 tranches est refusé en
    **422** : c'est la demande qui est hors de portée du poste, pas le service
    qui est en panne — et le message dit quoi baisser. En 500, la passerelle
    portait la faute d'un appelant qui a demandé trop grand."""
    atelier = banc(BUDGET_IMPOSSIBLE)
    r = atelier.post("/v1/render", json={"workflow": "video-tranchable", "duration_s": 2,
                                         "width": LARGEUR, "height": HAUTEUR, "fps": CADENCE})
    assert r.status_code == 422
    assert r.json()["type"].endswith("/input-value-refused")
    assert "tranches" in r.json()["detail"] and str(TRANCHES_MAX) in r.json()["detail"]
    assert atelier.faux.tranches_recues == []
    assert not atelier.get("/v1/jobs").json()["jobs"]


def test_un_champ_hors_modele_reste_refuse_sur_un_graphe(banc):
    """Le découpage s'insère avant le plan, jamais avant le contrôle des champs :
    un nom mal orthographié repartirait sinon avec un 202 sans rien piloter."""
    atelier = banc(BUDGET_POUR_TROIS)
    r = atelier.post("/v1/render", json={"workflow": "video-tranchable", "duration_s": 2,
                                         "width": LARGEUR, "height": HAUTEUR,
                                         "fps": CADENCE, "profondeur": 3})
    assert r.status_code == 422
    assert r.json()["type"].endswith("/unknown-workflow-input")
    assert "profondeur" in r.json()["detail"]
    assert atelier.faux.tranches_recues == []


def test_le_decoupage_se_demande_sans_job_et_sans_journal(banc):
    """`tranches_pour` répond la même chose qu'une étape de chaîne, avant même
    qu'un job existe — c'est ce qui permet de trancher un appel direct. Sans
    parent, rien n'est journalisé : il n'y a personne à qui le dire, et les
    chemins qui parlent d'ordinaire ne doivent pas échouer pour autant."""
    from comfyui_bridge.adapter.chaines import RunnerDeChaines
    atelier = banc(BUDGET_POUR_TROIS)
    runner = RunnerDeChaines(atelier.app.state.container)
    reglages = {"duration_s": 2, "width": LARGEUR, "height": HAUTEUR, "fps": CADENCE}
    tranches = runner.tranches_pour("video-tranchable", reglages)
    assert tranches["nombre"] == 3 and tranches["noeud"] == NOEUD
    assert tranches["images"] == int(math.ceil(DUREE_MAX * CADENCE))
    assert runner.tranches_pour("video-entiere", reglages) is None
    # La taille peut venir des défauts DÉCLARÉS du mode : un appelant qui ne
    # pose que la durée est tranché comme il le sera au rendu.
    assert runner.tranches_pour("video-tranchable", {"duration_s": 2})["nombre"] == 3
    # Les deux chemins qui parlent d'ordinaire à un parent : une taille qu'on ne
    # connaît pas avant le run, et un graphe qui ne se lit pas. Sans parent, ils
    # n'ont personne à qui le dire — et ne doivent pas échouer pour autant.
    assert runner.tranches_pour("video-tranchable",
                                {"duration_s": 2, "width": 0, "height": 0, "fps": 0}) is None
    assert runner.tranches_pour("jamais-declare", reglages) is None
    assert not atelier.get("/v1/jobs").json()["jobs"]


def test_un_noeud_qui_allonge_declare_de_combien(banc):
    """`allonge_max_s` : le nœud allonge d'au plus tant AU-DELÀ de la durée
    demandée — le compte des tranches le prend en plus, là où `duree_max_s`
    est une borne absolue. Jamais moins d'images que le nœud n'en rendra."""
    from comfyui_bridge.adapter.chaines import RunnerDeChaines
    runner = RunnerDeChaines(banc(BUDGET_POUR_TROIS).app.state.container)
    # 2 s demandées + 2 s d'allonge = 100 images : trois tranches, comme le
    # graphe qui déclare une borne absolue de 4 s.
    tranches = runner.tranches_pour("video-qui-allonge", {"duration_s": 2})
    assert tranches["nombre"] == 3
    assert tranches["images"] == int(math.ceil((2 + ALLONGE_MAX) * CADENCE))
    # 0,5 s demandée + 2 s d'allonge : 63 images, deux tranches — l'allonge
    # s'ajoute à la durée demandée, elle ne la remplace pas.
    moins = runner.tranches_pour("video-qui-allonge", {"duration_s": 0.5})
    assert moins["images"] == int(math.ceil((0.5 + ALLONGE_MAX) * CADENCE)) == 63
    assert moins["nombre"] == 2
    # Sans durée demandée, rien à compter : le run part entier.
    assert runner.tranches_pour("video-qui-allonge", {}) is None


@SANS_FFMPEG
def test_la_taille_se_lit_sur_la_video_d_entree_quand_le_graphe_ne_la_dit_pas(banc):
    """Une conclusion reprend la queue du déroulement telle qu'elle est : son
    graphe ne porte ni largeur ni hauteur. La passerelle les lit sur la vidéo
    d'entrée AVANT le run — sans quoi une conclusion 4K partirait entière."""
    from comfyui_bridge.adapter.chaines import RunnerDeChaines
    atelier = banc(BUDGET_POUR_TROIS)
    runner = RunnerDeChaines(atelier.app.state.container)
    # Sans taille et sans vidéo : rien à mesurer, le run part entier.
    assert runner.tranches_pour("video-sans-taille", {"duration_s": 2}) is None
    video = atelier.tmp / "queue.mp4"
    subprocess.run([montage_video.outil(), "-y", "-v", "error", "-f", "lavfi",
                    "-i", f"testsrc=size={LARGEUR}x{HAUTEUR}:rate={CADENCE}:duration=1",
                    "-pix_fmt", "yuv420p", str(video)], check=True)
    tranches = runner.tranches_pour("video-sans-taille", {"duration_s": 2},
                                    media={"video": str(video)})
    assert tranches is not None and tranches["nombre"] == 3
    assert (tranches["largeur"], tranches["hauteur"], tranches["fps"]) == (LARGEUR, HAUTEUR, CADENCE)
    # Une cadence donnée par le run l'emporte sur celle de la vidéo ; la taille
    # manquante vient toujours de la vidéo.
    moins_vite = runner.tranches_pour("video-sans-taille", {"duration_s": 2, "fps": 5},
                                      media={"video": str(video)})
    assert moins_vite is None                     # 20 images de 160×120 tiennent d'un seul tenant
    # Un média qui n'existe pas ne fait pas échouer le compte : il se lit comme
    # « rien à mesurer ».
    assert runner.tranches_pour("video-sans-taille", {"duration_s": 2},
                                media={"video": str(atelier.tmp / "absente.mp4")}) is None


PLEIN = {"totale_octets": 64 * 2 ** 30, "libre_octets": 2 ** 20,
         "commit_libre_octets": 2 ** 20, "par": "essai"}
LARGE = {"totale_octets": 64 * 2 ** 30, "libre_octets": 40 * 2 ** 30,
         "commit_libre_octets": 40 * 2 ** 30, "par": "essai"}


@SANS_FFMPEG
def test_une_tranche_attend_sa_place_quand_le_poste_est_plein(banc, monkeypatch):
    """Mesuré le 2026-09-16 : un voisin charge un modèle de 26 Go à la tranche
    15/31, et l'allocation échoue avec 18 Gio de RAM physique libre (limite de
    commit). La tranche doit ATTENDRE que la place revienne, en le disant."""
    from comfyui_bridge.adapter import chaines as module
    atelier = banc(BUDGET_POUR_TROIS, attente_place_s=60, attente_place_pas_s=0)
    # Le découpage voit la place ; c'est la tranche 1 qui trouve le poste plein,
    # deux fois, avant que la place revienne.
    reponses = [LARGE, PLEIN, PLEIN, LARGE]
    monkeypatch.setattr(module.materiel, "mesure_du_poste",
                        lambda: reponses.pop(0) if len(reponses) > 1 else reponses[0])
    job = _job(atelier, atelier.post("/v1/render", json={"workflow": "chaine-tranchee",
                                                         "secondes": 2, "label": "attente"}))
    assert job["status"] == "succeeded", job.get("problem")
    journal = "\n".join(str(l) for l in job["logs"])
    assert "n'a que 0.0 Gio de marge (physique 0.0 Gio, commit 0.0 Gio)" in journal
    assert "déclarés réservés — attente, jusqu'à 1 min" in journal
    assert "la place est revenue pour la tranche 1/3" in journal
    assert _etape(job, "rendu")["tranches"] == 3


@SANS_FFMPEG
def test_une_tranche_qui_ne_trouve_jamais_sa_place_est_refusee_en_le_disant(banc, monkeypatch):
    from comfyui_bridge.adapter import chaines as module
    atelier = banc(BUDGET_POUR_TROIS, attente_place_s=0, attente_place_pas_s=0)
    reponses = [LARGE]
    monkeypatch.setattr(module.materiel, "mesure_du_poste",
                        lambda: reponses.pop(0) if reponses else PLEIN)
    job = _job(atelier, atelier.post("/v1/render", json={"workflow": "chaine-tranchee",
                                                         "secondes": 2, "label": "sans-place"}))
    assert job["status"] == "failed"
    detail = job["problem"]["detail"]
    assert "la tranche 1/3 n'a pas trouvé sa place en 0 min" in detail
    assert "déclarés réservés" in detail and "materiel.local.json" in detail
    # Aucun run n'est parti : on n'a pas fait échouer le moteur pour le savoir.
    assert atelier.faux.tranches_recues == []


@SANS_FFMPEG
def test_sans_mesure_du_poste_une_tranche_part_sans_attendre(banc, monkeypatch):
    """Un poste qui ne sait pas dire sa mémoire ne retient personne."""
    from comfyui_bridge.adapter import chaines as module
    atelier = banc(BUDGET_POUR_TROIS, attente_place_s=60, attente_place_pas_s=0)
    monkeypatch.setattr(module.materiel, "mesure_du_poste", lambda: None)
    job = _job(atelier, atelier.post("/v1/render", json={"workflow": "chaine-tranchee",
                                                         "secondes": 2, "label": "aveugle"}))
    assert job["status"] == "succeeded", job.get("problem")
    assert not [l for l in job["logs"] if "attente" in str(l)]


@SANS_FFMPEG
def test_le_budget_est_ramene_a_la_marge_du_moment(banc, monkeypatch):
    """Quand le poste garde en ce moment plus que le déclaré, les tranches sont
    taillées sur ce qui est libre MAINTENANT : plus de tranches, jamais plus
    longues — et le journal le dit."""
    from comfyui_bridge.adapter import chaines as module
    atelier = banc(BUDGET_POUR_TROIS, attente_place_s=0, attente_place_pas_s=0)
    # 5 Mo de marge (facteur 1, rien n'est déclaré) : 23 040 000 / 5 000 000 → 5 tranches.
    etroit = {"totale_octets": 64 * 2 ** 30, "libre_octets": 5_000_000,
              "commit_libre_octets": 5_000_000, "par": "essai"}
    monkeypatch.setattr(module.materiel, "mesure_du_poste", lambda: etroit)
    job = _job(atelier, atelier.post("/v1/render", json={"workflow": "chaine-tranchee",
                                                         "secondes": 2, "label": "etroit"}))
    assert job["status"] == "succeeded", job.get("problem")
    assert _etape(job, "rendu")["tranches"] == 5
    journal = "\n".join(str(l) for l in job["logs"])
    assert "budget ramené à 0.0 Gio par la marge du moment" in journal
    assert "rendu en 5 tranches" in journal and "ramené" in journal
    # Chaque tranche a trouvé sa place sans attendre : 20 images × 230 400 = 4,6 Mo < 5 Mo.
    assert "attente" not in journal


@SANS_FFMPEG
def test_une_marge_trop_etroite_pour_64_tranches_est_refusee_avant_le_premier_run(banc, monkeypatch):
    from comfyui_bridge.adapter import chaines as module
    atelier = banc(BUDGET_POUR_TROIS, attente_place_s=0, attente_place_pas_s=0)
    minuscule = {"totale_octets": 64 * 2 ** 30, "libre_octets": 100_000,
                 "commit_libre_octets": 100_000, "par": "essai"}
    monkeypatch.setattr(module.materiel, "mesure_du_poste", lambda: minuscule)
    job = _job(atelier, atelier.post("/v1/render", json={"workflow": "chaine-tranchee",
                                                         "secondes": 2, "label": "minuscule"}))
    assert job["status"] == "failed"
    detail = job["problem"]["detail"]
    assert "dans la marge du moment" in detail and "libérer la mémoire, puis relancer" in detail
    assert atelier.faux.tranches_recues == []


@SANS_FFMPEG
def test_les_tranches_suivantes_attendent_sur_les_images_vraiment_rendues(banc, monkeypatch):
    """Le compte d'avant le run est une borne (4 s déclarées, 100 images) ; le
    backend d'essai rend 2 s en 3 tranches, soit 16 à 17 images chacune. Dès la
    première rendue, une marge qui ne tiendrait pas la borne (34 images) mais
    tient le réel (17) ne fait plus attendre."""
    from comfyui_bridge.adapter import chaines as module
    atelier = banc(BUDGET_POUR_TROIS, attente_place_s=0, attente_place_pas_s=0)
    large = {"totale_octets": 64 * 2 ** 30, "libre_octets": 40 * 2 ** 30,
             "commit_libre_octets": 40 * 2 ** 30, "par": "essai"}
    # 5 Mo : au-dessus de 17 × 230 400 = 3,9 Mo, en dessous de 34 × 230 400 = 7,8 Mo.
    moyen = {"totale_octets": 64 * 2 ** 30, "libre_octets": 5_000_000,
             "commit_libre_octets": 5_000_000, "par": "essai"}
    reponses = [large, large]            # le découpage, puis la garde de la tranche 1
    monkeypatch.setattr(module.materiel, "mesure_du_poste",
                        lambda: reponses.pop(0) if reponses else moyen)
    job = _job(atelier, atelier.post("/v1/render", json={"workflow": "chaine-tranchee",
                                                         "secondes": 2, "label": "reel"}))
    assert job["status"] == "succeeded", job.get("problem")
    assert _etape(job, "rendu")["tranches"] == 3
    assert not [l for l in job["logs"] if "attente" in str(l) or "n'a pas trouvé sa place" in str(l)]


@SANS_FFMPEG
def test_une_tranche_qui_echoue_faute_de_place_est_reprise_apres_attente(banc, monkeypatch):
    """Le poste peut changer ENTRE la garde et l'allocation (mesuré : un modèle
    de 21 Go chargé pendant le rendu). Une tranche qui échoue alors que la marge
    ne tient pas le pic attendu est reprise après attente, en le disant."""
    from comfyui_bridge.adapter import chaines as module
    atelier = banc(BUDGET_POUR_TROIS, attente_place_s=60, attente_place_pas_s=0)
    # Le découpage et la garde de la tranche 1 voient la place ; la tranche 2
    # échoue ; à l'examen, le poste est plein ; la garde de reprise le voit
    # plein une fois, puis la place revient.
    reponses = [LARGE, LARGE, LARGE, PLEIN, PLEIN, LARGE]
    monkeypatch.setattr(module.materiel, "mesure_du_poste",
                        lambda: reponses.pop(0) if len(reponses) > 1 else reponses[0])
    appels = []

    def casse_la_deuxieme(plan):
        appels.append(plan.overrides.get(f"{NOEUD}.segment_index"))
        if len(appels) == 2:
            raise BackendExecutionError("Unable to allocate 8.62 GiB")
    atelier.faux.avant = casse_la_deuxieme
    job = _job(atelier, atelier.post("/v1/render", json={"workflow": "chaine-tranchee",
                                                         "secondes": 2, "label": "reprise"}))
    assert job["status"] == "succeeded", job.get("problem")
    assert _etape(job, "rendu")["tranches"] == 3
    assert appels == [0, 1, 1, 2]                     # la tranche 2 a été reprise une fois
    journal = " | ".join(str(l) for l in job["logs"])
    assert "la tranche 2/3 a échoué (" in journal and "Unable to allocate" in journal
    assert "reprise après attente (essai 1/3)" in journal
    assert "la place est revenue pour la tranche 2/3" in journal


@SANS_FFMPEG
def test_une_tranche_qui_echoue_avec_de_la_place_n_est_pas_reprise(banc, monkeypatch):
    """Un échec avec de la place est celui du nœud : il se dit tel quel, sans
    reprise — reprendre aurait fait tourner trois fois une erreur sûre."""
    from comfyui_bridge.adapter import chaines as module
    atelier = banc(BUDGET_POUR_TROIS, attente_place_s=60, attente_place_pas_s=0)
    monkeypatch.setattr(module.materiel, "mesure_du_poste", lambda: LARGE)
    appels = []

    def casse_la_deuxieme(plan):
        appels.append(plan.overrides.get(f"{NOEUD}.segment_index"))
        if len(appels) == 2:
            raise BackendExecutionError("le nœud a refusé")
    atelier.faux.avant = casse_la_deuxieme
    job = _job(atelier, atelier.post("/v1/render", json={"workflow": "chaine-tranchee",
                                                         "secondes": 2, "label": "noeud"}))
    assert job["status"] == "failed"
    assert "le nœud a refusé" in job["problem"]["detail"]
    assert appels == [0, 1]
    assert not [l for l in job["logs"] if "reprise" in str(l)]


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
