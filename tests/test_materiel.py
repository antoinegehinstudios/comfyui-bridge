"""Le matériel du poste, déclaré — et le budget d'une tranche qui en sort.

Antoine, 2026-09-16 : « les limitations matérielles du PC doivent être dans un
fichier de réconciliation, qui permet de faire les calculs pour que le workflow
sache ajuster son nombre d'itérations (au cas où la RAM du PC venait à
changer) ». Le nombre de tranches se DÉDUIT de la mémoire de la machine : écrit
dans le code il aurait fallu le rouvrir à chaque barrette, écrit dans une chaîne
il aurait suivi le flux sur une autre machine, où il aurait été faux.
"""

import contextlib
import json
import pathlib
import tempfile

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from comfyui_bridge.adapter import materiel  # noqa: E402
from comfyui_bridge.adapter.chaines import budget_de_tranche  # noqa: E402
from comfyui_bridge.api.main import create_app  # noqa: E402
from comfyui_bridge.config import Settings  # noqa: E402
from comfyui_bridge.core.errors import WorkflowMappingError  # noqa: E402
from test_tranches import (BUDGET_POUR_TROIS, CADENCE, GRAPHE_TRANCHABLE,  # noqa: E402
                           HAUTEUR, LARGEUR, SANS_FFMPEG, BackendQuiTranche, _job)

# Les valeurs de ce poste : 64 Gio, 28 réservés, un pic à 2,5 fois le poids des
# images — (64 − 28) / 2,5 = 14,4 Gio par tranche.
DECLARE = {
    "memoire": {"totale_octets": 68719476736, "reservee_octets": 30064771072,
                "facteur_de_crete": 2.5},
    "memoire_graphique": {"totale_octets": 12884901888},
    "coeurs": 16,
}


def _poser(tmp: pathlib.Path, declare=DECLARE) -> pathlib.Path:
    (tmp / materiel.FICHIER).write_text(json.dumps(declare, ensure_ascii=False),
                                        encoding="utf-8")
    return tmp


# -- le fichier ----------------------------------------------------------------


def test_le_budget_se_deduit_de_la_memoire_declaree(tmp_path):
    """La formule, écrite une fois : ce qui reste au rendu, divisé par son pic."""
    lu = materiel.lire(_poser(tmp_path))
    assert lu["memoire"]["facteur_de_crete"] == 2.5
    assert lu["coeurs"] == 16 and lu["memoire_graphique"]["totale_octets"] == 12884901888
    budget = materiel.budget_tranche(lu)
    assert round(budget / 2 ** 30, 1) == 14.4
    # Sans fichier, rien n'est deviné : c'est l'appelant qui décide du repli.
    assert materiel.lire(tmp_path / "nulle-part") is None
    assert materiel.budget_tranche(None) is None


def test_un_fichier_qui_ne_tient_pas_est_refuse_en_le_nommant(tmp_path):
    """Un budget négatif ou nul donnerait un nombre de tranches absurde, et on ne
    le découvrirait qu'au premier rendu long."""
    with pytest.raises(WorkflowMappingError, match="memoire"):
        materiel.lire(_poser(tmp_path, {"coeurs": 4}))
    with pytest.raises(WorkflowMappingError, match="totale_octets"):
        materiel.lire(_poser(tmp_path, {"memoire": {"totale_octets": 0,
                                                    "reservee_octets": 1}}))
    with pytest.raises(WorkflowMappingError, match="il ne resterait rien"):
        materiel.lire(_poser(tmp_path, {"memoire": {"totale_octets": 8, "reservee_octets": 8}}))
    with pytest.raises(WorkflowMappingError, match="facteur_de_crete"):
        materiel.lire(_poser(tmp_path, {"memoire": {"totale_octets": 100, "reservee_octets": 10,
                                                    "facteur_de_crete": 0.5}}))
    (tmp_path / materiel.FICHIER).write_text("{ pas du json", encoding="utf-8")
    with pytest.raises(WorkflowMappingError, match="impossible de lire"):
        materiel.lire(tmp_path)


def test_l_ordre_est_surcharge_puis_fichier_puis_repli(tmp_path):
    """Écrit une seule fois : deux lectures de cet ordre auraient fini par se
    contredire — l'une découpant, l'autre disant pourquoi."""
    lu = materiel.lire(_poser(tmp_path))
    assert materiel.budget_et_provenance(lu, 0) == (materiel.budget_tranche(lu),
                                                    materiel.FICHIER)
    assert materiel.budget_et_provenance(lu, 4242)[0] == 4242
    assert materiel.budget_et_provenance(lu, 4242)[1] == materiel.SURCHARGE
    assert materiel.budget_et_provenance(None, 0) == (materiel.REPLI_OCTETS, materiel.REPLI)
    # Le repli est DIT : un budget deviné en silence aurait fait découper (ou
    # pas) sans que personne sache pourquoi.
    assert "repli" in materiel.dire(*materiel.budget_et_provenance(None, 0))
    assert materiel.FICHIER in materiel.dire(*materiel.budget_et_provenance(lu, 0))


