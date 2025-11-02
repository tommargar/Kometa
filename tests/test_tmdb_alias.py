from pathlib import Path
from types import SimpleNamespace
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
