"""Lecture d'un fichier du client : ligne d'en-têtes détectée (titre et lignes vides au-dessus), lignes vides
et lignes de total ignorées ; un fichier ordinaire est lu comme avant."""

from __future__ import annotations

import io

import xlsxwriter
from uie_core.imports import read_table
from uie_core.imports.profile import header_row

GERMAN = (
    "Artikelexport Müller Haus & Garten GmbH – Stand 22.10.2026;;;\r\n"
    ";;;\r\n"
    "Art.-Nr.;Artikelbezeichnung;VK netto;Bestand\r\n"
    "01000;Akku-Bohrschrauber;1.091,60;57\r\n"
    ";;;\r\n"
    "01001;Schraubendreher-Set;16,76;59\r\n"
    ";;;\r\n"
    "Summe;2 Artikel;;116\r\n"
).encode("cp1252")


def test_the_header_row_is_found_below_a_title_and_empty_lines():
    _, df = read_table(GERMAN, "liste.csv")
    assert df.columns == ["Art.-Nr.", "Artikelbezeichnung", "VK netto", "Bestand"]
    # lignes vides retirées, ligne de total retirée, zéros initiaux gardés (texte)
    assert df["Art.-Nr."].to_list() == ["01000", "01001"]
    assert df["VK netto"].to_list() == ["1.091,60", "16,76"]


def test_an_ordinary_file_is_read_as_before():
    _, df = read_table(b"name,price,qty\nTotal care kit,9.90,3\nB,1,2\n", "shop.csv")
    assert df.columns == ["name", "price", "qty"]
    assert df["name"].to_list() == ["Total care kit", "B"]  # « Total… » d'un article complet : gardé
    assert header_row([["", ""], ["a", ""], ["x", "y"]]) == 2
    assert header_row([["only"]]) == 0  # une seule colonne : ligne 1


def test_empty_or_repeated_header_cells_get_unique_names():
    _, df = read_table(b"Report;;\nSKU;;SKU\nA1;x;B1\n", "r.csv")
    assert df.columns == ["SKU", "Column 2", "SKU (2)"]


def test_an_xlsx_with_a_title_row_is_read_from_its_header_row():
    buf = io.BytesIO()
    wb = xlsxwriter.Workbook(buf)
    ws = wb.add_worksheet("Export")
    ws.write_row(0, 0, ["Price list 2026"])
    ws.write_row(2, 0, ["SKU", "Name", "Price"])
    ws.write_row(3, 0, ["A-1", "Oak tray", "39.9"])
    ws.write_row(4, 0, ["Total", "", "39.9"])
    wb.close()
    _, df = read_table(buf.getvalue(), "list.xlsx")
    assert df.columns == ["SKU", "Name", "Price"] and df["SKU"].to_list() == ["A-1"]
