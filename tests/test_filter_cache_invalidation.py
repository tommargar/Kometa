import sys
import types
from datetime import datetime, timezone
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

if "modules.builder" not in sys.modules:
    stub_builder = types.ModuleType("modules.builder")
    stub_builder.filters = {
        "movie": ["filepath"],
        "show": [],
        "season": [],
        "episode": [],
        "artist": [],
        "album": [],
        "track": []
    }
    stub_builder.date_filters = []
    stub_builder.string_filters = ["filepath"]
    stub_builder.boolean_filters = []
    stub_builder.number_filters = []
    stub_builder.date_attributes = []
    stub_builder.year_attributes = []
    stub_builder.number_attributes = []
    stub_builder.tag_attributes = []
    stub_builder.string_attributes = []
    stub_builder.float_attributes = []
    stub_builder.boolean_attributes = []

    def _missing_attr(name):  # pragma: no cover - fallback for other lookups
        return []

    stub_builder.__getattr__ = _missing_attr
    sys.modules["modules.builder"] = stub_builder

from modules.plex import Plex  # noqa: E402
from plexapi.video import Movie  # noqa: E402


class DummyMovie(Movie):
    """Simple Movie subclass that allows manual instantiation for testing."""
    pass


def _build_movie(rating_key="rk1"):
    movie = DummyMovie.__new__(DummyMovie)
    movie.ratingKey = rating_key
    movie.title = "Test Movie"
    movie.collections = []
    movie.media = []
    movie.fields = []
    movie.editionTitle = None
    return movie


def _build_plex(server):
    plex = Plex.__new__(Plex)
    plex.filter_items_cache = {}
    plex._search_choices_cache = {}
    plex.EmbyServer = server
    plex.Emby = {"Id": "library"}
    plex.config = types.SimpleNamespace(mediastingers={})
    plex.item_labels = lambda *_: []
    plex.overlay_destination_folder = "/tmp"
    plex.movie_rating_key_map = {}
    plex.show_rating_key_map = {}
    plex.album_rating_key_map = {}
    plex.track_rating_key_map = {}
    plex.agent = "tv.plex.agents.movie"
    return plex


def test_edit_tags_clears_filter_cache_entry():
    class Server:
        def __init__(self):
            self.tags = ["old"]

        def get_emby_item_tags(self, obj, library_id, from_cache=False):
            return list(self.tags)

        def get_emby_item_genres(self, obj, library_id, from_cache=False):
            return []

        def set_tags(self, rating_key, tags):
            self.tags = list(tags)

        def set_genres(self, rating_key, tags):
            self.tags = list(tags)

    server = Server()
    plex = _build_plex(server)
    movie = _build_movie()
    plex.filter_items_cache[movie.ratingKey] = {"Name": "stale"}
    plex._search_choices_cache["foo"] = "bar"

    plex.edit_tags("label", movie, add_tags=["fresh"], do_print=False)

    assert set(server.tags) == {"fresh", "old"}
    assert movie.ratingKey not in plex.filter_items_cache
    assert plex._search_choices_cache == {}


def test_alter_collection_invalidation_for_smart_labels():
    class Server:
        def __init__(self):
            self.added = []
            self.removed = []

        def add_tags(self, rating_key, tags):
            self.added.append((rating_key, list(tags)))

        def remove_tags(self, rating_key, tags):
            self.removed.append((rating_key, list(tags)))

        def add_remove_plex_object_from_collection(self, *args, **kwargs):
            return None

        def add_to_collection(self, *args, **kwargs):
            return True

        def create_collection(self, *args, **kwargs):
            return None

    server = Server()
    plex = _build_plex(server)
    movie = _build_movie("rk2")
    plex.filter_items_cache[movie.ratingKey] = {"Name": "stale"}
    plex._search_choices_cache["foo"] = "bar"

    plex.alter_collection([movie], "My Collection", smart_label_collection=True, add=True)

    assert server.added == [(movie.ratingKey, ["My Collection"])]
    assert movie.ratingKey not in plex.filter_items_cache
    assert plex._search_choices_cache == {}


def test_filters_use_updated_metadata_after_edit_tags():
    class Server:
        def __init__(self):
            self.tags = ["old"]
            self.path = "/cached/path"
            self.calls = 0

        def get_emby_item_tags(self, obj, library_id, from_cache=False):
            return list(self.tags)

        def get_emby_item_genres(self, obj, library_id, from_cache=False):
            return []

        def set_tags(self, rating_key, tags):
            self.tags = list(tags)

        def set_genres(self, rating_key, tags):
            self.tags = list(tags)

        def get_item(self, rating_key):
            self.calls += 1
            return {"Path": self.path}

    server = Server()
    plex = _build_plex(server)
    movie = _build_movie("rk3")
    plex.filter_items_cache[movie.ratingKey] = {"Path": "/stale/path"}

    plex.edit_tags("label", movie, add_tags=["fresh"], do_print=False)

    server.path = "/new/path"
    current_time = datetime.now(timezone.utc)
    assert plex.check_filter(movie, "filepath", "", "filepath", ["/new/path"], current_time)
    assert server.calls == 1
    assert plex.filter_items_cache[movie.ratingKey]["Path"] == "/new/path"
