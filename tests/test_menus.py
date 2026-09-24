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


def test_un_menu_se_filtre_sur_ce_que_la_ligne_porte(tmp_path):
    """« requiert » : ne garder du menu que les lignes qui portent ce que le
    champ exige. Mesuré le 2026-09-17 : le mode de révélation offrait les vingt
    et une structures du catalogue, dix-neuf sans accroche faisaient échouer
    son plan — des fantômes. Le filtre lit les lignes du fournisseur : une
    liste d'objets nommés (les temps), une liste de valeurs, une valeur."""
    from comfyui_bridge.adapter import menus
    fichier = tmp_path / "structures.json"
    fichier.write_text(json.dumps({"styles": {
        "avec-accroche": {"libelle": "Avec accroche",
                          "temps": [{"nom": "hook"}, {"nom": "corps"}], "familles": ["sociale"]},
        "sans-accroche": {"libelle": "Sans accroche", "temps": [{"nom": "continu"}],
                          "familles": ["longue"]},
        "muette": {"libelle": "Muette"},
    }}, ensure_ascii=False), encoding="utf-8")
    menu = {"source_fichier": {"chemin": str(fichier), "table": "styles", "libelle": "libelle"}}
    assert menus.valeurs(menu)[0] == ["avec-accroche", "sans-accroche", "muette"]
    assert menus.valeurs(menu, {"temps": "hook"})[0] == ["avec-accroche"]
    assert menus.valeurs(menu, {"familles": "longue"})[0] == ["sans-accroche"]
    assert menus.valeurs(menu, {"temps": "hook", "familles": "longue"})[0] == []
    # Une table écrite à la main se filtre de même, sur ses propres clés.
    ecrit = {"libelles": {"a": {"libelle": "A", "genre": "x"}, "b": {"libelle": "B", "genre": "y"}}}
    assert menus.valeurs(ecrit, {"genre": "y"})[0] == ["b"]


def test_un_menu_au_nom_du_champ_retitre_la_source_sans_effacer_ses_libelles(tmp_path):
    """2026-09-24 : le menu « ambiance » de l'encre (lanterne, chandelle…)
    recouvrait le catalogue d'ambiances d'une image, dont les valeurs
    s'affichaient en clés nues. Le menu homonyme retitre le champ et ce qu'il
    connaît de la source ; il n'efface pas le reste."""
    fichier = tmp_path / "ambiances.json"
    fichier.write_text(json.dumps({"styles": {"libre": {"libelle": "Libre", "famille": "—"},
                                              "calme": {"libelle": "Calme", "famille": "—"}}}),
                       encoding="utf-8")
    source = {"libelle": "Ambiance", "source_fichier": {"chemin": str(fichier), "table": "styles",
                                                        "libelle": "libelle", "groupe": "famille"}}
    homonyme = {"libelle": "Ambiance (encre)", "aide": "la lumière de la page",
                "libelles": {"lanterne": {"libelle": "Lanterne"}, "calme": {"libelle": "Calme (retitré)"}}}
    fusion = menus.retitrer(source, homonyme)
    assert fusion["libelle"] == "Ambiance (encre)" and fusion["aide"] == "la lumière de la page"
    choix, manque = menus.choix(["libre", "calme"], fusion)
    assert manque is None
    assert [c["libelle"] for c in choix] == ["Libre", "Calme (retitré)"]      # la source, retitrée où le menu la connaît
    assert "lanterne" not in fusion["libelles"]                              # ce que la source ignore ne s'y ajoute pas
    # Sans source lisible, les libellés du menu restent tels quels ; sans menu, la source seule.
    assert menus.retitrer({"libelle": "X"}, homonyme)["libelles"] == homonyme["libelles"]
    assert [c["libelle"] for c in menus.choix(["libre"], menus.retitrer(source, None))[0]] == ["Libre"]


def test_ce_qu_une_valeur_impose_se_declare_sur_les_faits_du_fournisseur(tmp_path):
    """2026-09-24, Antoine : « Héraldiste expose des éléments factuels, l'usage
    qui en est fait est propre au réalisateur ». La source porte des FAITS
    (une colonne, dans sa langue) ; la déclaration dit ici quel champ chaque
    fait impose, sous quelle condition, en quels mots. Un fait vide n'impose
    rien ; une condition non donnée non plus ; un lanceur reçoit `impose`
    en champs de la chaîne, jamais la langue du fournisseur."""
    fichier = tmp_path / "menu.json"
    fichier.write_text(json.dumps({"chartes": {
        "aucune": {"libelle": "Aucune", "marque": "—"},
        "pleine@1": {"libelle": "Pleine", "marque": "pleine", "faits": {
            "palette_en": "red (#E1000F)", "interdits_en": "blur",
            "titres": {"famille": "Marianne", "fichier": "http://x/f", "couleur": "#1a1a1a", "fond": "#ffffff"}}},
        "creuse@1": {"libelle": "Creuse", "marque": "creuse", "faits": {
            "palette_en": "", "interdits_en": "",
            "titres": {"famille": "Sans fichier", "fichier": None, "couleur": None, "fond": None}}},
        "muette@1": {"libelle": "Muette", "marque": "muette"}}}), encoding="utf-8")
    menu = {"source_fichier": {"chemin": str(fichier), "table": "chartes", "libelle": "libelle", "groupe": "marque",
                               "impose": {"colonne": "faits", "champs": {
                                   "palette": {"fait": "palette_en"},
                                   "negatif": {"fait": "interdits_en"},
                                   "police": {"fait": "titres.famille", "si": "titres.fichier"},
                                   "couleur_texte": {"fait": "titres.couleur"},
                                   "bandeau": {"fait": "titres.fond", "dit": "un fond plein {valeur} sous le texte"}}}}}
    choix, manque = menus.choix(["aucune", "pleine@1", "creuse@1", "muette@1"], menu)
    assert manque is None
    par = {c["valeur"]: c for c in choix}
    assert par["pleine@1"]["impose"] == {"palette": "red (#E1000F)", "negatif": "blur", "police": "Marianne",
                                         "couleur_texte": "#1a1a1a", "bandeau": "un fond plein #ffffff sous le texte"}
    assert par["creuse@1"]["impose"] == {}          # des faits vides, une police sans fichier : rien n'est imposé
    assert "impose" not in par["muette@1"]          # sans faits, rien n'est dit : le lanceur grise par la seule déclaration
    assert "impose" not in par["aucune"]
    # la forme nue : une colonne déjà en champs de la chaîne, relayée telle quelle
    fichier.write_text(json.dumps({"chartes": {"x": {"libelle": "X", "marque": "x", "impose": {"palette": "rouge"}}}}), encoding="utf-8")
    nue = {"source_fichier": {"chemin": str(fichier), "table": "chartes", "libelle": "libelle", "impose": "impose"}}
    assert menus.choix(["x"], nue)[0][0]["impose"] == {"palette": "rouge"}
