"""Exercise real project metadata through frozen overlays and source packaging."""

import ast
import hashlib
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import package_release as package
import release_version_adapter as adapter
import stage_release_assets as staging
import version_plan as versions
import version_receipt as receipts


class ProjectVersioningTest(unittest.TestCase):
    """Build disposable source exports; never allocate or publish a release."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        package.snapshot(ROOT, self.root)
        self.policy = json.loads((self.root / ".release-policy.json").read_text())
        self.base = versions.read_base_version(self.root, self.policy)
        self.name = self.policy["repository"].split("/")[1]
        # This offline source-archive test does not substitute for real wheel or OCI builds.
        config = json.loads((self.root / ".release-package.json").read_text())
        config["python_distribution"] = False
        (self.root / ".release-package.json").write_text(json.dumps(config))
        for command in (
            ["git", "init", "-q"],
            ["git", "add", "."],
            [
                "git",
                "-c",
                "user.name=Release fixture",
                "-c",
                "user.email=fixture@example.invalid",
                "-c",
                "commit.gpgsign=false",
                "commit",
                "-qm",
                "Project release fixture",
            ],
        ):
            subprocess.run(command, cwd=self.root, check=True, capture_output=True)
        self.sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=self.root, text=True
        ).strip()
        self.originals = {
            item["path"]: (self.root / item["path"]).read_bytes()
            for item in self.policy["versioning"]["files"]
        }
        self.environment = patch.dict(os.environ)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        os.environ.pop("RELEASE_VERSION_PLAN", None)

    def freeze(self, channel="beta"):
        for name, data in self.originals.items():
            (self.root / name).write_bytes(data)
        sequence = "20261013120000.42.2" if channel == "nightly" else 7
        plan = versions.create_plan(
            self.base, channel, sequence, self.sha, self.policy, 7
        )
        (self.root / ".release-plan.json").write_text(json.dumps(plan))
        subprocess.run(
            [
                sys.executable,
                str(self.root / "scripts/version_plan.py"),
                "sync",
                "--root",
                str(self.root),
                "--plan",
                ".release-plan.json",
            ],
            check=True,
            capture_output=True,
        )
        return plan

    @staticmethod
    def inventory(directory):
        return [
            {
                "name": path.name,
                "size": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            for path in sorted(directory.iterdir())
        ]

    def test_real_source_archive_and_receipt_match_all_channels(self):
        original_lock = (
            tomllib.loads(self.originals["uv.lock"].decode())
            if "uv.lock" in self.originals
            else None
        )
        for channel in ("beta", "rc", "nightly"):
            with self.subTest(channel=channel):
                plan = self.freeze(channel)
                identity = versions.projections(plan)["package"]
                python_identity = versions.projections(plan, "pep440")["package"]
                output = self.root / ("dist-" + channel)
                package.build_candidate(
                    self.root, self.base, channel, output, containers=False
                )
                source = output / f"{self.name}-{identity}.tar.gz"
                with tarfile.open(source) as archive:
                    metadata = tomllib.loads(
                        archive.extractfile(self.name + "/pyproject.toml")
                        .read()
                        .decode()
                    )
                    self.assertEqual(metadata["project"]["version"], python_identity)
                    if original_lock is not None:
                        lock = tomllib.loads(
                            archive.extractfile(self.name + "/uv.lock").read().decode()
                        )
                        package_name = metadata["project"]["name"]
                        self.assertEqual(
                            [
                                item
                                for item in lock["package"]
                                if item["name"] != package_name
                            ],
                            [
                                item
                                for item in original_lock["package"]
                                if item["name"] != package_name
                            ],
                        )
                        owned = [
                            item
                            for item in lock["package"]
                            if item["name"] == package_name
                        ]
                        self.assertEqual(
                            [item["version"] for item in owned], [python_identity]
                        )
                    for declaration in self.policy["versioning"]["files"]:
                        if declaration["format"] == "python":
                            module = ast.parse(
                                archive.extractfile(
                                    self.name + "/" + declaration["path"]
                                ).read()
                            )
                            values = [
                                node.value.value
                                for node in module.body
                                if isinstance(node, ast.Assign)
                                and any(
                                    isinstance(target, ast.Name)
                                    and target.id == declaration["field"]
                                    for target in node.targets
                                )
                            ]
                            self.assertEqual(values, [python_identity])
                staged = staging.stage(self.root, channel, [output.name + "/*"])
                checked = receipts.verify_receipts(
                    staged, plan, self.inventory(staged), self.policy
                )
                self.assertEqual(len(checked), 1)
                # Missing OCI/wheel payloads must not pass the complete publication inventory.
                if self.policy.get("container_assets"):
                    with self.assertRaisesRegex(ValueError, "missing"):
                        receipts.verify_declared_artifacts(staged, self.policy, plan)
                else:
                    self.assertTrue(
                        receipts.verify_declared_artifacts(staged, self.policy, plan)
                    )

    def test_wrong_channel_and_changed_inputs_cannot_be_packaged_or_receipted(self):
        self.freeze()
        with self.assertRaisesRegex(ValueError, "frozen plan"):
            adapter.checked_version(self.root, self.base, "rc")
        output = self.root / "dist-check"
        package.build_candidate(self.root, self.base, "beta", output, containers=False)
        with (self.root / "pyproject.toml").open("a") as stream:
            stream.write("\n# a post-build modification\n")
        with self.assertRaisesRegex(ValueError, "changed after plan preparation"):
            staging.stage(self.root, "changed", [output.name + "/*"])
        self.assertFalse((self.root / "release-assets/changed").exists())


if __name__ == "__main__":
    unittest.main()
