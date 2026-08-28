"""D'où vient un fichier produit : le graphe qui l'a fabriqué.

ComfyUI écrit DÉJÀ ce graphe dans ce qu'il produit — c'est ce qui permet de
glisser une image dans son interface et d'y retrouver le workflow :

    PNG   chunk tEXt ``prompt`` (et ``workflow`` au format interface)
    MP4   boîte ``moov/udta/meta``, clé ``prompt``
    FLAC  bloc VORBIS_COMMENT, champ ``prompt=``

Rien n'est donc écrit ici pour les formats qui savent porter leur origine : on
la LIT. Un fichier reste explicable seul, des mois plus tard, sans ce service et
sans l'historique du moteur — qui, lui, est purgé à chaque redémarrage.

Le résumé lisible est tiré du graphe par la MÊME analyse que le catalogue
(``autobind``), pour qu'un paramètre nommé ici soit celui nommé partout ailleurs.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Any

_RESUME = ("prompt", "negative_prompt", "width", "height", "latent_batch",
           "duration_s", "fps", "steps", "cfg", "seed")


def _png_text_chunks(data: bytes) -> dict[str, str]:
    out: dict[str, str] = {}
    i = 8
    while i + 8 <= len(data):
        taille = struct.unpack(">I", data[i:i + 4])[0]
        typ = data[i + 4:i + 8]
        corps = data[i + 8:i + 8 + taille]
        if typ in (b"tEXt", b"iTXt"):
            cle, _, valeur = corps.partition(b"\x00")
            if typ == b"iTXt":            # drapeaux + langue avant le texte
                valeur = valeur.split(b"\x00")[-1]
            out[cle.decode("latin-1")] = valeur.decode("utf-8", "replace")
        i += 12 + taille
        if typ == b"IEND":
            break
    return out


def _flac_comments(data: bytes) -> dict[str, str]:
    if data[:4] != b"fLaC":
        return {}
    i, out = 4, {}
    while i + 4 <= len(data):
        entete = data[i]
        taille = int.from_bytes(data[i + 1:i + 4], "big")
        corps = data[i + 4:i + 4 + taille]
        if entete & 0x7F == 4:            # VORBIS_COMMENT
            j = 4 + int.from_bytes(corps[:4], "little")
            nombre = int.from_bytes(corps[j:j + 4], "little")
            j += 4
            for _ in range(nombre):
                longueur = int.from_bytes(corps[j:j + 4], "little")
                j += 4
                cle, _, valeur = corps[j:j + longueur].partition(b"=")
                out[cle.decode("utf-8", "replace").lower()] = valeur.decode("utf-8", "replace")
                j += longueur
        i += 4 + taille
        if entete & 0x80:                 # dernier bloc de métadonnées
            break
    return out


def _mp4_json_after(data: bytes, cle: bytes) -> str | None:
    """Le JSON qui suit une clé de métadonnée, lu en équilibrant les accolades.

    Les métadonnées MP4 s'empilent dans ``udta/meta/keys`` + ``ilst`` ; plutôt
    que de reconstruire cet appariement, on repart de la clé et on lit l'objet
    JSON qui la suit, ce qui ne dépend d'aucune version du conteneur.
    """
    depart = data.find(cle)
    if depart < 0:
        return None
    # On part de `{"`, pas de `{` : la structure binaire du conteneur contient
    # des octets d'accolade qui ne sont pas du JSON, et compter à partir de l'un
    # d'eux fait courir l'équilibrage jusque dans les données vidéo.
    ouvre = data.find(b'{"', depart)
    while ouvre >= 0:
        niveau, i, dans_texte, echappe = 0, ouvre, False, False
        while i < len(data):
            c = data[i:i + 1]
            if dans_texte:
                if echappe:
                    echappe = False
                elif c == b"\\":
                    echappe = True
                elif c == b'"':
                    dans_texte = False
            elif c == b'"':
                dans_texte = True
            elif c == b"{":
                niveau += 1
            elif c == b"}":
                niveau -= 1
                if niveau == 0:
                    try:
                        brut = data[ouvre:i + 1].decode("utf-8")
                    except UnicodeDecodeError:
                        break            # ce n'était pas du texte : on essaie le suivant
                    return brut
            i += 1
        ouvre = data.find(b'{"', ouvre + 2)
    return None


def read_embedded(path: str | Path) -> dict[str, Any] | None:
    """Le graphe que le moteur a inscrit dans ce fichier. ``None`` s'il n'y en a pas.

    Illisible ou absent : on ne rend rien plutôt qu'une origine inventée.
    """
    p = Path(path)
    suffixe = p.suffix.lower()
    try:
        if suffixe == ".png":
            brut = _png_text_chunks(p.read_bytes()).get("prompt")
        elif suffixe in (".mp4", ".mov", ".m4a", ".webm", ".mkv"):
            brut = _mp4_json_after(p.read_bytes(), b"prompt")
        elif suffixe == ".flac":
            brut = _flac_comments(p.read_bytes()).get("prompt")
        else:
            return None
        return json.loads(brut) if brut else None
    except Exception:
        return None


def summarize(graph: dict[str, Any]) -> dict[str, Any]:
    """Ce qu'un humain veut relire : le texte demandé et les réglages.

    Les valeurs sont retrouvées par l'analyse qui sert déjà au catalogue, pour
    qu'un paramètre porte ici le nom qu'il porte partout ailleurs.
    """
    from . import autobind
    from .work import _total_steps

    bindings = autobind.derive_bindings(graph, {})
    resume: dict[str, Any] = {}
    from ..core.intention import is_media_param
    # Les pièces jointes en font partie, toutes : dire avec quelle image un
    # rendu a été fait n'a de sens que si on les nomme toutes, pas la première.
    for param in list(_RESUME) + sorted(p for p in bindings if is_media_param(p)):
        b = bindings.get(param)
        if not b:
            continue
        valeur = ((graph.get(b.node) or {}).get("inputs") or {}).get(b.input)
        if isinstance(valeur, (str, int, float)) and valeur != "":
            resume[param] = valeur
    etapes = _total_steps(graph)
    if etapes:
        resume["steps"] = etapes          # toutes les passes, pas un seul nœud
    resume["kind"] = autobind.infer_kind(graph)
    return resume
