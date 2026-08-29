"""Les pièces jointes d'un workflow : toutes nommées, toutes remplissables.

Mesuré avant correction, sur des workflows officiels ComfyUI extraits par la
passerelle elle-même : 10 des 17 entrées média de neuf workflows étaient
pilotables. Les sept autres — « Load Last Frame » d'un flf2v, trois des quatre
vues d'un assemblage, l'entrée son d'un s2v — n'avaient aucun champ, aucun nom,
aucun moyen d'être remplies. La découverte ne nommait qu'une image et une vidéo
par graphe.
"""

import pytest

from comfyui_bridge.adapter import autobind
from comfyui_bridge.adapter.catalog import _spec_from_graph
from comfyui_bridge.core.intention import (RenderIntent, intent_fields,
                                           is_media_param, media_param)


def _spec(graph, titles=None):
    return _spec_from_graph("wf", "wf.json", graph, {"titles": titles or {}})


ASSEMBLAGE = {
    "12": {"class_type": "LoadImage", "inputs": {"image": "angle_1.png"}},
    "13": {"class_type": "LoadImage", "inputs": {"image": "angle_2.png"}},
    "14": {"class_type": "LoadImage", "inputs": {"image": "angle_3.png"}},
    "15": {"class_type": "LoadImage", "inputs": {"image": "angle_4.png"}},
    "18": {"class_type": "ImageStitch", "inputs": {"image1": ["12", 0], "image2": ["13", 0]}},
    "19": {"class_type": "SaveImage", "inputs": {"filename_prefix": "x", "images": ["18", 0]}},
}


def test_les_quatre_images_d_un_assemblage_sont_toutes_nommees():
    b = autobind.derive_bindings(ASSEMBLAGE)
    assert [(p, b[p].node) for p in ("image", "image_2", "image_3", "image_4")] == [
        ("image", "12"), ("image_2", "13"), ("image_3", "14"), ("image_4", "15")]


def test_le_rang_suit_l_ordre_des_noeuds_pas_celui_du_dictionnaire():
    """« 10 » vient après « 9 ». Trier en texte ferait changer le rang d'une
    pièce jointe d'un workflow à l'autre sans que rien ne l'explique."""
    graph = {
        "10": {"class_type": "LoadImage", "inputs": {"image": "dix.png"}},
        "2": {"class_type": "LoadImage", "inputs": {"image": "deux.png"}},
        "9": {"class_type": "LoadImage", "inputs": {"image": "neuf.png"}},
        "20": {"class_type": "SaveImage", "inputs": {"filename_prefix": "x"}},
    }
    b = autobind.derive_bindings(graph)
    assert (b["image"].node, b["image_2"].node, b["image_3"].node) == ("2", "9", "10")


def test_une_entree_son_est_pilotable():
    """Aucun `LoadAudio` n'était lié : 18 modèles officiels en portent un, et
    l'entrée son d'un s2v n'avait donc aucun moyen d'être remplie."""
    graph = {
        "52": {"class_type": "LoadImage", "inputs": {"image": "ref.jpg"}},
        "58": {"class_type": "LoadAudio", "inputs": {"audio": "voix.mp3"}},
        "113": {"class_type": "SaveVideo", "inputs": {"filename_prefix": "x"}},
    }
    b = autobind.derive_bindings(graph)
    assert (b["audio"].node, b["audio"].input) == ("58", "audio")
    assert (b["image"].node, b["image"].input) == ("52", "image")


def test_une_entree_alimentee_par_un_lien_n_est_pas_une_piece_jointe():
    """Elle est PRODUITE par un autre nœud : l'offrir promettrait une valeur
    que l'injection écraserait aussitôt."""
    graph = {
        "1": {"class_type": "LoadImage", "inputs": {"image": ["7", 0]}},
        "2": {"class_type": "SaveImage", "inputs": {"filename_prefix": "x"}},
    }
    assert "image" not in autobind.derive_bindings(graph)


def test_le_nom_annonce_est_un_nom_que_l_appelant_peut_envoyer():
    """`/v1/workflows` annonce « image_2 » ; le refuser ensuite ferait mentir
    l'annonce."""
    assert is_media_param("image_2") and is_media_param("audio")
    assert not is_media_param("images") and not is_media_param("image_")
    assert media_param("image", 1) == "image" and media_param("image", 3) == "image_3"
    assert "image_2" in intent_fields(autobind.derive_bindings(ASSEMBLAGE))


