"""Les chaînes et la vitrine, par l'API — avec un backend qui livre de VRAIS
fichiers.

Le backend « cli » en essai à blanc n'écrit qu'un manifeste : une chaîne posée
dessus n'aurait rien à recoller ni à mesurer, et le test aurait vérifié qu'un
enchaînement de riens s'enchaîne. Celui d'ici écrit une image ou une vidéo pour
de bon, comme le moteur le ferait.
"""

import json
import pathlib
import subprocess
import tempfile

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from comfyui_bridge.adapter import montage_video  # noqa: E402
from comfyui_bridge.adapter.media import artifact_url, is_working_file, media_kind  # noqa: E402
from comfyui_bridge.api.main import create_app  # noqa: E402
from comfyui_bridge.config import Settings  # noqa: E402
from comfyui_bridge.core.plan import Artifact, BackendResult  # noqa: E402

SANS_FFMPEG = pytest.mark.skipif(
    not montage_video.disponible(),
    reason="ffmpeg/ffprobe absents de ce poste : le recollage ne peut pas être éprouvé")


class BackendQuiLivre:
    """Un backend d'essai qui écrit un vrai fichier, image ou vidéo."""

    def __init__(self, sortie: pathlib.Path) -> None:
        self.sortie = pathlib.Path(sortie)
        self.avant = None            # crochet : ce qui arrive PENDANT un run
        self.runs: list[str] = []

    def preview(self, plan):
        return {"workflow": {}}

    def submit(self, plan, on_enqueued=None, on_progress=None, on_note=None, on_started=None):
        self.runs.append(plan.workflow)
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
        if plan.kind == "video":
            fichier = dossier / f"{nom}_{len(self.runs)}.mp4"
            secondes = float(plan.params.get("duration_s") or 1)
            subprocess.run([montage_video.outil(), "-y", "-v", "error", "-f", "lavfi",
                            "-i", f"testsrc=size=160x120:rate=25:duration={secondes}",
                            "-pix_fmt", "yuv420p", str(fichier)], check=True)
        else:
            from PIL import Image
            fichier = dossier / f"{nom}_{len(self.runs)}.png"
            Image.new("RGB", (int(plan.params.get("width") or 64),
                              int(plan.params.get("height") or 64)),
                      (10, 20, 30)).save(fichier)
        from comfyui_bridge.adapter.measure import measure
        art = Artifact(kind=media_kind(fichier), path=str(fichier.resolve()),
                       url=artifact_url(self.sortie, fichier), bytes=fichier.stat().st_size,
                       measured=measure(fichier) or None)
        return BackendResult(artifacts=[art], raw_stdout="essai", execution_s=0.5)


CHAINE_SIMPLE = {
    "version": 1, "chaine": "chaine-simple",
    "resume": "deux rendus et un contrôle",
    "expose": {
        "largeur": {"type": "INT", "defaut": 64, "min": 16, "max": 256,
                    "libelle": "Largeur", "unite": "px"},
        "mode": {"type": "COMBO", "defaut": "a", "options": ["a", "b"], "libelle": "Mode"},
        "seuil": {"type": "INT", "defaut": 64, "min": 1, "max": 4096, "libelle": "Seuil"},
    },
    "etapes": [
        {"id": "un", "rendre": {"workflow": "sd15-txt2img", "prompt": "$mode",
                                "width": "$largeur", "height": "$largeur"}},
        {"id": "deux", "rendre": {"workflow": "sd15-txt2img", "prompt": "suite",
                                  "width": "$largeur", "height": "$largeur"}},
        {"id": "controle", "verifier": [
            {"id": "largeur_tenue", "valeur": "$un.mesure.width", "op": "gte",
             "attendu": "$seuil"}]},
    ],
    "livrable": "$deux.livrable",
}

