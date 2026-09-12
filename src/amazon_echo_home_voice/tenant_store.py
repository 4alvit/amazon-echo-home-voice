"""Encrypted, local, shared-process storage for household connections and login sessions."""

import argparse
import base64
from contextlib import closing, contextmanager
from dataclasses import asdict
import hashlib
import hmac
import getpass
import json
import os
from pathlib import Path
import secrets
import sqlite3
import stat
import time

from .gateway import GatewayConfig


class StoreError(Exception):
    """Storage is unavailable; messages never contain records or paths."""


class CapacityError(StoreError):
    """The temporary login flow capacity has been reached."""


_SCHEMA = """
CREATE TABLE metadata (id INTEGER PRIMARY KEY CHECK(id=1), marker BLOB NOT NULL);
CREATE TABLE connections (owner TEXT PRIMARY KEY, data BLOB NOT NULL);
CREATE TABLE flows (id TEXT PRIMARY KEY, browser TEXT NOT NULL,
                    data BLOB NOT NULL, expires INTEGER NOT NULL);
CREATE TABLE sessions (id TEXT PRIMARY KEY, owner TEXT NOT NULL,
                       data BLOB NOT NULL, expires INTEGER NOT NULL);
CREATE INDEX session_owner ON sessions(owner);
PRAGMA user_version = 1;
"""


