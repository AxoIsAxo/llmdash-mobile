import asyncio

import docker

_docker_available = None

# Hard caps so a misbehaving model (or prompt) can't hang the worker or the
# Docker daemon. The model-supplied timeout is clamped to this.
MAX_SANDBOX_TIMEOUT = 120
MAX_OUTPUT_CHARS = 100_000


def is_docker_available() -> bool:
    global _docker_available
    if _docker_available is not None:
        return _docker_available
    try:
        client = docker.from_env()
        client.ping()
        _docker_available = True
        return True
    except Exception:
        _docker_available = False
        return False


def _cap_output(output: str) -> str:
    if len(output) > MAX_OUTPUT_CHARS:
        return output[:MAX_OUTPUT_CHARS] + f"\n\n[Output truncated at {MAX_OUTPUT_CHARS} chars]"
    return output


async def run_in_alpine(command: str, timeout: int = 30) -> str:
    if not is_docker_available():
        return "Error: Docker is not available. Sandbox tool requires Docker."

    try:
        timeout = int(timeout)
    except (TypeError, ValueError):
        timeout = 30
    timeout = min(max(timeout, 1), MAX_SANDBOX_TIMEOUT)

    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "run", "--rm", "-i",
            "--memory", "512m",
            "--memory-swap", "1g",
            "--cpus", "1",
            "--pids-limit", "64",
            "alpine:latest",
            "sh", "-c", command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        output = ""
        if stdout:
            output += stdout.decode("utf-8", errors="replace")
        if stderr:
            if output:
                output += "\n--- stderr ---\n"
            output += stderr.decode("utf-8", errors="replace")
        return _cap_output(output or "(no output)")
    except asyncio.TimeoutError:
        return "Error: Command timed out"
    except FileNotFoundError:
        return "Error: Docker not found on host. Ensure Docker is installed."
    except Exception as e:
        return f"Error: {str(e)}"
