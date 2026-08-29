"""Ce qui est livré est-il ce qui a été demandé ?"""

import json
import struct
from pathlib import Path

from comfyui_bridge.core.delivery import compare, describe


def test_a_workflow_that_recomputes_the_size_is_reported():
    """Mesuré : 352x224 pour 1 s demandés ont produit 320x192 pour 0,75 s. Le
    livrable était juste pour ce workflow, l'appelant n'en savait rien."""
    gaps = compare({"width": 352, "height": 224, "duration_s": 1.0, "fps": 12},
                   {"width": 320, "height": 192, "duration_s": 0.75})
    assert [g["field"] for g in gaps] == ["width", "height", "duration_s"]
    assert gaps[0] == {"field": "width", "asked": 352, "delivered": 320}
    assert "demandé 352, livré 320" in describe(gaps)


def test_a_container_rounding_is_not_an_error():
    """Une image de plus ou de moins n'est pas un écart : 48 images à 24 i/s
    tiennent en 2,042 s dans un conteneur MP4."""
    assert compare({"duration_s": 2.0, "fps": 24}, {"duration_s": 2.042}) == []
    # …mais un quart de la durée en moins, si.
    assert compare({"duration_s": 2.0, "fps": 24}, {"duration_s": 1.5}) != []


def test_nothing_measured_means_nothing_claimed():
    """Un format qu'on ne sait pas lire ne produit aucun écart : une mesure
    absente ne doit pas se transformer en accusation."""
    assert compare({"width": 512, "height": 512}, {}) == []
    assert compare({}, {"width": 320}) == []
    # Un champ non demandé n'est pas comparé.
    assert compare({"width": 320}, {"width": 320, "duration_s": 9.0}) == []


def test_the_frame_count_is_compared_when_both_are_known():
    assert compare({"latent_batch": 16}, {"frames": 8}) == [
        {"field": "frames", "asked": 16, "delivered": 8}]
    assert compare({"latent_batch": 16}, {"frames": 16}) == []


def test_the_reader_only_claims_what_it_can_read(tmp_path):
    """WEBM, MKV, OGG ne sont pas lus ici : rien n'est annoncé plutôt qu'un
    chiffre inventé."""
    from comfyui_bridge.adapter.measure import measure

    for nom in ("clip.webm", "clip.mkv", "son.ogg", "inconnu.xyz"):
        fichier = tmp_path / nom
        fichier.write_bytes(b"\x00" * 64)
        assert measure(fichier) == {}
    # Un fichier annoncé lisible mais corrompu ne fait pas tomber la mesure.
    casse = tmp_path / "casse.mp4"
    casse.write_bytes(b"not an mp4 at all")
    assert measure(casse) == {}


def test_a_real_png_is_measured(tmp_path):
    from PIL import Image

    from comfyui_bridge.adapter.measure import measure
    chemin = tmp_path / "img.png"
    Image.new("RGB", (321, 123), (255, 255, 255)).save(chemin)
    assert measure(chemin) == {"width": 321, "height": 123}


def test_a_workflow_that_measures_delivers_its_numbers():
    """ComfyUI rapporte une sortie NON média sous la clé `files`. Ignorée, un
    graphe d'analyse livrait son illustration et jamais son résultat."""
    from comfyui_bridge.adapter.media import media_kind, output_refs

    entree = {"outputs": {"5": {"text": ["12 faits"],
                                "files": [{"filename": "faits_00001.txt", "type": "output"}],
                                "images": [{"filename": "annote_00001.png", "type": "output"}]}}}
    trouves = [ref["filename"] for ref in output_refs(entree)]
    assert trouves == ["annote_00001.png", "faits_00001.txt"]
    assert media_kind("faits_00001.txt") == "text"


def test_a_workflow_that_delivers_a_mesh_delivers_it():
    """MESURÉ sur un run réel de `3d_moge_perspective_to_mesh` : ComfyUI range
    la sortie de SaveGLB sous la clé `3d`, pas `images`. Sans cette clé, le
    maillage n'était jamais ramassé et le run livrait à sa place les deux
    aperçus temporaires du graphe — un résultat faux, pas une erreur."""
    from comfyui_bridge.adapter.media import media_kind, output_refs

    # L'historique du run, tel que ComfyUI l'a rendu : les deux aperçus de
    # normales du graphe, et le maillage sous `3d`.
    entree = {"outputs": {
        "47": {"images": [{"filename": "ComfyUI_temp_xpzrj_00001_.png",
                           "subfolder": "", "type": "temp"}]},
        "46": {"images": [{"filename": "ComfyUI_temp_otvyr_00001_.png",
                           "subfolder": "", "type": "temp"}]},
        "21": {"3d": [{"filename": "verif-3d_00001_.glb",
                       "subfolder": "cortex", "type": "output"}]}}}
    assert [r["filename"] for r in output_refs(entree)] == ["verif-3d_00001_.glb"]
    assert media_kind("verif-3d_00001_.glb") == "3d"


