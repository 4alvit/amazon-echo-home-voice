#!/usr/bin/env python3
"""One Cast owner and one Alexa Gunicorn master, with fail-together lifecycle."""

from __future__ import annotations

import ctypes
from dataclasses import dataclass, field
import ipaddress
import json
import os
from pathlib import Path
import signal
import socket
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit


COMMON_KEYS = {"IGW_URL", "IGW_READ_TOKEN", "CF_ACCESS_CLIENT_ID", "CF_ACCESS_CLIENT_SECRET"}
ALEXA_KEYS = COMMON_KEYS | {"ASK_SKILL_ID", "IGW_MAX_AGE_SECONDS", "IGW_TIMEOUT_SECONDS",
                            "ENERGY_VOICE_MODE"}
CAST_KEYS = COMMON_KEYS | {
    "CAST_API_TOKEN", "CAST_BIND_HOST", "CAST_HOST", "CAST_UUID", "CAST_PORT",
    "CAST_MEDIA_BASE_URL", "CAST_TTS_PROVIDER", "CAST_KITTEN_CA_FILE",
    "CAST_KITTEN_ENDPOINTS", "CAST_KITTEN_TIMEOUT", "CAST_KITTEN_TOKEN",
    "CAST_PIPER_MODEL", "CAST_PIPER_TIMEOUT", "IGW_MAX_RESPONSE_AGE",
    "IGW_ALLOW_LOCAL_HTTP", "CAST_CPUSET", "CAST_PIDS_LIMIT",
}
BASE_ENV = {"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8",
            "PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1",
            "HOME": "/tmp", "TMPDIR": "/tmp"}


class ConfigurationError(ValueError):
    """Messages contain field names only, never configuration values."""


def read_env(path: str, allowed: set[str]) -> dict[str, str]:
    source = Path(path)
    if source.is_symlink() or not stat.S_ISREG(source.stat().st_mode):
        raise ConfigurationError("Expected a regular environment file")
    if source.stat().st_size > 65536:
        raise ConfigurationError("Environment file exceeds size limit")
    result = {}
    for line in source.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep or key not in allowed or key in result or "\x00" in value:
            raise ConfigurationError("Unknown, duplicate or malformed environment entry")
        # Intentionally no shell expansion or dotenv quoting: bytes are literal.
        result[key] = value
    return result


def flag(env: dict, key: str, default: str = "true") -> bool:
    value = env.get(key, default)
    if value not in {"true", "false"}:
        raise ConfigurationError("Invalid boolean: " + key)
    return value == "true"


def integer(env: dict, key: str, default: int, low: int, high: int) -> int:
    try:
        value = int(env.get(key, str(default)))
        if not low <= value <= high:
            raise ValueError()
        return value
    except ValueError:
        raise ConfigurationError("Invalid integer: " + key) from None