def test_le_contenu_embarque_de_chaque_entree_est_dit():
    """Quatre images embarquées, quatre à annoncer : n'en dire qu'une laissait
    trois contenus partir dans le résultat sans un mot."""
    spec = _spec(ASSEMBLAGE)
    assert spec.carried == {"image": "angle_1.png", "image_2": "angle_2.png",
                            "image_3": "angle_3.png", "image_4": "angle_4.png"}


def test_l_element_neutre_couvre_toutes_les_images_et_aucun_son():
    """Il existe un neutre par CATÉGORIE. Dire « neutre envoyé » pour un son
    serait faux : aucun n'est fabriqué, le workflow garde le sien."""
    spec = _spec({**ASSEMBLAGE, "30": {"class_type": "LoadAudio",
                                       "inputs": {"audio": "voix.wav"}}})
    assert spec.profile.neutral_for == ("image", "image_2", "image_3", "image_4")
    assert "audio" in spec.carried


def test_le_titre_de_l_auteur_distingue_deux_images():
    """« image_2 » ne dit rien ; « Load Last Frame » dit tout. Le titre est ce
    que ComfyUI Desktop affiche, et l'export API le perd."""
    from comfyui_bridge.adapter import media_inputs
    graph = {
        "31": {"class_type": "LoadImage", "inputs": {"image": "debut.png"}},
        "39": {"class_type": "LoadImage", "inputs": {"image": "fin.png"}},
        "75": {"class_type": "SaveVideo", "inputs": {"filename_prefix": "x"}},
    }
    titles = {"31": "Load First Frame", "39": "Load Last Frame"}
    spec = _spec(graph, titles)
    décrites = media_inputs.describe(spec.bindings, spec.titles, spec.carried, graph)
    assert [(d["param"], d["label"], d["accept"]) for d in décrites] == [
        ("image", "Load First Frame", "image/*"), ("image_2", "Load Last Frame", "image/*")]


# -- le genre d'un workflow qui livre DEUX choses ---------------------------

VIDEO_ET_SON = {
    "302": {"class_type": "EmptyLTXVLatentVideo",
            "inputs": {"length": 97, "batch_size": 1, "width": 704, "height": 480}},
    "445": {"class_type": "SaveAudioMP3", "inputs": {"filename_prefix": "a", "quality": "V0"}},
    "479": {"class_type": "SaveVideo", "inputs": {"filename_prefix": "v"}},
}


def test_une_video_qui_porte_sa_bande_son_reste_une_video():
    """Lire l'audio d'abord faisait passer `template_image_speech_to_video`,
    modèle officiel, pour de l'audio."""
    assert autobind.infer_kind(VIDEO_ET_SON) == "video"


def test_et_son_nombre_d_images_ne_part_pas_sur_un_nombre_de_clips():
    """Conséquence directe du genre : dans un graphe vidéo, `batch_size` compte
    des CLIPS. Le prendre pour des images demandait N vidéos simultanées."""
    b = autobind.derive_bindings(VIDEO_ET_SON)
    assert (b["latent_batch"].node, b["latent_batch"].input) == ("302", "length")


def test_un_workflow_qui_ne_livre_que_du_son_reste_de_l_audio():
    graph = {
        "4": {"class_type": "EmptyLatentAudio", "inputs": {"seconds": 8, "batch_size": 1}},
        "7": {"class_type": "SaveAudio", "inputs": {"filename_prefix": "a"}},
    }
    assert autobind.infer_kind(graph) == "audio"


# -- côté appelant ----------------------------------------------------------

def test_l_intention_porte_autant_de_pieces_jointes_que_le_workflow_en_a():
    from comfyui_bridge.core.jobs import JobStore
    from comfyui_bridge.core.orchestrator import Orchestrator
    from comfyui_bridge.core.workflow import WorkflowProfile

    class _Registre:
        def get_profile(self, name):
            return WorkflowProfile("wf", "image",
                                   accepts=("image", "image_2", "image_3", "image_4",
                                            "filename_prefix"))

    orch = Orchestrator(backend=None, reconciler=None, journal=None, store=JobStore(),
                        host_id="h", registry=_Registre())
    plan = orch.build_plan(RenderIntent(workflow="wf", media={"image": "a.png",
                                                              "image_3": "c.png"}))
    assert plan.params["image"] == "a.png" and plan.params["image_3"] == "c.png"
    assert plan.ignored == ()


