"""Polite, same-origin catalogue scraper for the Books to Scrape sandbox."""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import logging
import math
import re
import sys
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from statistics import mean
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

BASE_URL = "https://books.toscrape.com/"
USER_AGENT = "BooksScraperPortfolio/1.0 (personal sandbox demo)"
CONCURRENCY = 3
MAX_RETRIES = 3
RATINGS = {"One": 1, "Two": 2, "Three": 3, "Four": 4, "Five": 5}
FIELDS = [
    "title", "category", "price_gbp", "availability_count", "rating", "upc",
    "product_type", "price_excl_tax", "price_incl_tax", "tax", "num_reviews",
    "description", "image_url", "product_url", "scraped_at",
]
MONEY_FIELDS = ("price_gbp", "price_excl_tax", "price_incl_tax", "tax")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def clean_text(value: str) -> str:
    return " ".join(value.split())


def catalogue_url(page_url: str, href: str) -> str:
    url = urljoin(page_url, href)
    if urlsplit(url).netloc != urlsplit(BASE_URL).netloc or not url.startswith(BASE_URL):
        raise ValueError(f"Refusing a link outside the sandbox: {url}")
    return url


def parse_price(text: str) -> float:
    match = re.fullmatch(r"£\s*(\d+\.\d{2})", clean_text(text))
    if not match:
        raise ValueError(f"Invalid GBP price: {text!r}")
    return float(Decimal(match.group(1)))


def parse_availability(text: str) -> int:
    match = re.fullmatch(r"In stock\s*\((\d+) available\)", clean_text(text))
    if match:
        return int(match.group(1))
    if clean_text(text).lower() == "out of stock":
        return 0
    raise ValueError(f"Invalid stock count: {text!r}")


def parse_rating(classes: list[str]) -> int:
    values = [RATINGS[name] for name in classes if name in RATINGS]
    if len(values) != 1:
        raise ValueError(f"Invalid star-rating classes: {classes!r}")
    return values[0]


def parse_listing(html: str, page_url: str) -> tuple[list[str], str | None]:
    soup = BeautifulSoup(html, "html.parser")
    anchors = soup.select("article.product_pod h3 a[href]")
    if not anchors:
        raise ValueError(f"No book links found on listing page: {page_url}")
    links = [catalogue_url(page_url, str(a["href"])) for a in anchors]
    next_link = soup.select_one("li.next a[href]")
    next_url = catalogue_url(page_url, str(next_link["href"])) if next_link else None
    return links, next_url


def parse_detail(html: str, product_url: str, scraped_at: str | None = None) -> dict:
    soup = BeautifulSoup(html, "html.parser")

    def required(selector: str):
        node = soup.select_one(selector)
        if node is None:
            raise ValueError(f"Missing {selector} on {product_url}")
        return node

    info = {}
    for tr in soup.select("table.table-striped tr"):
        label, value = tr.find("th"), tr.find("td")
        if label is not None and value is not None:
            info[clean_text(label.get_text())] = clean_text(value.get_text())
    needed = ["UPC", "Product Type", "Price (excl. tax)", "Price (incl. tax)",
              "Tax", "Availability", "Number of reviews"]
    missing = [name for name in needed if name not in info]
    if missing:
        raise ValueError(f"Missing product information: {missing}")
    breadcrumbs = soup.select("ul.breadcrumb li")
    if len(breadcrumbs) < 4:
        raise ValueError("Missing category breadcrumb")
    description_heading = soup.select_one("#product_description")
    description = description_heading.find_next_sibling("p") if description_heading else None
    image = required(".carousel-inner img[src]")
    row = {
        "title": clean_text(required(".product_main h1").get_text()),
        "category": clean_text(breadcrumbs[-2].get_text()),
        "price_gbp": parse_price(required(".product_main .price_color").get_text()),
        "availability_count": parse_availability(info["Availability"]),
        "rating": parse_rating(required(".product_main .star-rating").get("class", [])),
        "upc": info["UPC"],
        "product_type": info["Product Type"],
        "price_excl_tax": parse_price(info["Price (excl. tax)"]),
        "price_incl_tax": parse_price(info["Price (incl. tax)"]),
        "tax": parse_price(info["Tax"]),
        "num_reviews": int(info["Number of reviews"]),
        "description": clean_text(description.get_text()) if description else "",
        "image_url": catalogue_url(product_url, str(image["src"])),
        "product_url": catalogue_url(BASE_URL, product_url),
        "scraped_at": scraped_at if scraped_at is not None else utc_now(),
    }
    validate_book(row)
    return row


