# Saved sandbox HTML

These files are raw UTF-8 responses saved from the public scraping sandbox on
2026-11-10 after checking its robots.txt response (HTTP 404).

- `listing.html`: https://books.toscrape.com/catalogue/page-1.html
- `detail.html`: https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html

They are used by the offline parser tests. Tests also mutate copies in memory to
check absent optional descriptions, missing required fields and final-page
pagination. Neither fixture is modified by those tests.
