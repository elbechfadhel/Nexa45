"""Frontière du Core (ACCOUNTING phase 4) : la préparation d'un format cible, le moteur de mapping et les
exports ne connaissent aucun système ni format concret. Ni identifiant ni chaîne de caractères du CODE
(docstrings explicatives exclues) ne nomme DATEV, Buchungsstapel, un plan comptable (SKR), une clé de
taxe DATEV, une localisation Odoo ou Odoo : tout cela vit dans les domain packs (YAML) et les
connecteurs."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "uie_core"
MODULES = ["accounting", "mapping", "exports"]
FORBIDDEN = (
    "datev",
    "buchungsstapel",
    "debitoren",
    "kreditoren",
    "skr0",
    "bu-schl",
    "buschl",
    "l10n",
    "odoo",
    "lexware",
    "amazon",
)


def _docstrings(tree: ast.AST) -> set[int]:
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                ids.add(id(body[0].value))
    return ids


def _texts(tree: ast.AST) -> list[str]:
    skip = _docstrings(tree)
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            out.append(node.id)
        elif isinstance(node, ast.Attribute):
            out.append(node.attr)
        elif isinstance(node, ast.FunctionDef | ast.ClassDef | ast.AsyncFunctionDef):
            out.append(node.name)
        elif isinstance(node, ast.arg):
            out.append(node.arg)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip:
            out.append(node.value)
    return out


@pytest.mark.parametrize("module", MODULES)
def test_core_module_names_no_concrete_system_or_format(module):
    files = sorted((SRC / module).rglob("*.py"))
    assert files
    for py in files:
        for text in _texts(ast.parse(py.read_text(encoding="utf-8"))):
            low = text.lower()
            assert not any(word in low for word in FORBIDDEN), f"{py.relative_to(SRC)}: {text!r}"
