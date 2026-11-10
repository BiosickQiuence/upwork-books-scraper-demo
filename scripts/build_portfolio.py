"""Render portfolio PNGs exclusively from completed scrape and pytest outputs."""

from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# Keep matplotlib's cache in this project rather than writing to the user's home.
os.environ.setdefault("MPLCONFIGDIR", str(ROOT / ".mplconfig"))
os.environ.setdefault("XDG_CACHE_HOME", str(ROOT / ".mplconfig"))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from PIL import Image, ImageDraw, ImageFont

from scraper import category_summary

SIZE = (1600, 1100)
INK, MUTED, TEAL = "#183747", "#577080", "#147D83"
BG, BORDER = "#F3F7FA", "#DCE6ED"


def font(size: int, *, bold: bool = False, mono: bool = False):
    prop = font_manager.FontProperties(family="DejaVu Sans Mono" if mono else "DejaVu Sans",
                                       weight="bold" if bold else "normal")
    return ImageFont.truetype(font_manager.findfont(prop), size)


def shorten(draw, text: str, width: int, selected_font):
    if draw.textlength(text, font=selected_font) <= width:
        return text
    while text and draw.textlength(text + "…", font=selected_font) > width:
        text = text[:-1]
    return text.rstrip() + "…"


def canvas(title: str, subtitle: str):
    image = Image.new("RGB", SIZE, BG)
    draw = ImageDraw.Draw(image)
    draw.text((65, 34), "PERSONAL DEMO  /  PYTHON WEB SCRAPING", font=font(18, bold=True), fill=TEAL)
    draw.text((65, 79), title, font=font(38, bold=True), fill=INK)
    draw.text((65, 139), subtitle, font=font(23), fill=MUTED)
    return image, draw


def table_preview(rows, stats, target: Path):
    image, draw = canvas(
        f"books.toscrape.com → clean dataset ({len(rows):,} rows)",
        "A preview of 15 catalogue records. Prices are GBP; stock is the available quantity.",
    )
    draw.text((65, 190), f"{stats['listing_pages_fetched']} listing pages     "
              f"{stats['detail_pages_fetched']:,} detail pages     CSV + formatted Excel", font=font(22), fill=TEAL)
    draw.rounded_rectangle((50, 244, 1550, 1025), radius=16, fill="white", outline=BORDER, width=2)
    draw.rounded_rectangle((51, 245, 1549, 304), radius=15, fill="#E8F0F4")
    draw.rectangle((51, 280, 1549, 304), fill="#E8F0F4")
    headings = [(77, "TITLE"), (745, "CATEGORY"), (1035, "PRICE"), (1205, "RATING"), (1450, "STOCK")]
    for x, label in headings:
        draw.text((x, 265), label, font=font(18, bold=True), fill=INK)
    body_font = font(22)
    for index, row in enumerate(rows[:15]):
        top = 305 + index * 47
        if index % 2:
            draw.rectangle((52, top, 1548, top + 47), fill="#F7FAFC")
        y = top + 11
        draw.text((77, y), shorten(draw, row["title"], 630, body_font), font=body_font, fill=INK)
        draw.text((745, y), shorten(draw, row["category"], 260, body_font), font=body_font, fill=MUTED)
        draw.text((1035, y), f"£{row['price_gbp']:.2f}", font=body_font, fill=INK)
        for star in range(5):
            draw.text((1205 + star * 30, y - 1), "★", font=font(26),
                      fill="#A86C0A" if star < row["rating"] else "#C7D5DD")
        draw.text((1460, y), str(row["availability_count"]), font=body_font, fill=INK)
    draw.text((65, 1050), "Source: books.toscrape.com · Personal demo project against a scraping sandbox site",
              font=font(19), fill=MUTED)
    image.save(target)


