"""La REPRISE d'une chaîne échouée, là où elle s'est arrêtée.

Mesuré le 2026-09-16 : un 4K/60 rendu en 57 tranches pendant six heures a
échoué à l'étape suivante, sur un dépôt refusé par le moteur (413). Relancer la
chaîne aurait tout refait. Reprendre, c'est relire ce qui a abouti — livrables
et récits — et repartir à l'étape en échec, avec la même demande.
"""

import pathlib

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from comfyui_bridge.core.errors import BackendExecutionError  # noqa: E402
from test_chaines_api import SANS_FFMPEG, _job, atelier  # noqa: E402,F401


def _casser_le_deuxieme_run(faux):
    """Le deuxième run soumis au moteur échoue — une fois."""
    vus = []

    def avant(plan):
        vus.append(plan.workflow)
        if len(vus) == 2:
            raise BackendExecutionError("dépôt refusé : HTTP Error 413: Content Too Large")
    faux.avant = avant
    return vus


@SANS_FFMPEG
def test_une_chaine_echouee_reprend_a_l_etape_en_echec(atelier):
    vus = _casser_le_deuxieme_run(atelier.faux)
    echoue = _job(atelier, atelier.post("/v1/render", json={"workflow": "chaine-recollee",
                                                           "secondes": 1, "label": "casse"}))
    assert echoue["status"] == "failed" and echoue["problem"]["etape"] == "deux"
    assert [e["statut"] for e in echoue["etapes"]] == ["done", "failed", "skipped", "skipped"]

    reprise = _job(atelier, atelier.post(f"/v1/jobs/{echoue['id']}/reprendre"))
    assert reprise["status"] == "succeeded", reprise.get("problem")
    # L'étape « un » n'a pas été rejouée : un seul run de plus, celui de « deux ».
    assert vus == ["video-essai", "video-essai", "video-essai"]
    un, deux, final, controle = reprise["etapes"]
    assert un["statut"] == "done" and un["note"] == f"repris du job {echoue['id']}"
    assert un["job_id"] == echoue["etapes"][0]["job_id"]           # le même sous-job
    assert deux["statut"] == "done" and deux["note"] is None
    assert final["statut"] == "done" and controle["statut"] == "done"
    journal = " | ".join(str(l) for l in reprise["logs"])
    assert f"reprise demandée du job {echoue['id']}" in journal
    assert "1 étape(s) reprise(s) — un" in journal
    assert "étape un (rendre) reprise du job" in journal
    # Un seul livrable, comme toute chaîne réussie : le montage final.
    assert len(reprise["artifacts"]) == 1
    assert reprise["artifacts"][0]["kind"] == "video"
    # La demande d'origine est celle de la reprise.
    assert reprise["demande"]["label"] == "casse" and reprise["demande"]["secondes"] == 1


@SANS_FFMPEG
def test_une_reprise_refuse_ce_qui_n_a_rien_a_reprendre(atelier):
    reussi = _job(atelier, atelier.post("/v1/render", json={"workflow": "chaine-recollee",
                                                           "secondes": 1, "label": "ok"}))
    assert reussi["status"] == "succeeded"
    refus = atelier.post(f"/v1/jobs/{reussi['id']}/reprendre")
    assert refus.status_code == 422 and "rien à reprendre" in refus.json()["detail"]
    # Un graphe (pas une chaîne) échoué ne se reprend pas : il se rejoue.
    vus = []

    def casse(plan):
        vus.append(plan.workflow)
        raise BackendExecutionError("le moteur a refusé")
    atelier.faux.avant = casse
    graphe = _job(atelier, atelier.post("/v1/render", json={"workflow": "video-essai",
                                                           "duration_s": 1, "label": "g"}))
    assert graphe["status"] == "failed"
    refus = atelier.post(f"/v1/jobs/{graphe['id']}/reprendre")
    assert refus.status_code == 422 and "n'est pas une chaîne" in refus.json()["detail"]
    assert atelier.post("/v1/jobs/inconnu/reprendre").status_code == 404


@SANS_FFMPEG
def test_une_reprise_dont_le_livrable_a_disparu_le_dit_avant_de_rien_depenser(atelier):
    vus = _casser_le_deuxieme_run(atelier.faux)
    echoue = _job(atelier, atelier.post("/v1/render", json={"workflow": "chaine-recollee",
                                                           "secondes": 1, "label": "perdu"}))
    assert echoue["status"] == "failed"
    livrable = pathlib.Path(echoue["etapes"][0]["resultat"]["livrable"])
    livrable.unlink()
    reprise = _job(atelier, atelier.post(f"/v1/jobs/{echoue['id']}/reprendre"))
    assert reprise["status"] == "failed"
    detail = reprise["problem"]["detail"]
    assert "reprise impossible" in detail and "n'existe plus" in detail and livrable.name in detail
    assert vus == ["video-essai", "video-essai"]        # aucun run de plus


def test_une_etape_reprise_de_la_memoire_se_reprend_avec_son_recit(atelier):
    """Une étape gardée par clé (2026-09-19) n'a pas de sous-job quand elle a
    été reprise de la mémoire : une reprise de la chaîne relit son récit dans
    la mémoire, pas sur un sous-job qui n'existe pas — sans quoi l'aval qui
    lit « $plan.recit.hook » repartait sur le résumé de la fiche."""
    from test_chaines_api import RECIT_D_ESSAI
    atelier.faux.recit = {**RECIT_D_ESSAI, "empreinte": "sha256:memoire"}
    premier = _job(atelier, atelier.post("/v1/render", json={
        "workflow": "chaine-qui-se-souvient", "graine": 3}))
    assert premier["status"] == "succeeded", premier.get("problem")
    # Le second passage reprend le plan de la mémoire, puis casse à la peinture.
    vus = []

    def avant(plan):
        vus.append(plan.workflow)
        if len(vus) == 2:                       # relevé, puis la peinture (le plan est repris)
            raise BackendExecutionError("le moteur a refusé")
    atelier.faux.avant = avant
    echoue = _job(atelier, atelier.post("/v1/render", json={
        "workflow": "chaine-qui-se-souvient", "graine": 3}))
    assert echoue["status"] == "failed" and echoue["problem"]["etape"] == "peinture"
    plan = next(e for e in echoue["etapes"] if e["id"] == "plan")
    assert plan["job_id"] is None and plan["resultat"]["memoire"]["reprise"] is True
    reprise = _job(atelier, atelier.post(f"/v1/jobs/{echoue['id']}/reprendre"))
    assert reprise["status"] == "succeeded", reprise.get("problem")
    assert vus == ["sd15-txt2img"] * 3          # un seul run de plus : la peinture
    repris = next(e for e in reprise["etapes"] if e["id"] == "plan")
    assert repris["note"] == f"repris du job {echoue['id']}" and repris["job_id"] is None
    assert repris["resultat"]["recit"]["hook"] == "la lanterne"
    # …et la peinture a reçu la clé longue du récit, que la fiche ne garde pas.
    peinture = next(e for e in reprise["etapes"] if e["id"] == "peinture")
    sous = atelier.get(f"/v1/jobs/{peinture['job_id']}").json()
    assert sous["params"]["prompt"] == "i" * 120
