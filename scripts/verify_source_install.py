"""Install the exact source artifact in isolation and exercise its offline entry point."""

import argparse
import hashlib
import json
import subprocess
import sys
import tarfile
import tempfile
import tomllib
from pathlib import Path


def verify(directory: Path, root: Path) -> None:
    """Require complete integration files, installed version, and working CLI metadata."""
    policy = json.loads((root / ".release-policy.json").read_text())
    config = json.loads((root / ".release-package.json").read_text())
    project = policy["repository"].split("/")[1]
    archives = sorted(directory.glob(project + "-*.tar.gz"))
    if len(archives) != 1:
        raise ValueError("Expected exactly one project source archive")
    archive = archives[0].resolve()
    before = hashlib.sha256(archive.read_bytes()).hexdigest()
    with tarfile.open(archive) as source:
        names = source.getnames()
        for required in config["required"]:
            prefix = project + "/" + required
            if not any(
                name == prefix or name.startswith(prefix + "/") for name in names
            ):
                raise ValueError(
                    "Source archive lacks required integration input: " + required
                )
        if any(
            not name.startswith(project + "/") or "/.git/" in name for name in names
        ):
            raise ValueError("Unexpected source archive member")
        metadata = tomllib.loads(
            source.extractfile(project + "/pyproject.toml").read().decode()
        )
    package = metadata["project"]["name"]
    expected = metadata["project"]["version"]
    if project == "google-home-voice-stats":
        module = "igw_google_voice"
        cli = "igw_google_voice.__main__"
        extra = """
import igw_google_voice
import sysconfig
from pathlib import Path
assert igw_google_voice.__version__ == expected
for name in ('script/igw/announce_energy.yaml', 'automation/igw/battery_reserve.yaml',
             'automation/igw/energy_condition.yaml', 'automation/igw/daily_briefing.yaml'):
    assert (Path(sysconfig.get_path('data')) / 'share/igw-google-voice/blueprints' / name).is_file(), name
"""
    else:
        module = "amazon_echo_home_voice"
        cli = "amazon_echo_home_voice.cli"
        extra = ""
    with tempfile.TemporaryDirectory(prefix="voice-release-install-") as temporary:
        work = Path(temporary)
        environment = work / "venv"
        python = environment / "bin/python"
        subprocess.run(
            ["uv", "--no-config", "venv", "--python", sys.executable, str(environment)],
            check=True,
        )
        subprocess.run(
            [
                "uv",
                "--no-config",
                "pip",
                "install",
                "--python",
                str(python),
                "--require-hashes",
                "--only-binary=:all:",
                "-r",
                str(root / ".github/requirements-build.txt"),
            ],
            check=True,
        )
        subprocess.run(
            [
                "uv",
                "--no-config",
                "pip",
                "install",
                "--python",
                str(python),
                "--no-deps",
                "--no-build-isolation",
                f"{package} @ {archive.as_uri()}",
            ],
            check=True,
        )
        smoke = (
            "import importlib, importlib.metadata\n"
            f"expected = {expected!r}\n"
            f"assert importlib.metadata.version({package!r}) == expected\n"
            f"importlib.import_module({module!r})\n" + extra
        )
        subprocess.run([str(python), "-I", "-c", smoke], cwd=work, check=True)
        subprocess.run(
            [str(python), "-I", "-m", cli, "--help"],
            cwd=work,
            check=True,
            capture_output=True,
            text=True,
        )
    if hashlib.sha256(archive.read_bytes()).hexdigest() != before:
        raise ValueError("Source archive changed during installation verification")
    print(f"Verified isolated installation and offline CLI: {package} {expected}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    verify(args.directory, Path(__file__).resolve().parents[1])


if __name__ == "__main__":
    main()
