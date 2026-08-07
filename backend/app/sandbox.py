"""Per-conversation persistent Alpine sandbox for the run_command tool.

Each conversation gets ONE long-lived Docker container: anything the model
installs (`apk add`, `pip install`, `npm install`) or any files it creates
persist for the whole chat. Containers are removed when the conversation is
deleted, after an idle timeout, when the pool grows too large, on app
shutdown, and at boot (leftovers of a crashed run).

The sandbox runs a purpose-built image (`llmdash-sandbox:latest`, built once
and cached by the Docker daemon) that actually contains the tooling the skill
advertises — stock `alpine:latest` has only busybox, which made the model
discover missing curl/python/node on every fresh container.
"""

import asyncio
import os
import shutil
import subprocess
import tempfile
import time

_docker_available = None

# Hard caps so a misbehaving model (or prompt) can't hang the worker or the
# Docker daemon. The model-supplied timeout is clamped to this.
MAX_SANDBOX_TIMEOUT = 120
MAX_OUTPUT_CHARS = 100_000

SANDBOX_IMAGE = "llmdash-sandbox:latest"
# Everything the skill description promises, baked into one shared image so a
# per-conversation container starts instantly with the tools already present.
SANDBOX_IMAGE_PACKAGES = (
    "python3 py3-pip nodejs npm git curl wget gcc build-base "
    "musl-dev linux-headers ca-certificates"
)
SANDBOX_DOCKERFILE = (
    "FROM alpine:latest\n"
    f"RUN apk add --no-cache {SANDBOX_IMAGE_PACKAGES}\n"
    'CMD ["tail", "-f", "/dev/null"]\n'
)

SANDBOX_IDLE_TTL = 30 * 60  # drop after 30 min without use
SANDBOX_MAX_POOL = 16       # hard cap on live sandboxes

# conversation_id -> {"container": str, "last_used": float (monotonic)}
_sandboxes: dict[int, dict] = {}


def is_docker_available() -> bool:
    """True if the docker CLI can reach a daemon. Uses the CLI (not the python
    SDK) so setups like Colima whose socket only exists via the CLI context
    are detected correctly."""
    global _docker_available
    if _docker_available is not None:
        return _docker_available
    try:
        proc = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            timeout=5,
        )
        _docker_available = proc.returncode == 0
    except Exception:
        _docker_available = False
    return _docker_available


def _cap_output(output: str) -> str:
    if len(output) > MAX_OUTPUT_CHARS:
        return output[:MAX_OUTPUT_CHARS] + f"\n\n[Output truncated at {MAX_OUTPUT_CHARS} chars]"
    return output


