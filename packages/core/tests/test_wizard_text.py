"""Textes anglais de l'assistant : vrais singuliers et pluriels, nombres."""

from uie_core.wizard.text import count, number, plural


def test_true_singulars_and_plurals_never_value_s() -> None:
    assert count(1, "value") == "1 value"
    assert count(2, "value") == "2 values"
    assert count(0, "row") == "0 rows"
    assert count(1643, "missing product category") == "1,643 missing product categories"
    assert plural("barcode") == "barcodes"
    assert plural("box") == "boxes"
    assert plural("day") == "days"
    assert count(3, "amount", "amounts") == "3 amounts"
    assert number(1643) == "1,643"


def test_wizard_messages_have_no_value_s_and_no_french_quotes() -> None:
    """Chaînes de l'assistant (hors docstrings et commentaires, en français) : jamais « value(s) », jamais de
    guillemets français dans un texte anglais."""
    import io
    import re
    import tokenize
    from pathlib import Path

    import uie_core.wizard as wizard

    found = []
    for path in Path(wizard.__file__).parent.glob("*.py"):
        for t in tokenize.generate_tokens(io.StringIO(path.read_text(encoding="utf-8")).readline):
            if t.type not in (tokenize.STRING, tokenize.FSTRING_MIDDLE):
                continue
            if t.type == tokenize.STRING and t.string.lstrip("rbuRBUfF").startswith(('"""', "'''")):
                continue
            if re.search(r"[«»]|[a-z]\(s\)", t.string):
                found.append(f"{path.name}:{t.start[0]}: {t.string[:60]}")
    assert found == []
