"""Les entrées MÉDIA d'un graphe ComfyUI : quels nœuds en portent, et laquelle.

ComfyUI le déclare lui-même, nœud par nœud, dans ``/object_info`` : l'entrée
qui reçoit un fichier porte un drapeau ``image_upload`` / ``video_upload`` /
``audio_upload`` / ``file_upload``. Cette table en est le miroir hors-ligne —
la découverte tourne aussi quand le moteur est éteint, et les liaisons sont
re-dérivées du graphe à chaque lecture, donc elles ne peuvent pas dépendre de
ce que le moteur répondait ce jour-là.

Vérifiable à tout moment contre le moteur :

    GET /object_info  ->  les entrées portant un drapeau ``*_upload``

Ajouter un nœud de chargement (un custom node) = ajouter une ligne ici.
``/v1/workflows/{nom}/io`` signale d'ailleurs les entrées que ComfyUI déclare
téléversables et que cette table ne connaît pas : ce qui manque se dit.
"""

from __future__ import annotations

from typing import Any

# class_type ComfyUI -> (catégorie de média, nom de l'entrée qui reçoit le fichier)
MEDIA_LOADERS: dict[str, tuple[str, str]] = {
    "LoadImage": ("image", "image"),
    "LoadImageMask": ("image", "image"),
    "LoadImageOutput": ("image", "image"),
    "LoadVideo": ("video", "file"),
    "LoadAudio": ("audio", "audio"),
    "Load3D": ("3d", "model_file"),
    "Load3DAdvanced": ("3d", "model_file"),
    # Un nœud qui prend une image par son NOM sans être un chargeur d'image :
    # il ouvre le fichier lui-même (il a besoin du fichier, pas des pixels, pour
    # écrire ce qu'il en sait à côté). Absent d'ici, il n'apparaissait pas comme
    # une pièce jointe et le formulaire ne proposait rien à joindre.
    "ImageSavoir": ("image", "image"),
}

# Les drapeaux par lesquels ComfyUI annonce qu'une entrée reçoit un fichier.
UPLOAD_FLAGS: dict[str, str] = {
    "image_upload": "image",
    "video_upload": "video",
    "audio_upload": "audio",
    "file_upload": "3d",
}


# Où ComfyUI range une pièce jointe selon sa catégorie, et donc sous quel nom
# un graphe peut la citer. Images, vidéos et sons vivent à la racine du dossier
# d'entrée ; un modèle 3D vit dans « 3d/ » et Load3D le liste « 3d/<nom> ».
# Téléverser sans le dire rendait un nom que le graphe ne résolvait pas.
UPLOAD_SUBFOLDER: dict[str, str] = {"3d": "3d"}


def upload_subfolder(param_or_category: str) -> str:
    from ..core.intention import media_category
    cle = media_category(param_or_category) or param_or_category
    return UPLOAD_SUBFOLDER.get(cle, "")


def node_order(node_id: str) -> tuple:
    """Ordre STABLE des nœuds d'un graphe.

    Les identifiants sont des chaînes, dont « 320:290 » pour un nœud aplati
    depuis un sous-graphe. Trier en texte mettrait « 10 » avant « 9 » et le
    rang d'une pièce jointe changerait d'un workflow à l'autre sans raison.
    """
    out: list[tuple[int, Any]] = []
    for part in str(node_id).split(":"):
        out.append((0, int(part)) if part.isdigit() else (1, part))
    return tuple(out)


def media_nodes(graph: dict[str, Any]) -> list[tuple[str, str, str, str]]:
    """Les entrées média du graphe : (node_id, class_type, catégorie, entrée).

    Dans l'ordre des nœuds, et seulement celles qui portent une VALEUR (un nom
    de fichier) : une entrée alimentée par un lien est produite par un autre
    nœud, pas fournie par l'appelant.
    """
    out: list[tuple[str, str, str, str]] = []
    for nid in sorted(graph, key=node_order):
        node = graph.get(nid)
        if not isinstance(node, dict):
            continue
        found = MEDIA_LOADERS.get(node.get("class_type") or "")
        if not found:
            continue
        categorie, entree = found
        if isinstance((node.get("inputs") or {}).get(entree), str):
            out.append((str(nid), str(node.get("class_type")), categorie, entree))
    return out


# Ce qu'un sélecteur de fichier doit proposer, par catégorie. Une seule table,
# pour que la console et tout autre appelant filtrent pareil.
ACCEPT: dict[str, str] = {
    "image": "image/*",
    "video": "video/*",
    "audio": "audio/*",
    "3d": ".glb,.gltf,.obj,.fbx,.stl,.ply",
}


def describe(bindings, titles: dict[str, str] | None = None,
             carried: dict[str, Any] | None = None,
             graph: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Les pièces jointes d'un workflow, telles qu'un appelant les remplit.

    Une projection de ce que la découverte a déjà trouvé — pas une seconde
    source : le nom vient des liaisons, l'étiquette du titre que l'auteur a
    écrit dans ComfyUI, le contenu embarqué du graphe.
    """
    from ..core.intention import media_category
    from .labels import label_for
    from .neutral import has_neutral

    graph = graph or {}
    carried = carried or {}
    out: list[dict[str, Any]] = []
    for param, b in (bindings or {}).items():
        categorie = media_category(param)
        if not categorie:
            continue
        noeud = graph.get(b.node) or {}
        out.append({
            "param": param,
            "category": categorie,
            "node": b.node,
            "input": b.input,
            "class_type": noeud.get("class_type"),
            # Le nom que l'auteur a donné au nœud (« Load Last Frame ») : c'est
            # lui qui rend l'entrée reconnaissable, pas « image_2 ».
            "label": label_for(b.node, titles or {}),
            "carried": carried.get(param),
            "neutral": has_neutral(param),
            "accept": ACCEPT.get(categorie, "*/*"),
        })
    return sorted(out, key=lambda e: (e["category"], node_order(e["node"])))


def unbound(graph: dict[str, Any], bindings, object_info: dict[str, Any]) -> list[dict[str, Any]]:
    """Les entrées que ComfyUI déclare téléversables et que rien ne pilote ici.

    Le moteur porte les drapeaux ``*_upload`` dans ``/object_info`` ; la table
    de ce module en est le miroir hors-ligne. Un custom node absent de la table
    apparaît donc ICI plutôt que de disparaître en silence — ce qui manque se
    dit, et se corrige en ajoutant une ligne à ``MEDIA_LOADERS``.
    """
    pilotes = {(b.node, b.input) for b in (bindings or {}).values()}
    manquants: list[dict[str, Any]] = []
    for nid in sorted(graph, key=node_order):
        noeud = graph.get(nid)
        if not isinstance(noeud, dict):
            continue
        schema = object_info.get(noeud.get("class_type") or "") or {}
        declares: dict[str, Any] = {}
        for section in ("required", "optional"):
            declares.update((schema.get("input") or {}).get(section) or {})
        for entree, spec in declares.items():
            meta = next((x for x in (spec[1:] if isinstance(spec, list) else []) 
                         if isinstance(x, dict)), {})
            categorie = next((c for f, c in UPLOAD_FLAGS.items() if meta.get(f)), None)
            if not categorie:
                continue
            if (str(nid), entree) in pilotes:
                continue
            if not isinstance((noeud.get("inputs") or {}).get(entree), str):
                continue
            manquants.append({"node": str(nid), "input": entree, "category": categorie,
                              "class_type": noeud.get("class_type")})
    return manquants
