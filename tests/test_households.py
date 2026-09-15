"""Cross-household and storage tests use synthetic identities and credentials only."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
import os
from pathlib import Path
import sqlite3
import tempfile
from threading import Barrier
import time
import unittest
from unittest.mock import patch

from cryptography.fernet import Fernet

from amazon_echo_home_voice import accounts, lambda_handler
from amazon_echo_home_voice.gateway import GatewayConfig, UNAVAILABLE_TEXT
from amazon_echo_home_voice.oauth import Identity, InvalidToken, OAuthUnavailable
from amazon_echo_home_voice.tenant_store import TenantStore, StoreError
from test_skill import event, payload, SKILL_ID


ISSUER = "https://identity.example.com/realms/energy"


class StoreFixture(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = str(Path(self.directory.name) / "households.sqlite3")
        self.key = Fernet.generate_key().decode()
        self.now = 1000
        self.store = TenantStore(self.path, self.key, initialize=True, clock=lambda: self.now)
        self.first = GatewayConfig("https://first.example.com/v1/energy", "synthetic-first-token", public_only=True)
        self.second = GatewayConfig("https://second.example.com/v1/energy", "synthetic-second-token", public_only=True)


class StoreTests(StoreFixture):
    def test_identity_and_issuer_isolation_persist_across_process_instances(self):
        self.store.save_connection(ISSUER, "first-subject", self.first)
        self.store.save_connection(ISSUER, "second-subject", self.second)
        other = TenantStore(self.path, self.key)
        self.assertEqual(other.connection(ISSUER, "first-subject"), self.first)
        self.assertEqual(other.connection(ISSUER, "second-subject"), self.second)
        self.assertIsNone(other.connection(ISSUER + "-other", "first-subject"))
        self.assertIsNone(other.connection(ISSUER, "unknown-subject"))
        self.store.disconnect(ISSUER, "first-subject")
        self.assertIsNone(other.connection(ISSUER, "first-subject"))
        self.assertEqual(other.connection(ISSUER, "second-subject"), self.second)

    def test_disk_never_contains_plaintext_credentials_identities_or_session_tokens(self):
        self.store.save_connection(ISSUER, "synthetic-private-subject", self.first)
        session = self.store.create_session(ISSUER, "synthetic-private-subject", "synthetic-oauth-access-token", 1600)
        flow = self.store.create_flow("synthetic-verifier", "synthetic-browser")
        data = Path(self.path).read_bytes()
        for secret in (ISSUER, "synthetic-private-subject", self.first.url, self.first.read_token,
                       "synthetic-oauth-access-token", session, flow, "synthetic-verifier", "synthetic-browser"):
            self.assertNotIn(secret.encode(), data)
        self.assertEqual(Path(self.path).stat().st_mode & 0o777, 0o600)

    def test_wrong_key_missing_database_permissions_and_reset_fail_closed(self):
        for path, key in ((self.path, Fernet.generate_key().decode()), (self.path + "-missing", self.key), (self.path, "bad-key")):
            with self.assertRaises(StoreError):
                TenantStore(path, key)
        with self.assertRaises(StoreError):
            TenantStore(self.path, self.key, initialize=True)
        Path(self.path).chmod(0o644)
        with self.assertRaises(StoreError):
            TenantStore(self.path, self.key)
        Path(self.path).chmod(0o600)
        self.assertEqual(TenantStore(self.path, self.key).connection(ISSUER, "unknown"), None)

    def test_ciphertexts_are_bound_to_owner_and_session_row(self):
        self.store.save_connection(ISSUER, "one", self.first)
        self.store.save_connection(ISSUER, "two", self.second)
        one = self.store.create_session(ISSUER, "one", "oauth-one", 1600)
        two = self.store.create_session(ISSUER, "two", "oauth-two", 1600)
        with closing(sqlite3.connect(self.path)) as db, db:
            connections = db.execute("SELECT owner, data FROM connections").fetchall()
            db.execute("UPDATE connections SET data=? WHERE owner=?", (connections[0][1], connections[1][0]))
            sessions = db.execute("SELECT id, data FROM sessions").fetchall()
            db.execute("UPDATE sessions SET data=? WHERE id=?", (sessions[0][1], sessions[1][0]))
        with self.assertRaises(StoreError):
            self.store.connection(ISSUER, "two")
        with self.assertRaises(StoreError):
            self.store.session(two)
        self.assertEqual(self.store.session(one)["subject"], "one")

    def test_login_state_is_browser_bound_expires_and_is_single_use_under_race(self):
        state = self.store.create_flow("verifier", "browser")
        self.assertIsNone(self.store.consume_flow(state, "another-browser"))
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.store.consume_flow(state, "browser"), range(2)))
        self.assertCountEqual(results, ["verifier", None])
        state = self.store.create_flow("verifier", "browser")
        self.now += 301
        self.assertIsNone(self.store.consume_flow(state, "browser"))

    def test_sessions_expire_revoke_and_are_bounded_per_account(self):
        tokens = [self.store.create_session(ISSUER, "one", "oauth", 2000) for _ in range(7)]
        with closing(sqlite3.connect(self.path)) as db, db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], 5)
        self.store.delete_session(tokens[-1])
        self.assertIsNone(self.store.session(tokens[-1]))
        self.now = 1601
        self.assertTrue(all(self.store.session(token) is None for token in tokens))

    def test_concurrent_household_updates_do_not_cross_connections(self):
        prior = [replace(self.first, read_token=f"prior-{index}") for index in range(16)]
        updated = [replace(self.first, read_token=f"updated-{index}") for index in range(16)]
        for index, config in enumerate(prior):
            self.store.save_connection(ISSUER, str(index), config)
        stores = [TenantStore(self.path, self.key) for _ in prior]
        start = Barrier(4)

        def save(index):
            start.wait(timeout=10)
            try:
                stores[index].save_connection(ISSUER, str(index), updated[index])
            except StoreError as error:
                return error
            return None

        with ThreadPoolExecutor(max_workers=4) as pool:
            outcomes = list(pool.map(save, range(16)))
        self.assertIn(None, outcomes, "Concurrent writes must make progress")
        # Short lock waits deliberately allow a busy response. Read after all
        # writers finish so the isolation check does not race another commit.
        for index, error in enumerate(outcomes):
            if error is not None:
                self.assert_sqlite_busy(error)
            expected = updated[index] if error is None else prior[index]
            self.assertEqual(self.store.connection(ISSUER, str(index)), expected)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM connections").fetchone()[0], 16)
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        with self.assertRaises(StoreError):
            self.store.save_connection(ISSUER, "one", replace(self.first, public_only=False))

    def assert_sqlite_busy(self, error):
        self.assertEqual(str(error), "Household storage is unavailable")
        self.assertIsInstance(error.__context__, sqlite3.OperationalError)
        self.assertIn(error.__context__.sqlite_errorcode & 0xFF,
                      (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED))

    def test_busy_write_and_commit_fail_closed_without_changing_connections(self):
        self.store.save_connection(ISSUER, "one", self.first)
        self.store.save_connection(ISSUER, "two", self.second)
        updated = replace(self.first, read_token="synthetic-replacement-token")
        for statement in ("BEGIN IMMEDIATE", "BEGIN"):
            with self.subTest(lock=statement):
                with closing(sqlite3.connect(self.path)) as blocker:
                    before = blocker.execute("SELECT owner, data FROM connections ORDER BY owner").fetchall()
                    blocker.execute(statement)
                    # A reserved writer lock rejects INSERT; a shared reader
                    # lock permits INSERT but rejects COMMIT. Both must roll back.
                    blocker.execute("SELECT data FROM connections").fetchall()
                    try:
                        started = time.monotonic()
                        with patch("amazon_echo_home_voice.tenant_store.sqlite3.connect", wraps=sqlite3.connect) as connect:
                            with self.assertRaises(StoreError) as caught:
                                self.store.save_connection(ISSUER, "one", updated)
                        self.assertLess(time.monotonic() - started, 5)
                        self.assertEqual(connect.call_args.kwargs["timeout"], 0.25)
                        self.assert_sqlite_busy(caught.exception)
                    finally:
                        blocker.rollback()
                    self.assertEqual(blocker.execute("SELECT owner, data FROM connections ORDER BY owner").fetchall(), before)
                    self.assertEqual(blocker.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(self.store.connection(ISSUER, "one"), self.first)
                self.assertEqual(self.store.connection(ISSUER, "two"), self.second)
        # A released lock restores normal writes without a retry in the runtime.
        self.store.save_connection(ISSUER, "one", updated)
        self.assertEqual(self.store.connection(ISSUER, "one"), updated)
        self.assertEqual(self.store.connection(ISSUER, "two"), self.second)

    def test_operator_erasure_removes_only_target_household_and_all_its_sessions(self):
        for subject, config in (("one", self.first), ("two", self.second)):
            self.store.save_connection(ISSUER, subject, config)
        one = [self.store.create_session(ISSUER, "one", "first-access-token", 1600) for _ in range(3)]
        two = self.store.create_session(ISSUER, "two", "second-access-token", 1600)
        self.store.delete_household(ISSUER, "one")
        self.store.delete_household(ISSUER, "one")
        self.assertIsNone(self.store.connection(ISSUER, "one"))
        self.assertTrue(all(self.store.session(token) is None for token in one))
        self.assertEqual(self.store.connection(ISSUER, "two"), self.second)
        self.assertEqual(self.store.session(two)["subject"], "two")


class HouseholdSkillTests(StoreFixture):
    def setUp(self):
        super().setUp()
        self.environment = patch.dict(os.environ, {
            "ASK_SKILL_ID": SKILL_ID, "ENERGY_VOICE_MODE": "multi_household",
            "IGW_URL": "https://operator.example.com/v1/energy", "IGW_READ_TOKEN": "operator-secret",
            "TENANT_DB_PATH": self.path, "TENANT_ENCRYPTION_KEY": self.key,
        }, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.store.save_connection(ISSUER, "one", self.first)
        self.store.save_connection(ISSUER, "two", self.second)
        self.oauth_config = patch.object(accounts.OAuthConfig, "from_env")
        self.oauth_config.start()
        self.addCleanup(self.oauth_config.stop)

    def linked(self, token="token-one", kind="IntentRequest"):
        value = event(kind=kind)
        value["context"]["System"]["user"] = {"accessToken": token, "userId": "same-amazon-id"}
        return value

    def test_two_linked_homes_only_fetch_their_own_gateway_for_every_report(self):
        def identity(token):
            return Identity("one" if token == "token-one" else "two", ISSUER, 9999999999)
        with patch.object(accounts.OAuthClient, "introspect_alexa", side_effect=identity), \
             patch.object(lambda_handler, "fetch_energy", return_value=payload()) as fetch:
            for token, config in (("token-one", self.first), ("token-two", self.second)):
                for intent in lambda_handler.INTENTS:
                    value = self.linked(token)
                    value["request"]["intent"]["name"] = intent
                    result = lambda_handler.lambda_handler(value, None)
                    self.assertEqual(result["response"]["card"]["type"], "Simple")
                    self.assertEqual(fetch.call_args.args[0], config)
                    self.assertNotIn("operator-secret", str(fetch.call_args))

    def test_screen_reports_do_not_reuse_another_households_snapshot(self):
        def identity(token):
            return Identity("one" if token == "token-one" else "two", ISSUER, 9999999999)

        def reports(config):
            data = payload()
            household = "first" if config == self.first else "second"
            for report in data["reports"].values():
                report["text"] = f"Synthetic {household} household report."
            return data

        with patch.object(accounts.OAuthClient, "introspect_alexa", side_effect=identity), \
             patch.object(lambda_handler, "fetch_energy", side_effect=reports) as fetch:
            for token, config, expected, other in (
                ("token-one", self.first, "first", "second"),
                ("token-two", self.second, "second", "first"),
                ("token-one", self.first, "first", "second"),
            ):
                value = self.linked(token)
                value["context"]["System"]["device"] = {
                    "supportedInterfaces": {"Alexa.Presentation.APL": {}},
                }
                result = lambda_handler.lambda_handler(value, None)
                text = result["response"]["directives"][0]["datasources"]["energy"]["reports"][0]["text"]
                self.assertEqual(text, f"Synthetic {expected} household report.")
                self.assertNotIn(f"Synthetic {other} household report.", str(result))
                self.assertNotIn("operator-secret", str(result))
                self.assertEqual(fetch.call_args.args[0], config)

    def test_missing_invalid_expired_revoked_tokens_never_use_operator_gateway(self):
        with patch.object(accounts.OAuthClient, "introspect_alexa", side_effect=InvalidToken("invalid")), \
             patch.object(lambda_handler, "fetch_energy") as fetch:
            for value in (event(), self.linked(None), self.linked("expired"), self.linked("revoked"), self.linked(kind="LaunchRequest")):
                result = lambda_handler.lambda_handler(value, None)
                self.assertEqual(result["response"]["card"], {"type": "LinkAccount"})
            value = event()
            value["context"]["System"]["apiAccessToken"] = "not-an-account-token"
            self.assertEqual(lambda_handler.lambda_handler(value, None)["response"]["card"]["type"], "LinkAccount")
            fetch.assert_not_called()

    def test_disconnect_and_unknown_household_require_setup_not_fallback(self):
        self.store.disconnect(ISSUER, "one")
        with patch.object(accounts.OAuthClient, "introspect_alexa", return_value=Identity("one", ISSUER, 9999999999)), \
             patch.object(lambda_handler, "fetch_energy") as fetch:
            result = lambda_handler.lambda_handler(self.linked(), None)
            self.assertIn("connect your gateway", result["response"]["outputSpeech"]["text"])
            self.assertEqual(result["response"]["card"]["type"], "Simple")
            fetch.assert_not_called()

    def test_provider_outage_and_storage_failure_are_safe_unavailability(self):
        with patch.object(accounts.OAuthClient, "introspect_alexa", side_effect=OAuthUnavailable("private-details")), \
             patch.object(lambda_handler, "fetch_energy") as fetch:
            result = lambda_handler.lambda_handler(self.linked(), None)
            self.assertEqual(result["response"]["outputSpeech"]["text"], UNAVAILABLE_TEXT)
            self.assertEqual(result["response"]["card"]["type"], "Simple")
            fetch.assert_not_called()
        with patch.object(accounts.OAuthClient, "introspect_alexa", return_value=Identity("one", ISSUER, 9999999999)), \
             patch.dict(os.environ, {"TENANT_ENCRYPTION_KEY": "wrong"}), patch.object(lambda_handler, "fetch_energy") as fetch:
            self.assertEqual(lambda_handler.lambda_handler(self.linked(), None)["response"]["outputSpeech"]["text"], UNAVAILABLE_TEXT)
            fetch.assert_not_called()

    def test_unknown_mode_and_invalid_alexa_envelope_fail_before_identity_or_gateway(self):
        with patch.object(accounts.OAuthClient, "introspect_alexa") as auth, patch.object(lambda_handler, "fetch_energy") as fetch:
            with patch.dict(os.environ, {"ENERGY_VOICE_MODE": "typo"}):
                self.assertEqual(lambda_handler.lambda_handler(self.linked(), None)["response"]["outputSpeech"]["text"], UNAVAILABLE_TEXT)
            value = self.linked()
            value["context"]["System"]["application"]["applicationId"] = "wrong"
            with self.assertRaises(PermissionError):
                lambda_handler.lambda_handler(value, None)
            auth.assert_not_called()
            fetch.assert_not_called()