def test_une_piece_jointe_que_le_workflow_n_a_pas_est_dite_non_transmise():
    from comfyui_bridge.core.jobs import JobStore
    from comfyui_bridge.core.orchestrator import Orchestrator
    from comfyui_bridge.core.workflow import WorkflowProfile

    class _Registre:
        def get_profile(self, name):
            return WorkflowProfile("wf", "image", accepts=("image", "filename_prefix"))

    orch = Orchestrator(backend=None, reconciler=None, journal=None, store=JobStore(),
                        host_id="h", registry=_Registre())
    plan = orch.build_plan(RenderIntent(workflow="wf", media={"image_9": "x.png"}))
    assert plan.ignored == ("image_9",)


# -- surface HTTP -----------------------------------------------------------

pytest.importorskip("fastapi")
pytest.importorskip("httpx")


def _client(tmp_path):
    from fastapi.testclient import TestClient

    from comfyui_bridge.api.main import create_app
    from comfyui_bridge.config import Settings
    settings = Settings(comfy_backend="cli", dry_run=True, comfyui_base_url="http://127.0.0.1:9",
                        comfyui_request_timeout_s=1, hermes_db=tmp_path / "h.sqlite3",
                        comfy_output_dir=tmp_path / "out", hermes_mode="local",
                        workflows_dir=tmp_path / "workflows")
    return TestClient(create_app(settings))


def test_une_seconde_image_s_envoie_sous_le_nom_annonce(tmp_path):
    with _client(tmp_path) as c:
        assert c.post("/v1/workflows", json={"name": "assemblage",
                                             "workflow": ASSEMBLAGE}).status_code == 201
        annonce = c.get("/v1/workflows").json()["workflows"]["assemblage"]
        assert "image_2" in annonce["intent_fields"]
        assert [m["param"] for m in annonce["media_inputs"]] == [
            "image", "image_2", "image_3", "image_4"]

        # à la racine, sous le nom annoncé…
        pv = c.post("/v1/preview", json={"workflow": "assemblage", "image_2": "b.png"}).json()
        assert pv["graph"]["13"]["inputs"]["image"] == "b.png"
        # …ou groupées, pour un appelant qui les tient déjà dans une table
        pv = c.post("/v1/preview", json={"workflow": "assemblage",
                                         "media": {"image": "a.png", "image_4": "d.png"}}).json()
        assert pv["graph"]["12"]["inputs"]["image"] == "a.png"
        assert pv["graph"]["15"]["inputs"]["image"] == "d.png"


def test_un_nom_qui_n_est_pas_une_piece_jointe_reste_refuse(tmp_path):
    """Accepter les noms annoncés ne doit pas rouvrir la porte au silence : une
    faute de frappe est une erreur d'appelant, et elle se dit."""
    with _client(tmp_path) as c:
        assert c.post("/v1/preview", json={"prompt": "x", "imag": "a.png"}).status_code == 422


# -- déposer le fichier, et rendre le nom que le graphe peut citer ----------

def test_chaque_categorie_est_deposee_la_ou_le_moteur_la_cherche():
    from comfyui_bridge.adapter.media_inputs import upload_subfolder
    assert upload_subfolder("image") == "" and upload_subfolder("image_2") == ""
    assert upload_subfolder("video") == "" and upload_subfolder("audio") == ""
    # Load3D ne liste QUE le contenu de input/3d, et le cite « 3d/<nom> ».
    assert upload_subfolder("3d") == "3d"


def test_le_nom_rendu_est_celui_qu_un_graphe_peut_citer(monkeypatch):
    """Le moteur répond OÙ il a rangé le fichier. Ignorer ce sous-dossier
    rendait « cube.obj » là où Load3D n'accepte que « 3d/cube.obj » —
    mesuré contre /object_info : le premier n'est pas dans la liste validée."""
    import io as _io
    import json as _json

    from comfyui_bridge.adapter import neutral

    envoye = {}

    class _Reponse:
        def __init__(self, corps):
            self._corps = corps

        def read(self):
            return self._corps

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _faux_urlopen(req, timeout=None):
        envoye["corps"] = req.data
        return _Reponse(_json.dumps({"name": "cube.obj", "subfolder": "3d",
                                     "type": "input"}).encode())

    monkeypatch.setattr(neutral.urllib.request, "urlopen", _faux_urlopen)
    rendu = neutral.upload_image("http://moteur", "cube.obj", b"x", subfolder="3d")
    assert rendu == "3d/cube.obj"
    assert b'name="subfolder"' in envoye["corps"] and b"3d" in envoye["corps"]

    monkeypatch.setattr(neutral.urllib.request, "urlopen",
                        lambda req, timeout=None: _Reponse(
                            _json.dumps({"name": "a.png", "subfolder": ""}).encode()))
    assert neutral.upload_image("http://moteur", "a.png", b"x") == "a.png"
    _ = _io  # (import gardé lisible)


