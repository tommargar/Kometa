from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import modules.cache as cache_module
from modules import tvdb
from modules.cache import Cache
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


def test_show_credits_use_tvdb_people_ids_and_split_cast_from_crew():
    responses = [
        SimpleNamespace(
            status_code=200,
            json=lambda: {"data": {"token": "token-value"}},
        ),
        SimpleNamespace(
            status_code=200,
            json=lambda: {
                "data": {
                    "name": "Example Show",
                    "characters": [
                        {
                            "peopleId": 20,
                            "personName": "Show Writer",
                            "name": "",
                            "peopleType": "Writer",
                            "sort": 3,
                        },
                        {
                            "peopleId": 10,
                            "personName": "Lead Actor",
                            "name": "Main Role",
                            "peopleType": "Actor",
                            "sort": 1,
                        },
                    ],
                }
            },
        ),
    ]
    calls = []

    def post(url, json=None):
        calls.append(("post", url, json))
        return responses.pop(0)

    def get(url, headers=None):
        calls.append(("get", url, headers))
        return responses.pop(0)

    instance = tvdb.TVDb(
        requests=SimpleNamespace(post=post, get=get),
        cache=None,
        tvdb_language="deu",
        expiration=60,
        apikey="api-key",
    )

    credits = instance.get_show_credits(1234)

    assert credits.credits_source == "tvdb"
    assert credits.cast == [{"tvdb_id": 10, "name": "Lead Actor", "order": 0, "character": "Main Role", "person_type": "Actor"}]
    assert credits.crew == [
        {
            "tvdb_id": 20,
            "name": "Show Writer",
            "order": 0,
            "job": "Writer",
            "department": "Writer",
            "person_type": "Writer",
            "role": None,
        }
    ]
    assert calls[0][0] == "post"
    assert calls[1][2]["Authorization"] == "Bearer token-value"


def test_show_credits_require_tvdb_api_key():
    instance = tvdb.TVDb(
        requests=SimpleNamespace(),
        cache=None,
        tvdb_language="deu",
        expiration=60,
    )

    with pytest.raises(Failed, match="tvdb.apikey"):
        instance.get_show_credits(1234)


def test_movie_credits_use_movie_extended_endpoint():
    responses = [
        SimpleNamespace(status_code=200, json=lambda: {"data": {"token": "token-value"}}),
        SimpleNamespace(
            status_code=200,
            json=lambda: {
                "data": {
                    "name": "Example Movie",
                    "characters": [
                        {
                            "peopleId": 10,
                            "personName": "Lead Actor",
                            "name": "Main Role",
                            "peopleType": "Actor",
                            "sort": 1,
                        }
                    ],
                }
            },
        ),
    ]
    calls = []

    def post(url, json=None):
        calls.append(("post", url, json))
        return responses.pop(0)

    def get(url, headers=None):
        calls.append(("get", url, headers))
        return responses.pop(0)

    instance = tvdb.TVDb(
        requests=SimpleNamespace(post=post, get=get),
        cache=None,
        tvdb_language="deu",
        expiration=60,
        apikey="api-key",
    )

    credits = instance.get_movie_credits(555)

    assert credits.credits_source == "tvdb"
    assert credits.credits_level == "movie"
    assert credits.cast[0]["tvdb_id"] == 10
    assert calls[1][1].endswith("/movies/555/extended")


def test_tvdb_movie_credits_preserve_character_sequence_instead_of_sorting_by_sort_value():
    credits = tvdb.TVDb._show_credit_data(
        1935,
        {
            "name": "Seven Psychopaths",
            "characters": [
                {"peopleId": 310392, "personName": "Colin Farrell", "name": "Marty Faranan", "peopleType": "Actor", "sort": 5},
                {"peopleId": 333915, "personName": "Sam Rockwell", "name": "Billy Bickle", "peopleType": "Actor", "sort": 0},
                {"peopleId": 253775, "personName": "Woody Harrelson", "name": "Charlie Costello", "peopleType": "Actor", "sort": 5},
            ],
        },
        item_type="movie",
    )

    assert [(entry["name"], entry["order"]) for entry in credits["cast"]] == [
        ("Colin Farrell", 0),
        ("Sam Rockwell", 1),
        ("Woody Harrelson", 2),
    ]