class TenantStore:
    """One identity (issuer, subject) owns exactly one home; no caller supplies row IDs."""

    def __init__(self, path: str, key: str, *, clock=time.time, initialize=False):
        try:
            from cryptography.fernet import Fernet
            self._cipher = Fernet(key.encode("ascii"))
            self._hash_key = hmac.digest(base64.urlsafe_b64decode(key), b"home-energy-identity-v1", "sha256")
            self._path = Path(path)
            self._clock = clock
            if not self._path.is_absolute() or self._path.is_symlink():
                raise ValueError()
            if initialize:
                self._path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
            if self._path.parent.is_symlink() or stat.S_IMODE(self._path.parent.stat().st_mode) & 0o077:
                raise ValueError()
            if initialize:
                # Exclusive creation prevents an accidental reset of an existing installation.
                descriptor = os.open(self._path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                os.close(descriptor)
                with closing(sqlite3.connect(self._path)) as db, db:
                    db.executescript(_SCHEMA)
                    db.execute("INSERT INTO metadata VALUES (1, ?)", (self._encrypt({"version": 1}),))
            info = self._path.stat()
            if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) & 0o077:
                raise ValueError()
            with self._db() as db:
                if db.execute("PRAGMA user_version").fetchone()[0] != 1:
                    raise ValueError()
                marker = db.execute("SELECT marker FROM metadata WHERE id=1").fetchone()
                if not marker or self._decrypt(marker[0]) != {"version": 1}:
                    raise ValueError()
        except Exception:
            raise StoreError("Household storage is unavailable") from None

    @classmethod
    def from_env(cls, **kwargs):
        return cls(os.environ.get("TENANT_DB_PATH", ""), os.environ.get("TENANT_ENCRYPTION_KEY", ""), **kwargs)

    @contextmanager
    def _db(self):
        db = None
        try:
            # A runtime typo must never create a second, empty database.
            db = sqlite3.connect(self._path.as_uri() + "?mode=rw", uri=True, timeout=0.25)
            db.execute("PRAGMA secure_delete=ON")
            with db:
                yield db
        except (sqlite3.Error, OSError):
            raise StoreError("Household storage is unavailable") from None
        finally:
            if db is not None:
                db.close()

    def _digest(self, kind, *values):
        return hmac.new(self._hash_key, json.dumps([kind, *values], separators=(",", ":")).encode(), hashlib.sha256).hexdigest()

    def _owner(self, issuer, subject):
        if not isinstance(issuer, str) or not issuer or not isinstance(subject, str) or not subject or len(subject) > 512:
            raise StoreError("Invalid household identity")
        return self._digest("owner", issuer, subject)

    def _encrypt(self, value):
        return self._cipher.encrypt(json.dumps(value, separators=(",", ":")).encode())

    def _decrypt(self, value):
        try:
            result = json.loads(self._cipher.decrypt(value))
            if not isinstance(result, dict):
                raise ValueError()
            return result
        except Exception:
            raise StoreError("Household storage is unavailable") from None

    def connection(self, issuer, subject):
        owner = self._owner(issuer, subject)
        with self._db() as db:
            row = db.execute("SELECT data FROM connections WHERE owner=?", (owner,)).fetchone()
        if row is None:
            return None
        envelope = self._decrypt(row[0])
        if envelope.get("owner") != owner or not isinstance(envelope.get("config"), dict):
            raise StoreError("Household storage is unavailable")
        data = envelope["config"]
        # Defense in depth: a stored value cannot opt out of public egress checks.
        data["public_only"] = True
        try:
            return GatewayConfig(**data)
        except (TypeError, ValueError):
            raise StoreError("Household storage is unavailable") from None

    def save_connection(self, issuer, subject, config):
        if not config.public_only:
            raise StoreError("Public gateway validation is required")
        owner = self._owner(issuer, subject)
        with self._db() as db:
            db.execute("INSERT INTO connections VALUES (?, ?) ON CONFLICT(owner) DO UPDATE SET data=excluded.data",
                       (owner, self._encrypt({"owner": owner, "config": asdict(config)})))

    def save_connection_for_session(self, token, config):
        """Commit only while the same browser session still exists after the network check."""
        if not config.public_only:
            raise StoreError("Public gateway validation is required")
        key = self._digest("session", token)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT owner, data FROM sessions WHERE id=? AND expires>?", (key, self._clock())).fetchone()
            if row is None:
                raise StoreError("Login expired")
            session = self._decrypt(row[1])
            owner = self._owner(session.get("issuer"), session.get("subject"))
            if session.get("id") != key or row[0] != owner or session.get("expires", 0) <= self._clock():
                raise StoreError("Login expired")
            db.execute("INSERT INTO connections VALUES (?, ?) ON CONFLICT(owner) DO UPDATE SET data=excluded.data",
                       (owner, self._encrypt({"owner": owner, "config": asdict(config)})))

    def disconnect(self, issuer, subject):
        with self._db() as db:
            db.execute("DELETE FROM connections WHERE owner=?", (self._owner(issuer, subject),))

    def delete_household(self, issuer, subject):
        """Operator erasure after provider revocation; also remove every portal session."""
        owner = self._owner(issuer, subject)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM connections WHERE owner=?", (owner,))
            db.execute("DELETE FROM sessions WHERE owner=?", (owner,))

    def create_flow(self, verifier, browser_token):
        now = int(self._clock())
        state = secrets.token_urlsafe(32)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM flows WHERE expires<=?", (now,))
            db.execute("DELETE FROM sessions WHERE expires<=?", (now,))
            if db.execute("SELECT COUNT(*) FROM flows").fetchone()[0] >= 1000:
                raise CapacityError("Please try signing in again later")
            db.execute("INSERT INTO flows VALUES (?, ?, ?, ?)",
                       (self._digest("flow", state), self._digest("browser", browser_token),
                        self._encrypt({"id": self._digest("flow", state), "verifier": verifier}), now + 300))
        return state

    def consume_flow(self, state, browser_token):
        if not isinstance(state, str) or not isinstance(browser_token, str) or not state or not browser_token:
            return None
        key = self._digest("flow", state)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT browser, data, expires FROM flows WHERE id=?", (key,)).fetchone()
            if not row or row[2] <= self._clock() or not hmac.compare_digest(row[0], self._digest("browser", browser_token)):
                return None
            db.execute("DELETE FROM flows WHERE id=?", (key,))
        data = self._decrypt(row[1])
        if data.get("id") != key:
            raise StoreError("Household storage is unavailable")
        return data["verifier"]

    def create_session(self, issuer, subject, access_token, token_expires):
        owner = self._owner(issuer, subject)
        now = int(self._clock())
        expires = min(now + 600, int(token_expires))
        if expires <= now:
            raise StoreError("Login expired")
        token = secrets.token_urlsafe(32)
        data = {"id": self._digest("session", token), "issuer": issuer, "subject": subject, "access_token": access_token,
                "csrf": secrets.token_urlsafe(32), "expires": expires}
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM sessions WHERE expires<=?", (now,))
            # A bounded number of browsers per account, without logging their identities.
            db.execute("DELETE FROM sessions WHERE owner=? AND id NOT IN "
                       "(SELECT id FROM sessions WHERE owner=? ORDER BY expires DESC LIMIT 4)", (owner, owner))
            db.execute("INSERT INTO sessions VALUES (?, ?, ?, ?)",
                       (self._digest("session", token), owner, self._encrypt(data), expires))
        return token

    def session(self, token):
        if not isinstance(token, str) or not token or len(token) > 128:
            return None
        with self._db() as db:
            row = db.execute("SELECT data FROM sessions WHERE id=? AND expires>?",
                             (self._digest("session", token), self._clock())).fetchone()
        if not row:
            return None
        data = self._decrypt(row[0])
        if data.get("id") != self._digest("session", token) or data.get("expires", 0) <= self._clock():
            raise StoreError("Household storage is unavailable")
        return data

    def delete_session(self, token):
        with self._db() as db:
            db.execute("DELETE FROM sessions WHERE id=?", (self._digest("session", token),))


def main():
    parser = argparse.ArgumentParser(description="Manage private household storage. Keep the encryption key separately backed up.")
    parser.add_argument("command", choices=["init", "delete-household"])
    arguments = parser.parse_args()
    try:
        if arguments.command == "init":
            TenantStore.from_env(initialize=True)
        else:
            issuer = os.environ.get("OAUTH_ISSUER", "")
            if not issuer:
                raise StoreError("Issuer is required")
            # Private input avoids leaving a subject identifier in process arguments or history.
            subject = getpass.getpass("Identity-provider subject to erase (revoke its provider sessions first): ")
            TenantStore.from_env().delete_household(issuer, subject)
    except StoreError:
        parser.exit(1, "Unable to manage private storage. Check the database path, private permissions, encryption key, and account identity.\n")
    except (EOFError, KeyboardInterrupt):
        parser.exit(1, "Operation cancelled.\n")
    print("Household storage initialized." if arguments.command == "init" else "Household connection and portal sessions erased.")


if __name__ == "__main__":
    main()
