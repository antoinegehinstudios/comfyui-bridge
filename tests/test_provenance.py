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