def test_tvdb_series_credits_follow_website_cast_sort_order():
    credits = tvdb.TVDb._show_credit_data(
        272644,
        {
            "name": "12 Monkeys",
            "characters": [
                {"peopleId": 316307, "personName": "Tom Noonan", "name": "Pallid Man", "peopleType": "Actor", "sort": 7},
                {"peopleId": 338254, "personName": "Noah Bean", "name": "Aaron Marker", "peopleType": "Actor", "sort": 8},
                {"peopleId": 331729, "personName": "Amanda Schull", "name": "Cassandra Railly", "peopleType": "Actor", "sort": 2},
                {"peopleId": 370945, "personName": "Aaron Stanford", "name": "James Cole", "peopleType": "Actor", "sort": 1},
                {"peopleId": 271463, "personName": "Kirk Acevedo", "name": "José Ramse", "peopleType": "Actor", "sort": 5},
                {"peopleId": 531096, "personName": "Barbara Sukowa", "name": "Katarina Jones", "peopleType": "Actor", "sort": 4},
                {"peopleId": 304357, "personName": "Emily Hampshire", "name": "Jennifer Goines", "peopleType": "Actor", "sort": 3},
                {"peopleId": 252675, "personName": "Todd Stashwick", "name": "Theodore Deacon", "peopleType": "Actor", "sort": 6},
            ],
        },
        item_type="series",
    )

    assert [(entry["name"], entry["order"]) for entry in credits["cast"]] == [
        ("Aaron Stanford", 0),
        ("Amanda Schull", 1),
        ("Emily Hampshire", 2),
        ("Barbara Sukowa", 3),
        ("Kirk Acevedo", 4),
        ("Todd Stashwick", 5),
        ("Tom Noonan", 6),
        ("Noah Bean", 7),
    ]


def test_old_tvdb_credit_order_cache_is_expired_once(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_module, "logger", MagicMock())
    config_path = tmp_path / "config.yml"
    config_path.write_text("test: true\n", encoding="utf-8")
    cache = Cache(str(config_path), expiration=60)
    data = {
        "title": "Seven Psychopaths",
        "cast": [
            {"tvdb_id": 310392, "name": "Colin Farrell", "order": 0},
            {"tvdb_id": 333915, "name": "Sam Rockwell", "order": 1},
        ],
        "crew": [],
    }
    try:
        with cache.connection as connection:
            connection.execute(
                """
                INSERT INTO tvdb_item_credits(
                    tvdb_id, item_type, title, cast, crew,
                    credit_order_version, expiration_date
                ) VALUES(?, ?, ?, ?, ?, ?, date('now'))
                """,
                (1935, "movie", data["title"], "[]", "[]", 1),
            )

        _, expired = cache.query_tvdb_item_credits(1935, "movie", 60)
        assert expired

        cache.update_tvdb_item_credits(expired, 1935, "movie", data, 60)
        refreshed, expired = cache.query_tvdb_item_credits(1935, "movie", 60)
        assert not expired
        assert [entry["name"] for entry in refreshed["cast"]] == ["Colin Farrell", "Sam Rockwell"]
    finally:
        cache.close()


def test_series_order_v2_is_expired_without_expiring_item_order_v2(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_module, "logger", MagicMock())
    config_path = tmp_path / "config.yml"
    config_path.write_text("test: true\n", encoding="utf-8")
    cache = Cache(str(config_path), expiration=60)
    try:
        with cache.connection as connection:
            connection.execute(
                """
                INSERT INTO tvdb_series_credits(
                    tvdb_id, title, cast, crew,
                    credit_order_version, expiration_date
                ) VALUES(?, ?, ?, ?, ?, date('now'))
                """,
                (272644, "12 Monkeys", "[]", "[]", 2),
            )
            connection.execute(
                """
                INSERT INTO tvdb_item_credits(
                    tvdb_id, item_type, title, cast, crew,
                    credit_order_version, expiration_date
                ) VALUES(?, ?, ?, ?, ?, ?, date('now'))
                """,
                (4961780, "episode", "Splinter", "[]", "[]", 2),
            )

        _, series_expired = cache.query_tvdb_credits(272644, 60)
        _, episode_expired = cache.query_tvdb_item_credits(4961780, "episode", 60)

        assert series_expired
        assert not episode_expired
    finally:
        cache.close()


