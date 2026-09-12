"""Exercise the real ASK verifier when the optional webhook dependencies exist."""

import base64
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import unittest
from unittest.mock import patch

from amazon_echo_home_voice.webhook import verify_signature_and_timestamp


@unittest.skipUnless(importlib.util.find_spec("ask_sdk_webservice_support"), "Install .[webhook] for real signature tests")
class SignatureTests(unittest.TestCase):
    def setUp(self):
        from ask_sdk_webservice_support.verifier import RequestVerifier
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID

        self.verifier_class = RequestVerifier
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "echo-api.amazon.com")])
        self.certificate = (
            x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(self.key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(datetime.now(timezone.utc) - timedelta(minutes=1))
            .not_valid_after(datetime.now(timezone.utc) + timedelta(hours=1))
            .sign(self.key, hashes.SHA256())
        )

    def signed(self, when=None):
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding

        body = json.dumps({"version": "1.0", "request": {
            "type": "LaunchRequest", "requestId": "request-test",
            "locale": "en-US", "timestamp": (when or datetime.now(timezone.utc)).isoformat(),
        }})
        signature = self.key.sign(body.encode(), padding.PKCS1v15(), hashes.SHA256())
        return {"signaturecertchainurl": "https://s3.amazonaws.com/echo.api/test.pem", "signature-256": base64.b64encode(signature).decode()}, body

    def test_real_sha256_signature_accepts_original_and_rejects_changed_body(self):
        headers, body = self.signed()
        # Certificate retrieval/trust is the network boundary; body crypto stays real.
        with patch.object(self.verifier_class, "_retrieve_and_validate_certificate_chain", return_value=self.certificate):
            verify_signature_and_timestamp(headers, body)
            with self.assertRaises(Exception):
                verify_signature_and_timestamp(headers, body.replace("request-test", "tampered-request"))

    def test_real_timestamp_verifier_rejects_validly_signed_old_request(self):
        headers, body = self.signed(datetime.now(timezone.utc) - timedelta(minutes=5))
        with patch.object(self.verifier_class, "_retrieve_and_validate_certificate_chain", return_value=self.certificate):
            with self.assertRaises(Exception):
                verify_signature_and_timestamp(headers, body)

    def test_official_verifier_rejects_non_amazon_certificate_url_before_download(self):
        headers, body = self.signed()
        headers["signaturecertchainurl"] = "https://attacker.example/echo.api/cert.pem"
        with patch("amazon_echo_home_voice.webhook._download_certificate") as download:
            with self.assertRaises(Exception):
                verify_signature_and_timestamp(headers, body)
            download.assert_not_called()
