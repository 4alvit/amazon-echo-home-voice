"""Real subprocess tests for supervision; no NAS, Cast, IGW or accounts are used."""

import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request


RUNTIME = Path(__file__).resolve().parents[1] / "deploy/unified/supervisor.py"
SERVER = r'''
from http.server import BaseHTTPRequestHandler, HTTPServer
import json, os, pathlib, signal, subprocess, sys
port, mode, marker = int(sys.argv[1]), sys.argv[2], pathlib.Path(sys.argv[3])
if mode == "stubborn":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    grandchild = subprocess.Popen([sys.executable, "-c",
        "import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(100)"])
    marker.write_text(json.dumps([os.getpid(), grandchild.pid]))
else:
    marker.write_text(json.dumps([os.getpid()]))
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_GET(self):
        global mode
        if self.path == "/exit": os._exit(0)
        if self.path == "/unhealthy": mode = "unhealthy"
        if self.path == "/healthy": mode = "healthy"
        self.send_response(503 if mode == "unhealthy" else 200)
        self.end_headers()
        self.wfile.write(b'{"status":"ok"}')
HTTPServer(("127.0.0.1", port), Handler).serve_forever()
'''
DRIVER = r'''
import importlib.util, json, pathlib, sys
spec=importlib.util.spec_from_file_location("runtime", sys.argv[1])
r=importlib.util.module_from_spec(spec); sys.modules["runtime"]=r; spec.loader.exec_module(r)
config=json.loads(pathlib.Path(sys.argv[2]).read_text())
children=[r.Child(x["name"], tuple(x["argv"]), r.BASE_ENV, "127.0.0.1", x["port"], "/health")
          for x in config["children"]]
raise SystemExit(r.run(children, pathlib.Path(config["status"]), startup_timeout=config["startup"],
                       shutdown_timeout=0.4, failure_grace=3))
'''


@unittest.skipUnless(os.name == "posix", "POSIX process groups required")
class UnifiedProcessTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.server = self.root / "server.py"
        self.server.write_text(SERVER)
        self.driver = self.root / "driver.py"
        self.driver.write_text(DRIVER)
        self.status = self.root / "status.json"
        self.process = None
        self.addCleanup(self.stop)

    def stop(self):
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        if self.process is not None:
            self.process.stdout.close()

    def launch(self, modes=("healthy", "healthy")):
        entries = []
        for i, mode in enumerate(modes):
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            entries.append({"name": f"child{i}", "port": port,
                            "argv": [sys.executable, str(self.server), str(port),
                                     mode, str(self.root / f"pid{i}.json")]})
        config = self.root / "config.json"
        config.write_text(json.dumps({"children": entries, "status": str(self.status),
                                      "startup": 1.2 if "unhealthy" in modes else 8}))
        self.process = subprocess.Popen([sys.executable, str(self.driver), str(RUNTIME), str(config)],
                                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        return entries

    def wait_ready(self):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                self.fail(self.process.stdout.read())
            try:
                if json.loads(self.status.read_text()).get("ready"):
                    return
            except (OSError, ValueError):
                pass
            time.sleep(0.03)
        self.fail("Supervisor did not become ready")

    def assert_children_gone(self):
        pids = [pid for path in self.root.glob("pid*.json") for pid in json.loads(path.read_text())]
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            running = []
            for pid in pids:
                try:
                    os.kill(pid, 0)
                    running.append(pid)
                except ProcessLookupError:
                    pass
            if not running:
                return
            time.sleep(0.05)
        self.fail("Child or descendant survived bounded shutdown")

    def test_two_children_ready_then_sigterm_stops_both(self):
        self.launch()
        self.wait_ready()
        self.process.send_signal(signal.SIGTERM)
        self.assertEqual(self.process.wait(timeout=4), 0)
        self.assertFalse(json.loads(self.status.read_text())["ready"])
        self.assert_children_gone()

    def test_successful_child_exit_is_fatal_and_stops_peer(self):
        entries = self.launch()
        self.wait_ready()
        try:
            urllib.request.urlopen(f'http://127.0.0.1:{entries[0]["port"]}/exit', timeout=1)
        except OSError:
            pass
        self.assertEqual(self.process.wait(timeout=4), 1)
        self.assert_children_gone()

    def test_unhealthy_startup_has_deadline_and_cleanup(self):
        self.launch(("healthy", "unhealthy"))
        self.assertEqual(self.process.wait(timeout=5), 1)
        self.assertIn("startup deadline", self.process.stdout.read())
        self.assert_children_gone()

    def test_transient_readiness_failure_does_not_restart_children(self):
        entries = self.launch()
        self.wait_ready()
        url = f'http://127.0.0.1:{entries[0]["port"]}'
        try:
            urllib.request.urlopen(url + "/unhealthy", timeout=1)
        except urllib.error.HTTPError as error:
            error.close()
        except OSError:
            pass
        time.sleep(1.2)
        self.assertIsNone(self.process.poll())
        self.assertFalse(json.loads(self.status.read_text())["ready"])
        with urllib.request.urlopen(url + "/healthy", timeout=1):
            pass
        self.wait_ready()

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux subreaper orphan semantics")
    def test_stubborn_child_and_grandchild_are_killed_and_reaped(self):
        self.launch(("healthy", "stubborn"))
        self.wait_ready()
        started = time.monotonic()
        self.process.terminate()
        self.assertEqual(self.process.wait(timeout=4), 0)
        self.assertLess(time.monotonic() - started, 3)
        self.assert_children_gone()


if __name__ == "__main__":
    unittest.main()
