import re
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


def test_cached_person_filter_respects_requested_roles(monkeypatch):
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

    multi_role_person_id = "p1"
    api_items = [
        {
            "Id": "movie-both",
            "Type": "Movie",
            "Name": "Both Roles",
            "People": [
                {"Id": multi_role_person_id, "Type": "Actor"},
                {"Id": multi_role_person_id, "Type": "Director"},
            ],
        },
        {
            "Id": "movie-actor",
            "Type": "Movie",
            "Name": "Actor Only",
            "People": [
                {"Id": multi_role_person_id, "Type": "Actor"},
            ],
        },
        {
            "Id": "movie-director",
            "Type": "Movie",
            "Name": "Director Only",
            "People": [
                {"Id": multi_role_person_id, "Type": "Director"},
            ],
        },
        {
            "Id": "movie-unrelated",
            "Type": "Movie",
            "Name": "Other Person",
            "People": [
                {"Id": "p2", "Type": "Actor"},
            ],
        },
    ]

    hydrated_items = []

    class DummyRequestsGet:
        def __init__(self, store):
            self.store = store

        def __call__(self, url, headers=None, params=None):
            items = [dict(item) for item in api_items]
            self.store[:] = items
            return DummyResponse({"Items": api_items, "TotalRecordCount": len(api_items)})

    class StubEmbyServer:
        def __init__(self, items):
            self.items = list(items)
            self.headers = {}
            self.media_by_resolution = {}
            self.get_items_calls = 0

        def cache_filenames(self, items):
            return None

        def get_items(self, params):
            self.get_items_calls += 1
            include_types = params.get("IncludeItemTypes")
            allowed_types = {
                t.strip() for t in include_types.split(",") if t.strip()
            } if include_types else None

            person_ids = params.get("PersonIds")
            requested_ids = {
                pid.strip() for pid in person_ids.split(",") if pid.strip()
            } if person_ids else None

            person_types = params.get("PersonTypes")
            requested_roles = {
                role.strip().lower() for role in person_types.split(",") if role.strip()
            } if person_types else set()

            results = []
            for item in self.items:
                if allowed_types and item.get("Type") not in allowed_types:
                    continue
                if requested_ids:
                    people = item.get("People")
                    if not isinstance(people, list):
                        continue
                    match_found = False
                    for person in people:
                        pid = person.get("Id")
                        if pid is None or str(pid) not in requested_ids:
                            continue
                        if requested_roles:
                            person_type = person.get("Type")
                            if isinstance(person_type, str) and person_type.lower() in requested_roles:
                                match_found = True
                                break
                        else:
                            match_found = True
                            break
                    if not match_found:
                        continue
                results.append(item)
            return list(results)

        def convert_emby_to_plex(self, items, convert_people=True):
            return [item["Id"] for item in items]

        def get_custom_rating_from_item(self, item):
            return None

    dummy_requests = DummyRequestsGet(hydrated_items)
    monkeypatch.setattr(plex_module.requests, "get", dummy_requests)

    plex = Plex.__new__(Plex)
    plex.type = "Movie"
    plex.name = "Dummy"
    plex.Emby = {"Name": "Dummy", "Id": "library1"}
    plex.emby_server_url = "http://emby"
    plex.emby_user_id = "user"
    plex._emby_all_items = []
    plex._emby_all_items_native = []
    plex._search_choices_cache = {}
    plex._filter_items_cache = {}
    plex.EmbyServer = StubEmbyServer(api_items)

    plex.get_all_native(builder_level="movie")

    assert hydrated_items and all("People" in item for item in hydrated_items)

    actor_params = {
        "IncludeItemTypes": "Movie",
        "PersonIds": multi_role_person_id,
        "PersonTypes": "actor",
    }
    expected_actor_items = plex.EmbyServer.get_items(actor_params)
    expected_actor_result = plex.EmbyServer.convert_emby_to_plex(expected_actor_items)

    director_params = {
        "IncludeItemTypes": "Movie",
        "PersonIds": multi_role_person_id,
        "PersonTypes": "director",
    }
    expected_director_items = plex.EmbyServer.get_items(director_params)
    expected_director_result = plex.EmbyServer.convert_emby_to_plex(expected_director_items)

    plex.EmbyServer.get_items_calls = 0

    actor_result = plex.fetchItems(f"?type=1&actor={multi_role_person_id}")
    assert actor_result == expected_actor_result
    assert plex.EmbyServer.get_items_calls == 0
    assert "movie-director" not in actor_result

    plex.EmbyServer.get_items_calls = 0

    director_result = plex.fetchItems(f"?type=1&director={multi_role_person_id}")
    assert director_result == expected_director_result
    assert plex.EmbyServer.get_items_calls == 0