def test_episode_credits_map_guest_stars_directors_and_writers():
    data = {
        "name": "Winter Is Coming",
        "characters": [
            {"peopleId": 1, "personName": "Guest Actor", "name": "A Guest", "peopleType": "Guest Star", "sort": 1},
            {"peopleId": 2, "personName": "Episode Director", "peopleType": "Director", "sort": 2},
            {"peopleId": 3, "personName": "Episode Writer", "peopleType": "Writer", "sort": 3},
        ],
    }

    credits = tvdb.TVDb._show_credit_data(1001, data)

    assert credits["cast"] == [
        {
            "tvdb_id": 1,
            "name": "Guest Actor",
            "order": 0,
            "character": "A Guest",
            "person_type": "GuestStar",
        }
    ]
    assert [(entry["tvdb_id"], entry["person_type"], entry["role"]) for entry in credits["crew"]] == [
        (2, "Director", None),
        (3, "Writer", None),
    ]


def test_people_external_ids_bulk_maps_tmdb_and_imdb_remote_ids(monkeypatch):
    instance = tvdb.TVDb(
        requests=SimpleNamespace(),
        cache=None,
        tvdb_language="eng",
        expiration=60,
        apikey="api-key",
    )
    instance._api_token = "token"

    def api_get(path):
        assert path == "/people/501/extended"
        return {
            "remoteIds": [
                {"id": "101", "sourceName": "TheMovieDB.com"},
                {"id": "nm0000101", "sourceName": "IMDB"},
                {"id": "Q101", "sourceName": "Wikidata"},
            ]
        }

    monkeypatch.setattr(instance, "_api_get", api_get)

    assert instance.get_people_external_ids_bulk([501, "501"]) == {
        501: {
            "tmdb_id": 101,
            "imdb_id": "nm0000101",
            "wikidata_id": "Q101",
        }
    }


def test_people_external_ids_bulk_uses_unexpired_crosswalk_cache(monkeypatch):
    cached = {501: {"tmdb_id": 101, "imdb_id": "nm0000101"}}
    cache = SimpleNamespace(
        query_tvdb_people_external_ids=lambda tvdb_ids, expiration: (cached, []),
        update_tvdb_people_external_ids=lambda values: pytest.fail("unchanged crosswalk was rewritten"),
    )
    instance = tvdb.TVDb(
        requests=SimpleNamespace(),
        cache=cache,
        tvdb_language="eng",
        expiration=60,
        apikey="api-key",
    )
    monkeypatch.setattr(instance, "_api_get", lambda path: pytest.fail("cached crosswalk hit TVDb"))

    assert instance.get_people_external_ids_bulk([501]) == cached


def test_people_external_ids_bulk_does_not_cache_provider_failures(monkeypatch):
    monkeypatch.setattr(tvdb, "logger", SimpleNamespace(warning=lambda message: None))
    cache = SimpleNamespace(
        query_tvdb_people_external_ids=lambda tvdb_ids, expiration: ({}, [501]),
        update_tvdb_people_external_ids=lambda values: pytest.fail("failed TVDb lookup was cached"),
    )
    instance = tvdb.TVDb(
        requests=SimpleNamespace(),
        cache=cache,
        tvdb_language="eng",
        expiration=60,
        apikey="api-key",
    )
    instance._api_token = "token"
    monkeypatch.setattr(instance, "_api_get", lambda path: (_ for _ in ()).throw(Failed("temporary failure")))

    assert instance.get_people_external_ids_bulk([501]) == {}
