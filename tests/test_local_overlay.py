"""Ce qui décrit une machine ne doit jamais vivre dans le paquet."""

import json

from comfyui_bridge.adapter.local_overlay import local_path, merge, read_overlay


def test_the_overlay_adds_and_replaces_but_invents_nothing():
    livre = {"version": 1, "default": "attach",
             "engines": {"attach": {"base_url": "http://127.0.0.1:8188"}}}
    local = {"default": "local",
             "engines": {"local": {"base_url": "http://127.0.0.1:8188", "manage": True}}}
    fusion = merge(livre, local, "engines")
    assert set(fusion["engines"]) == {"attach", "local"}     # s'ajoute
    assert fusion["default"] == "local"                      # remplace
    assert fusion["version"] == 1                            # ce qu'elle tait reste


def test_a_local_entry_of_the_same_name_wins():
    fusion = merge({"engines": {"local": {"base_url": "a"}}},
                   {"engines": {"local": {"base_url": "b"}}}, "engines")
    assert fusion["engines"]["local"]["base_url"] == "b"


def test_no_overlay_changes_nothing(tmp_path):
    livre = {"engines": {"attach": {}}}
    assert merge(livre, {}, "engines") == livre
    assert read_overlay(tmp_path, tmp_path / "engines.json") == {}
    assert read_overlay(None, tmp_path / "engines.json") == {}


def test_a_broken_overlay_is_reported_not_swallowed(tmp_path):
    """Un fichier local invalide est une erreur de l'utilisateur : la taire
    ferait tourner la passerelle sur une configuration qu'il croit appliquée."""
    import pytest
    (tmp_path / "engines.local.json").write_text("{ pas du json", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        read_overlay(tmp_path, tmp_path / "engines.json")


def test_the_overlay_file_is_named_after_the_shipped_one(tmp_path):
    assert local_path(tmp_path, "a/b/engines.json").name == "engines.local.json"
    assert local_path(tmp_path, "reconciliation.json").name == "reconciliation.local.json"


def test_the_shipped_resources_name_nobody_s_machine():
    """Le paquet ne doit publier l'arborescence de personne."""
    from pathlib import Path
    import re

    res = Path(__file__).resolve().parent.parent / "comfyui_bridge" / "adapter" / "resources"
    # Une lettre de lecteur, pas le "p:/" de "http://".
    machine = re.compile(r"(?<![A-Za-z]):?(?<![A-Za-z])[A-Za-z]:[\/]|/home/|/Users/")
    for fichier in res.glob("*.json"):
        if fichier.name.endswith(".exemple.json"):
            continue                       # un exemple montre des chemins fictifs
        assert not machine.search(fichier.read_text(encoding="utf-8")), fichier.name
