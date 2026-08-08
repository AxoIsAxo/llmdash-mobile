"""P12 — skill marketplace.

Community skills plug into LLMDash without a code change: an admin/owner
installs a skill from a git repository, the manifest + entry module are
validated, and the skill is copied into the managed ``marketplace`` package
where the P11 loader auto-discovers it. Installed skills are gated by the
review gate (``approved``) and run sandboxed by their declared permission
scopes (P11's ``allowed_scopes`` enforcement).

Trust model: installing runs the skill's code in the server process, so
install/approve/uninstall are owner/admin-only. Manifest validation enforces
that a skill may only request scopes the trusted builtins already use, may
only be gated behind a known entitlement key, and that the shipped entry
module really defines the declared skill.

IMPORTANT: "is a marketplace skill" is derived from STATE membership
(``load_state()``), never from the class ``source`` attribute — a repo could
omit ``source`` and the class would default to ``builtin`` after a restart.
The loader additionally stamps ``source="marketplace"`` for modules found in
the marketplace package, but the gates key off the state file.
"""

from __future__ import annotations

import inspect
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from sqlalchemy import delete as sa_delete

from ..database import async_session, UserSkill
from ..entitlements import DEFAULT_ENTITLEMENTS
from .base import Skill
from .registry import SkillRegistry, skill_registry

logger = logging.getLogger(__name__)

MARKETPLACE_DIR = Path(__file__).parent / "marketplace"
STATE_FILE = MARKETPLACE_DIR / "state.json"
CATALOG_FILE = Path(__file__).parent.parent / "marketplace_catalog.json"

NAME_RE = re.compile(r"^[a-z0-9_]{1,64}$")
GIT_TIMEOUT = 60

# Scopes the marketplace knows about; a skill may only request scopes that
# the trusted builtins already use (enforced below via _allowed_scopes).
KNOWN_SCOPES = {"web", "git", "files", "sandbox", "theme", "render", "proton"}


def _allowed_scopes(registry: SkillRegistry) -> set[str]:
    return registry.builtin_scope_union()


# ---------------------------------------------------------------------------
# State (on-disk JSON next to the managed marketplace package)
# ---------------------------------------------------------------------------

# Set by load_state() when the state file exists but is unreadable — the
# gates then FAIL CLOSED: every skill found on disk counts as installed and
# unapproved, so nothing leaks to regular users until the owner fixes the file.
_state_corrupt = False


def load_state() -> dict:
    """name -> {version, source_url, approved, installed_at}."""
    global _state_corrupt
    _state_corrupt = False
    if not STATE_FILE.exists():
        return {}
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("state.json must be an object")
        return data
    except (ValueError, OSError) as exc:
        _state_corrupt = True
        logger.error("marketplace: state.json corrupt (%s) — failing closed", exc)
        return {}


def _installed_from_disk() -> set[str]:
    """Fallback for corrupt state: skill dirs/files present in the package."""
    if not MARKETPLACE_DIR.is_dir():
        return set()
    out = set()
    for p in MARKETPLACE_DIR.iterdir():
        if p.is_dir() and NAME_RE.match(p.name):
            out.add(p.name)
        elif p.is_file() and p.name.endswith(".py") and p.name != "__init__.py" and NAME_RE.match(p.name[:-3]):
            out.add(p.name[:-3])
    return out


def save_state(state: dict):
    if not state:
        STATE_FILE.unlink(missing_ok=True)
        return
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def read_catalog() -> dict:
    if not CATALOG_FILE.exists():
        return {"schema": "llmdash-marketplace-catalog/1", "entries": []}
    try:
        data = json.loads(CATALOG_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"entries": []}
    except (ValueError, OSError):
        return {"entries": []}


def installed_names() -> set[str]:
    """Names of installed marketplace skills — the AUTHORITY for what is
    a marketplace skill (see module docstring). Fails closed on corrupt state."""
    if _state_corrupt:
        return _installed_from_disk()
    return set(load_state().keys())


def approved_names() -> set[str]:
    if _state_corrupt:
        return set()  # fail closed: nothing is approved until the state is fixed
    return {name for name, info in load_state().items() if info.get("approved")}


