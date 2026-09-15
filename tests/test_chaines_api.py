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


# Le récit qu'un rendu d'essai écrit à côté de son média : des valeurs simples
# qu'une fiche peut montrer, et des structures lourdes (un calendrier, un objet
# imbriqué, une phrase longue) qu'elle ne doit pas embarquer.
RECIT_D_ESSAI = {
    "hook": "la lanterne", "hook_vu": {"atteint": 0.42}, "temps_retenue": 3,
    "climax_tenue_s": 2.4, "directed": True,
    "schedule": [{"t": 0.0, "zone": 1}, {"t": 1.0, "zone": 2}],
    "intention": "i" * 120,
}


class BackendQuiLivre:
    """Un backend d'essai qui écrit un vrai fichier, image ou vidéo — et, à
    côté, le récit du run en JSON, comme un graphe qui met en scène le fait."""

    def __init__(self, sortie: pathlib.Path) -> None:
        self.sortie = pathlib.Path(sortie)
        self.avant = None            # crochet : ce qui arrive PENDANT un run
        self.runs: list[str] = []
        # Un dict est écrit en JSON ; un texte est écrit tel quel (pour éprouver
        # un récit illisible) ; None n'écrit rien.
        self.recit: dict | str | None = dict(RECIT_D_ESSAI)
        # Un run qui n'a QUE des nombres à livrer : une étape qui documente une
        # image, une étape qui écrit un plan. Il réussit, et son livrable est
        # ce fichier-là.
        self.sans_media = False

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
        if self.sans_media:
            fichier = dossier / f"{nom}_{len(self.runs)}.json"
            fichier.write_text(json.dumps(self.recit or {}, ensure_ascii=False),
                               encoding="utf-8")
            return BackendResult(artifacts=[Artifact(
                kind=media_kind(fichier), path=str(fichier.resolve()),
                url=artifact_url(self.sortie, fichier), bytes=fichier.stat().st_size)],
                raw_stdout="essai", execution_s=0.5)
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
        artefacts = [art]
        if self.recit is not None:
            recit = fichier.with_suffix(".json")
            recit.write_text(self.recit if isinstance(self.recit, str)
                             else json.dumps(self.recit, ensure_ascii=False), encoding="utf-8")
            artefacts.append(Artifact(kind=media_kind(recit), path=str(recit.resolve()),
                                      url=artifact_url(self.sortie, recit),
                                      bytes=recit.stat().st_size))
        return BackendResult(artifacts=artefacts, raw_stdout="essai", execution_s=0.5)


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

# Un rendu réglé par un champ exposé (« inputs » vise une entrée de nœud), puis
# contrôlé sur le récit qu'il a écrit. Définie ICI, dans un fichier temporaire :
# ce n'est pas une chaîne de référence du paquet.
CHAINE_AU_RECIT = {
    "version": 1, "chaine": "chaine-au-recit",
    "resume": "un rendu réglé par un champ exposé, contrôlé sur le récit qu'il écrit",
    "expose": {
        "fond": {"type": "COMBO", "defaut": "washi", "options": ["washi", "sepia"],
                 "libelle": "Fond de départ"},
    },
    "etapes": [
        {"id": "un", "rendre": {"workflow": "sd15-txt2img", "prompt": "un récit",
                                "inputs": {"61.fond": "$fond"}}},
        {"id": "temps", "verifier": [
            {"id": "hook_nomme", "valeur": "$un.recit.hook", "op": "exists"},
            {"id": "hook_vu_a_2_5_s", "valeur": "$un.recit.hook_vu.atteint", "op": "gte",
             "attendu": 0.1}]},
    ],
    "livrable": "$un.livrable",
}

