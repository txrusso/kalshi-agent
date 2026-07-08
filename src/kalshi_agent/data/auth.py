import base64
import time

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

API_PREFIX = "/trade-api/v2"


class KalshiSigner:
    """Signs Kalshi REST requests per the RSA-PSS scheme in build spec §3.1:
    sign `timestamp_ms + METHOD + path` (path includes /trade-api/v2, excludes
    query string) with SHA-256 / MGF1-SHA256 / 32-byte salt, base64-encoded.
    """

    def __init__(self, api_key_id: str, private_key_pem: bytes) -> None:
        self.api_key_id = api_key_id
        key = serialization.load_pem_private_key(private_key_pem, password=None)
        if not isinstance(key, rsa.RSAPrivateKey):
            raise TypeError("Kalshi private key must be an RSA key")
        self._private_key: rsa.RSAPrivateKey = key

    def headers(self, method: str, path: str) -> dict[str, str]:
        """`path` must be the full request path including the /trade-api/v2
        prefix and must NOT include the query string."""
        timestamp_ms = str(int(time.time() * 1000))
        message = f"{timestamp_ms}{method.upper()}{path}".encode("utf-8")
        signature = self._private_key.sign(
            message,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=32),
            hashes.SHA256(),
        )
        return {
            "KALSHI-ACCESS-KEY": self.api_key_id,
            "KALSHI-ACCESS-TIMESTAMP": timestamp_ms,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode("utf-8"),
        }