def unapproved_names(registry: SkillRegistry | None = None) -> set[str]:
    """Installed-but-not-approved marketplace skills still in the registry."""
    registry = registry or skill_registry
    installed = installed_names()
    approved = approved_names()
    return {name for name in installed if name not in approved and registry.get(name) is not None}


def is_marketplace_visible(name: str, role: str) -> bool:
    """Review gate: owner/admin see everything; regular users only see
    approved marketplace skills (builtins are always visible)."""
    if role in ("owner", "admin"):
        return True
    if name not in installed_names():
        return True  # not a marketplace skill
    return name in approved_names()


def list_installed(registry: SkillRegistry | None = None) -> list[dict]:
    """Installed marketplace skills: state + live manifest from the registry."""
    registry = registry or skill_registry
    state = load_state()
    out = []
    for name in sorted(installed_names()):
        info = state.get(name, {})
        skill = registry.get(name)
        manifest = skill.to_manifest() if skill else {"name": name, "description": "", "category": "general", "scopes": [], "entitlement": None}
        out.append({
            "name": name,
            "version": info.get("version") or manifest.get("version", ""),
            "author": manifest.get("author", ""),
            "description": manifest.get("description", ""),
            "category": manifest.get("category", "general"),
            "scopes": manifest.get("scopes", []),
            "entitlement": manifest.get("entitlement"),
            "source_url": info.get("source_url", ""),
            "approved": bool(info.get("approved", False)),
            "installed_at": info.get("installed_at", ""),
        })
    return out


# ---------------------------------------------------------------------------
# Install / uninstall / approve
# ---------------------------------------------------------------------------

def _validate_manifest(manifest: dict, registry: SkillRegistry) -> None:
    errors = []
    name = manifest.get("name")
    if not isinstance(name, str) or not NAME_RE.match(name):
        errors.append("name must match ^[a-z0-9_]{1,64}$")
    if registry.get(name):
        errors.append(f"skill '{name}' already exists")
    if name in load_state():
        errors.append(f"skill '{name}' is already installed")
    for field in ("version", "author", "description"):
        if not isinstance(manifest.get(field), str) or not manifest[field].strip():
            errors.append(f"missing or invalid '{field}'")
    if not isinstance(manifest.get("category"), str) or not manifest["category"].strip():
        errors.append("missing or invalid 'category'")
    scopes = manifest.get("scopes")
    if not isinstance(scopes, list) or not all(isinstance(s, str) for s in scopes):
        errors.append("'scopes' must be a list of strings")
    else:
        unknown = set(scopes) - KNOWN_SCOPES
        if unknown:
            errors.append(f"unknown scopes: {sorted(unknown)}")
        extra = set(scopes) - _allowed_scopes(registry)
        if extra:
            errors.append(f"scopes not used by builtins (not allowed): {sorted(extra)}")
    entitlement = manifest.get("entitlement")
    if entitlement is not None and entitlement not in DEFAULT_ENTITLEMENTS:
        errors.append(f"unknown entitlement key: {entitlement}")
    if not isinstance(manifest.get("input_schema"), dict):
        errors.append("'input_schema' must be an object")
    entry = manifest.get("entry")
    if not isinstance(entry, str) or not entry.strip() or entry.startswith(".") or "/" in entry or "\\" in entry or not entry.endswith(".py"):
        errors.append("'entry' must be a flat .py module filename")
    if errors:
        raise ValueError("; ".join(errors))


def _find_skill_class(module, name: str):
    for _, cls in inspect.getmembers(module, inspect.isclass):
        if cls is Skill or not issubclass(cls, Skill):
            continue
        if getattr(cls, "name", None) == name and cls.__module__ == module.__name__:
            return cls
    return None


def _purge_module(name: str):
    """Drop the installed module (+ its submodules) from sys.modules so
    re-installs and uninstalls never see stale state."""
    prefix = f"app.skills.marketplace.{name}"
    for mod in [m for m in list(sys.modules) if m == prefix or m.startswith(prefix + ".")]:
        sys.modules.pop(mod, None)


