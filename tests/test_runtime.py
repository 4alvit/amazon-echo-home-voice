"""Boot the actual WSGI processes against initialized private storage when installed."""

from dataclasses import asdict
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

from cryptography.fernet import Fernet

from amazon_echo_home_voice.tenant_store import TenantStore
from test_oauth import config


@unittest.skipUnless(importlib.util.find_spec("gunicorn") and importlib.util.find_spec("ask_sdk_webservice_support"), "Install .[webhook] for real WSGI process tests")
class RuntimeTests(unittest.TestCase):
    def test_real_processes_load_private_database_and_keep_http_boundaries(self):
        with tempfile.TemporaryDirectory() as directory:
            key = Fernet.generate_key().decode()
            path = str(Path(directory) / "households.sqlite3")
            TenantStore(path, key, initialize=True)
            environment = os.environ.copy()
            environment.update({"ENERGY_VOICE_MODE": "multi_household", "ASK_SKILL_ID": "amzn1.ask.skill.synthetic",
                                "TENANT_DB_PATH": path, "TENANT_ENCRYPTION_KEY": key})
            environment.update({"OAUTH_" + name.upper(): str(value) for name, value in asdict(config()).items() if name != "timeout_seconds"})
            for app in ("webhook", "portal"):
                with self.subTest(app=app):
                    with socket.socket() as bound:
                        bound.bind(("127.0.0.1", 0))
                        port = bound.getsockname()[1]
                    process = subprocess.Popen([
                        sys.executable, "-m", "gunicorn", "--config", "python:amazon_echo_home_voice.gunicorn_config",
                        "--bind", f"127.0.0.1:{port}", "--workers", "1", "--threads", "2", "--timeout", "10",
                        f"amazon_echo_home_voice.{app}:application",
                    ], env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    opener = build_opener(ProxyHandler({}))
                    def request(path, data=None):
                        return Request(f"http://127.0.0.1:{port}" + path, data=data, headers={"Host": "portal.example", "Content-Type": "application/json"})
                    try:
                        for _ in range(100):
                            if process.poll() is not None:
                                self.fail("The WSGI process exited during startup")
                            try:
                                with opener.open(request("/health" if app == "webhook" else "/"), timeout=1) as response:
                                    data = response.read()
                                break
                            except URLError:
                                time.sleep(0.1)
                        else:
                            self.fail("The WSGI process did not become ready")
                        if app == "webhook":
                            self.assertTrue(json.loads(data)["skill_configured"])
                            with self.assertRaises(HTTPError) as rejected:
                                opener.open(request("/alexa", b"{}"), timeout=2)
                            self.assertEqual(rejected.exception.code, 400)
                            rejected.exception.close()
                            blocked_path = "/login"
                        else:
                            self.assertIn(b"Sign in", data)
                            self.assertNotIn(key.encode(), data)
                            blocked_path = "/alexa"
                        with self.assertRaises(HTTPError) as rejected:
                            opener.open(request(blocked_path), timeout=2)
                        self.assertEqual(rejected.exception.code, 404)
                        rejected.exception.close()
                    finally:
                        process.terminate()
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=5)