CHAINE_RECOLLEE = {
    "version": 1, "chaine": "chaine-recollee",
    "resume": "deux clips, recollés, mesurés",
    "expose": {"secondes": {"type": "FLOAT", "defaut": 1, "min": 1, "max": 3,
                            "libelle": "Durée d'un clip", "unite": "s"}},
    "etapes": [
        {"id": "un", "rendre": {"workflow": "video-essai", "duration_s": "$secondes"}},
        {"id": "deux", "rendre": {"workflow": "video-essai", "duration_s": "$secondes"}},
        {"id": "final", "recoller": {"parts": ["$un.livrable", "$deux.livrable"],
                                     "fps": 25, "largeur": 160, "hauteur": 120}},
        {"id": "controle", "verifier": [
            {"id": "duree_tenue", "valeur": "$final.mesure.duration_s", "op": "gte",
             "attendu": 1.8}]},
    ],
    "livrable": "$final.livrable",
}


@pytest.fixture()
def atelier():
    """Une passerelle dont le catalogue porte deux chaînes et une vitrine."""
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="comfybridge_chaines_"))
    (tmp / "chaine-simple.json").write_text(json.dumps(CHAINE_SIMPLE), encoding="utf-8")
    (tmp / "chaine-recollee.json").write_text(json.dumps(CHAINE_RECOLLEE), encoding="utf-8")
    (tmp / "reconciliation.local.json").write_text(json.dumps({
        "categories": {"essais": {"titre": "Essais", "ordre": 1}},
        "menus": {"mode": {"libelle": "Le mode",
                           "libelles": {"a": {"libelle": "Le premier", "groupe": "essais"}}}},
        "workflows": {
            "chaine-simple": {"kind": "image", "chaine": str(tmp / "chaine-simple.json"),
                              "titre": "Chaîne d'essai", "categorie": "essais", "ordre": 1},
            "chaine-recollee": {"kind": "video", "chaine": str(tmp / "chaine-recollee.json"),
                                "titre": "Chaîne recollée", "categorie": "essais", "ordre": 2},
            "video-essai": {"kind": "video", "workflow": "workflow_template.json",
                            "bindings": {"filename_prefix": {"node": "9",
                                                             "input": "filename_prefix"}}},
            "sd15-txt2img": {"titre": "Un graphe ordinaire", "categorie": "essais",
                             "ordre": 3},
        },
    }, ensure_ascii=False), encoding="utf-8")
    settings = Settings(comfy_backend="cli", dry_run=True,
                        comfyui_base_url="http://127.0.0.1:9", comfyui_request_timeout_s=1,
                        hermes_db=tmp / "hermes.sqlite3", comfy_output_dir=tmp / "out",
                        hermes_mode="local", workflows_dir=tmp / "workflows")
    app = create_app(settings)
    faux = BackendQuiLivre(settings.comfy_output_dir)
    app.state.container.orchestrator._backend = faux
    with TestClient(app) as client:
        client.faux = faux
        yield client


def _job(client, reponse):
    return client.get(f"/v1/jobs/{reponse.json()['id']}").json()


def test_le_catalogue_publie_ses_categories_et_ses_titres(atelier):
    """Un lanceur qui tiendrait sa propre liste la verrait vieillir dès qu'une
    entrée change de rangement."""
    d = atelier.get("/v1/workflows").json()
    assert d["categories"]["essais"]["titre"] == "Essais"
    chaine = d["workflows"]["chaine-simple"]
    assert chaine["chaine"] is True
    assert chaine["presentation"] == {"titre": "Chaîne d'essai", "resume": "",
                                      "categorie": "essais", "ordre": 1, "publie": True}
    assert [e["id"] for e in chaine["etapes"]] == ["un", "deux", "controle"]
    # Une entrée sans catégorie reste technique : elle n'est pas publiée.
    assert d["workflows"]["video-essai"]["presentation"]["publie"] is False
    # …et la vitrine posée sur un GRAPHE déposé le trouve quand même.
    assert d["workflows"]["sd15-txt2img"]["presentation"]["titre"] == "Un graphe ordinaire"


def test_io_d_une_chaine_se_decrit_moteur_eteint(atelier):
    """Une chaîne n'a pas de graphe à interroger : son contrat est écrit. Le
    taire moteur éteint rendait un formulaire vide pour une chaîne lançable."""
    io = atelier.get("/v1/workflows/chaine-simple/io").json()
    assert io["engine"]["available"] is False and io["described"] is True
    champs = {e["field"]: e for e in io["intent_inputs"]}
    assert champs["largeur"]["min"] == 16 and champs["largeur"]["unite"] == "px"
    assert champs["largeur"]["value"] == 64 and champs["largeur"]["derived"] is False
    # Le menu : la LISTE vient de la chaîne, les libellés sont déclarés.
    assert champs["mode"]["options"] == ["a", "b"]
    assert champs["mode"]["choix"] == [
        {"valeur": "a", "libelle": "Le premier", "groupe": "essais"},
        {"valeur": "b", "libelle": "b"}]


