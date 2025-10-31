import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules.emby_server import EmbyServer


class DummyResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


class DummySession:
    def __init__(self, payload: dict):
        self._payload = payload
        self.calls = 0

    def get(self, url, headers=None, params=None, timeout=None):
        self.calls += 1
        return DummyResponse(self._payload)


def test_tags_and_genres_use_items_cache(monkeypatch):
    server = EmbyServer.__new__(EmbyServer)
    server._items_cache = {}
    server._items_cache_fields = {}
    server._items_cache_ts = {}
    server.items_cache_ttl = 0
    server.item_cache = {}
    server.dirty_items = set()
    server.emby_server_url = "http://emby"
    server.api_key = "key"
    server.headers = {}
    server.session = DummySession({"Items": []})

    server.item_cache[1] = {
        "Id": 1,
        "LibraryId": "lib1",
        "Tags": ["ExistingTag"],
        "Genres": ["ExistingGenre"],
    }

    bulk_items = [
        {
            "Id": "1",
            "LibraryId": "lib1",
            "TagItems": [{"Name": "BulkTag"}],
            "GenreItems": [{"Name": "BulkGenre"}],
        },
        {
            "Id": "2",
            "ParentId": "lib1",
            "Tags": ["SecondTag"],
            "Genres": ["SecondGenre"],
        },
        {
            "Id": "3",
            "LibraryId": "lib2",
            "Tags": ["OtherLibTag"],
            "Genres": ["OtherLibGenre"],
        },
    ]

    server.session = DummySession({"Items": bulk_items})

    def fail_requests_get(url, *args, **kwargs):
        pytest.fail(f"Unexpected HTTP call to {url}")

    monkeypatch.setattr("modules.emby_server.requests.get", fail_requests_get)

    server.get_items_bulk(["1", "2", "3"])

    assert server.session.calls == 1
    assert set(server._items_cache.keys()) == {"1", "2", "3"}

    tags = server.get_emby_item_tags(None, library_id="lib1", search_all=True)
    genres = server.get_emby_item_genres(None, library_id="lib1", search_all=True)

    assert tags == ["BulkTag", "ExistingTag", "SecondTag"]
    assert genres == ["BulkGenre", "ExistingGenre", "SecondGenre"]


def test_cached_year_filter_matches_direct_call(monkeypatch):
    import sys
    import types
    from urllib.parse import parse_qs as _parse_qs, quote_plus as _quote_plus, urlparse as _urlparse

    class DummyLogger:
        def __getattr__(self, name):
            def _log(*args, **kwargs):
                return None

            return _log

    stub_builder = types.ModuleType("modules.builder")

    stub_library = types.ModuleType("modules.library")

    class DummyLibrary:
        pass

    stub_library.Library = DummyLibrary

    stub_poster = types.ModuleType("modules.poster")

    class DummyImageData:
        pass

    stub_poster.ImageData = DummyImageData

    stub_request = types.ModuleType("modules.request")
    stub_request.parse_qs = _parse_qs
    stub_request.quote_plus = _quote_plus
    stub_request.urlparse = _urlparse

    stub_util = types.ModuleType("modules.util")
    stub_util.logger = DummyLogger()

    class DummyFailed(Exception):
        pass

    stub_util.Failed = DummyFailed

    monkeypatch.setitem(sys.modules, "modules.builder", stub_builder)
    monkeypatch.setitem(sys.modules, "modules.library", stub_library)
    monkeypatch.setitem(sys.modules, "modules.poster", stub_poster)
    monkeypatch.setitem(sys.modules, "modules.request", stub_request)
    monkeypatch.setitem(sys.modules, "modules.util", stub_util)

    from modules.plex import Plex
    import modules.plex as plex_module

    plex_module.logger = DummyLogger()

    api_items = [
        {
            "Id": "series1",
            "Type": "Series",
            "ProductionYear": 2020,
            "PremiereDate": "2020-01-01T00:00:00Z",
            "Name": "Series One",
        },
        {
            "Id": "series2",
            "Type": "Series",
            "ProductionYear": 2021,
            "PremiereDate": "2021-01-01T00:00:00Z",
            "Name": "Series Two",
        },
    ]

    hydrated_items = []

    class DummyRequestsGet:
        def __init__(self, store):
            self.store = store

        def __call__(self, url, headers=None, params=None):
            fields = params.get("Fields", "")
            items = []
            for item in api_items:
                payload = {
                    "Id": item["Id"],
                    "Type": item["Type"],
                    "Name": item["Name"],
                }
                if "ProductionYear" in fields:
                    payload["ProductionYear"] = item["ProductionYear"]
                if "PremiereDate" in fields:
                    payload["PremiereDate"] = item["PremiereDate"]
                items.append(payload)
            self.store[:] = items
            return DummyResponse({"Items": items, "TotalRecordCount": len(items)})

    class StubEmbyServer:
        def __init__(self, items):
            self.items = items
            self.headers = {}
            self.media_by_resolution = {}
            self.get_items_calls = 0

        def cache_filenames(self, items):
            return None

        def get_items(self, params):
            self.get_items_calls += 1
            years = params.get("Years")
            include_types = params.get("IncludeItemTypes")
            allowed_years = {y.strip() for y in years.split(",") if y.strip()} if years else None
            allowed_types = {t.strip() for t in include_types.split(",") if t.strip()} if include_types else None
            results = []
            for item in api_items:
                if allowed_types and item["Type"] not in allowed_types:
                    continue
                if allowed_years and str(item["ProductionYear"]) not in allowed_years:
                    continue
                results.append(item)
            return results

        def convert_emby_to_plex(self, items, convert_people=True):
            return [item["Id"] for item in items]

        def get_custom_rating_from_item(self, item):
            return None

    dummy_requests = DummyRequestsGet(hydrated_items)
    monkeypatch.setattr(plex_module.requests, "get", dummy_requests)

    plex = Plex.__new__(Plex)
    plex.type = "Show"
    plex.name = "Dummy"
    plex.Emby = {"Name": "Dummy", "Id": "library1"}
    plex.emby_server_url = "http://emby"
    plex.emby_user_id = "user"
    plex._emby_all_items = []
    plex._emby_all_items_native = []
    plex._search_choices_cache = {}
    plex._filter_items_cache = {}
    plex.EmbyServer = StubEmbyServer(hydrated_items)

    plex.get_all_native(builder_level="show")

    assert hydrated_items and all("ProductionYear" in item for item in hydrated_items)
    assert all("PremiereDate" in item for item in hydrated_items)

    expected_items = [item for item in api_items if item["ProductionYear"] == 2020]
    expected_result = plex.EmbyServer.convert_emby_to_plex(expected_items)
    plex.EmbyServer.get_items_calls = 0

    result = plex.fetchItems("?type=2&year=2020")

    assert result == expected_result
    assert plex.EmbyServer.get_items_calls == 0
