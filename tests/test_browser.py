from comfyui_bridge.browser.headless import is_available


def test_is_available_contract():
    """Reusable browser capability reports its state honestly, whatever the env."""
    ok, reason = is_available()
    assert isinstance(ok, bool)
    assert isinstance(reason, str) and reason  # non-empty explanation either way
