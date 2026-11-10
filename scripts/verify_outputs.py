"""Independently reconcile delivered CSV, workbook, run evidence and PNGs."""

from __future__ import annotations

import csv
import hashlib
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean

from openpyxl import load_workbook
from PIL import Image, ImageStat

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output"
FIELDS = ["title", "category", "price_gbp", "availability_count", "rating", "upc",
          "product_type", "price_excl_tax", "price_incl_tax", "tax", "num_reviews",
          "description", "image_url", "product_url", "scraped_at"]
MONEY = {"price_gbp", "price_excl_tax", "price_incl_tax", "tax"}
INTEGERS = {"availability_count", "rating", "num_reviews"}


def main():
    stats = json.loads((OUTPUT / "run_summary.json").read_text(encoding="utf-8"))
    assert stats["status"] == "success", stats
    assert stats["listing_pages_fetched"] == 50
    assert stats["detail_pages_fetched"] == stats["product_links_found"] == 1000
    assert stats["rows"] == 1000
    assert stats["max_pages"] is None and stats["pagination_exhausted"]
    assert not stats["errors"]
    with (OUTPUT / "books.csv").open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames == FIELDS
        rows = list(reader)
    assert len(rows) == len({r["upc"] for r in rows}) == 1000
    assert len({r["product_url"] for r in rows}) == 1000
    groups = defaultdict(list)
    started, ended = map(datetime.fromisoformat, [stats["started_at"], stats["ended_at"]])
    for row in rows:
        for key in MONEY:
            row[key] = float(row[key])
        for key in INTEGERS:
            row[key] = int(row[key])
        assert 1 <= row["rating"] <= 5
        assert row["availability_count"] >= 0 and row["num_reviews"] >= 0
        assert all(row[key] >= 0 for key in MONEY)
        assert row["price_gbp"] == row["price_incl_tax"]
        assert round(row["price_excl_tax"] + row["tax"], 2) == row["price_incl_tax"]
        assert all(row[key] for key in ["title", "category", "upc", "product_type"])
        assert row["image_url"].startswith("https://books.toscrape.com/media/")
        assert row["product_url"].startswith("https://books.toscrape.com/catalogue/")
        stamp = datetime.fromisoformat(row["scraped_at"])
        assert stamp.utcoffset().total_seconds() == 0
        assert started <= stamp <= ended
        groups[row["category"]].append(row)
    workbook = load_workbook(OUTPUT / "books.xlsx", data_only=False)
    assert workbook.sheetnames == ["Books", "Summary"]
    books, summary = workbook["Books"], workbook["Summary"]
    assert books.max_row == 1001 and books.max_column == len(FIELDS)
    assert [c.value for c in books[1]] == FIELDS
    for index, row in enumerate(rows, 2):
        for key, cell in zip(FIELDS, books[index]):
            if key in MONEY:
                assert abs(cell.value - row[key]) < 1e-10
                assert "£" in cell.number_format
            else:
                # An empty CSV description is stored as an empty XLSX cell.
                assert (cell.value if cell.value is not None else "") == row[key]
            if key in MONEY | INTEGERS:
                assert cell.data_type == "n"
            else:
                assert cell.data_type != "f"
    assert summary.max_row == len(groups) + 1
    for category, count, price, rating in summary.iter_rows(min_row=2, values_only=True):
        group = groups[category]
        assert count == len(group)
        assert abs(price - mean(r["price_gbp"] for r in group)) < 1e-10
        assert abs(rating - mean(r["rating"] for r in group)) < 1e-10
    assert sum(summary.cell(r, 2).value for r in range(2, summary.max_row + 1)) == 1000
    for sheet in workbook:
        assert sheet.freeze_panes == "A2"
        assert sheet.auto_filter.ref == sheet.dimensions
        assert all(cell.font.bold for cell in sheet[1])
        assert all(sheet.column_dimensions[cell.column_letter].width >= 12 for cell in sheet[1])
    pytest_text = (OUTPUT / "pytest.txt").read_text(encoding="utf-8")
    assert " passed" in pytest_text and " failed" not in pytest_text and "ERROR" not in pytest_text
    images = []
    for name in ["01_table_preview.png", "02_summary_chart.png", "03_run_log.png"]:
        path = ROOT / "portfolio_images" / name
        with Image.open(path) as image:
            assert image.format == "PNG" and image.size == (1600, 1100)
            assert max(ImageStat.Stat(image.convert("RGB")).stddev) > 10
        images.append(str(path.relative_to(ROOT)))
    files = ["output/books.csv", "output/books.xlsx", "output/run_log.txt",
             "output/run_summary.json", "output/pytest.txt", "tests/fixtures/listing.html",
             "tests/fixtures/detail.html"] + images
    result = {
        "status": "passed", "unique_rows": len(rows), "listing_pages": stats["listing_pages_fetched"],
        "detail_pages": stats["detail_pages_fetched"], "categories": len(groups),
        "csv_xlsx_all_cells_match": True, "category_summaries_match": True,
        "workbook_formatting_passed": True, "image_dimensions": [1600, 1100],
        "empty_descriptions": sum(not r["description"] for r in rows),
        "price_min_gbp": min(r["price_gbp"] for r in rows),
        "price_max_gbp": max(r["price_gbp"] for r in rows),
        "sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in files},
    }
    (OUTPUT / "verification.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key != "sha256"}, indent=2))


if __name__ == "__main__":
    main()
