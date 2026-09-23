"""Ce qu'une pièce jointe doit ÊTRE pour que le moteur puisse l'ouvrir.

Le 2026-09-22, un dessin vectoriel (`retarus_2025_RGB.svg`) a été déposé comme
image de référence. Le nœud qui charge une image ne sait pas l'ouvrir ; il se
rabat alors sur le chemin VIDÉO, et la bibliothèque de démultiplexage meurt
d'une violation d'accès qui emporte le moteur ENTIER — pas une erreur de run,
un processus qui disparaît. Vingt et un lancements sur les vingt-quatre échecs
de la journée citent ce fichier ; entre eux, le moteur était mort et tout ce
qu'Antoine lançait échouait « moteur injoignable », « run perdu », ou après une
heure d'attente.

D'où cette porte, en deux temps :

* au DÉPÔT, le contenu décide (la catégorie « image » s'ouvre vraiment comme
  une image, sinon le dépôt est refusé en disant quoi faire) ;
* au LANCEMENT, le NOM décide — c'est tout ce qu'on a d'un fichier déjà chez le
  moteur (un rejeu, un raccourci d'hier) : une extension que le moteur ne sait
  pas ouvrir en image est refusée avant d'y toucher.

Le module ne lit aucun fichier et n'ouvre rien lui-même : il dit la RÈGLE. Le
jugement du contenu vit chez l'adaptateur, qui a Pillow.
"""

from __future__ import annotations

import os

# Ce qu'on sait NE PAS être une image matricielle, et qui traîne pourtant dans
# un dossier d'images : dessins vectoriels et documents. La liste est un refus
# NOMMÉ, pas une permission — ce qui n'y est pas est jugé sur son contenu au
# dépôt, là où on l'a sous la main.
PAS_DES_IMAGES: dict[str, str] = {
    ".svg": "un dessin vectoriel",
    ".svgz": "un dessin vectoriel compressé",
    ".eps": "un dessin vectoriel (PostScript)",
    ".ai": "un dessin vectoriel (Illustrator)",
    ".pdf": "un document",
    ".doc": "un document",
    ".docx": "un document",
    ".txt": "du texte",
    ".json": "des données",
}

# Ce qu'il faut faire, dit une fois : c'est la phrase que l'utilisateur lit.
QUOI_FAIRE = ("l'exporter en PNG (ou JPEG) avant de le déposer — le moteur "
              "n'ouvre que des images matricielles")


def refus_par_le_nom(nom: str, categorie: str) -> str | None:
    """Pourquoi ce NOM de fichier ne peut pas servir de pièce jointe de cette
    catégorie — ou ``None`` s'il n'y a rien à reprocher au nom.

    Le nom est tout ce qu'on a au lancement : le fichier est déjà chez le
    moteur, déposé il y a une minute ou la semaine dernière. Un refus ici évite
    au moteur un fichier qui le tue.
    """
    if categorie != "image":
        return None
    ext = os.path.splitext(str(nom or ""))[1].lower()
    quoi = PAS_DES_IMAGES.get(ext)
    if quoi is None:
        return None
    if ext in (".svg", ".svgz"):
        # Depuis le 2026-09-23 le DÉPÔT accueille un dessin vectoriel : il le
        # peint et c'est l'image peinte qui part chez le moteur. Ce qui reste
        # refusé, c'est de DÉSIGNER le document lui-même comme image — le
        # moteur ne sait pas l'ouvrir, et il en tombe.
        return (f"« {nom} » est {quoi} : le moteur ne sait pas l'ouvrir comme une image. "
                f"Le redéposer — le dépôt le peint et donne l'image — ou choisir l'image "
                f"déjà peinte ({os.path.splitext(nom)[0]}.png). Le document, lui, reste "
                f"utilisable dans le flux par le nœud qui le repeint.")
    return (f"« {nom} » est {quoi}, pas une image : {QUOI_FAIRE}. "
            f"(Mesuré le 2026-09-22 : un fichier de ce genre fait tomber le moteur "
            f"tout entier, et les productions suivantes avec.)")
