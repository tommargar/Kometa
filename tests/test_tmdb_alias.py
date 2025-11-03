from pathlib import Path
from types import MethodType, SimpleNamespace
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))

from modules.emby_server import EmbyServer


def _bind_methods(server):
    """Attach bound methods from EmbyServer needed for the tests."""
    for name in (
        "_is_latin_string",
        "_tmdb_person_translated_name_en",
        "_tmdb_person_alias_latin",
        "_romanize_local",
        "_strip_accents",
    ):
        method = getattr(EmbyServer, name)
        setattr(server, name, method.__get__(server, EmbyServer))


def make_server(*, aliases=None, raise_on_details=False, raise_on_translations=False):
    class DummyAPI3:
        def person_get_details(self, person_id):
            if raise_on_details:
                raise RuntimeError("details boom")
            return {"also_known_as": aliases or []}

        def person_get_translations(self, person_id):
            if raise_on_translations:
                raise RuntimeError("translations boom")
            return {}

    server = SimpleNamespace()
    server.config = SimpleNamespace(TMDb=SimpleNamespace(API3=DummyAPI3()))
    server._roman_name_cache = {}
    _bind_methods(server)
    return server


def test_tmdb_person_alias_latin_returns_ascii_alias():
    server = make_server(aliases=["Valid Alias", "別名"])

    result = server._tmdb_person_alias_latin(1234)

    assert result == "Valid Alias"


def test_get_romanized_person_name_gracefully_handles_tmdb_errors():
    server = make_server(raise_on_details=True, raise_on_translations=True)

    # Ensure the local romanizer provides a deterministic ASCII fallback.
    server._romanize_local = lambda name: "fallback-name"

    result = EmbyServer.get_romanized_person_name(server, 4321, "名")

    assert result == "fallback-name"


def test_get_person_info_bulk_prefers_lowest_id_and_skips_demote():
    tmdb_id = 123456

    class DummyResponse:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    class DummySession:
        def __init__(self, payload):
            self.payload = payload
            self.calls = []

        def get(self, url, headers=None, params=None, timeout=None):
            self.calls.append({"url": url, "params": params})
            return DummyResponse(self.payload)

    class DummyCache:
        def __init__(self, hinted):
            self.hinted = hinted
            self.expiration = 30
            self.updated = []

        def query_tmdb_person_map_bulk(self, ids, expiration):
            return ({tid: {"emby_id": self.hinted} for tid in ids}, None, None)

        def update_tmdb_person_map(self, *, expired, tmdb_id, emby_id, expiration):
            self.updated.append((tmdb_id, emby_id, expired, expiration))

    payload = {
        "Items": [
            {"Id": "211", "Name": "Existing", "ProviderIds": {"tmdb": str(tmdb_id)}},
            {"Id": "103", "Name": "Preferred", "ProviderIds": {"Tmdb": str(tmdb_id)}},
        ]
    }

    server = object.__new__(EmbyServer)
    server.emby_server_url = "http://example"
    server.user_id = "user"
    server.api_key = "token"
    server.headers = {}
    server.session = DummySession(payload)
    server.cached_tmdb_ids = {tmdb_id: "211"}
    server._person_dupes_choice_last = {}
    server._person_dupes_last = {}
    server._person_already_demoted = []
    server._demotions = []

    cache = DummyCache("211")
    server.config = SimpleNamespace(Cache=cache)

    def _ensure(self):
        return None

    def _demote(self, pid):
        self._demotions.append(pid)

    server._ensure_http_session = MethodType(_ensure, server)
    server._demote_duplicate_person = MethodType(_demote, server)

    result = EmbyServer.get_person_info_bulk(server, [tmdb_id])

    assert result[tmdb_id] == "103"
    assert cache.updated == [(tmdb_id, "103", False, cache.expiration)]
    assert server._demotions == []
