from types import SimpleNamespace

import pytest

from modules import tvdb
from modules.util import Failed


def _make_tvdb(response_status, content=b"<html></html>"):
    """Build a TVDb instance whose underlying requests.get returns the chosen status."""
    calls = []

    def get(url, language=None):
        calls.append((url, language))
        return fake_response

    fake_response = SimpleNamespace(
        status_code=response_status,
        reason="Mocked",
        content=content,
    )
    fake_requests = SimpleNamespace(get=get)
    instance = tvdb.TVDb(requests=fake_requests, cache=None, tvdb_language="eng", expiration=60)
    instance.calls = calls
    return instance


def test_notfound_is_failed_subclass():
    # Callers that want to keep catching every TVDb failure as Failed should still work.
    assert issubclass(tvdb.NotFound, Failed)


def test_get_request_raises_notfound_on_4xx():
    t = _make_tvdb(404)
    with pytest.raises(tvdb.NotFound):
        t.get_request("https://www.thetvdb.com/dereferrer/series/463160")


def test_get_request_raises_failed_on_5xx(monkeypatch):
    # Suppress tenacity's wait so the 6-retry loop finishes instantly.
    monkeypatch.setattr("time.sleep", lambda _: None)
    t = _make_tvdb(503)
    with pytest.raises(Failed) as excinfo:
        t.get_request("https://www.thetvdb.com/dereferrer/series/81189")
    # Must not be the NotFound subclass — 5xx is treated as transient.
    assert not isinstance(excinfo.value, tvdb.NotFound)


def test_tvdbobj_init_propagates_notfound_for_stale_id():
    t = _make_tvdb(404)
    with pytest.raises(tvdb.NotFound) as excinfo:
        tvdb.TVDbObj(t, 463160, is_movie=False, ignore_cache=True)
    assert "463160" in str(excinfo.value)
    assert "No Series found" in str(excinfo.value)


def test_numeric_movie_id_uses_cached_object_without_dereferrer_lookup(monkeypatch):
    cached_data = {
        "title": "Cached Movie",
        "summary": "Cached summary",
        "poster_url": "",
        "background_url": "",
        "logo_url": "",
        "icon_url": "",
        "release_date": None,
        "status": "Released",
        "genres": "Drama|Comedy",
    }
    cache = SimpleNamespace(
        query_tvdb=lambda tvdb_id, is_movie, expiration: (cached_data, False),
        update_tvdb=lambda expired, obj, expiration: None,
    )
    t = _make_tvdb(200)
    t.cache = cache
    monkeypatch.setattr(t, "get_id_from_url", lambda *args, **kwargs: pytest.fail("numeric TVDb ID was resolved through the dereferrer"))

    obj = t.get_tvdb_obj(28248, is_movie=True)

    assert obj.tvdb_id == 28248
    assert obj.genres == ["Drama", "Comedy"]
    assert t.calls == []


def test_movie_request_has_short_retry_budget(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _: None)
    t = _make_tvdb(503)

    with pytest.raises(tvdb.Unavailable):
        t.get_movie_request("https://www.thetvdb.com/dereferrer/movie/28248")

    assert len(t.calls) == 2


def test_movie_object_uses_short_retry_budget(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _: None)
    t = _make_tvdb(503)

    with pytest.raises(tvdb.Unavailable):
        tvdb.TVDbObj(t, 28248, is_movie=True, ignore_cache=True)

    assert len(t.calls) == 2
