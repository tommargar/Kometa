from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from modules import cache as cache_module
from modules import emby as emby_module
from modules import emby_cache as emby_cache_module
from modules import emby_server as emby_server_module
from modules.cache import Cache
from modules.emby_cache import EmbyCacheCoordinator, EmbyCacheDatabase
from modules.emby_server import EmbyServer
from modules.util import Failed


@pytest.fixture(autouse=True)
def mock_emby_logger(monkeypatch):
    monkeypatch.setattr(emby_server_module, "logger", MagicMock())
    monkeypatch.setattr(emby_module, "logger", MagicMock())


@pytest.fixture
def persistent_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(emby_cache_module, "logger", MagicMock())
    database = EmbyCacheDatabase(str(tmp_path))
    yield database
    database.close()


def test_emby_cache_uses_config_independent_database(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_module, "logger", MagicMock())
    monkeypatch.setattr(emby_cache_module, "logger", MagicMock())
    database = EmbyCacheDatabase(str(tmp_path))
    normal_cache = Cache(str(tmp_path / "config-test.yml"), 60)
    try:
        database.update_emby_items(
            "server-1",
            {
                "10": {
                    "etag": "etag-1",
                    "fields": {"Id", "Etag"},
                    "data": {"Id": "10", "Etag": "etag-1"},
                }
            },
        )
        assert database.cache_path("server-1") == str(tmp_path / "emby_cache" / "server-1.db")
        normal_tables = {row["name"] for row in normal_cache.connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert "emby_item_cache" not in normal_tables
        assert "emby_library_items" not in normal_tables
        assert "tmdb_person_map" not in normal_tables
        assert "false_friend_names" not in normal_tables
        assert "media_people_cache" not in normal_tables
        assert "plex_people_cache" in normal_tables
        emby_tables = {row["name"] for row in database.connection("server-1").execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert {"emby_item_cache", "emby_library_items", "tmdb_person_map", "false_friend_names"}.issubset(emby_tables)
    finally:
        normal_cache.close()
        database.close()


def test_emby_cache_uses_one_isolated_database_per_server(persistent_cache):
    records = {
        "10": {
            "etag": "etag-1",
            "fields": {"Id", "Etag", "Name"},
            "data": {"Id": "10", "Etag": "etag-1", "Name": "Production"},
        }
    }
    persistent_cache.update_emby_items("production-server", records)
    persistent_cache.update_emby_items(
        "test-server",
        {
            "10": {
                "etag": "etag-test",
                "fields": {"Id", "Etag", "Name"},
                "data": {"Id": "10", "Etag": "etag-test", "Name": "Test"},
            }
        },
    )

    assert persistent_cache.cache_path("production-server") != persistent_cache.cache_path("test-server")
    assert persistent_cache.query_emby_items("production-server", ["10"])["10"]["data"]["Name"] == "Production"
    assert persistent_cache.query_emby_items("test-server", ["10"])["10"]["data"]["Name"] == "Test"


def test_tvdb_people_crosswalk_is_provider_global_and_persistent(tmp_path, monkeypatch):
    monkeypatch.setattr(emby_cache_module, "logger", MagicMock())
    first_database = EmbyCacheDatabase(str(tmp_path))
    first = EmbyCacheCoordinator(first_database)
    first.update_tvdb_people_external_ids(
        {
            260544: {
                "tmdb_id": 18022,
                "imdb_id": "nm0571160",
                "wikidata_id": None,
            },
            7909161: {},
        }
    )
    first.close()

    restarted_database = EmbyCacheDatabase(str(tmp_path))
    restarted = EmbyCacheCoordinator(restarted_database)
    try:
        cached, missing = restarted.query_tvdb_people_external_ids([260544, 7909161, 999999], 60)

        assert cached == {
            260544: {
                "tmdb_id": 18022,
                "imdb_id": "nm0571160",
                "wikidata_id": None,
            },
            7909161: {
                "tmdb_id": None,
                "imdb_id": None,
                "wikidata_id": None,
            },
        }
        assert missing == [999999]
        assert restarted_database.cache_path("external-providers") == str(tmp_path / "emby_cache" / "external-providers.db")
    finally:
        restarted.close()


def test_bulk_people_identity_snapshot_can_clear_disproved_ids_and_emby_mapping(persistent_cache):
    persistent_cache.update_emby_person_identity(
        "server-1",
        -7909161,
        "Gina McKee",
        "gina mckee",
        "Gina McKee",
        imdb_id="nm0571160",
        tvdb_id="7909161",
        emby_id="139729",
        verified_at="2026-07-01T12:00:00",
        canonical_id=18022,
    )

    persistent_cache.update_emby_person_identities(
        "server-1",
        [
            {
                "tmdb_id": -7909161,
                "base_name": "Gina McKee",
                "normalized_name": "gina mckee",
                "display_name": "Gina McKee (II)",
                "imdb_id": None,
                "tvdb_id": "7909161",
                "emby_id": None,
                "verified_at": None,
                "canonical_id": None,
            }
        ],
    )

    identity = persistent_cache.query_emby_person_identities("server-1")[-7909161]
    assert identity["imdb_id"] is None
    assert identity["tvdb_id"] == "7909161"
    assert identity["emby_id"] is None
    assert identity["verified_at"] is None
    assert identity["canonical_id"] is None


def test_single_people_identity_snapshot_can_clear_disproved_ids_and_verification(persistent_cache):
    identity = {
        "server_key": "server-1",
        "tmdb_id": 18022,
        "base_name": "Gina McKee",
        "normalized_name": "gina mckee",
        "display_name": "Gina McKee",
    }
    persistent_cache.update_emby_person_identity(
        **identity,
        imdb_id="nm0571160",
        tvdb_id="260544",
        emby_id="139729",
        verified_at="2026-07-01T12:00:00",
    )
    persistent_cache.update_emby_person_identity(
        **identity,
        imdb_id=None,
        tvdb_id=None,
        emby_id=None,
        verified_at=None,
    )

    cached = persistent_cache.query_emby_person_identities("server-1")[18022]
    assert cached["imdb_id"] is None
    assert cached["tvdb_id"] is None
    assert cached["emby_id"] is None
    assert cached["verified_at"] is None


def test_legacy_people_identity_snapshots_can_clear_disproved_values(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_module, "logger", MagicMock())
    config_path = tmp_path / "config.yml"
    config_path.write_text("test: true\n", encoding="utf-8")
    database = Cache(str(config_path), 60)
    identity = {
        "server_id": "server-1",
        "tmdb_id": 18022,
        "base_name": "Gina McKee",
        "normalized_name": "gina mckee",
        "display_name": "Gina McKee",
    }
    try:
        database.update_emby_person_identity(
            **identity,
            imdb_id="nm0571160",
            tvdb_id="260544",
            emby_id="139729",
            verified_at="2026-07-01T12:00:00",
        )
        database.update_emby_person_identity(
            **identity,
            imdb_id=None,
            tvdb_id=None,
            emby_id=None,
            verified_at=None,
        )
        cached = database.query_emby_person_identities("server-1")[18022]
        assert cached["imdb_id"] is None
        assert cached["tvdb_id"] is None
        assert cached["emby_id"] is None
        assert cached["verified_at"] is None

        database.update_emby_person_identity(
            **identity,
            imdb_id="nm0571160",
            tvdb_id="260544",
            emby_id="139729",
            verified_at="2026-07-01T12:00:00",
        )
        database.update_emby_person_identities(
            "server-1",
            [
                {
                    "tmdb_id": 18022,
                    "base_name": "Gina McKee",
                    "normalized_name": "gina mckee",
                    "display_name": "Gina McKee",
                    "imdb_id": None,
                    "tvdb_id": None,
                    "emby_id": None,
                    "verified_at": None,
                }
            ],
        )
        cached = database.query_emby_person_identities("server-1")[18022]
        assert cached["imdb_id"] is None
        assert cached["tvdb_id"] is None
        assert cached["emby_id"] is None
        assert cached["verified_at"] is None
    finally:
        database.close()


def test_emby_specific_person_data_stays_in_server_database(persistent_cache):
    coordinator = EmbyCacheCoordinator(persistent_cache)
    coordinator.update_tmdb_person_map(
        "server-1",
        False,
        123,
        emby_id="person-1",
        name="Example",
        expiration=30,
    )
    mapping, missing, expired = coordinator.query_tmdb_person_map_bulk("server-1", [123], 30)

    assert mapping[123]["emby_id"] == "person-1"
    assert not missing
    assert not expired
    assert coordinator.add_false_friend_name("server-1", "John Smith")
    assert "john smith" in coordinator.query_false_friend_names("server-1")
    assert "john smith" not in coordinator.query_false_friend_names("server-2")


def test_emby_cache_persists_library_snapshot(persistent_cache):
    coordinator = EmbyCacheCoordinator(persistent_cache)
    coordinator.commit_library_snapshot(
        "server-1",
        "library-1",
        "movie",
        [
            {
                "Id": "10",
                "Etag": "etag-1",
                "Name": "Movie",
                "Genres": ["Drama"],
            }
        ],
        declared_fields={"Etag", "Name", "Genres"},
    )

    restarted = EmbyCacheCoordinator(persistent_cache)
    restarted.hydrate("server-1", ["10"])
    record = restarted.item_record("server-1", "10")

    assert record["etag"] == "etag-1"
    assert record["data"]["Genres"] == ["Drama"]
    assert {"Etag", "Name", "Genres"}.issubset(record["fields"])


def test_changed_etag_replaces_instead_of_merging_stale_fields(persistent_cache):
    coordinator = EmbyCacheCoordinator(persistent_cache)
    coordinator.upsert_items(
        "server-1",
        [{"Id": "10", "Etag": "etag-1", "Name": "Old", "Genres": ["Drama"]}],
        declared_fields={"Etag", "Name", "Genres"},
    )
    coordinator.upsert_items(
        "server-1",
        [{"Id": "10", "Etag": "etag-2", "Name": "New"}],
        declared_fields={"Etag", "Name"},
    )

    record = coordinator.item_record("server-1", "10")
    assert record["etag"] == "etag-2"
    assert record["data"]["Name"] == "New"
    assert "Genres" not in record["data"]


def test_direct_item_etag_does_not_replace_stable_manifest_etag(persistent_cache):
    coordinator = EmbyCacheCoordinator(persistent_cache)
    coordinator.upsert_items(
        "server-1",
        [{"Id": "10", "Etag": "stable-manifest", "Name": "Movie"}],
        declared_fields={"Etag", "Name"},
    )
    server = make_server(coordinator)
    server.emby_server_url = "http://emby"
    server.user_id = "user"
    server.api_key = "secret"
    server.headers = {}
    response = MagicMock()
    response.json.return_value = {
        "Id": "10",
        "Etag": "unstable-direct",
        "Name": "Movie",
        "Overview": "Fresh",
    }
    server.session = MagicMock()
    server.session.get.return_value = response

    item = server.get_item("10", force_refresh=True)

    assert "Etag" not in item
    assert item["Overview"] == "Fresh"
    assert coordinator.item_record("server-1", "10")["etag"] == "stable-manifest"


def test_stable_item_etags_use_unpublished_bulk_manifest(persistent_cache):
    server = make_server(EmbyCacheCoordinator(persistent_cache))
    server._ensure_http_session = MagicMock()
    server.get_items_bulk = MagicMock(
        return_value={
            "10": {"Id": "10", "Etag": "stable-10"},
            "20": {"Id": "20", "Etag": "stable-20"},
        }
    )

    assert server.get_stable_item_etags(["10", "20"]) == {
        "10": "stable-10",
        "20": "stable-20",
    }
    server.get_items_bulk.assert_called_once_with(
        ["10", "20"],
        fields=["Etag"],
        force_refresh=True,
        publish=False,
    )


def make_server(coordinator):
    server = EmbyServer.__new__(EmbyServer)
    server._central_cache = coordinator
    server.cache_server_key = "server-1"
    state = coordinator.bind_server(server.cache_server_key)
    server._items_cache = state["items"]
    server._items_cache_fields = state["fields"]
    server._items_cache_ts = state["timestamps"]
    server.item_cache = server._items_cache
    server.cached_plex_objects = state["converted"]
    server.dirty_items = state["dirty"]
    return server


def test_manifest_reuses_persistent_payload_when_etag_matches(persistent_cache):
    first = EmbyCacheCoordinator(persistent_cache)
    first.commit_library_snapshot(
        "server-1",
        "library-1",
        "movie",
        [{"Id": "10", "Etag": "etag-1", "Name": "Movie"}],
        declared_fields={"Etag", "Name"},
    )

    restarted = EmbyCacheCoordinator(persistent_cache)
    server = make_server(restarted)
    server.get_items = MagicMock(return_value=[{"Id": "10", "Etag": "etag-1"}])
    server.get_items_bulk = MagicMock()

    result = server.get_library_items_cached(
        "library-1",
        ["Movie"],
        ["Etag", "Name"],
        "movie",
        force_manifest=True,
    )

    assert result[0]["Name"] == "Movie"
    server.get_items_bulk.assert_not_called()


def test_manifest_fetches_and_commits_changed_etag(persistent_cache):
    coordinator = EmbyCacheCoordinator(persistent_cache)
    coordinator.commit_library_snapshot(
        "server-1",
        "library-1",
        "movie",
        [{"Id": "10", "Etag": "etag-1", "Name": "Old", "Genres": ["Drama"]}],
        declared_fields={"Etag", "Name", "Genres"},
    )
    server = make_server(coordinator)
    server.get_items = MagicMock(return_value=[{"Id": "10", "Etag": "etag-2"}])
    server.get_items_bulk = MagicMock(return_value={"10": {"Id": "10", "Etag": "etag-2", "Name": "New"}})

    result = server.get_library_items_cached(
        "library-1",
        ["Movie"],
        ["Etag", "Name"],
        "movie",
        force_manifest=True,
    )

    assert result == [{"Id": "10", "Etag": "etag-2", "Name": "New"}]
    assert "Genres" not in coordinator.item_record("server-1", "10")["data"]
    server.get_items_bulk.assert_called_once_with(
        ["10"],
        fields=["Etag", "Name"],
        force_refresh=True,
        publish=False,
    )


def test_manifest_mismatch_does_not_replace_committed_snapshot(persistent_cache):
    coordinator = EmbyCacheCoordinator(persistent_cache)
    coordinator.commit_library_snapshot(
        "server-1",
        "library-1",
        "movie",
        [{"Id": "10", "Etag": "etag-1", "Name": "Old"}],
        declared_fields={"Etag", "Name"},
    )
    server = make_server(coordinator)
    server.get_items = MagicMock(return_value=[{"Id": "10", "Etag": "etag-2"}])
    server.get_items_bulk = MagicMock(return_value={"10": {"Id": "10", "Etag": "etag-raced", "Name": "Raced"}})

    with pytest.raises(Failed):
        server.get_library_items_cached(
            "library-1",
            ["Movie"],
            ["Etag", "Name"],
            "movie",
            force_manifest=True,
        )

    restarted = EmbyCacheCoordinator(persistent_cache)
    restarted.hydrate("server-1", ["10"])
    assert restarted.item_record("server-1", "10")["etag"] == "etag-1"


def test_manifest_removes_items_no_longer_in_library(persistent_cache):
    coordinator = EmbyCacheCoordinator(persistent_cache)
    coordinator.commit_library_snapshot(
        "server-1",
        "library-1",
        "movie",
        [
            {"Id": "10", "Etag": "etag-1", "Name": "Keep"},
            {"Id": "20", "Etag": "etag-2", "Name": "Remove"},
        ],
        declared_fields={"Etag", "Name"},
    )
    server = make_server(coordinator)
    server.get_items = MagicMock(return_value=[{"Id": "10", "Etag": "etag-1"}])
    server.get_items_bulk = MagicMock()

    result = server.get_library_items_cached(
        "library-1",
        ["Movie"],
        ["Etag", "Name"],
        "movie",
        force_manifest=True,
    )

    assert [item["Id"] for item in result] == ["10"]
    restarted = EmbyCacheCoordinator(persistent_cache)
    restarted.hydrate("server-1", ["20"])
    assert restarted.item_record("server-1", "20") is None


def test_successful_remote_image_update_invalidates_item():
    server = EmbyServer.__new__(EmbyServer)
    server.emby_server_url = "http://emby"
    server.headers = {}
    server.session = MagicMock()
    server.session.post.return_value = SimpleNamespace(status_code=204)
    server.invalidate_item = MagicMock()

    assert server.set_image_smart("10", "https://example.com/poster.jpg", "Primary") is True
    server.invalidate_item.assert_called_once_with("10")


def test_external_poster_tag_change_invalidates_image_compare():
    library = emby_module.Emby.__new__(emby_module.Emby)
    library.config = SimpleNamespace(Cache=MagicMock())
    library.config.Cache.query_image_map.return_value = ("", "asset-old-tag", "")
    library.image_table_name = "image_map_test"
    library.EmbyServer = MagicMock()
    library.EmbyServer.get_item.side_effect = [
        {"Id": "10", "Etag": "etag-2", "ImageTags": {"Primary": "new-tag"}},
        {"Id": "10", "Etag": "etag-3", "ImageTags": {"Primary": "uploaded-tag"}},
    ]
    library.show_asset_not_needed = False
    library._upload_image = MagicMock(return_value=True)
    item = SimpleNamespace(ratingKey="10")
    poster = SimpleNamespace(
        compare="asset",
        attribute="poster",
        message="",
        prefix="",
    )

    uploaded = library.upload_images(item, poster=poster)

    assert uploaded == (True, False, False)
    library._upload_image.assert_called_once_with(item, poster)
    library.config.Cache.update_image_map.assert_called_once_with(
        "10",
        "image_map_test",
        "",
        "asset-uploaded-tag",
    )
