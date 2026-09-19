"""Single source for artifact facts: media kind by extension, and artifact URL.

Both backends produce artifacts; before this module each had its own extension
table and its own URL construction, and they had already drifted (audio known to
one, ignored by the other). One definition, imported by both.
"""

from __future__ import annotations

from pathlib import Path

# Extension (en minuscules, sans point) -> Artifact.kind
KIND_BY_EXT: dict[str, str] = {
    "png": "image", "jpg": "image", "jpeg": "image", "webp": "image",
    "mp4": "video", "webm": "video", "gif": "video", "mkv": "video",
    "flac": "audio", "wav": "audio", "mp3": "audio", "ogg": "audio",
    # Un workflow peut livrer une GÉOMÉTRIE : un maillage estimé depuis une
    # photo (MoGe), un nuage de points, un splat. Sans ces extensions, le
    # maillage produit n'apparaissait dans aucune liste de livrables et se
    # faisait passer pour une image, faute de mieux.
    "glb": "3d", "gltf": "3d", "obj": "3d", "fbx": "3d", "stl": "3d",
    "usdz": "3d", "ply": "3d", "splat": "3d", "spz": "3d", "ksplat": "3d",
    # Un workflow ne produit pas que du média : certains MESURENT et rendent des
    # nombres, une légende, une liste de régions. Ignorer ces fichiers livrait
    # l'illustration d'une analyse sans jamais livrer son résultat.
    "txt": "text", "json": "text", "csv": "text", "md": "text", "yaml": "text",
    # Ce qu'un run écrit pour le run SUIVANT — un conditionnement, un latent
    # (relais entre deux runs d'un même tour) : ni un média, ni des nombres.
    "pt": "relais", "latent": "relais",
}

MEDIA_EXT = frozenset("." + e for e, k in KIND_BY_EXT.items() if k not in ("text", "relais"))
DATA_EXT = frozenset("." + e for e, k in KIND_BY_EXT.items() if k == "text")
# Tout ce qu'un run peut livrer. Les deux backends s'y réfèrent : leurs tables
# d'extensions avaient déjà divergé une fois, l'un connaissant l'audio et
# l'autre pas.
DELIVERABLE_EXT = MEDIA_EXT | DATA_EXT

# Les clés sous lesquelles ComfyUI rapporte ce qu'un nœud a produit, dans son
# historique. `files` est celle des sorties NON média — elle manquait, et un
# graphe d'analyse ne livrait donc jamais ses nombres. `3d` est celle de la
# géométrie : LU dans le moteur (comfy_extras/nodes_save_3d.py, SaveGLB rend
# `IO.NodeOutput(ui={"3d": results})`) et MESURÉE sur un run réel — sans elle,
# le maillage écrit par le moteur n'était jamais ramassé, et le run livrait à sa
# place les aperçus temporaires du graphe (ou rien, quand il n'y en a pas).
# « latents » (SaveLatent) et « conditionnements » (SauverConditionnement) : ce
# qu'un run écrit pour le run SUIVANT — un relais entre deux runs d'un même
# tour — se ramasse comme un livrable, sans quoi il n'existerait pas ici.
# `images_lot` : un LOT d'images écrit tel quel en tenseur (`SauverImages`, paquet
# conditionnement-en-fichier) — le relais « queue » d'un montage par tours. Mesuré
# le 2026-09-19 : le nœud écrivait bien son .pt, rapporté sous cette clé, et la
# passerelle ne le ramassait pas ; le tour suivant échouait « aucun fichier ne lui
# a été confié » après onze minutes de rendu. Une clé que le moteur rapporte et
# que ce module ignore est un livrable perdu en silence.
OUTPUT_KEYS = ("images", "gifs", "videos", "audio", "3d", "files", "latents", "conditionnements",
               "images_lot")

# La VERSION de ce mécanisme de livraison : ce que ce module sait ramasser et
# reconnaître. Elle est enregistrée avec chaque run, comme WORK_MODEL l'est pour
# le barème de charge, parce qu'un échec peut venir du mécanisme et non du
# moteur. Hermes s'en sert pour ne pas retenir contre un workflow le verdict
# d'un processus qui n'existe plus. À incrémenter quand ce module change ce
# qu'il ramasse ou comment il le nomme.
#   1: images / gifs / videos / audio / files
#   2: + la clé `3d` (géométrie) et les extensions de maillage
#   3: + la clé `images_lot` (un lot d'images en tenseur : le relais « queue »)
DELIVERY_MECHANISM = 3

# Ce que ce service écrit lui-même dans le dossier de sortie pour travailler :
# un backend qui ramasse « tout fichier nouveau » se livrerait ses propres
# brouillons comme s'ils étaient le résultat demandé.
# Suffixe du fichier d'origine, écrit à côté des livrables qui ne savent pas
# embarquer la leur.
SIDECAR_SUFFIX = ".origine.json"

# Le dossier où une chaîne pose ses pièces intermédiaires (queues extraites,
# images de raccord). Ce sont de vrais .mp4 et de vrais .png, indistinguables
# d'un livrable par leur extension : sans ce nom réservé, la liste des livrables
# offrait 50 images de travail avant la vidéo commandée.
DOSSIER_DE_TRAVAIL = "_travail"


def is_working_file(path: Path | str) -> bool:
    p = Path(path)
    nom = p.name
    return (nom.startswith("_workflow_") or nom.endswith(".manifest.json")
            # L'origine écrite à côté d'un livrable qui ne sait pas la porter
            # accompagne ce livrable : elle n'en est pas un elle-même.
            or nom.endswith(SIDECAR_SUFFIX)
            # Tout ce qui vit sous un dossier de travail, à quelque profondeur.
            or DOSSIER_DE_TRAVAIL in p.parts[:-1])


def output_refs(entry: dict) -> list[dict]:
    """Les fichiers qu'un run a produits, tels que ComfyUI les rapporte.

    Une référence est un dict `{filename, subfolder, type}` ; ce que le moteur
    range sous ces clés sans cette forme (un chemin nu, une légende) ne désigne
    aucun fichier à rapatrier. Les deux backends et les tests lisent CETTE
    fonction : la boucle était recopiée, donc libre de diverger.
    """
    refs = [ref
            for out in (entry.get("outputs") or {}).values()
            for key in OUTPUT_KEYS
            for ref in (out.get(key) or [])
            if isinstance(ref, dict) and ref.get("filename")]
    # ComfyUI marque les aperçus en type « temp » ; seuls les « output » sont le
    # livrable. Livrer un aperçu ferait mentir le résultat.
    finals = [r for r in refs if r.get("type", "output") == "output"]
    return finals or refs


def media_kind(path: Path | str) -> str:
    return KIND_BY_EXT.get(Path(path).suffix.lstrip(".").lower(), "image")


def artifact_url(out_dir: Path, path: Path) -> str:
    """URL under the /artifacts mount that serves ``out_dir``."""
    rel = Path(path).resolve().relative_to(Path(out_dir).resolve()).as_posix()
    return f"/artifacts/{rel}"
