"""Offline parser checks against real, saved sandbox HTML."""

from copy import deepcopy
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from scraper import (
    FIELDS, dedupe_books, parse_availability, parse_detail, parse_listing,
    parse_price, parse_rating, validate_book,
)

FIXTURES = Path(__file__).parent / "fixtures"
LISTING_URL = "https://books.toscrape.com/catalogue/page-1.html"
DETAIL_URL = "https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html"
STAMP = "2026-11-10T05:00:00+00:00"


@pytest.fixture
def detail_html():
    return (FIXTURES / "detail.html").read_text(encoding="utf-8")


@pytest.fixture
def book(detail_html):
    return parse_detail(detail_html, DETAIL_URL, STAMP)


def test_listing_links_and_next():
    html = (FIXTURES / "listing.html").read_text(encoding="utf-8")
    links, following = parse_listing(html, LISTING_URL)
    assert len(links) == 20
    assert len(set(links)) == 20
    assert links[0] == DETAIL_URL
    assert following == "https://books.toscrape.com/catalogue/page-2.html"


def test_final_page_stops_pagination():
    soup = BeautifulSoup((FIXTURES / "listing.html").read_text(encoding="utf-8"), "html.parser")
    soup.select_one("li.next").decompose()
    assert parse_listing(str(soup), LISTING_URL)[1] is None


def test_listing_rejects_empty_html():
    with pytest.raises(ValueError, match="No book links"):
        parse_listing("<html></html>", LISTING_URL)


def test_listing_rejects_external_links():
    html = '<article class="product_pod"><h3><a href="https://example.org/book">Book</a></h3></article>'
    with pytest.raises(ValueError, match="outside the sandbox"):
        parse_listing(html, LISTING_URL)


def test_all_detail_fields(book):
    assert list(book) == FIELDS
    assert book["title"] == "A Light in the Attic"
    assert book["category"] == "Poetry"
    assert book["price_gbp"] == 51.77
    assert book["price_excl_tax"] == 51.77
    assert book["price_incl_tax"] == 51.77
    assert book["tax"] == 0.0
    assert book["availability_count"] == 22
    assert book["rating"] == 3
    assert book["upc"] == "a897fe39b1053632"
    assert book["product_type"] == "Books"
    assert book["num_reviews"] == 0
    assert book["description"].startswith("It's hard to imagine a world")
    assert "\n" not in book["description"]
    assert book["image_url"] == "https://books.toscrape.com/media/cache/fe/72/fe72f0532301ec28892ae79a629a293c.jpg"
    assert book["product_url"] == DETAIL_URL
    assert book["scraped_at"] == STAMP


def test_missing_optional_description_is_empty(detail_html):
    soup = BeautifulSoup(detail_html, "html.parser")
    soup.select_one("#product_description").decompose()
    assert parse_detail(str(soup), DETAIL_URL)["description"] == ""


@pytest.mark.parametrize("selector", [".product_main h1", ".product_main .star-rating", "table.table-striped", "ul.breadcrumb"])
def test_required_detail_elements_fail_loudly(detail_html, selector):
    soup = BeautifulSoup(detail_html, "html.parser")
    soup.select_one(selector).decompose()
    with pytest.raises(ValueError):
        parse_detail(str(soup), DETAIL_URL)


@pytest.mark.parametrize("text,expected", [("£51.77", 51.77), (" £0.00\n", 0.0), ("£ 10.01", 10.01)])
def test_prices_are_floats(text, expected):
    value = parse_price(text)
    assert type(value) is float
    assert value == expected


@pytest.mark.parametrize("text", ["$4.50", "£nan", "unknown", "£-1.00", "£3.456"])
def test_malformed_prices_rejected(text):
    with pytest.raises(ValueError):
        parse_price(text)


@pytest.mark.parametrize("text,expected", [("In stock (22 available)", 22), ("\n In stock (1 available) ", 1), ("Out of stock", 0)])
def test_stock_counts(text, expected):
    assert parse_availability(text) == expected


def test_stock_does_not_invent_a_missing_count():
    with pytest.raises(ValueError):
        parse_availability("In stock")


@pytest.mark.parametrize("word,value", [("One", 1), ("Two", 2), ("Three", 3), ("Four", 4), ("Five", 5)])
def test_rating_classes(word, value):
    assert parse_rating(["star-rating", word]) == value


@pytest.mark.parametrize("classes", [["star-rating"], ["Six"], ["One", "Two"]])
def test_invalid_ratings_rejected(classes):
    with pytest.raises(ValueError):
        parse_rating(classes)


@pytest.mark.parametrize("field,value", [
    ("availability_count", "22"), ("rating", 6), ("num_reviews", -1),
    ("price_gbp", float("nan")), ("price_gbp", float("inf")),
    ("price_gbp", -1.0), ("price_gbp", 51), ("upc", ""),
    ("product_url", "/relative"), ("image_url", "https://example.org/photo.jpg"),
    ("scraped_at", "2026-11-10T05:00:00"), ("tax", 1.0),
])
def test_type_and_value_validation(book, field, value):
    book[field] = value
    with pytest.raises(ValueError):
        validate_book(book)


def test_dedupe_uses_upc_not_title(book):
    different_upc = deepcopy(book)
    different_upc["upc"] = "another-upc"
    rows, duplicates = dedupe_books([book, deepcopy(book), different_upc])
    assert len(rows) == 2
    assert duplicates == 1
    assert rows[0] is book
