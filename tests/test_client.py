"""Network policy checks use httpx mock transport; no live traffic."""

import asyncio
import logging
import time

import httpx
import pytest

import scraper
from scraper import BASE_URL, PoliteClient, RunStats


def run_scenario(tmp_path, responses, *, url=BASE_URL):
    async def scenario():
        stats = RunStats(delay_seconds=0)
        client = PoliteClient(0, stats, logging.getLogger("test"))
        await client.client.aclose()
        calls = []

        def handle(request):
            calls.append(request)
            response = responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return response

        client.client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        try:
            await client.check_robots(tmp_path)
            result = await client.get(url)
            return result, stats, calls
        finally:
            await client.client.aclose()

    return asyncio.run(scenario())


def test_robots_404_allows_public_pages(tmp_path):
    response, stats, calls = run_scenario(tmp_path, [httpx.Response(404), httpx.Response(200, text="ok")])
    assert response.text == "ok"
    assert calls[0].url.path == "/robots.txt"
    assert stats.robots_status == 404
    assert stats.requests_attempted == 2


def test_robots_disallow_prevents_catalogue_request(tmp_path):
    with pytest.raises(ValueError, match="does not permit"):
        run_scenario(tmp_path, [httpx.Response(200, text="User-agent: *\nDisallow: /\n")])


@pytest.mark.parametrize("failure", [httpx.Response(503), httpx.ReadTimeout("temporary timeout")])
def test_transient_failures_retry_with_backoff(tmp_path, monkeypatch, failure):
    waits = []

    async def record_sleep(seconds):
        waits.append(seconds)

    monkeypatch.setattr(scraper.asyncio, "sleep", record_sleep)
    _, stats, calls = run_scenario(tmp_path, [httpx.Response(404), failure, httpx.Response(200)])
    assert stats.retries == 1
    assert len(calls) == 3
    assert 1.0 in waits


def test_http_403_is_not_retried(tmp_path):
    with pytest.raises(httpx.HTTPStatusError):
        run_scenario(tmp_path, [httpx.Response(404), httpx.Response(403)])


def test_retry_exhaustion_is_an_error(tmp_path, monkeypatch):
    waits = []

    async def record_sleep(seconds):
        waits.append(seconds)

    monkeypatch.setattr(scraper.asyncio, "sleep", record_sleep)
    with pytest.raises(httpx.HTTPStatusError):
        run_scenario(tmp_path, [httpx.Response(404)] + [httpx.Response(503) for _ in range(4)])
    assert [value for value in waits if value >= 1] == [1.0, 2.0, 4.0]


def test_shared_request_gap_and_concurrency_limit(tmp_path):
    async def scenario():
        starts = []
        active = 0
        peak = 0

        async def handle(request):
            nonlocal active, peak
            starts.append(time.monotonic())
            active += 1
            peak = max(peak, active)
            try:
                await asyncio.sleep(0.03)
                return httpx.Response(404 if request.url.path == "/robots.txt" else 200)
            finally:
                active -= 1

        client = PoliteClient(0.01, RunStats(), logging.getLogger("test"))
        await client.client.aclose()
        client.client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        try:
            await client.check_robots(tmp_path)
            await asyncio.gather(*(client.get(BASE_URL) for _ in range(12)))
        finally:
            await client.client.aclose()
        assert 1 < peak <= 3
        assert all(after - before >= 0.009 for before, after in zip(starts, starts[1:]))

    asyncio.run(scenario())