def summary_chart(rows, stats, target: Path):
    top = category_summary(rows)[:12]
    names = [row["category"] for row in top]
    counts = [row["count"] for row in top]
    prices = [row["avg_price_gbp"] for row in top]
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 16,
                         "axes.edgecolor": BORDER, "axes.labelcolor": INK,
                         "xtick.color": MUTED, "ytick.color": INK})
    fig, (left, right) = plt.subplots(1, 2, figsize=(16, 11), dpi=100, sharey=True)
    fig.patch.set_facecolor(BG)
    fig.subplots_adjust(left=0.205, right=0.93, top=0.76, bottom=0.13, wspace=0.22)
    fig.text(0.04, 0.95, "PERSONAL DEMO  /  CATALOGUE SUMMARY", fontsize=15, color=TEAL, weight="bold")
    fig.text(0.04, 0.89, "The 12 largest book categories", fontsize=32, color=INK, weight="bold")
    fig.text(0.04, 0.84, f"{len(rows):,} unique books across {len(category_summary(rows))} categories. "
             "Average prices are calculated within each category.", fontsize=17, color=MUTED)
    positions = list(range(len(top)))
    count_bars = left.barh(positions, counts, height=0.62, color=TEAL, zorder=3)
    price_bars = right.barh(positions, prices, height=0.62, color="#537FAD", zorder=3)
    left.set_yticks(positions, names)
    left.invert_yaxis()
    left.set_xlim(0, max(counts) * 1.22)
    right.set_xlim(0, max(prices) * 1.26)
    for ax, label in ((left, "Books in category"), (right, "Average price (GBP)")):
        ax.set_facecolor(BG)
        ax.set_title(label, loc="left", fontsize=20, color=INK, pad=24, weight="bold")
        ax.xaxis.grid(True, color=BORDER, zorder=0)
        ax.tick_params(axis="both", length=0, pad=10)
        for spine in ax.spines.values():
            spine.set_visible(False)
    right.tick_params(axis="y", labelleft=False)
    for bar, value in zip(count_bars, counts):
        left.text(value + max(counts) * 0.025, bar.get_y() + bar.get_height()/2,
                  str(value), va="center", fontsize=16, color=INK)
    for bar, value in zip(price_bars, prices):
        right.text(value + max(prices) * 0.025, bar.get_y() + bar.get_height()/2,
                   f"£{value:.2f}", va="center", fontsize=16, color=INK)
    fig.text(0.04, 0.045, "Source: books.toscrape.com · Scraping sandbox · Counts and averages from the completed run",
             fontsize=14, color=MUTED)
    fig.savefig(target, dpi=100, facecolor=BG)
    plt.close(fig)


def run_log(stats, pytest_text: str, target: Path):
    image, draw = canvas("Full crawl and offline tests", "Run summary and pytest output from the saved local execution.")
    draw.rounded_rectangle((60, 205, 1540, 1030), radius=18, fill="white", outline=BORDER, width=2)
    draw.line((61, 260, 1539, 260), fill=BORDER, width=2)
    draw.text((90, 220), "books-scraper / execution record", font=font(20, mono=True), fill=MUTED)
    limit = f" --max-pages {stats['max_pages']}" if stats["max_pages"] is not None else ""
    lines = [
        (f"$ .venv/bin/python scraper.py --delay {stats['delay_seconds']:g}{limit}", TEAL),
        (f"Started (UTC)       {stats['started_at']}", INK),
        (f"Ended (UTC)         {stats['ended_at']}", INK),
        (f"Duration            {stats['duration_seconds']:.3f} seconds", INK),
        (f"Listing pages       {stats['listing_pages_fetched']}", INK),
        (f"Detail pages        {stats['detail_pages_fetched']:,}", INK),
        (f"Unique rows (UPC)   {stats['rows']:,}", INK),
        (f"Duplicates removed  {stats['duplicates_removed']}", INK),
        (f"Retries / errors    {stats['retries']} / {len(stats['errors'])}", INK),
        (f"Robots check        HTTP {stats['robots_status']}", INK),
        (f"Crawl settings      {stats['delay_seconds']:.1f}s gap, ≤{stats['concurrency']} in flight", INK),
        (f"Run status          {stats['status'].upper()}", TEAL),
        ("", INK),
        ("$ .venv/bin/python -m pytest -q", TEAL),
    ]
    lines.extend((line, INK) for line in pytest_text.strip().splitlines())
    y = 294
    for line, color in lines:
        draw.text((90, y), line, font=font(23, mono=True), fill=color)
        y += 40
    if y > 1020:
        raise ValueError("Run output no longer fits the portfolio image")
    draw.text((65, 1050), "Personal demo project against a scraping sandbox site · No client data",
              font=font(19), fill=MUTED)
    image.save(target)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=ROOT / "output")
    parser.add_argument("--image-dir", type=Path, default=ROOT / "portfolio_images")
    args = parser.parse_args()
    stats = json.loads((args.out_dir / "run_summary.json").read_text(encoding="utf-8"))
    if stats["status"] != "success":
        raise ValueError("Portfolio images require a successful real run")
    with (args.out_dir / "books.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        for key in ("price_gbp", "price_excl_tax", "price_incl_tax", "tax"):
            row[key] = float(row[key])
        for key in ("availability_count", "rating", "num_reviews"):
            row[key] = int(row[key])
    if len(rows) != stats["rows"]:
        raise ValueError("CSV rows disagree with run summary")
    pytest_text = (args.out_dir / "pytest.txt").read_text(encoding="utf-8")
    if " passed" not in pytest_text or " failed" in pytest_text or "ERROR" in pytest_text:
        raise ValueError("Portfolio image requires passing real pytest output")
    args.image_dir.mkdir(parents=True, exist_ok=True)
    table_preview(rows, stats, args.image_dir / "01_table_preview.png")
    summary_chart(rows, stats, args.image_dir / "02_summary_chart.png")
    run_log(stats, pytest_text, args.image_dir / "03_run_log.png")
    print("Rendered 3 portfolio PNGs at 1600 × 1100 from real output data.")


if __name__ == "__main__":
    main()