# Un champ dont la LISTE n'est pas écrite dans la chaîne : elle est celle d'un
# menu déclaré, qui n'est lui-même que la projection d'un fichier tenu par un
# fournisseur (ici un faux paquet de structures). Recopier la liste ici la
# figerait au jour où on l'a écrite.
CHAINE_AU_MENU = {
    "version": 1, "chaine": "chaine-au-menu",
    "resume": "un menu dont la liste appartient au fournisseur",
    "expose": {
        "structure": {"type": "COMBO", "defaut": "en-boucle",
                      "options_depuis": {"menu": "structure"},
                      "libelle": "Structure du récit"},
    },
    "etapes": [{"id": "un", "rendre": {"workflow": "sd15-txt2img",
                                       "prompt": "$structure"}}],
    "livrable": "$un.livrable",
}

STRUCTURES = {"styles": {
    "en-boucle": {"libelle": "En boucle", "famille": "structure sociale"},
    "lente": {"libelle": "Contemplation lente", "famille": "structure longue"},
}}

# Une étape FACULTATIVE : « appel » ne se joue que si le champ « texte » est
# renseigné (« quand »). Sautée, elle rend le livrable qu'elle devait reprendre,
# et le montage qui la nomme tient toujours.
CHAINE_FACULTATIVE = {
    "version": 1, "chaine": "chaine-facultative",
    "resume": "une étape qui ne se joue que si un champ est renseigné",
    "expose": {"texte": {"type": "STRING", "defaut": "", "libelle": "Appel final"}},
    "etapes": [
        {"id": "une", "rendre": {"workflow": "video-essai", "prompt": "une",
                                 "duration_s": 1.0}},
        {"id": "appel", "quand": "$texte",
         "rendre": {"workflow": "video-essai", "prompt": "$texte", "duration_s": 1.0,
                    "media": {"video": "$une.livrable"}}},
        {"id": "montage", "recoller": {"parts": ["$une.livrable", "$appel.livrable"],
                                       "fps": 25, "largeur": 160, "hauteur": 120}},
    ],
    "livrable": "$montage.livrable",
}

# Le livrable d'une étape « rendre » est un CHEMIN local ; une étape suivante
# qui le reprend en média (ici « deux » lit « $une.livrable ») ne doit jamais
# le voir tel quel. « image_2 » n'est déjà pas un fichier d'ici : rien à
# déposer, elle passe telle quelle.
CHAINE_MEDIA_LIVRABLE = {
    "version": 1, "chaine": "chaine-media-livrable",
    "resume": "un rendu qui reprend en média le livrable d'un rendu précédent",
    "etapes": [
        {"id": "une", "rendre": {"workflow": "sd15-txt2img", "prompt": "une"}},
        {"id": "deux", "rendre": {"workflow": "sd15-txt2img", "prompt": "deux",
                                  "media": {"image": "$une.livrable",
                                            "image_2": "deja-depose.png"}}},
    ],
    "livrable": "$deux.livrable",
}


