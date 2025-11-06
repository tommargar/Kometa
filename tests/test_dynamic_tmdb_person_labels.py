import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

sys.path.append(str(Path(__file__).resolve().parents[1]))


def load_meta_with_stubs():
    meta_path = Path(__file__).resolve().parents[1] / "modules" / "meta.py"
    spec = importlib.util.spec_from_file_location("test_meta_module", meta_path)
    module = importlib.util.module_from_spec(spec)

    stub_modules = {
        "modules.plex": ModuleType("modules.plex"),
        "modules.ergast": ModuleType("modules.ergast"),
        "modules.letterboxd": ModuleType("modules.letterboxd"),
    }
    stub_modules["modules.plex"].new_plex_agents = []
    stub_modules["modules.plex"].library_types = []
    stub_modules["modules.plex"].item_advance_keys = {}

    originals = {}
    for name, stub in stub_modules.items():
        originals[name] = sys.modules.get(name)
        sys.modules[name] = stub

    try:
        spec.loader.exec_module(module)
    finally:
        for name, original in originals.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original

    return module


meta = load_meta_with_stubs()
TMDB_PERSON_ID_LABEL_PREFIX = meta.TMDB_PERSON_ID_LABEL_PREFIX
TMDB_PERSON_NAME_LABEL_PREFIX = meta.TMDB_PERSON_NAME_LABEL_PREFIX
encode_tmdb_person_labels = meta.encode_tmdb_person_labels
decode_tmdb_person_labels = meta.decode_tmdb_person_labels
merge_tmdb_person_labels = meta.merge_tmdb_person_labels


class DummyLabel(SimpleNamespace):
    def __init__(self, tag):
        super().__init__(tag=tag)


class DummyCollection:
    def __init__(self, title, labels):
        self.title = title
        self._labels = labels


class DummyLibrary:
    def __init__(self, collections):
        self._collections = collections

    def get_all_collections(self, label=None):
        if label is None:
            return list(self._collections)
        return [col for col in self._collections if label in col._labels]

    def item_labels(self, item):
        return [DummyLabel(tag) for tag in item._labels]


def test_dynamic_map_recovers_tagged_tmdb_person_and_labels():
    map_name = "PeopleMap"
    tagged_id = "101"
    tagged_name = "Tagged Person"

    tagged_collection_labels = [map_name]
    tagged_collection_labels.extend(encode_tmdb_person_labels(tagged_id, tagged_name))
    untagged_collection_labels = [map_name]

    collections = [
        DummyCollection(tagged_name, tagged_collection_labels),
        DummyCollection("Orphaned Person", untagged_collection_labels),
    ]

    library = DummyLibrary(collections)

    auto_list = {"202": "Fresh Person"}
    all_keys = {"202": "Fresh Person"}
    exclude = []

    tmdb_map = merge_tmdb_person_labels(library, map_name, auto_list, all_keys, exclude)

    for key in list(auto_list.keys()):
        if key.isdigit() and key not in tmdb_map:
            tmdb_map[key] = key

    assert auto_list["101"] == tagged_name
    assert all_keys["101"] == tagged_name
    assert set(auto_list.keys()) == {"101", "202"}

    assert tmdb_map["101"] == "101"
    assert tmdb_map["202"] == "202"

    recovered_labels = [map_name]
    recovered_labels.extend(encode_tmdb_person_labels(tmdb_map["101"], auto_list["101"]))
    recovered_id, recovered_name = decode_tmdb_person_labels(recovered_labels)
    assert recovered_id == "101"
    assert recovered_name == tagged_name

    fresh_labels = [map_name]
    fresh_labels.extend(encode_tmdb_person_labels(tmdb_map["202"], auto_list["202"]))
    assert f"{TMDB_PERSON_ID_LABEL_PREFIX}202" in fresh_labels
    assert any(label.startswith(TMDB_PERSON_NAME_LABEL_PREFIX) for label in fresh_labels)
