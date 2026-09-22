"""Ce qui FIGE le service se voit, et un lancement rejoué ne double pas.

Le 2026-09-22, Antoine : « il y a encore un failed to fetch sur maestro, cela
bloque les productions ». Mesuré ce jour-là sur la passerelle en service : un
premier `GET /v1/workflows` a tenu 14,6 s et, pendant ce temps, `/healthz`
(13,6 s), `/v1/jobs` (13,5 s) et `/v1/file` (13,3 s) ont attendu — la boucle
était tenue par le catalogue. Du dehors : un écran qui ne répond plus, et pas
une ligne de trace de ce côté-ci.

Deux choses ici : la MESURE (un appel lent ou raté laisse une trace datée, le
veilleur dit quand la boucle elle-même était tenue) et l'IDEMPOTENCE (rejouer
un lancement dont la réponse s'est perdue rend le même job).
"""

import asyncio
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from comfyui_bridge.api import lenteurs as L  # noqa: E402
from comfyui_bridge.api.idempotence import Cles  # noqa: E402
from test_chaines_api import _job, atelier  # noqa: E402,F401


# -- la mesure -----------------------------------------------------------------


def test_un_appel_lent_ou_rate_laisse_une_trace_datee():
    """Les appels ordinaires ne laissent rien (le journal serait illisible) ;
    ceux qui dépassent le seuil, et ceux qui ratent, sont gardés avec leur
    durée — c'est ce qui manquait pour nommer une panne après coup."""
    m = L.Lenteurs()
    rapide = m.commence("GET", "/v1/jobs")
    m.finit(rapide, 200)
    assert m.appels == m.appels.__class__() or len(m.appels) == 0
    lent = m.commence("GET", "/v1/workflows")
    lent["depuis"] -= L.SEUIL_LENT_S + 0.5          # comme s'il avait duré
    m.finit(lent, 200)
    rate = m.commence("POST", "/v1/render")
    m.finit(rate, None, "ConnectionResetError: coupé")
    vue = m.vue()
    assert vue["appels_mesures"] == 3 and vue["appels_lents"] == 1 and vue["appels_rates"] == 1
    dits = {a["chemin"]: a for a in vue["derniers_appels"]}
    assert set(dits) == {"/v1/workflows", "/v1/render"}
    assert dits["/v1/workflows"]["duree_s"] >= L.SEUIL_LENT_S
    assert dits["/v1/render"]["erreur"].startswith("ConnectionResetError")
    assert dits["/v1/render"]["quand"].endswith("+00:00")
    # Un appel FINI ne reste pas « en vol » : sinon le veilleur accuserait un
    # appel de tenir la boucle des heures après sa fin.
    assert vue["en_vol"] == []


def test_ce_qui_est_en_vol_se_nomme_avec_son_age():
    m = L.Lenteurs()
    en_cours = m.commence("GET", "/v1/workflows")
    en_cours["depuis"] -= 3.0
    assert m.vus_en_vol() == ["GET /v1/workflows (depuis 3.0 s)"]
    m.finit(en_cours, 200)
    assert m.vus_en_vol() == []


def test_le_veilleur_mesure_le_retard_de_la_boucle_et_nomme_ce_qui_la_tenait():
    """Le veilleur ne fait qu'attendre et regarder l'heure : un réveil en
    retard ne peut vouloir dire qu'une chose — la boucle était tenue, et tout
    le reste attendait (les autres appels, les flux des jobs en cours)."""

    async def scenario():
        m = L.Lenteurs()
        appel = m.commence("GET", "/v1/workflows")
        veilleur = asyncio.create_task(L.veiller(m, pas=0.05, seuil=0.2))
        await asyncio.sleep(0.1)
        time.sleep(0.6)                           # la boucle est TENUE, pour de vrai
        await asyncio.sleep(0.15)
        veilleur.cancel()
        m.finit(appel, 200)
        return m.vue()

    vue = asyncio.run(scenario())
    assert vue["derniers_retards"], "un blocage de 0,6 s doit se voir"
    dernier = vue["derniers_retards"][-1]
    assert dernier["retard_s"] >= 0.2
    assert any(x.startswith("GET /v1/workflows") for x in dernier["en_vol"])


def test_la_passerelle_publie_ce_qui_l_a_figee(atelier):
    vue = atelier.get("/v1/lenteurs").json()
    assert vue["seuil_lent_s"] == L.SEUIL_LENT_S
    assert vue["appels_mesures"] >= 1              # cet appel-ci est déjà compté
    assert "derniers_retards" in vue and "en_vol" in vue
    assert vue["idempotence"]["clés_retenues"] == 0


