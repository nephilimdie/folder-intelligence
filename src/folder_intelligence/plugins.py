"""Caricamento automatico dei plugin (comandi ed estrattori).

Ogni file .py dentro la cartella del pacchetto (es. `commands/`) viene importato e
ogni sottoclasse concreta della classe base con un attributo `name` viene registrata.
Si possono aggiungere plugin personali anche in `~/.folder_intelligence/<cartella>/`.
"""

from __future__ import annotations

import importlib
import importlib.util
import inspect
import logging
import pkgutil
import sys
from types import ModuleType
from typing import TypeVar

from .audit import app_home

log = logging.getLogger(__name__)
T = TypeVar("T")


def _classes_in(module: ModuleType, base: type[T]) -> list[type[T]]:
    found = []
    for _, obj in inspect.getmembers(module, inspect.isclass):
        if (issubclass(obj, base) and obj is not base and not inspect.isabstract(obj)
                and obj.__module__ == module.__name__ and getattr(obj, "name", "")):
            found.append(obj)
    return found


def load(package: str, base: type[T], user_dir: str | None = None) -> list[T]:
    """Istanzia tutti i plugin del pacchetto `package` (+ eventuale cartella utente)."""
    pkg = importlib.import_module(package)
    classes: list[type[T]] = []
    for info in pkgutil.iter_modules(pkg.__path__):
        if info.name.startswith("_") or info.name == "base":
            continue
        try:
            classes += _classes_in(importlib.import_module(f"{package}.{info.name}"), base)
        except Exception as e:  # un plugin rotto non deve bloccare l'app
            log.warning("Plugin %s.%s non caricato: %s", package, info.name, e)

    if user_dir:
        folder = app_home() / user_dir
        for file in sorted(folder.glob("*.py")) if folder.is_dir() else []:
            mod_name = f"fi_user_{user_dir}_{file.stem}"
            try:
                spec = importlib.util.spec_from_file_location(mod_name, file)
                module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
                sys.modules[mod_name] = module
                spec.loader.exec_module(module)  # type: ignore[union-attr]
                classes += _classes_in(module, base)
            except Exception as e:
                log.warning("Plugin utente %s non caricato: %s", file, e)

    seen: dict[str, T] = {}
    for cls in classes:
        seen[cls.name] = cls()  # type: ignore[attr-defined]  # l'ultimo vince: l'utente può sovrascrivere
    return list(seen.values())
