#!/usr/bin/env python3
"""Format/validate disposable source copies. Never plan/apply or read state."""

# Preserve the existing operator-facing CLI filename.
# pylint: disable=invalid-name
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


def stage_sources(root, stage):
    """Copy tracked/nonignored source while excluding state and private variable files."""
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=root,
        capture_output=True,
        check=True,
    )
    for name in set(result.stdout.decode().split("\0")):
        source = root / name
        if not name or not source.is_file() or source.is_symlink():
            continue
        if ".terraform" in source.parts or ".tfstate" in source.name:
            continue
        if source.name.endswith((".tfvars", ".tfvars.json")):
            continue
        target = stage / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    for fixture in stage.glob("deployments/*/compose.redacted.yml"):
        shutil.copyfile(fixture, fixture.with_name("compose.yml"))


def isolated_environment(temporary):
    """Remove Terraform credentials/options and use temporary provider/config paths."""
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("TF_VAR_", "TF_TOKEN_", "TF_CLI_ARGS"))
    }
    configuration = temporary / "empty.tfrc"
    configuration.write_text("", encoding="utf-8")
    cache = temporary / "provider-cache"
    cache.mkdir()
    environment.update(
        TF_IN_AUTOMATION="1",
        TF_INPUT="0",
        TF_CLI_CONFIG_FILE=str(configuration),
        TF_PLUGIN_CACHE_DIR=str(cache),
    )
    return environment


def validate_roots(stage, roots, temporary):
    """Run only fmt, backend-disabled init, and validate for each confined root."""
    environment = isolated_environment(temporary)
    for index, relative in enumerate(roots):
        environment["TF_DATA_DIR"] = str(temporary / f"terraform-data-{index}")
        directory = stage / relative
        if (
            not directory.resolve().is_relative_to(stage.resolve())
            or not directory.is_dir()
        ):
            raise SystemExit(f"Invalid Terraform root: {relative}")
        print(f"Validating Terraform root: {relative}", flush=True)
        for command in (
            ["terraform", "fmt", "-check", "-recursive"],
            ["terraform", "init", "-backend=false", "-input=false", "-no-color"],
            ["terraform", "validate", "-no-color"],
        ):
            subprocess.run(command, cwd=directory, env=environment, check=True)


def main():
    """Validate disposable source without accessing a backend or running a plan."""
    root = Path(__file__).resolve().parents[1]
    policy = json.loads((root / ".release-policy.json").read_text())
    if not shutil.which("terraform"):
        raise SystemExit("Install Terraform 1.15.7 before running scripts/ci.sh")
    with tempfile.TemporaryDirectory(prefix="terraform-ci-") as temporary:
        stage = Path(temporary) / "source"
        stage.mkdir()
        stage_sources(root, stage)
        validate_roots(stage, policy["terraform_roots"], Path(temporary))
    print(
        "Terraform format/schema passed; no plan, apply, backend, state or live-resource checks."
    )


if __name__ == "__main__":
    main()