def test_un_appel_qui_leve_est_mesure_puis_releve(atelier):
    """Une panne serveur laisse une trace ici AUSSI — sans quoi le seul endroit
    où elle existait était la pile, dans le journal, sans sa durée."""
    avant = atelier.get("/v1/lenteurs").json()["appels_rates"]
    assert atelier.get("/v1/jobs/jamais-vu").status_code == 404   # refus ordinaire : pas un raté
    assert atelier.get("/v1/lenteurs").json()["appels_rates"] == avant


# -- l'idempotence -------------------------------------------------------------


def test_une_cle_rend_le_meme_job_pendant_sa_duree():
    cles = Cles(duree_s=10.0, memoire=3)
    assert cles.lire("k1") is None and cles.lire(None) is None
    cles.retenir("k1", "job-1")
    assert cles.lire("k1") == "job-1"
    cles.retenir(None, "job-2")                   # sans clé, rien n'est retenu
    assert cles.vue()["clés_retenues"] == 1
    # Périmée, elle ne rend plus rien : une clé sert au réessai, pas à rejouer
    # la production d'hier.
    vieille = Cles(duree_s=-1.0)
    vieille.retenir("k2", "job-2")
    assert vieille.lire("k2") is None
    # La mémoire a un plafond : les plus vieilles s'oublient, la dernière tient.
    petite = Cles(duree_s=100.0, memoire=2)
    for i in range(4):
        petite.retenir(f"k{i}", f"job-{i}")
    assert petite.vue()["clés_retenues"] == 2 and petite.lire("k3") == "job-3"


def test_relancer_avec_la_meme_cle_ne_cree_pas_un_second_job(atelier):
    """Le geste qui sauve une production quand la réponse se perd : rejouer la
    demande. Sans clé, c'est un second job (deux fois la carte, deux
    livrables) ; avec elle, c'est le même, et la passerelle le dit."""
    demande = {"workflow": "chaine-simple", "largeur": 96}
    premier = atelier.post("/v1/render", json=demande, headers={"Idempotency-Key": "abc-123"})
    assert premier.status_code == 202
    ident = premier.json()["id"]
    assert "X-Idempotence" not in premier.headers

    rejoue = atelier.post("/v1/render", json=demande, headers={"Idempotency-Key": "abc-123"})
    assert rejoue.status_code == 202
    assert rejoue.json()["id"] == ident
    assert rejoue.headers["X-Idempotence"] == "rejouee"
    assert rejoue.headers["Location"] == f"/v1/jobs/{ident}"

    # Une AUTRE clé est une autre production, même demande à l'identique.
    autre = atelier.post("/v1/render", json=demande, headers={"Idempotency-Key": "def-456"})
    assert autre.json()["id"] != ident
    # …et sans clé, rien ne change : deux lancements font deux jobs.
    sans = atelier.post("/v1/render", json=demande)
    assert sans.json()["id"] not in (ident, autre.json()["id"])
    assert atelier.get("/v1/lenteurs").json()["idempotence"]["clés_retenues"] == 2

    for ident in (ident, autre.json()["id"], sans.json()["id"]):
        _job(atelier, atelier.get(f"/v1/jobs/{ident}"))        # on laisse les runs finir


def test_rejouer_avec_la_meme_cle_ne_cree_pas_un_second_job(atelier):
    """Le rejeu crée un job comme un lancement : il a la même porte, sinon un
    lien coupé sur « Rejouer » doublait encore la production."""
    premier = atelier.post("/v1/render", json={"workflow": "chaine-simple", "largeur": 96})
    job = _job(atelier, premier)
    assert job["status"] == "succeeded", job.get("problem")

    un = atelier.post(f"/v1/jobs/{job['id']}/rejouer", json={},
                      headers={"Idempotency-Key": "rejeu-1"})
    assert un.status_code == 202 and "X-Idempotence" not in un.headers
    deux = atelier.post(f"/v1/jobs/{job['id']}/rejouer", json={},
                        headers={"Idempotency-Key": "rejeu-1"})
    assert deux.status_code == 202 and deux.json()["id"] == un.json()["id"]
    assert deux.headers["X-Idempotence"] == "rejouee"
    # …et sans clé, rejouer reste rejouer : un autre job.
    trois = atelier.post(f"/v1/jobs/{job['id']}/rejouer", json={})
    assert trois.json()["id"] != un.json()["id"]
    for ident in (un.json()["id"], trois.json()["id"]):
        _job(atelier, atelier.get(f"/v1/jobs/{ident}"))