def test_ce_qui_s_ingere_par_l_API_peut_se_retirer(tmp_path):
    """Un graphe importé sans provenance ComfyUI n'en est pas moins à nous : le
    refuser au retrait laissait un extrait orphelin, appelable et indélogeable."""
    with _client(tmp_path) as c:
        assert c.post("/v1/workflows", json={"name": "jetable",
                                             "workflow": ASSEMBLAGE}).status_code == 201
        r = c.delete("/v1/workflows/jetable")
        assert r.status_code == 200 and r.json()["removed"] == "jetable"
        assert "jetable" not in c.get("/v1/workflows").json()["workflows"]
        # …mais une entrée DÉCLARÉE dans le fichier de réconciliation, non.
        assert c.delete("/v1/workflows/sd15-txt2img").status_code == 500


# -- un maillage est un genre, pas un défaut --------------------------------

def test_un_workflow_qui_enregistre_un_maillage_le_dit():
    """Sans SaveVideo ni SaveAudio, infer_kind tombait sur « image » par DÉFAUT
    — pas par constat — et le catalogue annonçait « image » pour un GLB de
    5,9 Mo (mesuré sur 3d_moge_perspective_to_mesh)."""
    graph = {
        "9": {"class_type": "LoadImage", "inputs": {"image": "vue.png"}},
        "21": {"class_type": "SaveGLB", "inputs": {"filename_prefix": "3d/x"}},
    }
    assert autobind.infer_kind(graph) == "3d"


def test_une_video_l_emporte_encore_sur_un_maillage_annexe():
    """Un tour de caméra rendu à côté d'un maillage reste une illustration :
    l'ordre de lecture existant ne bouge pas."""
    graph = {"1": {"class_type": "SaveVideo", "inputs": {"filename_prefix": "v"}},
             "2": {"class_type": "SaveGLB", "inputs": {"filename_prefix": "m"}}}
    assert autobind.infer_kind(graph) == "video"


def test_un_seul_mot_pour_une_seule_chose():
    """Le fichier produit s'annonce « 3d » (adapter/media.py) : l'entrée qui le
    charge porte le même mot. Deux orthographes pour la même chose, c'est la
    dérive que ce dépôt corrige partout ailleurs."""
    from comfyui_bridge.adapter.media import KIND_BY_EXT
    from comfyui_bridge.core.intention import MEDIA_CATEGORIES, MediaKind
    assert "3d" in MEDIA_CATEGORIES
    assert KIND_BY_EXT["glb"] == "3d"
    assert MediaKind.MODEL_3D.value == "3d"


def test_un_modele_3d_joint_est_pilotable():
    graph = {
        "1": {"class_type": "Load3D",
              "inputs": {"model_file": "3d/cube.obj", "width": 512, "height": 512}},
        "2": {"class_type": "SaveGLB", "inputs": {"filename_prefix": "3d/x"}},
    }
    b = autobind.derive_bindings(graph)
    assert (b["3d"].node, b["3d"].input) == ("1", "model_file")
    spec = _spec(graph)
    assert spec.carried == {"3d": "3d/cube.obj"}
    # Aucun élément neutre pour un maillage : on ne prétend pas en fabriquer un.
    assert spec.profile.neutral_for == ()


def test_un_run_echoue_ne_reste_pas_en_vol(tmp_path):
    """Le journal des runs en vol dit ce qui NOUS EST DÛ. Un run que le moteur a
    terminé par une erreur reste dans son historique, donc il n'a jamais l'air
    disparu : il y restait pour toujours, repassé en revue à chaque reprise
    (mesuré — une entrée d'un workflow retiré depuis longtemps traînait encore)."""
    from comfyui_bridge.adapter.inflight import InflightLog, recover

    log = InflightLog(tmp_path / "inflight.json")
    log.add("p-echec", workflow="wf", config="c", work=None, params={}, at="t", kind="image")
    log.add("p-encours", workflow="wf", config="c", work=None, params={}, at="t", kind="image")

    class _Moteur:
        def collect(self, prompt_id, plan=None):
            return None                       # rien de définitif à ramasser
        def settled(self, prompt_id):
            return prompt_id == "p-echec"     # le moteur a tranché celui-là
        def vanished(self, prompt_id):
            return False                      # …et il le connaît encore

    etats = {r["prompt_id"]: r["state"] for r in recover(_Moteur(), log, None, "h")}
    assert etats == {"p-echec": "failed"}
    assert list(log.entries()) == ["p-encours"]
