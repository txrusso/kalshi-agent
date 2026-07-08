from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding

from kalshi_agent.data.auth import KalshiSigner
from kalshi_agent.config import Settings


def _signer() -> KalshiSigner:
    s = Settings()
    return KalshiSigner(s.kalshi_api_key_id, s.private_key_pem)


def test_headers_contain_expected_keys():
    signer = _signer()
    headers = signer.headers("GET", "/trade-api/v2/markets")
    assert set(headers) == {"KALSHI-ACCESS-KEY", "KALSHI-ACCESS-TIMESTAMP", "KALSHI-ACCESS-SIGNATURE"}
    assert headers["KALSHI-ACCESS-KEY"] == signer.api_key_id
    assert headers["KALSHI-ACCESS-TIMESTAMP"].isdigit()


def test_signature_verifies_against_public_key():
    signer = _signer()
    method, path = "GET", "/trade-api/v2/portfolio/balance"
    headers = signer.headers(method, path)

    import base64

    message = f"{headers['KALSHI-ACCESS-TIMESTAMP']}{method}{path}".encode("utf-8")
    signature = base64.b64decode(headers["KALSHI-ACCESS-SIGNATURE"])
    public_key = signer._private_key.public_key()

    # Should not raise
    public_key.verify(
        signature,
        message,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=32),
        hashes.SHA256(),
    )


def test_tampered_message_fails_verification():
    signer = _signer()
    headers = signer.headers("GET", "/trade-api/v2/markets")

    import base64

    signature = base64.b64decode(headers["KALSHI-ACCESS-SIGNATURE"])
    public_key = signer._private_key.public_key()
    tampered = b"wrong-message"

    try:
        public_key.verify(
            signature,
            tampered,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=32),
            hashes.SHA256(),
        )
        assert False, "expected InvalidSignature"
    except InvalidSignature:
        pass