def test_une_chaine_enchaine_ses_etapes_et_livre(atelier):
    r = atelier.post("/v1/render", json={"workflow": "chaine-simple", "largeur": 128,
                                         "label": "essai"})
    assert r.status_code == 202 and r.headers["Location"]
    # À l'acceptation, la chaîne dit déjà ce qu'elle VA faire.
    assert [e["statut"] for e in r.json()["etapes"]] == ["todo", "todo", "todo"]
    job = _job(atelier, r)
    assert job["status"] == "succeeded", job.get("problem")
    assert [e["statut"] for e in job["etapes"]] == ["done", "done", "done"]
    assert all(e["job_id"] for e in job["etapes"] if e["genre"] == "rendre")
    # Le livrable déclaré vient EN TÊTE des artefacts, avec un chemin qui existe.
    livrable = pathlib.Path(job["artifacts"][0]["path"])
    assert livrable.is_file() and livrable.suffix == ".png"
    assert job["params"] == {"largeur": 128, "mode": "a", "seuil": 64}
    # Les sous-jobs sont des runs comme les autres, et disent de qui ils sont.
    sous = atelier.get("/v1/jobs/" + job["etapes"][0]["job_id"]).json()
    assert sous["parent"] == job["id"] and sous["status"] == "succeeded"
    assert job["progress"]["value"] == job["progress"]["max"] == 3


def test_un_controle_faux_arrete_la_chaine_et_garde_les_fichiers(atelier):
    """Les fichiers déjà écrits restent livrés : c'est en les regardant qu'on
    comprend pourquoi le contrôle a dit non."""
    r = atelier.post("/v1/render", json={"workflow": "chaine-simple", "largeur": 32,
                                         "seuil": 999})
    job = _job(atelier, r)
    assert job["status"] == "failed"
    assert job["problem"]["problem_kind"] == "controle-echoue"
    assert job["problem"]["etape"] == "controle"
    assert "largeur_tenue" in job["problem"]["detail"] and "32" in job["problem"]["detail"]
    assert [e["statut"] for e in job["etapes"]] == ["done", "done", "failed"]
    assert job["artifacts"] and pathlib.Path(job["artifacts"][0]["path"]).is_file()


def test_annuler_une_chaine_saute_les_etapes_restantes(atelier):
    """Le parent est arrêté PENDANT sa première étape : ce qui reste n'est pas
    fait, et rien n'est retenu contre la chaîne."""
    conteneur = atelier.app.state.container

    def arreter(_plan):
        for j in conteneur.store.list(10):
            if j.etapes and j.status.value == "running":
                conteneur.store.request_cancel(j.id)

    atelier.faux.avant = arreter
    job = _job(atelier, atelier.post("/v1/render", json={"workflow": "chaine-simple"}))
    assert job["status"] == "cancelled"
    assert [e["statut"] for e in job["etapes"]] == ["done", "skipped", "skipped"]
    assert job["problem"]["problem_kind"] == "cancelled"
    assert len(atelier.faux.runs) == 1                 # la deuxième n'a pas tourné


def test_les_jobs_se_listent_du_plus_recent_au_plus_ancien(atelier):
    atelier.post("/v1/render", json={"workflow": "chaine-simple", "label": "un"})
    atelier.post("/v1/render", json={"workflow": "chaine-simple", "label": "deux"})
    jobs = atelier.get("/v1/jobs?limit=50").json()["jobs"]
    chaines = [j for j in jobs if j["workflow"] == "chaine-simple"]
    assert len(chaines) == 2
    assert chaines[0]["created_at"] >= chaines[1]["created_at"]
    assert chaines[0]["demande"]["label"] == "deux"
    # Les sous-jobs ne sont PAS là : une création, une carte. Ils vivent dans
    # les étapes de leur parent, et se listent sur demande.
    assert not any(j["parent"] for j in jobs)
    avec = atelier.get("/v1/jobs?limit=50&enfants=1").json()["jobs"]
    assert any(j["parent"] for j in avec)
    assert len(avec) > len(jobs)


