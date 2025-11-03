import sys
import types
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from modules.emby_server import EmbyServer


def _build_test_server():
    server = EmbyServer.__new__(EmbyServer)
    server.config = types.SimpleNamespace(Cache=None)
    server.cached_tmdb_ids = {}
    server.emby_server_url = "http://example.com"
    server.api_key = "token"
    server.headers = {}
    server.missing_tmdb_ids = set()
    server.get_person_info_bulk = lambda *args, **kwargs: {}
    server.get_items_bulk = lambda *args, **kwargs: {}
    server._norm_provider_ids = lambda provider_ids: ({}, None)
    return server


def test_crew_bucket_sort_is_deterministic():
    server = _build_test_server()

    crew = [
        {"person_id": 101, "name": "Writer B", "job": "screenplay"},
        {"person_id": 102, "name": "Writer Autor", "job": "writer"},
        {"person_id": 103, "name": "Writer Story", "job": "story"},
        {"person_id": 104, "name": "Writer A", "job": "screenplay"},
    ]

    first = server.build_emby_people_from_tmdb([], crew, provider="tmdb")
    second = server.build_emby_people_from_tmdb([], crew, provider="tmdb")

    assert first == second

    expected = [
        ("Autor", "Writer Autor"),
        ("Story", "Writer Story"),
        (None, "Writer A"),
        (None, "Writer B"),
    ]
    assert [(entry.get("Role"), entry.get("Name")) for entry in first] == expected
