"""Check security boundaries of the operator's Keycloak import generator."""

import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


_PATH = Path(__file__).resolve().parents[1] / "deploy/multi-household/generate_realm.py"
_SPEC = importlib.util.spec_from_file_location("generate_realm", _PATH)
realm_generator = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(realm_generator)


class RealmGenerationTests(unittest.TestCase):
    def setUp(self):
        self.config = {
            "OAUTH_ISSUER": "https://auth.example.com/realms/home-energy",
            "OAUTH_ALEXA_CLIENT_ID": "home-energy-alexa",
            "OAUTH_ALEXA_CLIENT_SECRET": "a" * 64,
            "OAUTH_PORTAL_CLIENT_ID": "home-energy-portal",
            "OAUTH_PORTAL_CLIENT_SECRET": "b" * 64,
            "OAUTH_PORTAL_REDIRECT_URI": "https://connect.example.com/callback",
        }
        self.redirects = ["https://alexa.example.com/exact-skill-callback"]

    def test_scopes_and_audiences_do_not_cross_clients(self):
        realm = realm_generator.build_realm(self.config, self.redirects)
        alexa, portal = realm["clients"]
        self.assertEqual(alexa["optionalClientScopes"], ["energy:read"])
        self.assertEqual(portal["optionalClientScopes"], [])
        self.assertEqual(alexa["redirectUris"], self.redirects)
        self.assertEqual(portal["redirectUris"], [self.config["OAUTH_PORTAL_REDIRECT_URI"]])
        for client in (alexa, portal):
            self.assertFalse(client["publicClient"])
            self.assertFalse(client["implicitFlowEnabled"])
            self.assertFalse(client["directAccessGrantsEnabled"])
            self.assertFalse(client["serviceAccountsEnabled"])
            self.assertEqual(client["attributes"]["pkce.code.challenge.method"], "S256")
            mapper = client["protocolMappers"][0]["config"]
            self.assertEqual(mapper["included.client.audience"], client["clientId"])
            self.assertEqual(mapper["introspection.token.claim"], "true")
            subject_mapper = client["protocolMappers"][1]
            self.assertEqual(subject_mapper["protocolMapper"], "oidc-sub-mapper")
            self.assertEqual(subject_mapper["config"]["access.token.claim"], "true")
            self.assertEqual(subject_mapper["config"]["introspection.token.claim"], "true")
        self.assertFalse(realm["registrationAllowed"])
        self.assertTrue(realm["revokeRefreshToken"])
        self.assertEqual(realm["refreshTokenMaxReuse"], 0)

    def test_redirect_allowlist_rejects_unsafe_or_duplicate_entries(self):
        invalid = [
            [], self.redirects * 2, ["http://alexa.example.com/callback"],
            ["https://alexa.example.com/*"], ["https://user:secret@alexa.example.com/callback"],
            ["https://alexa.example.com:8443/callback"], ["https://alexa.example.com/callback#fragment"],
            ["https://alexa.example.com/callback\n"],
        ]
        for redirects in invalid:
            with self.subTest(redirects=redirects), self.assertRaises(ValueError):
                realm_generator.build_realm(self.config, redirects)

    def test_rejects_configuration_drift_or_reused_credentials(self):
        invalid = [
            {"OAUTH_TOKEN_URL": "https://other.example.com/token"},
            {"OAUTH_ISSUER": "https://auth.example.com/realms/wrong-realm"},
            {"OAUTH_PORTAL_CLIENT_ID": "home-energy-alexa"},
            {"OAUTH_PORTAL_CLIENT_SECRET": "a" * 64},
            {"OAUTH_ALEXA_CLIENT_SECRET": "replace-with-a-random-alexa-client-secret"},
            {"OAUTH_PORTAL_REDIRECT_URI": "https://connect.example.com/wrong-path"},
        ]
        for override in invalid:
            with self.subTest(keys=list(override)), self.assertRaises(ValueError):
                realm_generator.build_realm(self.config | override, self.redirects)

    def test_output_is_private_and_cannot_overwrite_or_follow_symlink(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            directory.chmod(0o700)
            output = directory / "realm.json"
            realm = realm_generator.build_realm(self.config, self.redirects)
            realm_generator.write_private(output, realm)
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(output.read_text()), realm)
            with self.assertRaises(FileExistsError):
                realm_generator.write_private(output, realm)
            alias = directory / "alias.json"
            alias.symlink_to(output)
            with self.assertRaises(FileExistsError):
                realm_generator.write_private(alias, realm)
            directory.chmod(0o755)
            with self.assertRaises(ValueError):
                realm_generator.write_private(directory / "public.json", realm)

    def test_environment_file_is_literal_and_requires_private_permissions(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "private.env"
            path.write_text("OAUTH_ALEXA_CLIENT_SECRET=$(do-not-run)\n")
            path.chmod(0o600)
            with self.assertRaises(ValueError):
                realm_generator.read_env(path)
            path.write_text("OAUTH_ALEXA_CLIENT_SECRET=literal-value\n")
            path.chmod(0o644)
            with self.assertRaises(ValueError):
                realm_generator.read_env(path)

    def test_bootstrap_generates_distinct_private_secrets_without_printing_them(self):
        source = _PATH.parent
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for filename in ("prepare_config.py", ".env.example"):
                shutil.copyfile(source / filename, directory / filename)
            command = [sys.executable, str(directory / "prepare_config.py")]
            completed = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(completed.returncode, 0)
            environment = directory / ".env"
            original = environment.read_text()
            values = realm_generator.read_env(environment)
            secrets = [values[key] for key in (
                "POSTGRES_PASSWORD", "KC_BOOTSTRAP_ADMIN_PASSWORD",
                "OAUTH_ALEXA_CLIENT_SECRET", "OAUTH_PORTAL_CLIENT_SECRET", "TENANT_ENCRYPTION_KEY",
            )]
            self.assertEqual(len(secrets), len(set(secrets)))
            for secret in secrets:
                self.assertNotIn(secret, completed.stdout + completed.stderr)
            self.assertEqual(environment.stat().st_mode & 0o777, 0o600)
            self.assertEqual((directory / "private").stat().st_mode & 0o777, 0o700)
            repeated = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(repeated.returncode, 0)
            self.assertEqual(environment.read_text(), original)


if __name__ == "__main__":
    unittest.main()
