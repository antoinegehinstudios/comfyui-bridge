"""Ce qui est livré est-il ce qui a été demandé ?"""

from comfyui_bridge.core.delivery import compare, describe


def test_a_workflow_that_recomputes_the_size_is_reported():
    """Mesuré : 352x224 pour 1 s demandés ont produit 320x192 pour 0,75 s. Le
    livrable était juste pour ce workflow, l'appelant n'en savait rien."""
    gaps = compare({"width": 352, "height": 224, "duration_s": 1.0, "fps": 12},
                   {"width": 320, "height": 192, "duration_s": 0.75})
    assert [g["field"] for g in gaps] == ["width", "height", "duration_s"]
    assert gaps[0] == {"field": "width", "asked": 352, "delivered": 320}
    assert "demandé 352, livré 320" in describe(gaps)


def test_a_container_rounding_is_not_an_error():
    """Une image de plus ou de moins n'est pas un écart : 48 images à 24 i/s
    tiennent en 2,042 s dans un conteneur MP4."""
    assert compare({"duration_s": 2.0, "fps": 24}, {"duration_s": 2.042}) == []
    # …mais un quart de la durée en moins, si.
    assert compare({"duration_s": 2.0, "fps": 24}, {"duration_s": 1.5}) != []


def test_nothing_measured_means_nothing_claimed():
    """Un format qu'on ne sait pas lire ne produit aucun écart : une mesure
    absente ne doit pas se transformer en accusation."""
    assert compare({"width": 512, "height": 512}, {}) == []
    assert compare({}, {"width": 320}) == []
    # Un champ non demandé n'est pas comparé.
    assert compare({"width": 320}, {"width": 320, "duration_s": 9.0}) == []


def test_the_frame_count_is_compared_when_both_are_known():
    assert compare({"latent_batch": 16}, {"frames": 8}) == [
        {"field": "frames", "asked": 16, "delivered": 8}]
    assert compare({"latent_batch": 16}, {"frames": 16}) == []


def test_the_reader_only_claims_what_it_can_read(tmp_path):
    """WEBM, MKV, OGG ne sont pas lus ici : rien n'est annoncé plutôt qu'un
    chiffre inventé."""
    from comfyui_bridge.adapter.measure import measure

    for nom in ("clip.webm", "clip.mkv", "son.ogg", "inconnu.xyz"):
        fichier = tmp_path / nom
        fichier.write_bytes(b"\x00" * 64)
        assert measure(fichier) == {}
    # Un fichier annoncé lisible mais corrompu ne fait pas tomber la mesure.
    casse = tmp_path / "casse.mp4"
    casse.write_bytes(b"not an mp4 at all")
    assert measure(casse) == {}


def test_a_real_png_is_measured(tmp_path):
    from PIL import Image

    from comfyui_bridge.adapter.measure import measure
    chemin = tmp_path / "img.png"
    Image.new("RGB", (321, 123), (255, 255, 255)).save(chemin)
    assert measure(chemin) == {"width": 321, "height": 123}
