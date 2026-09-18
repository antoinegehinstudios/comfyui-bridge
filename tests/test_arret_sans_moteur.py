"""Arrêter un run que le moteur ne tient plus n'est jamais une erreur serveur.

Mesuré le 2026-09-18 : le moteur mort sous un run, `POST /cancel` rendait 500
(l'interrogation de la file levait avant tout), et le run restait « en cours »
pour toujours ; après le redémarrage de la passerelle, le même appel rendait
encore 500 (un job relu du disque n'était pas réinséré dans le magasin, et
toute écriture levait KeyError). Ici : un moteur injoignable ou qui a oublié
le run CLÔT le job, en le disant ; un job relu redevient pilotable.
"""

import pathlib
import tempfile

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from comfyui_bridge.api.main import create_app  # noqa: E402
from comfyui_bridge.config import Settings  # noqa: E402
from comfyui_bridge.core.jobs import JobStatus  # noqa: E402


@pytest.fixture()
def atelier():
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="comfybridge_arret_"))
    # Le moteur est à une adresse où personne n'écoute : injoignable.
    settings = Settings(comfy_backend="cli", dry_run=True, comfyui_base_url="http://127.0.0.1:9",
                        comfyui_request_timeout_s=1, hermes_db=tmp / "h.sqlite3",
                        comfy_output_dir=tmp / "out", hermes_mode="local",
                        workflows_dir=tmp / "workflows")
    app = create_app(settings)
    with TestClient(app) as c:
        c.store = app.state.container.store
        c.conteneur = app.state.container
        yield c


def _un_run_en_cours(store):
    job = store.create(kind="video", workflow="w", params={})
    store.set_engine_ref(job.id, "prompt-perdu")
    store.set_status(job.id, JobStatus.RUNNING)
    return job


def test_un_moteur_injoignable_clot_le_run_au_lieu_de_rendre_500(atelier):
    job = _un_run_en_cours(atelier.store)
    r = atelier.post(f"/v1/jobs/{job.id}/cancel")
    assert r.status_code == 200, r.text
    assert r.json()["cancelled"] is True and r.json()["how"] == "moteur-absent"
    fiche = atelier.get(f"/v1/jobs/{job.id}").json()
    assert fiche["status"] == "cancelled"
    assert fiche["problem"]["problem_kind"] == "cancelled"
    assert "ne répond plus" in fiche["problem"]["detail"]


def test_un_run_que_le_moteur_a_oublie_est_clos(atelier):
    # Le moteur répond, mais sa file ne connaît plus ce run : il est mort
    # (moteur relancé), pas en cours.
    job = _un_run_en_cours(atelier.store)
    atelier.conteneur.comfyui.queue = lambda: {"pending": [], "running": []}
    r = atelier.post(f"/v1/jobs/{job.id}/cancel")
    assert r.status_code == 200, r.text
    assert r.json() == {"cancelled": True, "how": "moteur-absent",
                        "reason": "le moteur ne connaît plus ce run"}
    assert atelier.get(f"/v1/jobs/{job.id}").json()["status"] == "cancelled"


def test_un_run_deja_fini_que_le_moteur_a_oublie_reste_tel_quel(atelier):
    job = atelier.store.create(kind="video", workflow="w", params={})
    atelier.store.set_engine_ref(job.id, "prompt-fini")
    atelier.store.mark_succeeded(job.id, [], duration_s=1.0)
    atelier.conteneur.comfyui.queue = lambda: {"pending": [], "running": []}
    r = atelier.post(f"/v1/jobs/{job.id}/cancel")
    assert r.status_code == 200 and r.json()["cancelled"] is False
    assert atelier.get(f"/v1/jobs/{job.id}").json()["status"] == "succeeded"


def test_une_chaine_en_cours_au_demarrage_est_close_et_se_reprend():
    # La chaîne s'exécute dans un fil de la passerelle : après un redémarrage,
    # ce fil n'existe plus — la fiche « running » est un zombie.
    import json
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="comfybridge_orphelins_"))
    jobs = tmp / "jobs"
    jobs.mkdir(parents=True)
    fiche = {"id": "abc123", "kind": "video", "workflow": "chaine-x", "status": "running",
             "params": {}, "etapes": [{"id": "rendu", "statut": "running", "job_id": "sous"}],
             "logs": [], "artifacts": [], "demande": {}, "created_at": "2026-09-18T09:00:00+00:00",
             "updated_at": "2026-09-18T09:00:00+00:00"}
    (jobs / "abc123.json").write_text(json.dumps(fiche), encoding="utf-8")
    simple = {**fiche, "id": "def456", "etapes": [], "engine_ref": "au-moteur"}
    (jobs / "def456.json").write_text(json.dumps(simple), encoding="utf-8")
    settings = Settings(comfy_backend="cli", dry_run=True, comfyui_base_url="http://127.0.0.1:9",
                        comfyui_request_timeout_s=1, hermes_db=tmp / "h.sqlite3",
                        comfy_output_dir=tmp / "out", hermes_mode="local",
                        workflows_dir=tmp / "workflows")
    with TestClient(create_app(settings)) as c:
        chaine = c.get("/v1/jobs/abc123").json()
        assert chaine["status"] == "failed"
        assert chaine["problem"]["problem_kind"] == "chaine-interrompue"
        assert "reprendre" in chaine["problem"]["detail"]
        # Un run simple reste aux mains du moteur (rattrapage des runs en vol).
        assert c.get("/v1/jobs/def456").json()["status"] == "running"


def test_un_job_relu_du_disque_redevient_pilotable(atelier):
    # Le redémarrage vide le magasin ; la fiche est sur le disque.
    job = _un_run_en_cours(atelier.store)
    with atelier.store._lock:
        atelier.store._jobs.clear()
    assert atelier.get(f"/v1/jobs/{job.id}").status_code == 200
    r = atelier.post(f"/v1/jobs/{job.id}/cancel")          # levait KeyError → 500
    assert r.status_code == 200, r.text
    assert atelier.get(f"/v1/jobs/{job.id}").json()["status"] == "cancelled"
