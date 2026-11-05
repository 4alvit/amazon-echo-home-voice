"""Regression checks for coupled source, deployment locks, and Lambda runtime."""
from pathlib import Path
import shutil
import tempfile
import unittest

from scripts.check_dependency_locks import LOCKS, ROOT, check, expected_version


class DependencyLockTests(unittest.TestCase):
    def test_checked_in_sources_and_locks_agree(self):
        check()

    def test_stale_deployment_lock_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for filename in (*LOCKS, "requirements-webhook.in", "pyproject.toml"):
                destination = root / filename
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / filename, destination)
            path = root / "src/requirements.txt"
            path.write_text(path.read_text().replace(
                "cryptography==" + expected_version(), "cryptography==0.0.0"
            ))
            with self.assertRaisesRegex(ValueError, "src/requirements.txt"):
                check(root)

    def test_stale_installed_lambda_dependency_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Installed cryptography"):
            check(installed="0.0.0")