async def _docker(*args: str, timeout: float = 60.0) -> tuple[int, str, str]:
    """Run a `docker` CLI command. Returns (exit_code, stdout, stderr)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return -1, "", "Error: Docker not found on host. Ensure Docker is installed."
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return -1, "", "Error: Command timed out"
    return proc.returncode or 0, stdout.decode("utf-8", errors="replace"), stderr.decode("utf-8", errors="replace")


def _container_name(conversation_id: int) -> str:
    return f"llmdash-sandbox-{conversation_id}"


async def _ensure_sandbox_image() -> str | None:
    """Make sure the tooled sandbox image exists, building it once if missing.
    Returns an error message or None."""
    rc, _, _ = await _docker("image", "inspect", SANDBOX_IMAGE)
    if rc == 0:
        return None
    ctx = tempfile.mkdtemp(prefix="llmdash-sandbox-")
    try:
        with open(os.path.join(ctx, "Dockerfile"), "w") as f:
            f.write(SANDBOX_DOCKERFILE)
        rc, _, err = await _docker("build", "-t", SANDBOX_IMAGE, ctx, timeout=600)
        if rc != 0:
            # A concurrent build (e.g. the startup task) may have won the race.
            rc2, _, _ = await _docker("image", "inspect", SANDBOX_IMAGE)
            if rc2 == 0:
                return None
            return f"Error: could not build sandbox image: {err.strip()[:300]}"
    finally:
        shutil.rmtree(ctx, ignore_errors=True)
    return None


async def _get_or_create_container(conversation_id: int) -> str | None:
    """Return the running container for a conversation, creating it if needed."""
    name = _container_name(conversation_id)
    rc, _, _ = await _docker("inspect", name)
    if rc == 0:
        await _docker("start", name)  # no-op if already running
        return name
    rc, _, _ = await _docker(
        "create",
        "--name", name,
        "--memory", "512m",
        "--memory-swap", "1g",
        "--cpus", "1",
        "--pids-limit", "64",
        "--label", "llmdash=sandbox",
        SANDBOX_IMAGE,
        "tail", "-f", "/dev/null",
    )
    if rc != 0:
        return None
    await _docker("start", name)
    return name


async def _sweep() -> None:
    """Remove idle sandboxes and enforce the pool cap (LRU eviction)."""
    now = time.monotonic()
    victims = [cid for cid, e in _sandboxes.items() if now - e["last_used"] > SANDBOX_IDLE_TTL]
    if len(_sandboxes) > SANDBOX_MAX_POOL:
        ordered = sorted(_sandboxes.items(), key=lambda kv: kv[1]["last_used"])
        victims.extend(cid for cid, _ in ordered[: len(_sandboxes) - SANDBOX_MAX_POOL])
    for cid in victims:
        entry = _sandboxes.pop(cid, None)
        if entry:
            await _docker("rm", "-f", entry["container"])


async def remove_sandbox(conversation_id: int) -> None:
    """Stop and remove a conversation's sandbox (e.g. conversation deleted)."""
    _sandboxes.pop(conversation_id, None)
    name = _container_name(conversation_id)
    rc, _, _ = await _docker("inspect", name)
    if rc == 0:
        await _docker("rm", "-f", name)


async def cleanup_all() -> None:
    """Remove every sandbox container (app shutdown / boot leftovers)."""
    _sandboxes.clear()
    rc, out, _ = await _docker("ps", "-aq", "--filter", "label=llmdash=sandbox")
    if rc == 0:
        ids = [i for i in out.split() if i]
        if ids:
            await _docker("rm", "-f", *ids)


async def _sweeper_loop() -> None:
    while True:
        await asyncio.sleep(300)
        try:
            await _sweep()
        except Exception:
            pass


def start_sweeper() -> asyncio.Task:
    return asyncio.create_task(_sweeper_loop())


def _format_result(rc: int, stdout: str, stderr: str) -> str:
    if rc == -1 and "timed out" in stderr:
        return "Error: Command timed out"
    output = stdout
    if stderr:
        if output:
            output += "\n--- stderr ---\n"
        output += stderr
    if not output.strip():
        output = "(no output)"
    return _cap_output(output)


async def run_in_alpine(command: str, timeout: int = 30, conversation_id: int = 0) -> str:
    if not is_docker_available():
        return "Error: Docker is not available. Sandbox tool requires Docker."

    try:
        timeout = int(timeout)
    except (TypeError, ValueError):
        timeout = 30
    timeout = min(max(timeout, 1), MAX_SANDBOX_TIMEOUT)

    if not conversation_id:
        # No conversation context (defensive fallback): one-shot container.
        rc, out, err = await _docker(
            "run", "--rm",
            "--memory", "512m", "--memory-swap", "1g", "--cpus", "1", "--pids-limit", "64",
            "alpine:latest", "sh", "-c", command,
            timeout=timeout,
        )
        return _format_result(rc, out, err)

    err = await _ensure_sandbox_image()
    if err:
        return err
    container = await _get_or_create_container(conversation_id)
    if not container:
        return "Error: could not start the sandbox container for this conversation."
    _sandboxes[conversation_id] = {"container": container, "last_used": time.monotonic()}

    rc, out, err = await _docker("exec", container, "sh", "-c", command, timeout=timeout)
    return _format_result(rc, out, err)