def ipv4(value: str, *, loopback: bool) -> str:
    try:
        address = ipaddress.IPv4Address(value)
        if address.is_unspecified or address.is_multicast or address.is_link_local:
            raise ValueError()
        if loopback:
            if not address.is_loopback:
                raise ValueError()
        elif not any(address in ipaddress.IPv4Network(network) for network in
                     ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")):
            raise ValueError()
        return str(address)
    except ValueError:
        raise ConfigurationError("Expected an explicit permitted IPv4 bind address") from None


@dataclass(frozen=True)
class Child:
    name: str
    argv: tuple[str, ...]
    environment: dict[str, str] = field(repr=False)
    host: str
    port: int
    health_path: str


def child_specs(env: dict[str, str]) -> list[Child]:
    specs = []
    if flag(env, "UNIFIED_ENABLE_ALEXA"):
        config = read_env(env.get("UNIFIED_ALEXA_ENV_FILE", "/run/voice-config/alexa.env"),
                          ALEXA_KEYS)
        if config.get("ENERGY_VOICE_MODE", "personal") != "personal":
            raise ConfigurationError("Unified runtime supports personal Alexa mode only")
        if not all(config.get(key) for key in ("ASK_SKILL_ID", "IGW_URL", "IGW_READ_TOKEN")):
            raise ConfigurationError("Missing required personal Alexa configuration")
        host = ipv4(env.get("UNIFIED_ALEXA_BIND", "127.0.0.1"), loopback=True)
        port = integer(env, "UNIFIED_ALEXA_PORT", 8091, 1024, 65535)
        config["ENERGY_VOICE_MODE"] = "personal"
        command = (sys.executable, "-m", "gunicorn", "--config",
                   "python:amazon_echo_home_voice.gunicorn_config", "--bind", f"{host}:{port}",
                   "--workers", "2", "--threads", "4", "--timeout", "10",
                   "--graceful-timeout", "8", "--worker-tmp-dir", "/tmp",
                   "amazon_echo_home_voice.webhook:application")
        specs.append(Child("alexa", command, BASE_ENV | config, host, port, "/health"))
    if flag(env, "UNIFIED_ENABLE_CAST"):
        config = read_env(env.get("UNIFIED_CAST_ENV_FILE", "/run/voice-config/cast.env"),
                          CAST_KEYS)
        if not all(config.get(key) for key in ("CAST_BIND_HOST", "CAST_MEDIA_BASE_URL",
                                             "IGW_URL", "IGW_READ_TOKEN", "CAST_API_TOKEN",
                                             "CAST_HOST", "CAST_UUID")):
            raise ConfigurationError("Missing required Cast configuration")
        host = ipv4(config["CAST_BIND_HOST"], loopback=False)
        port = integer(config, "CAST_PORT", 8091, 1024, 65535)
        media = urlsplit(config["CAST_MEDIA_BASE_URL"])
        try:
            if (media.scheme != "http" or media.hostname != host or media.port != port
                    or media.path not in ("", "/") or media.username or media.password
                    or media.query or media.fragment):
                raise ValueError()
        except ValueError:
            raise ConfigurationError("Cast media origin must match its explicit LAN listener") from None
        # Optional local Piper dependencies/models are deliberately not packaged.
        if config.get("CAST_TTS_PROVIDER", "espeak") not in {"espeak", "kitten"}:
            raise ConfigurationError("This image supports espeak or external Kitten speech")
        if config.get("CAST_TTS_PROVIDER") == "kitten":
            ca = Path(config.get("CAST_KITTEN_CA_FILE", ""))
            if not ca.is_absolute() or not ca.is_file():
                raise ConfigurationError("Readable speech CA file is required")
        # Resource options belong to Compose, never to the child environment.
        config.pop("CAST_CPUSET", None)
        config.pop("CAST_PIDS_LIMIT", None)
        config["CAST_BIND_HOST"], config["CAST_PORT"] = host, str(port)
        specs.append(Child("cast", (sys.executable, "-m", "igw_google_voice.cast_service"),
                           BASE_ENV | config, host, port, "/healthz"))
    if not specs:
        raise ConfigurationError("Enable at least one application")
    return specs


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def probe(child: Child, timeout: float = 3) -> bool:
    # Never use proxy env, follow redirects, send credentials or request home data.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(f"http://{child.host}:{child.port}{child.health_path}", timeout=timeout) as res:
            body = res.read(4097)
            return res.status == 200 and len(body) <= 4096 and json.loads(body).get("status") == "ok"
    except urllib.error.HTTPError as error:
        error.close()
        return False
    except (OSError, ValueError, AttributeError):
        return False


def check_free_ports(specs: list[Child]) -> None:
    sockets = []
    try:
        for spec in specs:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sockets.append(sock)
            # Match the real HTTP listeners: a completed connection in TIME_WAIT
            # must not block a restart, while an active listening socket still does.
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((spec.host, spec.port))
    except OSError:
        raise ConfigurationError("Application listener is unavailable; old service may still own it") from None
    finally:
        for sock in sockets:
            sock.close()


def adopt_orphans() -> None:
    if sys.platform.startswith("linux"):
        # Also reap descendants when used below an init rather than as PID 1.
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
            raise RuntimeError("Cannot enable child reaping")


def signal_group(process: subprocess.Popen, sig: int) -> None:
    try:
        os.killpg(process.pid, sig)
    except ProcessLookupError:
        pass


def stop_children(processes: list[subprocess.Popen], timeout: float) -> None:
    for process in processes:
        signal_group(process, signal.SIGTERM)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if all(process.poll() is not None for process in processes):
            break
        time.sleep(0.05)
    # A dead master can leave grandchildren behind; kill the whole saved group.
    for process in processes:
        signal_group(process, signal.SIGKILL)
    for process in processes:
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
    # Reap grandchildren adopted by PID 1 / PR_SET_CHILD_SUBREAPER.
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        try:
            pid, _ = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            break
        if pid == 0:
            time.sleep(0.02)


def write_status(path: Path, ready: bool, children: dict[str, bool]) -> None:
    temporary = path.with_suffix(".tmp")
    with open(temporary, "w", encoding="utf-8") as stream:
        json.dump({"ready": ready, "pid": os.getpid(), "updated": time.monotonic(),
                   "children": children}, stream)
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def run(specs: list[Child], status_path: Path, startup_timeout: float = 90,
        shutdown_timeout: float = 12, failure_grace: float = 60,
        probe_timeout: float = 3) -> int:
    adopt_orphans()
    check_free_ports(specs)
    stopped = False

    def request_stop(signum, frame):
        nonlocal stopped
        stopped = True

    previous = {sig: signal.signal(sig, request_stop) for sig in (signal.SIGTERM, signal.SIGINT)}
    processes = []
    health = {spec.name: False for spec in specs}
    try:
        write_status(status_path, False, health)
        for spec in specs:
            if stopped:
                return 0
            processes.append(subprocess.Popen(spec.argv, env=spec.environment, start_new_session=True))
        deadline = time.monotonic() + startup_timeout
        ready_once, unhealthy_since = False, None
        while not stopped:
            if any(process.poll() is not None for process in processes):
                print("unified: required application exited", file=sys.stderr, flush=True)
                return 1
            health = {}
            for spec in specs:
                if stopped:
                    break
                health[spec.name] = probe(spec, timeout=probe_timeout)
            if stopped:
                break
            ready = all(health.values())
            write_status(status_path, ready, health)
            if ready:
                ready_once, unhealthy_since = True, None
            elif ready_once:
                if unhealthy_since is None:
                    unhealthy_since = time.monotonic()
                if time.monotonic() - unhealthy_since >= failure_grace:
                    print("unified: application health deadline exceeded", file=sys.stderr, flush=True)
                    return 1
            elif time.monotonic() >= deadline:
                print("unified: application startup deadline exceeded", file=sys.stderr, flush=True)
                return 1
            time.sleep(1)
        return 0
    finally:
        try:
            write_status(status_path, False, {name: False for name in health})
        finally:
            stop_children(processes, shutdown_timeout)
            for sig, handler in previous.items():
                signal.signal(sig, handler)


def main() -> int:
    try:
        specs = child_specs(dict(os.environ))
        return run(specs, Path("/tmp/unified-status.json"),
                   integer(os.environ, "UNIFIED_STARTUP_TIMEOUT", 90, 5, 300),
                   integer(os.environ, "UNIFIED_SHUTDOWN_TIMEOUT", 12, 1, 20),
                   integer(os.environ, "UNIFIED_FAILURE_GRACE", 60, 15, 600),
                   integer(os.environ, "UNIFIED_PROBE_TIMEOUT", 3, 1, 10))
    except (ConfigurationError, OSError, RuntimeError):
        print("unified: startup/configuration failed; check private configuration and listeners",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