def test_cached_resolution_filter_uses_emby_cache(monkeypatch):
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
            "Id": "movie-1080",
            "Type": "Movie",
            "ProductionYear": 2022,
            "PremiereDate": "2022-01-01T00:00:00Z",
            "Name": "Full HD",
        },
        {
            "Id": "movie-720",
            "Type": "Movie",
            "ProductionYear": 2020,
            "PremiereDate": "2020-01-01T00:00:00Z",
            "Name": "HD",
        },
    ]

    hydrated_items = []

    class DummyRequestsGet:
        def __init__(self, store):
            self.store = store

        def __call__(self, url, headers=None, params=None):
            items = [dict(item) for item in api_items]
            self.store[:] = items
            return DummyResponse({"Items": items, "TotalRecordCount": len(items)})

    class StubEmbyServer:
        def __init__(self, items):
            self.items = list(items)
            self.headers = {}
            self.media_by_resolution = {
                "1080p": {"movie-1080"},
                "720p": {"movie-720"},
            }
            self.get_items_calls = 0

        def cache_filenames(self, items):
            return None

        def get_items(self, params):
            self.get_items_calls += 1
            return list(self.items)

        def convert_emby_to_plex(self, items, convert_people=True):
            return [item["Id"] for item in items]

        def get_custom_rating_from_item(self, item):
            return None

        def get_item(self, item_id):
            for item in self.items:
                if item.get("Id") == item_id:
                    return item
            return None

    dummy_requests = DummyRequestsGet(hydrated_items)
    monkeypatch.setattr(plex_module.requests, "get", dummy_requests)

    plex = Plex.__new__(Plex)
    plex.type = "Movie"
    plex.name = "Dummy"
    plex.Emby = {"Name": "Dummy", "Id": "library1"}
    plex.emby_server_url = "http://emby"
    plex.emby_user_id = "user"
    plex._emby_all_items = []
    plex._emby_all_items_native = []
    plex._search_choices_cache = {}
    plex._filter_items_cache = {}
    plex.EmbyServer = StubEmbyServer(api_items)

    plex.get_all_native(builder_level="movie")

    assert hydrated_items and all("Id" in item for item in hydrated_items)

    plex.EmbyServer.get_items_calls = 0

    result = plex.fetchItems("?type=1&resolution=1080p")

    assert result == ["movie-1080"]
    assert plex.EmbyServer.get_items_calls == 0


