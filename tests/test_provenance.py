"""D'où vient un fichier produit — lu, jamais recopié."""

import json
import struct

from comfyui_bridge.adapter.provenance import read_embedded, summarize

GRAPHE = {
    "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "m.safetensors"}},
    "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "un phare dans la brume",
                                                     "clip": ["1", 1]}},
    "3": {"class_type": "EmptyLatentImage", "inputs": {"width": 768, "height": 512,
                                                       "batch_size": 1}},
    "4": {"class_type": "KSampler", "inputs": {"seed": 4242, "steps": 24, "cfg": 6.5,
                                               "model": ["1", 0], "positive": ["2", 0],
                                               "negative": ["2", 0], "latent_image": ["3", 0]}},
    "5": {"class_type": "SaveImage", "inputs": {"filename_prefix": "x", "images": ["4", 0]}},
}


def _png_avec_prompt(chemin, graphe):
    """Un PNG minimal portant son graphe, comme ComfyUI l'écrit."""
    def chunk(typ, corps):
        return (struct.pack(">I", len(corps)) + typ + corps
                + struct.pack(">I", __import__("zlib").crc32(typ + corps) & 0xFFFFFFFF))

    texte = b"prompt\x00" + json.dumps(graphe).encode("utf-8")
    entete = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    donnees = __import__("zlib").compress(b"\x00\x00\x00\x00")
    chemin.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", entete)
                       + chunk(b"tEXt", texte) + chunk(b"IDAT", donnees) + chunk(b"IEND", b""))


def test_a_png_carries_its_own_origin(tmp_path):
    """Le moteur inscrit le graphe dans ce qu'il produit : on le lit, on ne le
    recopie pas — une copie à côté serait une seconde vérité qui peut mentir."""
    fichier = tmp_path / "sortie.png"
    _png_avec_prompt(fichier, GRAPHE)
    lu = read_embedded(fichier)
    assert lu == GRAPHE


def test_the_summary_names_parameters_as_everywhere_else(tmp_path):
    """Les valeurs sont retrouvées par l'analyse qui sert au catalogue : un
    paramètre porte ici le nom qu'il porte dans le contrat d'entrée."""
    resume = summarize(GRAPHE)
    assert resume["prompt"] == "un phare dans la brume"
    assert (resume["width"], resume["height"]) == (768, 512)
    assert resume["steps"] == 24 and resume["cfg"] == 6.5 and resume["seed"] == 4242
    assert resume["kind"] == "image"


def test_a_format_that_carries_nothing_says_so(tmp_path):
    """Ni invention, ni silence trompeur : un format muet rend None, et c'est
    ce qui justifie de lui écrire un fichier compagnon."""
    for nom in ("mesures.txt", "table.csv", "vue.webp", "inconnu.xyz"):
        fichier = tmp_path / nom
        fichier.write_bytes(b"pas de metadonnees ici")
        assert read_embedded(fichier) is None


def test_a_corrupt_file_does_not_invent_an_origin(tmp_path):
    faux = tmp_path / "casse.png"
    faux.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 40)
    assert read_embedded(faux) is None


def test_the_companion_file_is_not_itself_a_deliverable():
    """Il accompagne un livrable, il n'en est pas un."""
    from comfyui_bridge.adapter.media import SIDECAR_SUFFIX, is_working_file
    assert is_working_file("mesures.txt" + SIDECAR_SUFFIX)
    assert not is_working_file("mesures.txt")