def validate_book(row: dict) -> None:
    if set(row) != set(FIELDS):
        raise ValueError("Book fields do not match the output schema")
    for name in ("title", "category", "upc", "product_type", "image_url", "product_url", "scraped_at"):
        if not isinstance(row[name], str) or not row[name]:
            raise ValueError(f"{name} must be a nonempty string")
    if not isinstance(row["description"], str):
        raise ValueError("description must be a string")
    for name in MONEY_FIELDS:
        if type(row[name]) is not float or not math.isfinite(row[name]) or row[name] < 0:
            raise ValueError(f"{name} must be a finite, nonnegative float")
    for name in ("availability_count", "rating", "num_reviews"):
        if type(row[name]) is not int or row[name] < 0:
            raise ValueError(f"{name} must be a nonnegative integer")
    if not 1 <= row["rating"] <= 5:
        raise ValueError("rating must be 1–5")
    for name in ("image_url", "product_url"):
        if catalogue_url(BASE_URL, row[name]) != row[name]:
            raise ValueError(f"{name} must be absolute")
    timestamp = datetime.fromisoformat(row["scraped_at"])
    if timestamp.utcoffset() is None or timestamp.utcoffset().total_seconds() != 0:
        raise ValueError("scraped_at must be UTC ISO 8601")
    if row["price_gbp"] != row["price_incl_tax"]:
        raise ValueError("Displayed price and inclusive price disagree")
    if round(row["price_excl_tax"] + row["tax"], 2) != row["price_incl_tax"]:
        raise ValueError("Tax and prices do not reconcile")


def dedupe_books(rows: list[dict]) -> tuple[list[dict], int]:
    seen = set()
    unique = []
    for row in rows:
        validate_book(row)
        if row["upc"] not in seen:
            seen.add(row["upc"])
            unique.append(row)
    return unique, len(rows) - len(unique)


def category_summary(rows: list[dict]) -> list[dict]:
    groups = defaultdict(list)
    for row in rows:
        groups[row["category"]].append(row)
    return [
        {"category": category, "count": len(books),
         "avg_price_gbp": mean(book["price_gbp"] for book in books),
         "avg_rating": mean(book["rating"] for book in books)}
        for category, books in sorted(groups.items(), key=lambda pair: (-len(pair[1]), pair[0]))
    ]