def test_cached_rating_sort_normalizes_nested_payloads(monkeypatch):
    import types
    from urllib.parse import parse_qs as _parse_qs, quote_plus as _quote_plus, urlparse as _urlparse

    class CapturingLogger:
        def __init__(self):
            self.errors = []

        def error(self, message, *args, **kwargs):
            self.errors.append((message, args, kwargs))

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

    capturing_logger = CapturingLogger()

    stub_util = types.ModuleType("modules.util")
    stub_util.logger = capturing_logger

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

    plex_module.logger = capturing_logger

    api_items = [
        {"Id": "movie-missing", "Type": "Movie", "Name": "Missing", "CriticRating": None},
        {"Id": "movie-high", "Type": "Movie", "Name": "High", "CriticRating": "95%"},
        {
            "Id": "movie-dict",
            "Type": "Movie",
            "Name": "Dict",
            "CriticRating": {"Value": "88.5/100", "Votes": 10},
        },
        {
            "Id": "movie-list",
            "Type": "Movie",
            "Name": "List",
            "CriticRating": ["70 of 100", {"Ignored": "text"}],
        },
        {"Id": "movie-zero", "Type": "Movie", "Name": "Zero", "CriticRating": 0},
    ]

    hydrated_items = []

    class DummyRequestsGet:
        def __init__(self, store):
            self.store = store

        def __call__(self, url, headers=None, params=None):
            items = [dict(item) for item in api_items]
            self.store[:] = items
            return DummyResponse({"Items": items, "TotalRecordCount": len(items)})

    class StubEmbyServer:
        def __init__(self, items):
            self.items = list(items)
            self.headers = {}
            self.media_by_resolution = {}
            self.get_items_calls = 0

        def cache_filenames(self, items):
            return None

        def _normalize_rating(self, value):
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return float(value)
            if isinstance(value, str):
                match = re.search(r"[-+]?\d*\.?\d+", value)
                if match:
                    try:
                        return float(match.group())
                    except (TypeError, ValueError):
                        return None
                return None
            if isinstance(value, dict):
                for sub_value in value.values():
                    normalized = self._normalize_rating(sub_value)
                    if normalized is not None:
                        return normalized
                return None
            if isinstance(value, (list, tuple, set)):
                for sub_value in value:
                    normalized = self._normalize_rating(sub_value)
                    if normalized is not None:
                        return normalized
                return None
            return None

        def get_items(self, params):
            self.get_items_calls += 1
            include_types = params.get("IncludeItemTypes")
            allowed_types = {
                t.strip() for t in include_types.split(",") if t.strip()
            } if include_types else None

            results = []
            for item in self.items:
                if allowed_types and item.get("Type") not in allowed_types:
                    continue
                results.append(dict(item))

            sort_by = params.get("SortBy")
            if sort_by:
                reverse_order = params.get("SortOrder", "Ascending").lower() == "descending"

                sortable = []
                none_bucket = []
                for item in results:
                    value = item.get(sort_by)
                    if sort_by == "CriticRating":
                        value = self._normalize_rating(value)
                    if value is None:
                        none_bucket.append(item)
                    else:
                        sortable.append((value, item))
                sortable.sort(key=lambda pair: pair[0], reverse=reverse_order)
                results = [item for _, item in sortable] + none_bucket

            return [dict(item) for item in results]

        def convert_emby_to_plex(self, items, convert_people=True):
            return [item["Id"] for item in items]

        def get_custom_rating_from_item(self, item):
            return None

        def get_item(self, item_id):
            for item in self.items:
                if str(item.get("Id")) == str(item_id):
                    return dict(item)
            raise KeyError(item_id)

    dummy_requests = DummyRequestsGet(hydrated_items)
    monkeypatch.setattr(plex_module.requests, "get", dummy_requests)

    plex = Plex.__new__(Plex)
    plex.type = "Movie"
    plex.name = "Dummy"
    plex.Emby = {"Name": "Dummy", "Id": "library1"}
    plex.emby_server_url = "http://emby"
    plex.emby_user_id = "user"
    plex._emby_all_items = []
    plex._emby_all_items_native = []
    plex._search_choices_cache = {}
    plex._filter_items_cache = {}
    plex.EmbyServer = StubEmbyServer(api_items)

    plex.get_all_native(builder_level="movie")

    assert hydrated_items and all("CriticRating" in item for item in hydrated_items)

    expected_items = plex.EmbyServer.get_items(
        {
            "IncludeItemTypes": "Movie",
            "ParentId": "library1",
            "Recursive": "true",
            "SortBy": "CriticRating",
            "SortOrder": "Descending",
        }
    )
    expected_result = plex.EmbyServer.convert_emby_to_plex(expected_items)
    plex.EmbyServer.get_items_calls = 0

    result = plex.fetchItems("?type=1&sort=rating:desc")

    assert result == expected_result
    assert plex.EmbyServer.get_items_calls == 0
    assert capturing_logger.errors == []


def test_resolution_filter_applied_after_api_fetch(monkeypatch):
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

    class StubEmbyServer:
        def __init__(self):
            self.media_by_resolution = {}
            self.get_items_calls = 0
            self.get_resolutions_calls = 0
            self.last_converted = None

        def get_resolutions(self):
            self.get_resolutions_calls += 1
            self.media_by_resolution = {
                "1080p": ["movie-1080"],
                "4k": ["movie-4k"],
                "hdr": ["movie-hdr"],
                "plus": [],
                "dvhdr": [],
                "dvhdrplus": [],
            }
            return []

        def get_items(self, params):
            self.get_items_calls += 1
            return [
                {"Id": "movie-1080", "Type": "Movie", "Name": "Full HD"},
                {"Id": "movie-4k", "Type": "Movie", "Name": "Ultra HD"},
            ]

        def convert_emby_to_plex(self, items, convert_people=True):
            self.last_converted = list(items)
            return [item["Id"] for item in items]

    plex = Plex.__new__(Plex)
    plex.type = "Movie"
    plex.name = "Dummy"
    plex.Emby = {"Name": "Dummy", "Id": "library1"}
    plex.emby_server_url = "http://emby"
    plex.emby_user_id = "user"
    plex._emby_all_items = []
    plex._emby_all_items_native = []
    plex._search_choices_cache = {}
    plex._filter_items_cache = {}
    plex.EmbyServer = StubEmbyServer()

    plex._can_use_emby_cache = lambda params: False

    result = plex.fetchItems("?type=1&resolution=1080p")

    assert result == ["movie-1080"]
    assert plex.EmbyServer.get_items_calls == 1
    assert plex.EmbyServer.get_resolutions_calls >= 1
    assert plex.EmbyServer.last_converted and len(plex.EmbyServer.last_converted) == 1
    assert plex.EmbyServer.last_converted[0]["Id"] == "movie-1080"
