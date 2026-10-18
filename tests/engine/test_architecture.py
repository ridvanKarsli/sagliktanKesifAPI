"""Motorun katman kuralı: bir modül yalnızca kendi katmanından ya da daha alt katmanlardan içe aktarabilir.

    core → sources → catalog / extraction → scoring → evaluation → insights / health → pipeline

Motor sunucuyu (`kesif_server`) hiçbir zaman içe aktarmaz. Yeni bir modül eklendiğinde bu test, bağımlılığın yanlış
yöne kurulmasını (ör. puanlamanın rapora bağlanması) ilk anda yakalar.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ENGINE = Path(__file__).resolve().parents[2] / "src" / "kesif_engine"

LAYERS = {
    "core": 0,
    "sources": 1,
    "catalog": 2,
    "extraction": 2,
    "scoring": 3,
    "evaluation": 4,
    "insights": 5,
    "health": 5,
    "pipeline": 6,
}


def _layer_of(module: str) -> str:
    parts = module.split(".")
    return parts[1] if len(parts) > 1 else ""


def _imports(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            if node.module == "kesif_engine":
                for alias in node.names:
                    yield f"kesif_engine.{alias.name}"
            else:
                yield node.module
        elif isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom) and node.level > 0:
            yield "<göreli>"


MODULES = sorted(p for p in ENGINE.rglob("*.py") if p.name != "__init__.py" or p.parent.name == "pipeline")


@pytest.mark.parametrize("path", MODULES, ids=lambda p: str(p.relative_to(ENGINE)))
def test_layer_direction(path):
    rel = path.relative_to(ENGINE)
    own = rel.parts[0].removesuffix(".py")
    assert own in LAYERS, f"{rel}: katmanı tanımsız; LAYERS sözlüğüne ekleyin"
    for target in _imports(path):
        assert target != "<göreli>", f"{rel}: göreli içe aktarma yerine mutlak yol kullanın"
        assert not target.startswith("kesif_server"), f"{rel}: motor sunucuyu içe aktaramaz ({target})"
        if not target.startswith("kesif_engine."):
            continue
        dep = _layer_of(target)
        assert dep in LAYERS, f"{rel}: bilinmeyen katman {target}"
        assert LAYERS[dep] <= LAYERS[own], f"{rel} ({own}) üst katmana bağımlı: {target} ({dep})"


def test_every_module_is_in_a_layer():
    top = {p.name.removesuffix(".py") for p in ENGINE.iterdir() if (p.is_dir() and (p / "__init__.py").exists()) or p.suffix == ".py"}
    top -= {"__init__", "__pycache__", "resources"}
    assert top <= set(LAYERS), f"katmansız paket/modül: {sorted(top - set(LAYERS))}"