def test_la_mesure_du_poste_ne_ment_jamais_par_zero():
    """Elle ne sert qu'à AVERTIR qu'un fichier ment ; rendre zéro plutôt que rien
    aurait fait croire à une machine sans mémoire."""
    mesure = materiel.mesure_du_poste()
    if mesure is None:
        pytest.skip("ce poste ne sait pas dire sa mémoire : le cas est prévu, et dit")
    assert mesure["totale_octets"] > 0 and mesure["libre_octets"] > 0
    assert mesure["par"] in ("psutil", "GlobalMemoryStatusEx")


def test_un_fichier_qui_annonce_plus_que_le_poste_est_signale(tmp_path):
    """« le fichier dit 512 Go, le poste en a 64 » : l'erreur qu'on ne découvrait
    qu'en panne sèche. La marge de 5 % évite de crier sur les 0,13 % qu'un
    système garde toujours pour lui (mesuré : 68 631 527 424 octets rendus pour
    68 719 476 736 déclarés)."""
    enorme = {"memoire": {"totale_octets": 512 * 2 ** 30, "reservee_octets": 2 ** 30,
                          "facteur_de_crete": 2.0}}
    vu = materiel.etat(materiel.lire(_poser(tmp_path, enorme)), 0)
    if vu["mesure"] is None:
        pytest.skip("mémoire non mesurable ici")
    assert any("le fichier dit 512 Go" in a for a in vu["avertissements"])
    # …et les valeurs de CE poste ne déclenchent rien.
    juste = materiel.etat(materiel.lire(_poser(tmp_path)), 0)
    assert not [a for a in juste["avertissements"] if "le fichier dit" in a]


def test_la_marge_du_moment_est_la_plus_petite_des_deux_mesures():
    """Sous Windows, une allocation heurte d'abord la limite de COMMIT (RAM +
    fichier d'échange) : 8,6 Gio refusés avec 18 Gio de RAM physique libre,
    mesuré le 2026-09-16. La marge est donc le plus petit des deux."""
    assert materiel.marge_du_moment({"libre_octets": 18 * 2 ** 30,
                                     "commit_libre_octets": 6 * 2 ** 30}) == 6 * 2 ** 30
    assert materiel.marge_du_moment({"libre_octets": 18 * 2 ** 30}) == 18 * 2 ** 30
    assert materiel.marge_du_moment(None) is None
    assert materiel.marge_du_moment({"par": "rien"}) is None
    assert materiel.dire_la_marge({"libre_octets": 18 * 2 ** 30,
                                   "commit_libre_octets": 6 * 2 ** 30}) ==         "physique 18.0 Gio, commit 6.0 Gio"
    assert materiel.dire_la_marge(None) == "mémoire non mesurable"


def test_un_poste_qui_garde_plus_que_le_declare_est_signale(tmp_path, monkeypatch):
    """Le budget reste déclaré ; mais quand le poste garde EN CE MOMENT plus que
    le fichier ne dit (un voisin a chargé 26 Go), la route le dit — c'est la
    raison pour laquelle une tranche attend."""
    monkeypatch.setattr(materiel, "mesure_du_poste",
                        lambda: {"totale_octets": 64 * 2 ** 30, "libre_octets": 18 * 2 ** 30,
                                 "commit_libre_octets": 7 * 2 ** 30, "par": "essai"})
    vu = materiel.etat(materiel.lire(_poser(tmp_path)), 0)
    assert vu["marge_du_moment_octets"] == 7 * 2 ** 30
    assert any("le poste garde en ce moment 57 Go" in a and "28 Go déclarés" in a
               for a in vu["avertissements"])
    # Le poste dans son état déclaré : rien à dire.
    monkeypatch.setattr(materiel, "mesure_du_poste",
                        lambda: {"totale_octets": 64 * 2 ** 30, "libre_octets": 36 * 2 ** 30,
                                 "commit_libre_octets": 40 * 2 ** 30, "par": "essai"})
    calme = materiel.etat(materiel.lire(_poser(tmp_path)), 0)
    assert not [a for a in calme["avertissements"] if "garde en ce moment" in a]


# -- ce que la passerelle en fait ----------------------------------------------