def install_from_url(url: str, registry: SkillRegistry | None = None) -> dict:
    """Clone a marketplace skill repo, validate manifest + entry, install it.

    Sync/blocking (git subprocess) — call via asyncio.to_thread from the API.
    Raises ValueError with a safe-to-surface message on any failure."""
    registry = registry or skill_registry
    url = (url or "").strip()
    if not url.startswith(("https://", "http://", "file://", "git@")):
        raise ValueError("install URL must be https://, file:// or git@ssh")

    tmp = Path(tempfile.mkdtemp(prefix="llmdash_market_"))
    try:
        proc = subprocess.run(
            ["git", "clone", "--depth", "1", "--quiet", url, str(tmp)],
            capture_output=True, text=True, timeout=GIT_TIMEOUT,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
        if proc.returncode != 0:
            raise ValueError(f"git clone failed: {(proc.stderr or proc.stdout).strip()[:300]}")

        manifest_file = tmp / "manifest.json"
        if not manifest_file.exists():
            raise ValueError("repository has no manifest.json at its root")
        try:
            manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            raise ValueError(f"manifest.json is not valid JSON: {exc}")

        _validate_manifest(manifest, registry)

        name = manifest["name"]
        entry_file = tmp / manifest["entry"]
        if not entry_file.is_file():
            raise ValueError(f"entry module '{manifest['entry']}' not found in repository")

        # Install as a package dir so multi-file skills and relative imports
        # work: marketplace/<name>/__init__.py (the entry) + sibling modules.
        dest_dir = MARKETPLACE_DIR / name
        if dest_dir.exists():
            raise ValueError(f"a directory for skill '{name}' already exists in the marketplace")
        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(entry_file, dest_dir / "__init__.py")
            for other in sorted(tmp.glob("*.py")):
                if other.name in ("__init__.py", manifest["entry"]):
                    continue
                shutil.copy2(other, dest_dir / other.name)

            import importlib
            _purge_module(name)
            installed_module = importlib.import_module(f"app.skills.marketplace.{name}")
            installed_cls = _find_skill_class(installed_module, name)
            if installed_cls is None:
                raise ValueError("installed module does not define the Skill subclass declared in the manifest")
            if getattr(installed_cls, "version", None) != manifest.get("version"):
                raise ValueError("skill class version does not match manifest version")
            if set(getattr(installed_cls, "scopes", ())) != set(manifest.get("scopes", [])):
                raise ValueError("skill class scopes do not match manifest scopes")
            if getattr(installed_cls, "entitlement", None) != manifest.get("entitlement"):
                raise ValueError("skill class entitlement does not match manifest entitlement")
            installed_cls.source = "marketplace"  # in-memory stamp; the loader re-stamps on restart
            skill = installed_cls()
        except Exception:
            shutil.rmtree(dest_dir, ignore_errors=True)
            raise

        registry.register(skill)

        state = load_state()
        state[name] = {
            "version": manifest["version"],
            "source_url": url,
            "approved": False,  # review gate: owner must approve before users see it
            "installed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        save_state(state)
        logger.info("marketplace: installed skill %s from %s", name, url)
        return {
            "name": name,
            "version": manifest["version"],
            "source_url": url,
            "approved": False,
            "manifest": manifest,
        }
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


async def uninstall(name: str, registry: SkillRegistry | None = None) -> None:
    """Remove the skill's code + state + per-user rows instantly."""
    registry = registry or skill_registry
    state = load_state()
    if name not in state:
        raise ValueError(f"'{name}' is not an installed marketplace skill")
    shutil.rmtree(MARKETPLACE_DIR / name, ignore_errors=True)
    (MARKETPLACE_DIR / f"{name}.py").unlink(missing_ok=True)  # legacy flat installs
    state.pop(name, None)
    save_state(state)
    registry.unregister(name)
    _purge_module(name)
    async with async_session() as sess:
        await sess.execute(sa_delete(UserSkill).where(UserSkill.skill_name == name))
        await sess.commit()
    logger.info("marketplace: uninstalled skill %s", name)


def approve(name: str) -> None:
    state = load_state()
    if name not in state:
        raise ValueError(f"'{name}' is not an installed marketplace skill")
    state[name]["approved"] = True
    save_state(state)
    logger.info("marketplace: approved skill %s", name)
