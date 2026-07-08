from kalshi_agent.config import Settings


def test_settings_load_from_env():
    s = Settings()
    assert s.kalshi_api_key_id
    assert s.kalshi_private_key_path.exists()
    assert s.mode == "PAPER"


def test_private_key_is_valid_pem():
    s = Settings()
    pem = s.private_key_pem
    assert pem.startswith(b"-----BEGIN")
    assert b"PRIVATE KEY-----" in pem
