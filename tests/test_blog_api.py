"""Tests for the BlogAPI FastAPI app and the scrape helper.

The network is never touched. ``requests.get`` is stubbed for the scrape
tests, and the module-level ``cache`` is reset between every API test so the
endpoints are exercised against known, fixed data.
"""

from unittest.mock import MagicMock, patch

import json

import pytest
from fastapi.testclient import TestClient

import main
from scrape import scrape_blogs

# A small, well-formed blog page used to exercise scrape_blogs.
SAMPLE_HTML = """
<html>
  <body>
    <article>
      <h3>First Post</h3>
      <a href="/blog/first-post">Read</a>
      <time datetime="2026-08-01">August 1, 2026</time>
    </article>
    <article>
      <h3>Second Post</h3>
      <a href="/blog/second-post">Read</a>
      <time datetime="2026-07-20">July 20, 2026</time>
    </article>
    <!-- An entry that is missing a date should be skipped, not crash. -->
    <article>
      <h3>Broken Post</h3>
      <a href="/blog/broken-post">Read</a>
    </article>
    <!-- An entry that is missing a link should be skipped too. -->
    <article>
      <h3>No Link</h3>
      <time datetime="2026-06-01">June 1, 2026</time>
    </article>
  </body>
</html>
"""

EXPECTED_BLOGS = [
    {
        "title": "First Post",
        "link": "https://project516.dev/blog/first-post",
        "date": "2026-08-01",
    },
    {
        "title": "Second Post",
        "link": "https://project516.dev/blog/second-post",
        "date": "2026-07-20",
    },
]


class _FakeResponse:
    """Just enough of requests.Response for scrape_blogs."""

    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


@pytest.fixture()
def no_startup_refresh():
    """Stub main.refresh_cache so neither the lifespan hook nor the cache
    endpoint ever touches the network. Tests that care configure the stub;
    tests exercising the real refresh_cache just don't request this.
    """
    stub = MagicMock(return_value=[])
    patcher = patch.object(main, "refresh_cache", stub)
    patcher.start()
    try:
        yield stub
    finally:
        patcher.stop()


@pytest.fixture()
def client(no_startup_refresh):
    """A TestClient with the rate limiter disabled so tests stay stable.

    slowapi counts hits per remote address, and a tight test run would trip
    the 5/minute limit almost immediately. Pointing the limiter at a storage
    backend that always reports a clean slate keeps the tests deterministic.
    """
    original_enabled = main.limiter.enabled
    main.limiter.enabled = False
    try:
        with TestClient(main.app) as test_client:
            # Startup already consumed one refresh call; drop it so per-test
            # call-count assertions see only what the test itself triggered.
            no_startup_refresh.reset_mock()
            yield test_client
    finally:
        main.limiter.enabled = original_enabled


@pytest.fixture(autouse=True)
def reset_cache():
    """Reset the module-level cache before and after every test."""
    saved = main.cache
    main.cache = []
    yield
    main.cache = saved


# --- scrape.py --------------------------------------------------------------


def test_scrape_blogs_parses_well_formed_entries():
    fake = _FakeResponse(SAMPLE_HTML)
    with patch("scrape.requests.get", return_value=fake):
        result = scrape_blogs("https://example.invalid/blog.html")
    assert result == EXPECTED_BLOGS


def test_scrape_blogs_resolves_relative_links():
    """Relative hrefs are resolved against BLOG_BASE_URL, not the scrape URL."""
    fake = _FakeResponse(
        '<article><h3>X</h3><a href="/p/1">x</a><time datetime="2026-01-01">d</time></article>'
    )
    with patch("scrape.requests.get", return_value=fake):
        result = scrape_blogs("https://other.invalid/page.html")
    assert result == [
        {"title": "X", "link": "https://project516.dev/p/1", "date": "2026-01-01"}
    ]


def test_scrape_blogs_skips_malformed_entries():
    """Entries missing title, link, or date are dropped, never cause a crash."""
    fake = _FakeResponse(SAMPLE_HTML)
    with patch("scrape.requests.get", return_value=fake):
        result = scrape_blogs("https://example.invalid/blog.html")
    # The two broken <article> blocks must not appear in the output.
    titles = [b["title"] for b in result]
    assert "Broken Post" not in titles
    assert "No Link" not in titles
    assert len(result) == 2


def test_scrape_blogs_returns_empty_when_no_articles():
    fake = _FakeResponse("<html><body><p>no posts here</p></body></html>")
    with patch("scrape.requests.get", return_value=fake):
        result = scrape_blogs("https://example.invalid/blog.html")
    assert result == []


def test_scrape_blogs_sends_user_agent_header():
    """A User-Agent header is sent so the host does not block the request."""
    fake = _FakeResponse(SAMPLE_HTML)
    with patch("scrape.requests.get", return_value=fake) as mock_get:
        scrape_blogs("https://example.invalid/blog.html")
    headers = mock_get.call_args.kwargs["headers"]
    assert "User-Agent" in headers
    assert headers["User-Agent"]
    # A timeout must always be set so a slow host cannot hang the scrape.
    assert mock_get.call_args.kwargs["timeout"]