def write_outputs(rows: list[dict], out_dir: Path) -> None:
    with (out_dir / "books.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    workbook = Workbook()
    books = workbook.active
    books.title = "Books"
    books.append(FIELDS)
    for row in rows:
        books.append([row[key] for key in FIELDS])
        # Keep scraped text as text, including any leading equals sign or numeric UPC.
        for cell in books[books.max_row]:
            if isinstance(cell.value, str):
                cell.data_type = "s"
    summary = workbook.create_sheet("Summary")
    summary.append(["Category", "Book count", "Average price (GBP)", "Average rating"])
    for row in category_summary(rows):
        summary.append(list(row.values()))
    for sheet in (books, summary):
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        sheet.sheet_view.showGridLines = False
        sheet.row_dimensions[1].height = 32
        sheet.print_title_rows = "1:1"
        for cell in sheet[1]:
            cell.font = Font(name="Calibri", bold=True, color="FFFFFF", size=11)
            cell.fill = PatternFill("solid", fgColor="153B50")
            cell.alignment = Alignment(vertical="center", wrap_text=True)
        for cells in sheet.iter_rows(min_row=2):
            for cell in cells:
                cell.font = Font(name="Calibri", size=11, color="203544")
                cell.alignment = Alignment(vertical="center")
                if cell.row % 2 == 0:
                    cell.fill = PatternFill("solid", fgColor="F1F6F8")
            sheet.row_dimensions[cells[0].row].height = 23
    widths = [56, 23, 15, 21, 12, 23, 19, 20, 20, 14, 16, 90, 65, 65, 29]
    for index, width in enumerate(widths, 1):
        books.column_dimensions[get_column_letter(index)].width = width
    for name in MONEY_FIELDS:
        for column in books.iter_cols(min_col=FIELDS.index(name) + 1,
                                      max_col=FIELDS.index(name) + 1, min_row=2):
            for cell in column:
                cell.number_format = '"£"#,##0.00'
    for index, width in enumerate([28, 18, 25, 21], 1):
        summary.column_dimensions[get_column_letter(index)].width = width
    for cells in summary.iter_rows(min_row=2):
        cells[1].number_format = "#,##0"
        cells[2].number_format = '"£"#,##0.00'
        cells[3].number_format = "0.00"
    workbook.save(out_dir / "books.xlsx")


@dataclass
class RunStats:
    started_at: str = field(default_factory=utc_now)
    ended_at: str = ""
    duration_seconds: float = 0.0
    listing_pages_fetched: int = 0
    detail_pages_fetched: int = 0
    product_links_found: int = 0
    rows: int = 0
    duplicates_removed: int = 0
    retries: int = 0
    requests_attempted: int = 0
    robots_status: int | None = None
    robots_note: str = ""
    delay_seconds: float = 0.4
    concurrency: int = CONCURRENCY
    max_pages: int | None = None
    pagination_exhausted: bool = False
    status: str = "running"
    errors: list[str] = field(default_factory=list)


class PoliteClient:
    """Bound concurrency and space all request starts, including retries."""

    def __init__(self, delay: float, stats: RunStats, logger: logging.Logger):
        self.delay, self.stats, self.logger = delay, stats, logger
        self.gate = asyncio.Lock()
        self.slots = asyncio.Semaphore(CONCURRENCY)
        self.last_start: float | None = None
        self.robots: RobotFileParser | None = None
        self.client = httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT}, timeout=httpx.Timeout(30.0, connect=15.0),
            limits=httpx.Limits(max_connections=CONCURRENCY, max_keepalive_connections=CONCURRENCY),
            follow_redirects=False,
        )

    async def get(self, url: str, *, robots_request: bool = False) -> httpx.Response:
        catalogue_url(BASE_URL, url)
        if not robots_request and (self.robots is None or not self.robots.can_fetch(USER_AGENT, url)):
            raise ValueError(f"robots.txt does not permit fetching {url}")
        for attempt in range(MAX_RETRIES + 1):
            response = None
            try:
                async with self.slots:
                    async with self.gate:
                        if self.last_start is not None:
                            await asyncio.sleep(max(0.0, self.delay - (time.monotonic() - self.last_start)))
                        self.last_start = time.monotonic()
                        self.stats.requests_attempted += 1
                    response = await self.client.get(url)
                if response.status_code == 429 or 500 <= response.status_code <= 599:
                    response.raise_for_status()
                if robots_request and response.status_code in (404, 410):
                    return response
                response.raise_for_status()
                return response
            except (httpx.TimeoutException, httpx.TransportError, httpx.HTTPStatusError) as exc:
                retryable = response is None or response.status_code == 429 or response.status_code >= 500
                if not retryable or attempt == MAX_RETRIES:
                    raise
                backoff = float(2 ** attempt)
                if response is not None and response.headers.get("Retry-After", "").isdigit():
                    backoff = max(backoff, float(response.headers["Retry-After"]))
                self.stats.retries += 1
                self.logger.warning("Retry %s/%s after %.1fs for %s (%s)",
                                    attempt + 1, MAX_RETRIES, backoff, url, type(exc).__name__)
                await asyncio.sleep(backoff)
        raise RuntimeError("Unreachable retry state")

    async def check_robots(self, out_dir: Path) -> None:
        url = urljoin(BASE_URL, "robots.txt")
        response = await self.get(url, robots_request=True)
        self.stats.robots_status = response.status_code
        (out_dir / "robots.txt").write_bytes(response.content)
        (out_dir / "robots_headers.txt").write_text(
            f"URL: {url}\nChecked at: {utc_now()}\nHTTP status: {response.status_code}\n"
            + "\n".join(f"{key}: {value}" for key, value in response.headers.items()) + "\n",
            encoding="utf-8",
        )
        self.robots = RobotFileParser(url)
        if response.status_code in (404, 410):
            self.robots.parse([])
            self.stats.robots_note = f"HTTP {response.status_code}: no published robots.txt rules."
        else:
            self.robots.parse(response.text.splitlines())
            self.stats.robots_note = "Published robots.txt rules checked before crawling."
            crawl_delay = self.robots.crawl_delay(USER_AGENT)
            if crawl_delay is not None:
                self.delay = max(self.delay, float(crawl_delay))
                self.stats.delay_seconds = self.delay
        self.logger.info("Robots check: %s", self.stats.robots_note)


