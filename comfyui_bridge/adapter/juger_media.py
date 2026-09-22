"""Ouvrir une pièce jointe pour savoir si le moteur le pourra.

Le contrat (ce qui est une image, et ce qu'il faut dire) vit dans
`core/pieces_jointes.py` ; ici, le seul geste qui demande une bibliothèque :
ouvrir les octets. Si Pillow n'est pas là, on ne prétend pas juger — on laisse
passer en le disant, plutôt que de refuser un dépôt valide.
"""

from __future__ import annotations

import io

from ..core.pieces_jointes import QUOI_FAIRE, refus_par_le_nom


def refus(octets: bytes, nom: str, categorie: str) -> str | None:
    """Pourquoi le moteur ne pourra pas ouvrir cette pièce jointe, ou None.

    Le NOM d'abord (un dessin vectoriel se refuse sans rien ouvrir), puis le
    CONTENU pour une image : Pillow l'ouvre, ou personne ne l'ouvrira.
    """
    par_le_nom = refus_par_le_nom(nom, categorie)
    if par_le_nom is not None:
        return par_le_nom
    if categorie != "image" or not octets:
        return None
    try:
        from PIL import Image
    except Exception:                                    # noqa: BLE001 — sans Pillow, on ne juge pas
        return None
    try:
        with Image.open(io.BytesIO(octets)) as vue:
            vue.verify()                                 # lit l'en-tête et la structure
    except Exception as exc:                             # noqa: BLE001 — l'échec EST la réponse
        debut = octets[:80].decode("utf-8", "replace").strip().replace("\n", " ")
        return (f"« {nom} » ne s'ouvre pas comme une image ({type(exc).__name__}) : "
                f"{QUOI_FAIRE}. Début du fichier : {debut[:60]!r}")
    return None
