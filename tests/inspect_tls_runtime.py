"""Report native fixture support without changing the mandatory TLS assertions."""
import http.client
import json
from pathlib import Path
import shutil
import ssl
import subprocess  # nosec B404: fixed OpenSSL diagnostic commands, no shell
import tempfile

from cryptography import x509
from cryptography.hazmat.backends import default_backend
from native_tls_support import native_certificate_results
from test_tls_policy import calibrate, make_chains


def inspect_runtime():
    executable = shutil.which("openssl")

    def command(*arguments, data=None):
        if executable is None:
            return {"available": False, "reason": "OpenSSL CLI is absent from this image"}
        # Test-runner OpenSSL plus fixed argv and ephemeral public certificates.
        result = subprocess.run(  # nosec B603
            [executable, *arguments], input=data, capture_output=True, timeout=15
        )
        return {
            "returncode": result.returncode,
            "stdout": result.stdout.decode(errors="replace"),
            "stderr": result.stderr.decode(errors="replace"),
        }

    result = {
        "python_openssl": ssl.OPENSSL_VERSION,
        "cryptography_openssl": default_backend().openssl_version_text(),
        "cli_version": command("version", "-a"),
        "native_curves": command("ecparam", "-list_curves"),
        "python_curve_support": {},
        "chains": {},
    }
    for curve in ("prime192v1", "secp224r1", "prime256v1"):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        try:
            context.set_ecdh_curve(curve)
            result["python_curve_support"][curve] = {"accepted": True}
        except (ValueError, ssl.SSLError) as error:
            result["python_curve_support"][curve] = {
                "accepted": False, "type": type(error).__name__, "error": str(error)
            }
    with tempfile.TemporaryDirectory(prefix="native-fixture-diagnostic-") as directory:
        chains = make_chains(Path(directory))
        if executable is None:
            result["native_certificate_decode"] = native_certificate_results(chains)
        for name in ("strong", "strong-ec", "weak-ec-root"):
            certificate, _, ca = chains[name]
            root = x509.load_pem_x509_certificate(ca.read_bytes())
            leaf = x509.load_pem_x509_certificate(certificate.read_bytes())
            # This verifies the actual generated issuer name and signature using
            # cryptography's independent provider, including the root self-signature.
            leaf.verify_directly_issued_by(root)
            root.verify_directly_issued_by(root)
            handshakes = {}
            for version in (ssl.TLSVersion.TLSv1_2, ssl.TLSVersion.TLSv1_3):
                try:
                    calibrate(chains[name], version)
                    handshakes[version.name] = {"verified_request": True}
                except (OSError, AssertionError, http.client.HTTPException) as error:
                    handshakes[version.name] = {
                        "verified_request": False, "type": type(error).__name__, "error": str(error)
                    }
            result["chains"][name] = {
                "python_verified_handshakes": handshakes,
                "independent_signatures_valid": True,
                "root_key_bits": root.public_key().key_size,
                "native_verify": command(
                    "verify", "-auth_level", "0", "-purpose", "sslserver",
                    "-verify_hostname", "localhost", "-CAfile", str(ca), str(certificate),
                ),
                "native_public_key": command("x509", "-in", str(ca), "-text", "-noout"),
            }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    inspect_runtime()