async def crawl(args: argparse.Namespace, stats: RunStats, logger: logging.Logger) -> list[dict]:
    client = PoliteClient(args.delay, stats, logger)
    try:
        await client.check_robots(args.out_dir)
        next_url = urljoin(BASE_URL, "catalogue/page-1.html")
        visited = set()
        product_urls = []
        while next_url is not None and (args.max_pages is None or len(visited) < args.max_pages):
            if next_url in visited:
                raise ValueError(f"Pagination loop detected: {next_url}")
            response = await client.get(next_url)
            html = response.content.decode("utf-8")
            links, following = parse_listing(html, next_url)
            visited.add(next_url)
            stats.listing_pages_fetched += 1
            product_urls.extend(links)
            logger.info("Listing page %d: %d books; %s", len(visited), len(links), next_url)
            next_url = following
        stats.pagination_exhausted = next_url is None
        stats.product_links_found = len(product_urls)
        results: list[dict | None] = [None] * len(product_urls)
        queue = asyncio.Queue()
        for index, url in enumerate(product_urls):
            queue.put_nowait((index, url))

        async def worker():
            while not queue.empty():
                index, url = queue.get_nowait()
                try:
                    response = await client.get(url)
                    stats.detail_pages_fetched += 1
                    results[index] = parse_detail(response.content.decode("utf-8"), url)
                    if stats.detail_pages_fetched % 50 == 0:
                        logger.info("Detail progress: %d/%d", stats.detail_pages_fetched, len(product_urls))
                except (httpx.HTTPError, ValueError, UnicodeError) as exc:
                    message = f"{url}: {type(exc).__name__}: {exc}"
                    stats.errors.append(message)
                    logger.error(message)
                finally:
                    queue.task_done()

        await asyncio.gather(*(worker() for _ in range(CONCURRENCY)))
        return [row for row in results if row is not None]
    finally:
        await client.client.aclose()


def positive_int(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return value


def nonnegative_float(text: str) -> float:
    value = float(text)
    if not math.isfinite(value) or value < 0:
        raise argparse.ArgumentTypeError("must be finite and nonnegative")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-pages", type=positive_int, default=None,
                        help="stop after this many listing pages (default: follow next to the end)")
    parser.add_argument("--delay", type=nonnegative_float, default=0.4,
                        help="minimum seconds between all request starts (default: 0.4)")
    parser.add_argument("--out-dir", type=Path, default=Path("output"), help="output folder")
    args = parser.parse_args(argv)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("books_scraper")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    formatter = logging.Formatter("%(asctime)sZ %(levelname)s %(message)s", "%Y-%m-%dT%H:%M:%S")
    formatter.converter = time.gmtime
    for handler in (logging.FileHandler(args.out_dir / "run_log.txt", mode="w", encoding="utf-8"),
                    logging.StreamHandler(sys.stdout)):
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    stats = RunStats(delay_seconds=args.delay, max_pages=args.max_pages)
    start = time.monotonic()
    logger.info("START %s; delay=%.2fs; concurrency=%d; max_pages=%s",
                stats.started_at, args.delay, CONCURRENCY, args.max_pages or "all")
    try:
        parsed = asyncio.run(crawl(args, stats, logger))
        rows, stats.duplicates_removed = dedupe_books(parsed)
        stats.rows = len(rows)
        write_outputs(rows, args.out_dir)
        if stats.errors:
            stats.status = "failed"
        else:
            stats.status = "success"
    except (httpx.HTTPError, ValueError, OSError) as exc:
        stats.status = "failed"
        stats.errors.append(f"{type(exc).__name__}: {exc}")
        logger.exception("Scrape failed")
    finally:
        stats.ended_at = utc_now()
        stats.duration_seconds = round(time.monotonic() - start, 3)
        logger.info("END %s; status=%s", stats.ended_at, stats.status)
        logger.info("RUN SUMMARY")
        logger.info("Listing pages fetched: %d", stats.listing_pages_fetched)
        logger.info("Detail pages fetched: %d", stats.detail_pages_fetched)
        logger.info("Unique rows: %d", stats.rows)
        logger.info("Duplicates removed (UPC): %d", stats.duplicates_removed)
        logger.info("Retries: %d; Errors: %d", stats.retries, len(stats.errors))
        logger.info("Duration: %.3f seconds", stats.duration_seconds)
        (args.out_dir / "run_summary.json").write_text(
            json.dumps(asdict(stats), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        for handler in logger.handlers:
            handler.close()
        logger.handlers.clear()
    return 0 if stats.status == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