@pytest.fixture()
def banc():
    """Fabrique : `banc(declare=…, surcharge=…)` rend une passerelle tranchable."""
    pile = contextlib.ExitStack()

    def batir(declare=DECLARE, surcharge: int = 0) -> TestClient:
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="comfybridge_materiel_"))
        (tmp / "video-tranchable.json").write_text(json.dumps(GRAPHE_TRANCHABLE),
                                                   encoding="utf-8")
        if declare is not None:
            _poser(tmp, declare)
        (tmp / "reconciliation.local.json").write_text(json.dumps({
            "workflows": {"video-tranchable": {
                "kind": "video", "workflow": str(tmp / "video-tranchable.json"),
                "bindings": {"filename_prefix": {"node": "9", "input": "filename_prefix"}},
                "defaults": {"width": LARGEUR, "height": HAUTEUR, "fps": CADENCE}}},
        }, ensure_ascii=False), encoding="utf-8")
        settings = Settings(comfy_backend="cli", dry_run=True,
                            comfyui_base_url="http://127.0.0.1:9", comfyui_request_timeout_s=1,
                            hermes_db=tmp / "hermes.sqlite3", comfy_output_dir=tmp / "out",
                            hermes_mode="local", workflows_dir=tmp / "workflows",
                            tranche_octets=surcharge)
        app = create_app(settings)
        app.state.container.orchestrator._backend = BackendQuiTranche(settings.comfy_output_dir)
        client = pile.enter_context(TestClient(app))
        client.faux = app.state.container.orchestrator._backend
        return client

    yield batir
    pile.close()


def test_la_route_dit_le_declare_le_mesure_et_le_budget(banc):
    atelier = banc()
    vu = atelier.get("/v1/materiel").json()
    assert vu["declare"]["memoire"]["reservee_octets"] == 30064771072
    assert vu["budget_tranche_gio"] == 14.4
    assert vu["provenance"] == materiel.FICHIER
    assert vu["mesure"] is None or vu["mesure"]["totale_octets"] > 0

    # Sans fichier : le repli, DIT.
    nu = banc(declare=None).get("/v1/materiel").json()
    assert nu["declare"] is None and nu["provenance"] == materiel.REPLI
    assert nu["budget_tranche_octets"] == materiel.REPLI_OCTETS
    assert any("repli" in a for a in nu["avertissements"])

    # Une surcharge d'essai l'emporte, et se nomme.
    force = banc(surcharge=BUDGET_POUR_TROIS).get("/v1/materiel").json()
    assert force["provenance"] == materiel.SURCHARGE
    assert force["budget_tranche_octets"] == BUDGET_POUR_TROIS


def test_le_budget_du_conteneur_suit_le_meme_ordre(banc):
    """Le découpage et la route lisent le MÊME budget : c'est ce qui permet
    d'expliquer un rendu en vingt-trois tranches sans lire le code."""
    c = banc().app.state.container
    assert budget_de_tranche(c) == (materiel.budget_tranche(c.materiel), materiel.FICHIER)
    autre = banc(surcharge=BUDGET_POUR_TROIS).app.state.container
    assert budget_de_tranche(autre) == (BUDGET_POUR_TROIS, materiel.SURCHARGE)
    assert budget_de_tranche(banc(declare=None).app.state.container) == (
        materiel.REPLI_OCTETS, materiel.REPLI)


@SANS_FFMPEG
def test_le_journal_d_un_rendu_tranche_cite_la_provenance(banc):
    """« budget de 7,5 Gio par tranche, d'après materiel.local.json » : un
    découpage qui change parce que le fichier du poste a bougé doit se lire."""
    # Un poste étroit : (8 − 0,5) / 1 = 7,5 Gio… mais ici on veut trois tranches
    # sur un essai minuscule, donc une mémoire à la mesure de l'essai.
    etroit = {"memoire": {"totale_octets": 24_000_000, "reservee_octets": 8_000_000,
                          "facteur_de_crete": 2.0}}
    atelier = banc(declare=etroit)
    assert atelier.get("/v1/materiel").json()["budget_tranche_octets"] == 8_000_000
    job = _job(atelier, atelier.post("/v1/render", json={
        "workflow": "video-tranchable", "duration_s": 2, "width": LARGEUR,
        "height": HAUTEUR, "fps": CADENCE}))
    assert job["status"] == "succeeded", job.get("problem")
    rendu = [e for e in job["etapes"] if e["id"] == "rendu"][0]
    assert rendu["tranches"] == 3
    journal = "\n".join(job["logs"])
    # Sous le gibioctet, le budget se dit en mébioctets : « 0.0 Gio » se lisait
    # comme une panne alors que c'est celui d'un essai.
    assert f"budget de 8 Mio par tranche, d'après {materiel.FICHIER}" in journal
