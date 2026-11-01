"""Hardened identity deployments fail closed when private overrides disappear."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("identity_compose", Path(__file__).resolve().parents[1] / "deploy/multi-household/identity_compose.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class IdentityImagesTests(unittest.TestCase):
    def setUp(self):
        self.pins = patch.object(MODULE, "HARDENED", {
            "keycloak": "sha256:" + "1" * 64,
            "postgres": "sha256:" + "2" * 64,
        })
        self.pins.start()
        self.addCleanup(self.pins.stop)
        self.hardened = {name: "registry.example.invalid/" + name + "-security@" + digest
                         for name, digest in MODULE.HARDENED.items()}
        self.upstream = {name: "registry.example.invalid/" + name + "@sha256:" + "a" * 64
                         for name in MODULE.HARDENED}

    def test_pinned_generic_installation_is_allowed(self):
        self.assertFalse(MODULE.validate_images(self.upstream, {}))

    def test_missing_override_is_rejected_for_running_hardened_installation(self):
        with self.assertRaises(ValueError):
            MODULE.validate_images(self.upstream, self.hardened)

    def test_recovery_marker_requires_both_hardened_bindings(self):
        with self.assertRaises(ValueError):
            MODULE.validate_images(self.upstream, {}, required=True)

    def test_hardened_bindings_and_custom_registry_are_allowed(self):
        self.assertTrue(MODULE.validate_images(self.hardened, {}, required=True))

    def test_floating_and_injected_images_are_rejected(self):
        for invalid in ("postgres:latest", "bad\nimage@sha256:" + "a" * 64):
            with self.assertRaises(ValueError):
                MODULE.validate_images({**self.upstream, "postgres": invalid}, {})

    def test_recovery_allows_only_the_exact_previous_pair(self):
        previous = {name: "registry.example.invalid/" + name + "@" + digest
                    for name, digest in MODULE.PREVIOUS.items()}
        self.assertFalse(MODULE.validate_images(previous, self.hardened, required=True, rollback=True))
        for wrong in (self.upstream, {**previous, "keycloak": self.hardened["keycloak"]}):
            with self.assertRaises(ValueError):
                MODULE.validate_images(wrong, self.hardened, required=True, rollback=True)

    def test_stop_does_not_require_image_approval(self):
        with patch("sys.argv", ["identity_compose.py", "--", "stop", "keycloak"]), \
             patch.object(MODULE, "capture", side_effect=AssertionError("Image preflight called")), \
             patch.object(MODULE.subprocess, "run") as run:
            run.return_value.returncode = 0
            self.assertEqual(MODULE.main(), 0)
        self.assertEqual(run.call_args.args[0][-2:], ["stop", "keycloak"])

    def test_start_restart_cannot_bypass_image_replacement(self):
        for command in ("start", "restart"):
            with self.subTest(command=command), \
                 patch("sys.argv", ["identity_compose.py", "--", command, "keycloak"]), \
                 patch.object(MODULE, "capture", side_effect=AssertionError("Image preflight called")), \
                 patch.object(MODULE.subprocess, "run") as run, \
                 patch("sys.stderr"):
                self.assertEqual(MODULE.main(), 1)
                run.assert_not_called()

    def test_up_cannot_keep_stale_containers_with_no_recreate(self):
        for flag in ("--no-recreate", "--no-recreate=true", "--no-recreate=false"):
            with self.subTest(flag=flag), \
                 patch("sys.argv", ["identity_compose.py", "--", "up", "-d", flag]), \
                 patch.object(MODULE, "capture", side_effect=AssertionError("Image preflight called")), \
                 patch.object(MODULE.subprocess, "run") as run, \
                 patch("sys.stderr"):
                self.assertEqual(MODULE.main(), 1)
                run.assert_not_called()

    def test_new_marker_is_private_despite_inherited_modes(self):
        mkdir = Path.mkdir
        real_open = os.open
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            def inherited_mkdir(path, *args, **kwargs):
                mkdir(path, *args, **kwargs)
                path.chmod(0o711)
            def inherited_open(path, flags, mode=0o777):
                fd = real_open(path, flags, mode)
                os.fchmod(fd, 0o644)
                return fd
            dump = json.dump
            def check_before_write(value, output):
                self.assertEqual(os.fstat(output.fileno()).st_mode & 0o777, 0o600)
                self.assertEqual((directory / 'private').stat().st_mode & 0o777, 0o700)
                dump(value, output)
            configured = json.dumps({'services': {name: {'image': ref} for name, ref in self.hardened.items()}})
            with patch('sys.argv', ['identity_compose.py', '--directory', str(directory), '--', 'up', '-d']), \
                 patch.object(MODULE, 'capture', side_effect=[configured, '', '']), \
                 patch.object(MODULE.subprocess, 'run') as run, \
                 patch.object(Path, 'mkdir', inherited_mkdir), \
                 patch.object(MODULE.os, 'open', inherited_open), \
                 patch.object(MODULE.json, 'dump', check_before_write):
                run.return_value.returncode = 0
                self.assertEqual(MODULE.main(), 0)
            self.assertTrue((directory / 'private' / MODULE.MARKER).is_file())

    def test_existing_broad_marker_directory_is_rejected_unchanged(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            directory.chmod(0o755)
            with self.assertRaises(ValueError):
                MODULE.ensure_marker_directory(directory)
            self.assertEqual(directory.stat().st_mode & 0o777, 0o755)
