"""Textes anglais de l'assistant : nombres et vrais singuliers / pluriels (jamais « value(s) »)."""

from __future__ import annotations

import re

_IRREGULAR = {"child": "children", "person": "people", "datum": "data", "is": "are", "was": "were"}


def plural(word: str) -> str:
    """Pluriel anglais du DERNIER mot (« product category » → « product categories »)."""
    head, _, last = word.rpartition(" ")
    lower = last.casefold()
    if lower in _IRREGULAR:
        out = _IRREGULAR[lower]
    elif re.search(r"[^aeiou]y$", lower):
        out = last[:-1] + "ies"
    elif re.search(r"(s|x|z|ch|sh)$", lower):
        out = last + "es"
    else:
        out = last + "s"
    return f"{head} {out}" if head else out


def number(n: int) -> str:
    """1643 → « 1,643 » (séparateur des milliers anglais)."""
    return f"{n:,}"


def count(n: int, singular: str, plural_form: str | None = None) -> str:
    """« 1 row », « 2 rows », « 1,643 product categories »."""
    return f"{number(n)} {singular if n == 1 else plural_form or plural(singular)}"


_FORMS = re.compile(r"\[([^\]|]*)\|([^\]|]*)\]")


def say(template: str, n: int, **values: object) -> str:
    """Phrase accordée au nombre : « [value is|values are] » → la forme du singulier pour 1, sinon celle du
    pluriel ; puis `{n}` (nombre formaté) et les autres valeurs."""
    text = _FORMS.sub(lambda m: m.group(1) if n == 1 else m.group(2), template)
    return text.format(n=number(n), **values)


__all__ = ["count", "number", "plural", "say"]
