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
