"""P11 — auto-discovery of skills.

Skills are discovered by scanning the ``builtins`` package (and the
``marketplace`` package, populated by P12) instead of a hand-maintained list:
every module in the package is imported and every ``Skill`` subclass with a
``name`` is registered. Source/category/scopes come from the skill's own
manifest attributes.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from types import ModuleType

from .base import Skill
from .registry import SkillRegistry, skill_registry


def _discover_module(module: ModuleType, registry: SkillRegistry):
    for _, cls in inspect.getmembers(module, inspect.isclass):
        if cls is Skill or not issubclass(cls, Skill):
            continue
        name = getattr(cls, "name", None)
        if not name or name.startswith("_"):
            continue
        try:
            registry.register(cls())
        except Exception as exc:  # a broken marketplace skill must not kill startup
            import logging
            logging.getLogger(__name__).warning(f"skill discovery: failed to instantiate {cls.__name__}: {exc}")


def _discover_package(package_name: str, registry: SkillRegistry):
    try:
        package = importlib.import_module(package_name)
    except ImportError:
        return
    for mod in pkgutil.iter_modules(package.__path__):
        if mod.name.startswith("_"):
            continue
        try:
            module = importlib.import_module(f"{package_name}.{mod.name}")
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning(f"skill discovery: failed to import {package_name}.{mod.name}: {exc}")
            continue
        _discover_module(module, registry)


def register_builtins() -> SkillRegistry:
    """Register every builtin skill plus any installed marketplace skills."""
    _discover_package("app.skills.builtins", skill_registry)
    _discover_package("app.skills.marketplace", skill_registry)
    return skill_registry
