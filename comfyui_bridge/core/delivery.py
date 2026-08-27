"""Comparer ce qui a été livré à ce qui a été demandé.

Un workflow peut recalculer ce qu'on lui donne : 352x224 demandés pour 1 s
produisent 320x192 pour 0,75 s quand le latent est construit à la moitié de la
taille puis suréchantillonné. Le livrable est juste — pour ce workflow — mais
l'appelant n'en sait rien.

Ce module ne lit aucun fichier : il compare des nombres. La mesure du média est
faite par l'adaptateur, la seule couche qui sait ouvrir un conteneur.
"""

from __future__ import annotations

from typing import Any

# Une image de plus ou de moins est un arrondi de conteneur, pas un écart. En
# l'absence de cadence connue, on tolère 5 % — au-delà, ce n'est plus un arrondi.
_FRAMES_OF_TOLERANCE = 1.5
_RELATIVE_TOLERANCE = 0.05


def compare(asked: dict[str, Any], measured: dict[str, Any]) -> list[dict[str, Any]]:
    """Les écarts entre la demande et le média produit, champ par champ.

    Vide quand tout concorde — ou quand rien n'est comparable : ce qui n'a pas
    été demandé, ou n'a pas pu être mesuré, ne produit aucun écart.
    """
    if not asked or not measured:
        return []

    gaps: list[dict[str, Any]] = []
    for field in ("width", "height"):
        want, got = asked.get(field), measured.get(field)
        if want and got and int(want) != int(got):
            gaps.append({"field": field, "asked": int(want), "delivered": int(got)})

    want_duration = asked.get("duration_s")
    got_duration = measured.get("duration_s")
    if want_duration and got_duration:
        fps = asked.get("fps")
        tolerance = (_FRAMES_OF_TOLERANCE / float(fps)) if fps else (
            float(want_duration) * _RELATIVE_TOLERANCE)
        if abs(float(got_duration) - float(want_duration)) > tolerance:
            gaps.append({"field": "duration_s", "asked": round(float(want_duration), 3),
                         "delivered": round(float(got_duration), 3)})

    want_frames, got_frames = asked.get("latent_batch"), measured.get("frames")
    if want_frames and got_frames and int(want_frames) != int(got_frames):
        gaps.append({"field": "frames", "asked": int(want_frames), "delivered": int(got_frames)})
    return gaps


def describe(gaps: list[dict[str, Any]]) -> str:
    """Une phrase pour le journal du run."""
    parts = [f"{g['field']} demandé {g['asked']}, livré {g['delivered']}" for g in gaps]
    return "écart entre la demande et le média livré — " + " · ".join(parts)