@pytest.fixture()
def atelier():
    """Une passerelle dont le catalogue porte deux chaînes et une vitrine."""
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="comfybridge_chaines_"))
    (tmp / "chaine-simple.json").write_text(json.dumps(CHAINE_SIMPLE), encoding="utf-8")
    (tmp / "chaine-recollee.json").write_text(json.dumps(CHAINE_RECOLLEE), encoding="utf-8")
    (tmp / "chaine-au-recit.json").write_text(json.dumps(CHAINE_AU_RECIT), encoding="utf-8")
    (tmp / "chaine-au-menu.json").write_text(json.dumps(CHAINE_AU_MENU), encoding="utf-8")
    (tmp / "chaine-media-livrable.json").write_text(json.dumps(CHAINE_MEDIA_LIVRABLE),
                                                     encoding="utf-8")
    (tmp / "chaine-facultative.json").write_text(json.dumps(CHAINE_FACULTATIVE),
                                                 encoding="utf-8")
    (tmp / "structures.json").write_text(json.dumps(STRUCTURES, ensure_ascii=False),
                                         encoding="utf-8")
    (tmp / "reconciliation.local.json").write_text(json.dumps({
        "categories": {"essais": {"titre": "Essais", "ordre": 1}},
        "menus": {"mode": {"libelle": "Le mode",
                           "libelles": {"a": {"libelle": "Le premier", "groupe": "essais"}}},
                  "structure": {"libelle": "La structure",
                                "source_fichier": {"chemin": str(tmp / "structures.json"),
                                                   "table": "styles", "libelle": "libelle",
                                                   "groupe": "famille"}}},
        "workflows": {
            "chaine-au-menu": {"kind": "image", "chaine": str(tmp / "chaine-au-menu.json"),
                               "titre": "Chaîne au menu", "categorie": "essais", "ordre": 5},
            "chaine-media-livrable": {"kind": "image",
                                      "chaine": str(tmp / "chaine-media-livrable.json"),
                                      "titre": "Chaîne média livrable",
                                      "categorie": "essais", "ordre": 6},
            "chaine-facultative": {"kind": "video",
                                   "chaine": str(tmp / "chaine-facultative.json"),
                                   "titre": "Chaîne facultative",
                                   "categorie": "essais", "ordre": 7},
            "chaine-simple": {"kind": "image", "chaine": str(tmp / "chaine-simple.json"),
                              "titre": "Chaîne d'essai", "categorie": "essais", "ordre": 1},
            "chaine-recollee": {"kind": "video", "chaine": str(tmp / "chaine-recollee.json"),
                                "titre": "Chaîne recollée", "categorie": "essais", "ordre": 2},
            "chaine-au-recit": {"kind": "image", "chaine": str(tmp / "chaine-au-recit.json"),
                                "titre": "Chaîne au récit", "categorie": "essais", "ordre": 4},
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
        client.tmp = tmp
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


def test_un_rendre_qui_reprend_un_livrable_le_depose_chez_le_moteur(atelier, monkeypatch):
    """Le livrable d'une étape précédente est un CHEMIN local ; l'étape suivante
    qui le reprend en média doit le déposer chez le moteur et citer le nom
    rendu — jamais le chemin. Une valeur qui n'est déjà pas un fichier d'ici
    (un nom déjà déposé) n'a rien à déposer : elle passe telle quelle."""
    from comfyui_bridge.adapter import neutral

    class _Reponse:
        def __init__(self, corps):
            self._corps = corps

        def read(self):
            return self._corps

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    depose_sous = "moteur-a-depose-ceci.png"
    monkeypatch.setattr(neutral.urllib.request, "urlopen", lambda req, timeout=None:
                        _Reponse(json.dumps({"name": depose_sous}).encode()))

    vus = []
    atelier.faux.avant = lambda plan: vus.append(dict(plan.params))
    job = _job(atelier, atelier.post("/v1/render", json={"workflow": "chaine-media-livrable"}))
    assert job["status"] == "succeeded", job.get("problem")
    livrable_local = job["etapes"][0]["resultat"]["livrable"]
    # Le CHEMIN local produit par la première étape est devenu le NOM que le
    # moteur a rendu — jamais le chemin lui-même — dans ce que reçoit la
    # deuxième.
    assert vus[1]["image"] == depose_sous
    assert vus[1]["image"] != livrable_local
    attendu = (f"livrable {pathlib.Path(livrable_local).name} déposé chez le "
              f"moteur sous « {depose_sous} »")
    assert any(attendu in ligne for ligne in job["logs"])
    # « image_2 » n'était déjà pas un fichier d'ici : rien à déposer.
    assert vus[1]["image_2"] == "deja-depose.png"


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


def test_le_recit_d_un_rendu_se_controle_et_sa_fiche_le_resume(atelier):
    """Un graphe qui met en scène livre, avec son média, le récit de ce qu'il
    a décidé : la chaîne le lit sans savoir qui l'a écrit et le contrôle, et un
    champ exposé règle l'entrée de nœud visée par « inputs ». La fiche du job
    n'en garde que les valeurs simples — lire hook et durée sans embarquer le
    calendrier (≈ 90 Ko sur un récit réel)."""
    vus = []
    atelier.faux.avant = lambda plan: vus.append(dict(plan.overrides))
    job = _job(atelier, atelier.post("/v1/render", json={"workflow": "chaine-au-recit",
                                                         "fond": "sepia"}))
    assert job["status"] == "succeeded", job.get("problem")
    assert vus == [{"61.fond": "sepia"}]                # le champ a atteint le nœud
    rendu, temps = job["etapes"]
    assert [ctl["ok"] for ctl in temps["resultat"]["controles"]] == [True, True]
    assert temps["resultat"]["controles"][1]["mesure"] == 0.42
    # La fiche : les scalaires du premier niveau — ni le calendrier, ni l'objet
    # imbriqué, ni la phrase longue.
    assert rendu["resultat"]["recit"] == {"hook": "la lanterne", "temps_retenue": 3,
                                          "climax_tenue_s": 2.4, "directed": True}
    # Le récit est un artefact du sous-job, à côté du média — et ce n'est pas
    # lui que la chaîne livre.
    sous = atelier.get("/v1/jobs/" + rendu["job_id"]).json()
    assert [a["kind"] for a in sous["artifacts"]] == ["image", "text"]
    assert pathlib.Path(job["artifacts"][0]["path"]).suffix == ".png"


def test_une_etape_qui_ne_livre_que_des_nombres_reussit_et_son_recit_se_lit(atelier):
    """Toutes les étapes ne rendent pas un média : documenter une image, écrire
    un plan, cela ne produit que des nombres. L'étape échouait sur « le run a
    réussi sans livrer de média » alors que c'est justement son récit que la
    suite attend."""
    atelier.faux.sans_media = True
    job = _job(atelier, atelier.post("/v1/render", json={"workflow": "chaine-au-recit"}))
    assert job["status"] == "succeeded", job.get("problem")
    rendu, temps = job["etapes"]
    assert [ctl["ok"] for ctl in temps["resultat"]["controles"]] == [True, True]
    # Le livrable de l'étape EST le fichier de nombres : il n'y en a pas d'autre.
    assert rendu["resultat"]["livrable"].endswith(".json")
    sous = atelier.get("/v1/jobs/" + rendu["job_id"]).json()
    assert [a["kind"] for a in sous["artifacts"]] == ["text"]


def test_la_liste_d_un_menu_reste_celle_du_fournisseur(atelier):
    """Une chaîne qui offre un vocabulaire (les structures de récit) ne le
    recopie pas : elle nomme le menu, et la liste est celle du fichier que le
    fournisseur tient. Recopiée, elle aurait vieilli au premier ajout."""
    champ = next(e for e in atelier.get("/v1/workflows/chaine-au-menu/io").json()
                 ["intent_inputs"] if e["field"] == "structure")
    assert champ["options"] == ["en-boucle", "lente"]
    # Le libellé le plus proche (celui de la chaîne) l'emporte sur celui du menu,
    # mais les CHOIX sont habillés par ce que le fournisseur écrit.
    assert champ["libelle"] == "Structure du récit"
    assert champ["choix"] == [
        {"valeur": "en-boucle", "libelle": "En boucle", "groupe": "structure sociale"},
        {"valeur": "lente", "libelle": "Contemplation lente", "groupe": "structure longue"}]
    # Hors de cette liste, la passerelle refuse — c'est elle qui fait autorité.
    refus = atelier.post("/v1/render", json={"workflow": "chaine-au-menu",
                                             "structure": "inventee"})
    assert refus.status_code == 422
    assert refus.json()["type"].endswith("/input-value-refused")
    assert refus.json()["options"] == ["en-boucle", "lente"]
    # Le fournisseur ajoute une structure : elle apparaît sans toucher la chaîne.
    fichier = atelier.tmp / "structures.json"
    enrichi = json.loads(fichier.read_text(encoding="utf-8"))
    enrichi["styles"]["saccadee"] = {"libelle": "Saccadée", "famille": "structure courte"}
    fichier.write_text(json.dumps(enrichi, ensure_ascii=False), encoding="utf-8")
    import os
    os.utime(fichier, (fichier.stat().st_atime, fichier.stat().st_mtime + 10))
    champ = next(e for e in atelier.get("/v1/workflows/chaine-au-menu/io").json()
                 ["intent_inputs"] if e["field"] == "structure")
    assert champ["options"] == ["en-boucle", "lente", "saccadee"]
    assert atelier.post("/v1/render", json={"workflow": "chaine-au-menu",
                                            "structure": "saccadee"}).status_code < 400


def test_un_recit_qui_ne_tient_pas_la_regle_arrete_la_chaine(atelier):
    """Le contrôle échoue AVANT les étapes suivantes, nomme celui qui a dit
    non et dit la valeur mesurée : de combien on a raté se lit sans refaire le
    run."""
    atelier.faux.recit = {**RECIT_D_ESSAI, "hook_vu": {"atteint": 0.02}}
    job = _job(atelier, atelier.post("/v1/render", json={"workflow": "chaine-au-recit"}))
    assert job["status"] == "failed"
    assert job["problem"]["problem_kind"] == "controle-echoue"
    assert job["problem"]["etape"] == "temps"
    assert "hook_vu_a_2_5_s" in job["problem"]["detail"] and "0.02" in job["problem"]["detail"]
    faux = [ctl for ctl in job["problem"]["controles"] if not ctl["ok"]]
    assert [ctl["id"] for ctl in faux] == ["hook_vu_a_2_5_s"] and faux[0]["mesure"] == 0.02
    assert [e["statut"] for e in job["etapes"]] == ["done", "failed"]


def test_un_recit_illisible_est_dit_et_la_cle_reste_absente(atelier):
    """Un JSON cassé ne devient pas un récit vide sur lequel « exists »
    passerait par hasard : il est dit au journal, la clé reste absente, et le
    contrôle échoue en nommant ce qui manque."""
    atelier.faux.recit = "{ pas du json"
    job = _job(atelier, atelier.post("/v1/render", json={"workflow": "chaine-au-recit"}))
    assert job["status"] == "failed" and job["problem"]["etape"] == "temps"
    assert any("récit illisible" in ligne for ligne in job["logs"])
    assert "recit" not in job["etapes"][0]["resultat"]
    assert "hook_nomme" in job["problem"]["detail"]


def test_le_recit_est_le_premier_json_livre_hors_compagnon_et_doit_etre_un_objet(tmp_path):
    """Le compagnon d'origine est aussi un JSON, posé à côté de chaque
    livrable : pris pour le récit, il aurait fait échouer un contrôle sur des
    clés qu'il n'a pas. Et un JSON valide qui n'est pas un objet n'est pas un
    récit : « $etape.recit.cle » n'y lirait rien, autant le dire tout de
    suite."""
    from comfyui_bridge.adapter.chaines import _recit
    from comfyui_bridge.adapter.media import SIDECAR_SUFFIX
    dits = []
    compagnon = tmp_path / ("image.png" + SIDECAR_SUFFIX)
    compagnon.write_text(json.dumps({"fichier": "image.png"}), encoding="utf-8")
    recit = tmp_path / "image.json"
    recit.write_text(json.dumps({"hook": "x"}), encoding="utf-8")
    artefacts = [Artifact(kind="image", path=str(tmp_path / "image.png")),
                 Artifact(kind="text", path=str(compagnon)),
                 Artifact(kind="text", path=str(recit))]
    assert _recit(artefacts, dits.append) == {"hook": "x"} and dits == []
    recit.write_text("[1, 2, 3]", encoding="utf-8")
    assert _recit(artefacts, dits.append) is None
    assert dits == ["image.json : un objet JSON était attendu, trouvé list"]
    # Sans fichier de nombres, pas de récit — et rien à dire.
    assert _recit(artefacts[:1], dits.append) is None and len(dits) == 1


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
    # Rien de partiel ne traîne : l'aperçu est écrit de côté puis remplacé d'un
    # coup, sinon un lecteur pouvait recevoir un fichier tronqué pendant la
    # refabrication (mesuré : 0 octet servi en 200).
    apercus = pathlib.Path(atelier.app.state.container.settings.hermes_db).parent / "apercus"
    assert not [f for f in apercus.iterdir() if ".part." in f.name]

    # Un fichier hors du dossier de sortie n'est pas un livrable : refusé, nommé.
    r = atelier.put("/v1/workflows/chaine-recollee/apercu", json={"path": "C:/Windows/notepad.exe"})
    assert r.status_code == 422
    assert atelier.delete("/v1/workflows/chaine-recollee/apercu").json()["removed"] is True
    assert "apercu_url" not in atelier.get("/v1/workflows").json()["workflows"]["chaine-recollee"]["presentation"]


@SANS_FFMPEG
def test_le_nom_donne_puis_le_type_nomment_tous_les_fichiers(atelier):
    """Règle générale de nommage, tenue par la passerelle puisque c'est elle qui
    écrit : « <nom donné>_<type> » sur le livrable final d'une chaîne (le type
    est la chaîne), et sur chaque rendu d'étape (le type est son workflow, le
    nom porte l'étape). Un lanceur retrouve sa production à son nom, et sait
    laquelle de deux productions homonymes vient de quel mode."""
    job = _job(atelier, atelier.post("/v1/render", json={"workflow": "chaine-recollee",
                                                         "label": "Mon-yokai"}))
    assert job["status"] == "succeeded", job.get("problem")
    final = pathlib.Path(job["artifacts"][0]["path"]).name
    assert final.startswith("Mon-yokai_chaine-recollee-final_"), final
    sous = [e for e in job["etapes"] if e.get("job_id")]
    assert sous, "aucune étape rendue"
    premier = atelier.get(f"/v1/jobs/{sous[0]['job_id']}").json()
    assert premier["params"]["filename_prefix"] == f"cortex/Mon-yokai-{sous[0]['id']}_video-essai"
    # Sans nom donné : le type seul, jamais deux fois.
    job = _job(atelier, atelier.post("/v1/render", json={"workflow": "chaine-recollee"}))
    assert pathlib.Path(job["artifacts"][0]["path"]).name.startswith("chaine-recollee-final_")


def test_une_etape_facultative_est_sautee_quand_son_champ_est_vide(atelier, monkeypatch):
    """« Si pas de CTA spécifié, on ne met pas de CTA : on saute l'étape. »
    Sans texte, « appel » n'est pas jouée — statut « skipped », raison dite —
    et rend le livrable qu'elle devait reprendre : le montage qui la nomme
    recolle deux parts, dont deux fois la première. Avec un texte, elle se
    joue comme avant."""
    # le dépôt chez le moteur est court-circuité : il n'y a pas de moteur ici
    monkeypatch.setattr("comfyui_bridge.adapter.neutral.upload_image",
                        lambda base, nom, contenu, *reste, **autres: nom)
    r = atelier.post("/v1/render", json={"workflow": "chaine-facultative", "label": "sans"})
    assert r.status_code == 202, r.text
    job = _job(atelier, r)
    assert job["status"] == "succeeded", job.get("problem")
    statuts = {e["id"]: e["statut"] for e in job["etapes"]}
    assert statuts == {"une": "done", "appel": "skipped", "montage": "done"}
    appel = next(e for e in job["etapes"] if e["id"] == "appel")
    assert "vide" in (appel["note"] or "") and appel["job_id"] is None
    assert appel["resultat"]["livrable"].endswith(".mp4")
    assert next(e for e in job["etapes"] if e["id"] == "montage")["resultat"]["parts"] == 2
    r = atelier.post("/v1/render", json={"workflow": "chaine-facultative", "label": "avec",
                                         "texte": "La suite, bientôt"})
    job = _job(atelier, r)
    assert job["status"] == "succeeded", job.get("problem")
    assert [e["statut"] for e in job["etapes"]] == ["done", "done", "done"]
