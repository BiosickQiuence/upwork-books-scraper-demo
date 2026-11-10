"""Check numeric/text preservation and the useful spreadsheet features."""

import csv
from pathlib import Path

from openpyxl import load_workbook

from scraper import FIELDS, parse_detail, write_outputs


def test_csv_and_workbook_preserve_types_and_identifiers(tmp_path):
    html = (Path(__file__).parent / "fixtures" / "detail.html").read_text(encoding="utf-8")
    row = parse_detail(html, "https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html")
    row["upc"] = "0000123456789012"
    row["title"] = "=A title that must remain text"
    row["description"] = 'A description with a comma, Unicode £ and "quotes".'
    write_outputs([row], tmp_path)
    with (tmp_path / "books.csv").open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames == FIELDS
        csv_row = next(reader)
    assert csv_row["upc"] == row["upc"]
    assert csv_row["description"] == row["description"]
    wb = load_workbook(tmp_path / "books.xlsx", data_only=False)
    assert wb.sheetnames == ["Books", "Summary"]
    books, summary = wb["Books"], wb["Summary"]
    assert books["A2"].value == row["title"] and books["A2"].data_type == "s"
    assert books["F2"].value == row["upc"] and books["F2"].data_type == "s"
    assert books["C2"].value == row["price_gbp"] and books["C2"].data_type == "n"
    assert "£" in books["C2"].number_format
    assert list(summary.values)[1] == ("Poetry", 1, 51.77, 3)
    for sheet in wb:
        assert sheet.freeze_panes == "A2"
        assert sheet.auto_filter.ref == sheet.dimensions
        assert all(cell.font.bold for cell in sheet[1])
