"""Licensed stock photography. No test here touches a photo service.

The behaviour worth protecting is not "it downloads a picture" — it is that
every way this can fail ends in a demo that still builds, with a sentence
saying what happened.
"""
import json

import pytest
import requests

import generate
import stock


class FakeResponse:
    def __init__(self, payload=None, status_code=200, content=b"jpeg-bytes"):
        self._payload = payload or {}
        self.status_code = status_code
        self.content = content
        self.text = json.dumps(self._payload)

    def json(self):
        return self._payload


def pexels_page(count=3, alt="A cedar shingle roof on a bungalow"):
    return {"photos": [
        {"src": {"large2x": f"https://example.test/{i}.jpg"},
         "alt": f"{alt} {i}", "width": 1880, "height": 1253,
         "photographer": f"Photographer {i}",
         "url": f"https://example.test/photo/{i}"}
        for i in range(count)]}


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setattr(stock, "CACHE_DIR", str(tmp_path / "cache"))
    return tmp_path


@pytest.fixture
def keyed(monkeypatch):
    monkeypatch.setenv("PEXELS_API_KEY", "test-key")


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
def test_every_template_has_something_to_search_for():
    # A template with no queries would silently get another template's
    # photographs, which is worse than getting none.
    assert set(stock.QUERIES) == set(generate.TEMPLATES)
    for template, queries in stock.QUERIES.items():
        assert len(queries) >= 4, template


def test_the_queries_are_concrete_rather_than_the_category_again():
    # "roofing" returns a man in a hard hat pointing at a clipboard.
    for template, queries in stock.QUERIES.items():
        for query in queries:
            assert len(query.split()) >= 2, f"{template}: {query!r}"


def test_no_key_is_not_an_error(cache, tmp_path, monkeypatch):
    monkeypatch.delenv("PEXELS_API_KEY", raising=False)
    monkeypatch.setattr(requests, "get", lambda *a, **k:
                        pytest.fail("asked the network without a key"))
    result = stock.fetch("trade", 4, str(tmp_path / "site"))
    assert result.photos == []
    assert "key" in result.note and "pexels.com/api" in result.note


def test_an_unknown_provider_is_reported_rather_than_raised(cache, tmp_path):
    result = stock.fetch("trade", 2, str(tmp_path / "site"),
                         {"provider": "getty", "api_key": "x"})
    assert result.photos == []
    assert "getty" in result.note


def test_configured_answers_whether_photographs_are_possible(monkeypatch):
    monkeypatch.delenv("PEXELS_API_KEY", raising=False)
    assert stock.configured({}) is False
    assert stock.configured({"stock": {"api_key": "k"}}) is True
    # A key still sitting at its placeholder is not a key.
    assert stock.configured({"stock": {"api_key": "PASTE_ME"}}) is False


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------
def test_photographs_are_downloaded_and_written_beside_the_page(cache, tmp_path,
                                                                keyed, monkeypatch):
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        if "api.pexels.com" in url:
            return FakeResponse(pexels_page())
        return FakeResponse(content=b"\xff\xd8jpeg")

    monkeypatch.setattr(requests, "get", fake_get)
    dest = tmp_path / "site"
    result = stock.fetch("trade", 3, str(dest))

    assert len(result.photos) == 3
    for photo in result.photos:
        assert (dest / photo.src).read_bytes() == b"\xff\xd8jpeg"
        assert photo.alt and photo.credit.endswith("on Pexels")
    # Downloaded, not hotlinked: nothing in the page points at the service.
    for entry in result.as_content():
        assert not entry["src"].startswith("http")


def test_alt_text_comes_from_the_service_and_a_photo_without_it_is_skipped(
        cache, tmp_path, keyed, monkeypatch):
    # Inventing alt text from the search term is how a photograph of a ladder
    # ends up described as a roofer at work.
    payload = pexels_page(2)
    payload["photos"][0]["alt"] = ""
    monkeypatch.setattr(requests, "get", lambda url, **k:
                        FakeResponse(payload) if "api.pexels" in url
                        else FakeResponse())
    result = stock.fetch("trade", 1, str(tmp_path / "site"))
    assert len(result.photos) == 1
    assert result.photos[0].alt.endswith("1")


def test_the_same_photograph_is_only_downloaded_once(cache, tmp_path, keyed,
                                                     monkeypatch):
    downloads = []

    def fake_get(url, **kwargs):
        if "api.pexels.com" in url:
            return FakeResponse(pexels_page(1))
        downloads.append(url)
        return FakeResponse()

    monkeypatch.setattr(requests, "get", fake_get)
    stock.fetch("trade", 1, str(tmp_path / "a"))
    stock.fetch("trade", 1, str(tmp_path / "b"))
    # Forty demos of the same trade share their pictures. That is the point of
    # the cache, and it matters most in a car park before walking in.
    assert len(downloads) == 1
    assert (tmp_path / "b").exists()


@pytest.mark.parametrize("status,expected", [
    (401, "rejected the key"), (403, "rejected the key"),
    (429, "rate limit"), (500, "HTTP 500"),
])
def test_a_failing_service_leaves_a_sentence_not_an_exception(
        cache, tmp_path, keyed, monkeypatch, status, expected):
    monkeypatch.setattr(requests, "get",
                        lambda *a, **k: FakeResponse({}, status_code=status))
    result = stock.fetch("food", 3, str(tmp_path / "site"))
    assert result.photos == []
    assert expected in result.note


def test_a_dead_network_leaves_a_sentence_too(cache, tmp_path, keyed, monkeypatch):
    def boom(*args, **kwargs):
        raise requests.ConnectionError("no route to host")

    monkeypatch.setattr(requests, "get", boom)
    result = stock.fetch("salon", 3, str(tmp_path / "site"))
    assert result.photos == []
    assert "generated artwork" in result.note


def test_two_businesses_on_one_template_do_not_get_the_same_pictures(
        cache, tmp_path, keyed, monkeypatch):
    pages = []

    def fake_get(url, params=None, **kwargs):
        if "api.pexels.com" in url:
            pages.append(params["page"])
            return FakeResponse(pexels_page(1))
        return FakeResponse()

    monkeypatch.setattr(requests, "get", fake_get)
    stock.fetch("trade", 1, str(tmp_path / "a"), seed="place-a")
    stock.fetch("trade", 1, str(tmp_path / "b"), seed="place-b")
    assert len(set(pages)) == 2


def test_the_manifest_records_where_each_photograph_came_from():
    result = stock.Result(photos=[stock.Photo(
        src="00-a.jpg", alt="A roof", width=1, height=1,
        credit="A. Person on Pexels", url="https://example.test/1")])
    entries = json.loads(stock.manifest(result))
    assert entries[0]["credit"] == "A. Person on Pexels"
    assert entries[0]["source"] == "https://example.test/1"