def test_un_job_survit_au_redemarrage(tmp_path):
    """Sans persistance, un redémarrage laissait des livrables dont plus rien
    ne disait ce qui les avait produits."""
    from comfyui_bridge.core.jobs import JobStore

    store = JobStore(persist_dir=tmp_path / "jobs")
    job = store.create(kind="video", workflow="wf", params={"width": 8},
                       demande={"workflow": "wf", "width": 8})
    store.append_log(job.id, "quelque chose")
    store.mark_succeeded(job.id, [Artifact(kind="video", path="C:/x.mp4", bytes=12)],
                         duration_s=3.0)

    repris = JobStore(persist_dir=tmp_path / "jobs")
    relu = repris.get(job.id)
    assert relu.status.value == "succeeded" and relu.duration_s == 3.0
    assert relu.artifacts[0].path == "C:/x.mp4"
    assert relu.demande == {"workflow": "wf", "width": 8}
    assert [j.id for j in repris.list(10)] == [job.id]


def test_rejouer_reprend_la_demande_et_remplace_un_champ(atelier):
    premier = _job(atelier, atelier.post("/v1/render", json={
        "workflow": "chaine-simple", "largeur": 96, "label": "origine"}))
    r = atelier.post(f"/v1/jobs/{premier['id']}/rejouer", json={"reglages": {"largeur": 48}})
    assert r.status_code == 202
    rejoue = _job(atelier, r)
    assert rejoue["id"] != premier["id"]
    assert rejoue["params"]["largeur"] == 48
    assert rejoue["demande"]["label"] == "origine"        # le reste ne bouge pas
    # Un champ que la chaîne n'expose pas est refusé, même au rejeu.
    mauvais = atelier.post(f"/v1/jobs/{premier['id']}/rejouer",
                           json={"reglages": {"inexistant": 1}})
    assert mauvais.status_code == 422


def test_les_valeurs_hors_contrat_sont_refusees(atelier):
    hors = atelier.post("/v1/render", json={"workflow": "chaine-simple", "largeur": 9999})
    assert hors.status_code == 422
    assert hors.json()["type"].endswith("/input-value-refused")
    inconnu = atelier.post("/v1/render", json={"workflow": "chaine-simple", "profondeur": 3})
    assert inconnu.status_code == 422
    assert "profondeur" in inconnu.json()["detail"]
    # …et un champ inconnu sur un GRAPHE reste refusé comme avant.
    graphe = atelier.post("/v1/preview", json={"workflow": "sd15-txt2img", "latent_batch": 4})
    assert graphe.status_code == 422


def test_estimer_une_chaine_se_tait_quand_une_etape_n_a_pas_de_mesure(atelier):
    d = atelier.post("/v1/estimate", json={"workflow": "chaine-simple"}).json()
    assert d["chaine"] is True and [e["id"] for e in d["etapes"]] == ["un", "deux"]
    assert d["estimate"] is None and "jamais été mesurée" in d["manque"]


def test_les_fichiers_de_travail_ne_sont_pas_des_livrables(tmp_path):
    """Ce sont de vrais .mp4 : sans dossier réservé, la liste des livrables
    offrait cinquante images de travail avant la vidéo commandée."""
    assert is_working_file(tmp_path / "cortex" / "_travail" / "abc" / "queue.mp4") is True
    assert is_working_file(tmp_path / "cortex" / "video.mp4") is False
    # Le dossier lui-même n'est pas « un fichier de travail » par son nom seul.
    assert is_working_file(tmp_path / "_travail") is False


