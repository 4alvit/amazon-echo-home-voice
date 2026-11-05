"""Regression checks for coupled source, deployment locks, and Lambda runtime."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "check_dependency_locks", ROOT / "scripts/check_dependency_locks.py"
)
CHECKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKER)


class DependencyLockTests(unittest.TestCase):
    @unittest.skipUnless((ROOT / "pyproject.toml").is_file(), "source checkout check")
    def test_checked_in_sources_and_locks_agree(self):
        CHECKER.check(ROOT)

    def fixture(self, root):
        (root / "pyproject.toml").write_text(
            '[project]\ndependencies = ["cryptography==1.0.0"]\n'
            '[project.optional-dependencies]\nmulti-household = ["cryptography==1.0.0"]\n'
        )
        for filename in (*CHECKER.LOCKS, "requirements-webhook.in"):
            path = root / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("cryptography==1.0.0\n")

    def test_stale_deployment_lock_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.fixture(root)
            CHECKER.check(root)
            (root / "src/requirements.txt").write_text("cryptography==0.0.0\n")
            with self.assertRaisesRegex(ValueError, "src/requirements.txt"):
                CHECKER.check(root)

    def test_stale_installed_lambda_dependency_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.fixture(root)
            CHECKER.check(root, installed="1.0.0")
            with self.assertRaisesRegex(ValueError, "Installed cryptography"):
                CHECKER.check(root, installed="0.0.0")
