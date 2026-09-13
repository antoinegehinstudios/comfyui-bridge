"""Habiller un menu sans jamais recopier la liste du fournisseur."""

import json

from comfyui_bridge.adapter import menus


def test_sans_declaration_une_valeur_reste_elle_meme():
    choix, manque = menus.choix(["a", "b"], None)
    assert choix == [{"valeur": "a", "libelle": "a"}, {"valeur": "b", "libelle": "b"}]
    assert manque is None
    # Un champ libre n'est pas un menu vide.
    assert menus.choix(None, None) == (None, None)


def test_des_libelles_ecrits_a_la_main():
    choix, manque = menus.choix(["a", "b"], {"libelles": {
        "a": {"libelle": "Le premier", "resume": "celui-là", "groupe": "essais"}}})
    assert choix[0] == {"valeur": "a", "libelle": "Le premier", "resume": "celui-là",
                        "groupe": "essais"}
    assert choix[1] == {"valeur": "b", "libelle": "b"}     # sans libellé : telle quelle
    assert manque is None


def test_une_source_du_fournisseur_est_projetee_a_la_lecture(tmp_path):
    """Rien n'est recopié : le jour où le fournisseur ajoute un style, il
    apparaît. La liste, elle, reste celle du fournisseur."""
    source = tmp_path / "graphiques.json"
    source.write_text(json.dumps({"styles": {
        "sumi-e": {"libelle": "Sumi-e (lavis)", "famille": "encre & lavis",
                   "resume": "Encre noire sur washi.", "prompt": "…"},
        "ukiyo-e": {"libelle": "Ukiyo-e", "famille": "estampe", "resume": "Bois gravé."},
    }}, ensure_ascii=False), encoding="utf-8")
    menu = {"source_fichier": {"chemin": str(source), "table": "styles",
                               "libelle": "libelle", "resume": "resume", "groupe": "famille"}}
    # La liste demandée ne contient PAS ukiyo-e : c'est le fournisseur qui la
    # donne, la déclaration ne fait que l'habiller.
    choix, manque = menus.choix(["sumi-e", "inconnu-du-fichier"], menu)
    assert manque is None
    assert choix[0] == {"valeur": "sumi-e", "libelle": "Sumi-e (lavis)",
                        "resume": "Encre noire sur washi.", "groupe": "encre & lavis"}
    assert choix[1] == {"valeur": "inconnu-du-fichier", "libelle": "inconnu-du-fichier"}


def test_une_source_illisible_ne_se_tait_pas(tmp_path):
    """Un menu silencieusement dégarni ressemble trait pour trait à un menu
    normal."""
    menu = {"source_fichier": {"chemin": str(tmp_path / "absent.json"), "table": "styles"}}
    choix, manque = menus.choix(["a"], menu)
    assert choix == [{"valeur": "a", "libelle": "a"}]
    assert manque and "absent.json" in manque


def test_une_declaration_vide_le_dit_aussi():
    choix, manque = menus.choix(["a"], {"libelle": "Un champ"})
    assert choix == [{"valeur": "a", "libelle": "a"}]
    assert manque and "source_fichier" in manque


def test_l_unite_vient_du_nom_du_champ_ou_de_la_declaration():
    assert menus.unite("duration_s") == "s"
    assert menus.unite("width") == "px"
    assert menus.unite("mode") is None
    assert menus.unite("duration_s", "ms") == "ms"        # ce qui est déclaré l'emporte
