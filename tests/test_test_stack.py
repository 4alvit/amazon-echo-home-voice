"""Render only: never start containers or use operator credentials."""

import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("test_stack_helper", ROOT / "scripts/test-stack.py")
HELPER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HELPER)


def synthetic_environment(path):
    template = ROOT / "deploy/multi-household/.env.example"
    path.write_text(template.read_text(), encoding="utf-8")
    path.chmod(0o600)


class LifecycleTests(unittest.TestCase):
    def test_bare_invocation_only_displays_help(self):
        output = io.StringIO()
        with patch("sys.stdout", output), patch.object(HELPER.subprocess, "run") as run, \
             self.assertRaises(SystemExit) as result:
            HELPER.main([])
        self.assertEqual(result.exception.code, 0)
        self.assertIn("initialize", output.getvalue())
        self.assertIn("--project-name", output.getvalue())
        run.assert_not_called()

    def test_commands_are_explicit_and_preserve_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            env = Path(temporary) / ".env"
            synthetic_environment(env)
            for action in ("check", "initialize", "up", "status", "stop"):
                with self.subTest(action=action):
                    command = HELPER.command([action, "--env-file", str(env)])
                    self.assertEqual(command[:2], ["docker", "compose"])
                    self.assertIn("home-energy-test", command)
                    self.assertIn(str(ROOT / "deploy/test"), command)
                    self.assertNotIn("down", command)
                    self.assertNotIn("--volumes", command)
                    self.assertNotIn("--remove-orphans", command)
                    self.assertIn("setup" if action == "initialize" else "test", command)
            self.assertEqual(HELPER.command(["initialize", "--env-file", str(env)])[-3:],
                             ["run", "--rm", "initialize"])
            self.assertEqual(HELPER.command(["stop", "--env-file", str(env)])[-5:],
                             ["stop", "postgres", "keycloak", "alexa", "portal"])
            with self.assertRaises(SystemExit), patch("sys.stderr"):
                HELPER.command(["stop", "--env-file", str(env), "--project-name", "igw-alexa-test"])

    def test_scoped_environment_prevents_compose_and_app_override(self):
        with tempfile.TemporaryDirectory() as temporary:
            env = Path(temporary) / ".env"
            synthetic_environment(env)
            with patch.dict(os.environ, {"COMPOSE_FILE": "wrong.yaml", "COMPOSE_PROFILES": "setup",
                                         "ASK_SKILL_ID": "wrong", "DOCKER_CONTEXT": "selected-context"}), \
                 patch.object(HELPER.subprocess, "run") as run:
                run.return_value.returncode = 0
                self.assertEqual(HELPER.main(["check", "--env-file", str(env)]), 0)
            child_env = run.call_args.kwargs["env"]
            self.assertNotIn("COMPOSE_FILE", child_env)
            self.assertNotIn("COMPOSE_PROFILES", child_env)
            self.assertNotIn("ASK_SKILL_ID", child_env)
            self.assertEqual(child_env["DOCKER_CONTEXT"], "selected-context")
            self.assertEqual(run.call_args.args[0][-2:], ["config", "--quiet"])

    def test_prepare_config_writes_only_selected_directory_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            command = [sys.executable, str(ROOT / "deploy/multi-household/prepare_config.py"),
                       "--output-dir", str(directory)]
            result = subprocess.run(command, text=True, capture_output=True, check=True)
            env = directory / ".env"
            before = env.read_bytes()
            self.assertEqual(env.stat().st_mode & 0o777, 0o600)
            self.assertEqual((directory / "private").stat().st_mode & 0o777, 0o700)
            values = dict(line.split("=", 1) for line in env.read_text().splitlines() if "=" in line)
            self.assertNotIn(values["TENANT_ENCRYPTION_KEY"], result.stdout + result.stderr)
            again = subprocess.run(command, text=True, capture_output=True)
            self.assertNotEqual(again.returncode, 0)
            self.assertEqual(env.read_bytes(), before)


class ComposeRenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which("docker"):
            raise unittest.SkipTest("Docker Compose plugin unavailable")
        version = subprocess.run(["docker", "compose", "version"], capture_output=True, timeout=10)
        if version.returncode:
            raise unittest.SkipTest("Docker Compose plugin unavailable")

    def render(self, env, *arguments):
        # `config` only loads local files; it does not connect to a Docker daemon.
        command = ["docker", "compose", "--project-directory", str(ROOT / "deploy/test"),
                   "--env-file", str(env), "-f", str(ROOT / "deploy/test/compose.yaml"),
                   *arguments]
        clean_env = {key: os.environ[key] for key in ("PATH", "HOME") if key in os.environ}
        return subprocess.run(command, env=clean_env, text=True, capture_output=True,
                              check=True, timeout=20).stdout

    def test_private_image_overrides_survive_compose_extends(self):
        with tempfile.TemporaryDirectory() as temporary:
            env = Path(temporary) / ".env"
            synthetic_environment(env)
            values = {"KEYCLOAK_IMAGE": "registry.example.invalid/keycloak-security@sha256:" + "a" * 64,
                      "POSTGRES_IMAGE": "registry.example.invalid/postgres-security@sha256:" + "b" * 64}
            with env.open("a") as output:
                for key, value in values.items():
                    output.write(key + "=" + value + "\n")
            document = json.loads(self.render(env, "--profile", "*", "config", "--format", "json"))
            self.assertEqual(document["services"]["keycloak"]["image"], values["KEYCLOAK_IMAGE"])
            self.assertEqual(document["services"]["postgres"]["image"], values["POSTGRES_IMAGE"])

    def test_rendered_profiles_commands_ports_paths_and_persistence(self):
        with tempfile.TemporaryDirectory() as temporary:
            env = Path(temporary) / ".env"
            synthetic_environment(env)
            self.assertEqual(self.render(env, "config", "--services").strip(), "")
            document = json.loads(self.render(env, "--profile", "*", "config", "--format", "json"))
            services = document["services"]
            self.assertEqual(set(services), {"postgres", "keycloak", "alexa", "portal", "initialize"})
            for name, service in services.items():
                self.assertEqual(service["restart"], "no")
                self.assertEqual(service["profiles"], ["setup"] if name == "initialize" else ["test"])
                self.assertNotIn("container_name", service)
            self.assertTrue(services["postgres"]["image"].startswith("postgres:17.11-bookworm@sha256:"))
            self.assertTrue(services["keycloak"]["image"].startswith("quay.io/keycloak/keycloak:26.8.1@sha256:"))
            self.assertEqual(services["keycloak"]["command"], ["start", "--import-realm"])
            self.assertEqual(services["initialize"]["command"], ["energy-voice-admin", "init"])
            self.assertEqual(services["portal"]["command"][-1], "amazon_echo_home_voice.portal:application")
            self.assertIsNone(services["alexa"].get("command"))
            for name, port in (("alexa", "28091"), ("portal", "28092"), ("keycloak", "28085")):
                bindings = services[name]["ports"]
                self.assertEqual(len(bindings), 1)
                self.assertEqual(bindings[0]["host_ip"], "127.0.0.1")
                self.assertEqual(bindings[0]["published"], port)
            for name in ("alexa", "portal", "initialize"):
                service = services[name]
                self.assertEqual(service["build"]["context"], str(ROOT))
                self.assertEqual(service["image"], "home-energy-test:local")
                mount, = service["volumes"]
                self.assertEqual(mount["source"], str(ROOT / "deploy/test/private/data"))
                self.assertEqual(mount["target"], "/data")
                self.assertFalse(mount["bind"].get("create_host_path", False))
            mount, = services["keycloak"]["volumes"]
            self.assertEqual(mount["source"], str(ROOT / "deploy/test/private/home-energy-realm.json"))
            self.assertTrue(mount["read_only"])
            self.assertTrue(document["networks"]["identity"]["internal"])
            self.assertEqual(document["volumes"]["identity-database"]["name"],
                             "home-energy-test_identity-database")