def test_a_mesh_is_a_deliverable_named_for_what_it_is():
    """Sans extension 3D dans la table, `GET /v1/artifacts` ne listait jamais un
    maillage produit, et `media_kind` le disait « image » par défaut."""
    from comfyui_bridge.adapter.media import DELIVERABLE_EXT, media_kind

    for nom in ("scene.glb", "scene.gltf", "scene.obj", "nuage.ply"):
        assert Path(nom).suffix in DELIVERABLE_EXT, nom
        assert media_kind(nom) == "3d", nom


def test_what_the_engine_reports_without_a_file_is_not_a_deliverable():
    """Save3DAdvanced rapporte sous `result` un chemin nu et l'état de sa
    caméra — rien à rapatrier. Ramasser cela ferait échouer le téléchargement
    sur une référence qui ne désigne aucun fichier."""
    from comfyui_bridge.adapter.media import output_refs

    entree = {"outputs": {"7": {"result": ["3d/ComfyUI_00001_.glb", {"position": [0, 0, 5]}, None]}}}
    assert output_refs(entree) == []


def _glb_minuscule(sommets: int, triangles: int) -> bytes:
    """Un GLB conforme : entête, puis le morceau JSON qui décrit la scène."""
    doc = json.dumps({
        "asset": {"version": "2.0"},
        "accessors": [{"count": sommets}, {"count": triangles * 3}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1}]}],
    }).encode("utf-8")
    doc += b" " * (-len(doc) % 4)              # les morceaux sont alignés sur 4
    return (struct.pack("<4sII", b"glTF", 2, 12 + 8 + len(doc))
            + struct.pack("<I4s", len(doc), b"JSON") + doc)


def test_a_mesh_is_measured_by_what_a_mesh_has(tmp_path):
    from comfyui_bridge.adapter.measure import measure

    maillage = tmp_path / "ComfyUI_00001_.glb"
    maillage.write_bytes(_glb_minuscule(sommets=1234, triangles=2400))
    assert measure(maillage) == {"vertices": 1234, "triangles": 2400}
    # Une géométrie n'a ni largeur ni durée : la demande ne produit aucun écart
    # plutôt qu'un « livré 0 » inventé.
    assert compare({"width": 1024, "height": 1024}, measure(maillage)) == []


def test_a_geometry_format_that_is_not_read_claims_nothing(tmp_path):
    """OBJ, STL, PLY, splats ne sont pas lus ici — et un GLB tronqué non plus."""
    from comfyui_bridge.adapter.measure import measure

    for nom in ("scene.obj", "scene.stl", "nuage.ply", "nuage.splat"):
        fichier = tmp_path / nom
        fichier.write_bytes(b"v 0 0 0" * 8)
        assert measure(fichier) == {}, nom
    tronque = tmp_path / "coupe.glb"
    tronque.write_bytes(_glb_minuscule(8, 12)[:14])
    assert measure(tronque) == {}
    pas_un_glb = tmp_path / "faux.glb"
    pas_un_glb.write_bytes(b"not a glb at all, not even close")
    assert measure(pas_un_glb) == {}


def test_the_service_never_delivers_its_own_working_files():
    """Le backend CLI écrit ses brouillons dans le dossier de sortie : ramasser
    « tout fichier nouveau » les livrerait comme s'ils étaient le résultat."""
    from comfyui_bridge.adapter.media import DELIVERABLE_EXT, is_working_file

    assert ".json" in DELIVERABLE_EXT            # un résultat peut être un .json…
    assert is_working_file("_workflow_ab12.json")          # …mais pas celui-ci
    assert is_working_file("cortex_video_ab12.manifest.json")
    assert not is_working_file("mesures_00001.json")


def test_both_backends_share_one_definition_of_a_deliverable():
    """Leurs tables d'extensions avaient déjà divergé une fois."""
    import inspect

    from comfyui_bridge.adapter import comfy_cli, comfy_http
    for module in (comfy_cli, comfy_http):
        source = inspect.getsource(module)
        assert '"images", "gifs"' not in source, module.__name__   # plus de liste recopiée
