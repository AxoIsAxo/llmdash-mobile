import asyncio
import tempfile
import os
import json
from typing import Optional

import docker


_docker_available = None


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


async def run_in_alpine(command: str, timeout: int = 30) -> str:
    if not is_docker_available():
        return "Error: Docker is not available. Sandbox tool requires Docker."

    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "run", "--rm", "-i",
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
        return output or "(no output)"
    except asyncio.TimeoutError:
        return "Error: Command timed out"
    except FileNotFoundError:
        return "Error: Docker not found on host. Ensure Docker is installed."
    except Exception as e:
        return f"Error: {str(e)}"


async def run_script_in_alpine(script: str, timeout: int = 30) -> str:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".sh", delete=False) as f:
        f.write("#!/bin/sh\n")
        f.write(script)
        f.flush()
        script_path = f.name

    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "run", "--rm", "-i",
            "-v", f"{script_path}:/tmp/script.sh:ro",
            "alpine:latest",
            "sh", "/tmp/script.sh",
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
        return output or "(no output)"
    except asyncio.TimeoutError:
        return "Error: Command timed out"
    except Exception as e:
        return f"Error: {str(e)}"
    finally:
        try:
            os.unlink(script_path)
        except Exception:
            pass