# --- main.py endpoints ------------------------------------------------------


def test_get_blogs_returns_cached_list(client):
    main.cache = list(EXPECTED_BLOGS)
    response = client.get("/blogs")
    assert response.status_code == 200
    assert response.json() == EXPECTED_BLOGS


def test_get_blogs_returns_empty_list_when_cache_empty(client):
    main.cache = []
    response = client.get("/blogs")
    assert response.status_code == 200
    assert response.json() == []


def test_get_latest_blog_returns_first_entry(client):
    main.cache = list(EXPECTED_BLOGS)
    response = client.get("/blogs/latest")
    assert response.status_code == 200
    assert response.json() == EXPECTED_BLOGS[0]


def test_get_latest_blog_returns_null_when_empty(client):
    main.cache = []
    response = client.get("/blogs/latest")
    assert response.status_code == 200
    assert response.json() is None


def test_search_blogs_matches_case_insensitively(client):
    main.cache = list(EXPECTED_BLOGS)
    response = client.get("/blogs/search", params={"query": "SECOND"})
    assert response.status_code == 200
    assert response.json() == [EXPECTED_BLOGS[1]]


def test_search_blogs_returns_not_found_message(client):
    """The documented contract: no match -> a 200 with a message JSON object."""
    main.cache = list(EXPECTED_BLOGS)
    response = client.get("/blogs/search", params={"query": "does-not-exist"})
    assert response.status_code == 200
    assert response.json() == {"message": "Blog not found"}


def test_search_blogs_matches_partial_title(client):
    main.cache = list(EXPECTED_BLOGS)
    response = client.get("/blogs/search", params={"query": "post"})
    assert response.status_code == 200
    assert len(response.json()) == 2


def test_load_cache_rejects_valid_json_that_is_not_a_list(tmp_path):
    """A truthy dict in the cache file must not skip the startup refresh or
    break cache[0]; it is treated as an empty cache."""
    bad = tmp_path / "cache.json"
    bad.write_text('{"invalid": true}')
    with patch("main.CACHE_FILE", str(bad)):
        assert main._load_cache() == []


def test_refresh_cache_scrapes_and_persists(tmp_path):
    """refresh_cache scrapes, writes the payload to CACHE_FILE, and returns it."""
    cache_file = tmp_path / "cache.json"
    fake_blogs = [
        {"title": "Fresh", "link": "https://project516.dev/fresh", "date": "2026-09-01"}
    ]
    with (
        patch("main.scrape_blogs", return_value=fake_blogs),
        patch("main.CACHE_FILE", str(cache_file)),
    ):
        result = main.refresh_cache()

    assert result == fake_blogs
    assert cache_file.exists()
    with open(cache_file, "r") as f:
        assert json.load(f) == fake_blogs


def test_cache_endpoint_refreshes_from_stub(client, no_startup_refresh):
    """POST /blogs/cache stores whatever refresh_cache returns."""
    no_startup_refresh.return_value = [
        {"title": "Fresh", "link": "https://project516.dev/fresh", "date": "2026-09-01"}
    ]
    response = client.post("/blogs/cache")

    assert response.status_code == 200
    assert response.json() == {"message": "Blogs cached successfully"}
    no_startup_refresh.assert_called_once()
    assert main.cache == no_startup_refresh.return_value


def test_cache_endpoint_returns_generic_500_without_leaking_details(
    client, no_startup_refresh
):
    """The 500 detail must not echo exception text back to the client (#9)."""
    no_startup_refresh.side_effect = RuntimeError("boom secret stuff")
    with patch.object(main, "CACHE_FILE", "/nonexistent/cache.json"):
        response = client.post("/blogs/cache")
    assert response.status_code == 500
    assert response.json()["detail"] == "Failed to refresh blog cache"
    assert "boom" not in response.text


def test_startup_populates_empty_cache(no_startup_refresh):
    """A fresh deploy has no cache file, so the lifespan hook must fill the
    cache itself instead of serving [] until a manual POST."""
    no_startup_refresh.return_value = [
        {"title": "Boot", "link": "https://project516.dev/boot", "date": "2026-08-23"}
    ]
    with TestClient(main.app):
        pass
    no_startup_refresh.assert_called_once()
    assert len(main.cache) == 1


def test_startup_skips_refresh_when_cache_loaded_from_disk(no_startup_refresh):
    """If the cache file existed at import time, startup must not re-scrape."""
    main.cache = list(EXPECTED_BLOGS)
    try:
        with TestClient(main.app):
            pass
    finally:
        main.cache = []
    no_startup_refresh.assert_not_called()


def test_startup_survives_refresh_failure(no_startup_refresh):
    """An unreachable blog source must not stop the server from booting."""
    no_startup_refresh.side_effect = RuntimeError("network down")
    with TestClient(main.app) as test_client:
        response = test_client.get("/blogs")
    assert response.status_code == 200
    assert response.json() == []


# --- landing page -----------------------------------------------------------


def test_root_returns_html_landing_page(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")
    body = response.text
    # A few markers from the landing page, confirming it rendered at all.
    assert "<title>Blog API</title>" in body
    assert "/docs" in body