def test_companion_carries_only_what_no_file_can(tmp_path):
    """Le compagnon dit le nom du workflow, et NE REDIT PAS le reste."""
    from comfyui_bridge.adapter.sidecar import write_companion
    from comfyui_bridge.core.plan import ExecutionPlan, RenderIntent

    livrable = tmp_path / "sortie_00001_.txt"
    livrable.write_text("resultat", encoding="utf-8")
    # Un VRAI plan : c'est sa forme réelle qu'on veut voir tenir, `config` y
    # étant une propriété calculée et non un champ.
    plan = ExecutionPlan(intent=RenderIntent(prompt="un chat"), kind="image",
                         workflow="scene-render-analysis",
                         params={"prompt": "un chat", "seed": 7})

    ecrit = write_companion(livrable, plan, graph={"1": {"class_type": "LoadImage"}})
    assert ecrit == tmp_path / "sortie_00001_.txt.origine.json"

    d = json.loads(ecrit.read_text(encoding="utf-8"))
    assert d["workflow"] == "scene-render-analysis"
    assert d["fichier"] == "sortie_00001_.txt"
    assert d["prompt"] == {"1": {"class_type": "LoadImage"}}   # .txt ne sait pas le porter
    # Rien de ce que le fichier sait dire de lui-même : deux versions d'une même
    # valeur ne font pas deux preuves, seulement un doute à départager.
    assert "demande" not in d and "kind" not in d and "empreinte_config" not in d


def test_companion_is_not_a_deliverable(tmp_path):
    """Sinon le backend CLI livrerait le compagnon qu'il vient d'écrire."""
    from comfyui_bridge.adapter.media import DELIVERABLE_EXT, is_working_file
    from comfyui_bridge.adapter.sidecar import write_companion

    livrable = tmp_path / "a.png"
    livrable.write_bytes(b"\x89PNG\r\n\x1a\n")
    ecrit = write_companion(livrable, None)
    assert ecrit.suffix.lower() in DELIVERABLE_EXT and is_working_file(ecrit)


def test_companion_failure_never_costs_the_artifact(tmp_path):
    """Un dossier disparu ne doit pas faire perdre le livrable."""
    from comfyui_bridge.adapter.sidecar import write_companion
    assert write_companion(tmp_path / "absent" / "x.png", None) is None


def test_any_backend_delivers_with_its_origin(tmp_path):
    """La règle tient sur le PORT : un backend qui l'ignore la reçoit quand même."""
    from comfyui_bridge.adapter.sidecar import WithOrigin
    from comfyui_bridge.core.plan import Artifact, BackendResult, ExecutionPlan, RenderIntent
    from comfyui_bridge.adapter.media import SIDECAR_SUFFIX

    livrable = tmp_path / "sortie.txt"
    livrable.write_text("resultat", encoding="utf-8")

    class BackendMuet:
        """N'écrit aucune origine, et n'a pas à le savoir."""
        def preview(self, plan): return {"9": {"class_type": "SaveText"}}
        def submit(self, plan, *a, **k):
            return BackendResult(artifacts=[Artifact(kind="text", path=str(livrable),
                                                     url="/artifacts/sortie.txt", bytes=8)])

    plan = ExecutionPlan(intent=RenderIntent(prompt="p"), params={"seed": 3},
                         workflow="un-workflow", kind="text")
    WithOrigin(BackendMuet()).submit(plan)

    compagnon = livrable.with_name(livrable.name + SIDECAR_SUFFIX)
    d = json.loads(compagnon.read_text(encoding="utf-8"))
    assert d["workflow"] == "un-workflow"
    assert d["prompt"] == {"9": {"class_type": "SaveText"}}   # .txt ne le porte pas


def test_port_wrapper_stays_transparent(tmp_path):
    """Envelopper ne doit rien retirer : le reste du backend passe au travers."""
    from comfyui_bridge.adapter.sidecar import WithOrigin

    class Backend:
        def load_of(self, plan): return 4.0, 2
        def health(self): return {"ok": True}
    enveloppe = WithOrigin(Backend())
    assert enveloppe.load_of(None) == (4.0, 2)
    assert enveloppe.health() == {"ok": True}


def test_recovered_run_keeps_the_kind_it_was_launched_with(tmp_path):
    """Un livrable vidéo repris ne doit pas se déclarer image."""
    from comfyui_bridge.adapter.inflight import InflightLog, _plan_from

    log = InflightLog(tmp_path / "inflight.json")
    log.add("p1", workflow="minimax-h3-court", config="x240", work=1.0,
            params={"fps": 24}, at="2026-08-28T00:00:00+00:00", kind="video")

    plan = _plan_from(log.entries()["p1"])
    assert plan.kind == "video"
    assert plan.workflow == "minimax-h3-court"
