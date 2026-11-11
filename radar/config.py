"""Carga de configuración (YAML) con sobrescrituras desde la línea de comandos."""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATH = ROOT / "config" / "settings.yaml"


class Settings(dict):
    """Diccionario con acceso por atributos y rutas resueltas contra la raíz del proyecto."""

    def __getattr__(self, key: str) -> Any:
        try:
            value = self[key]
        except KeyError as exc:  # pragma: no cover
            raise AttributeError(key) from exc
        return Settings(value) if isinstance(value, dict) else value

    def path(self, key: str) -> Path:
        p = Path(self[key])
        return p if p.is_absolute() else ROOT / p

    def domain_limit(self, domain: str) -> int:
        limits = self["per_domain_limit"]
        return int(limits.get(domain, limits["default"]))


def load(path: str | Path | None = None, overrides: dict | None = None) -> Settings:
    with open(path or DEFAULT_PATH, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    for dotted, value in (overrides or {}).items():
        if value is None:
            continue
        node = data
        *parents, last = dotted.split(".")
        for part in parents:
            node = node.setdefault(part, {})
        node[last] = value
    return Settings(copy.deepcopy(data))