@SANS_FFMPEG
def test_une_chaine_recolle_ses_rendus_en_un_livrable(atelier):
    """La fin de chaîne : ce qui est rendu doit être la vidéo commandée, pas la
    collection de bouts qui a servi à la produire."""
    job = _job(atelier, atelier.post("/v1/render", json={"workflow": "chaine-recollee",
                                                         "secondes": 1, "label": "joint"}))
    assert job["status"] == "succeeded", job.get("problem")
    livrable = pathlib.Path(job["artifacts"][0]["path"])
    assert livrable.is_file() and livrable.suffix == ".mp4"
    mesure = montage_video.mesurer(livrable)
    assert 1.8 <= mesure["duration_s"] <= 2.4          # les deux clips sont là
    final = [e for e in job["etapes"] if e["id"] == "final"][0]
    assert final["resultat"]["parts"] == 2
    controle = [e for e in job["etapes"] if e["id"] == "controle"][0]
    assert controle["resultat"]["controles"][0]["ok"] is True
    # Le livrable est servi par la passerelle, et son URL n'est pas reconstruite
    # par l'appelant.
    assert job["artifacts"][0]["url"].startswith("/artifacts/")


def test_les_copies_de_reference_des_chaines_suivent_les_donnees():
    """Une chaîne modifiée dans `_data/` sans sa copie de référence laisserait
    le savoir sur la machine et le paquet sur l'ancienne version : à la
    prochaine installation, c'est l'ancienne qui repartirait. Sur un poste
    sans `_data/chaines`, il n'y a rien à comparer — et c'est dit."""
    racine = pathlib.Path(__file__).resolve().parents[1]
    donnees = racine / "_data" / "chaines"
    if not donnees.is_dir():
        pytest.skip("pas de _data/chaines sur ce poste : rien à comparer")
    reference = racine / "comfyui_bridge" / "adapter" / "resources" / "chaines-exemples"
    ecarts = []
    for fichier in sorted(donnees.glob("*.json")):
        jumeau = reference / fichier.name
        if not jumeau.exists():
            ecarts.append(f"{fichier.name} : aucune copie de référence")
            continue
        a = json.loads(fichier.read_text(encoding="utf-8"))
        b = json.loads(jumeau.read_text(encoding="utf-8"))
        if a != b:
            ecarts.append(f"{fichier.name} : la copie de référence diverge des données")
    assert not ecarts, " ; ".join(ecarts)


@SANS_FFMPEG
def test_un_livrable_devient_l_apercu_anime_de_son_mode(atelier):
    """Un lanceur montre ce qu'un mode PRODUIT avant qu'on le lance : la
    passerelle fabrique la vignette depuis un livrable et la sert elle-même.
    Sans fichier, aucune adresse n'est promise — une image cassée par carte."""
    avant = atelier.get("/v1/workflows").json()["workflows"]["chaine-recollee"]
    assert "apercu_url" not in avant["presentation"]
    assert atelier.get("/v1/workflows/chaine-recollee/apercu").status_code == 422

    job = _job(atelier, atelier.post("/v1/render", json={"workflow": "chaine-recollee"}))
    assert job["status"] == "succeeded"
    r = atelier.put("/v1/workflows/chaine-recollee/apercu", json={"job_id": job["id"]})
    assert r.status_code == 201, r.text
    assert r.json()["apercu_url"] == "/v1/workflows/chaine-recollee/apercu"
    assert r.json()["octets"] > 0

    g = atelier.get("/v1/workflows/chaine-recollee/apercu")
    assert g.status_code == 200
    # WebP animé quand l'encodeur est là (dix fois plus léger, mesuré), GIF sinon
    # — et le format annoncé est celui du fichier servi.
    if r.json()["format"] == "webp":
        assert g.headers["content-type"].startswith("image/webp")
        assert g.content[:4] == b"RIFF" and g.content[8:12] == b"WEBP"
    else:
        assert g.headers["content-type"].startswith("image/gif")
        assert g.content[:6] in (b"GIF87a", b"GIF89a")
    apres = atelier.get("/v1/workflows").json()["workflows"]["chaine-recollee"]
    assert apres["presentation"]["apercu_url"] == "/v1/workflows/chaine-recollee/apercu"

    # Un fichier hors du dossier de sortie n'est pas un livrable : refusé, nommé.
    r = atelier.put("/v1/workflows/chaine-recollee/apercu", json={"path": "C:/Windows/notepad.exe"})
    assert r.status_code == 422
    assert atelier.delete("/v1/workflows/chaine-recollee/apercu").json()["removed"] is True
    assert "apercu_url" not in atelier.get("/v1/workflows").json()["workflows"]["chaine-recollee"]["presentation"]
