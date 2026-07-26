import copy
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import modules.cache as cache_module
import modules.emby_people as emby_people_module
import modules.emby_server as emby_server_module
from modules.cache import Cache
from modules.emby_cache import EmbyCacheCoordinator, EmbyCacheDatabase
from modules.emby_people import EmbyPeopleSync, PersonIdentity, normalize_person_name, roman_number

cache_module.logger = MagicMock()
emby_people_module.logger = MagicMock()


class FakeResponse:
    status_code = 204


class FakeTMDb:
    def __init__(
        self,
        imdb_ids=None,
        names=None,
        tvdb_ids=None,
        wikidata_ids=None,
        wikidata_entities=None,
    ):
        self.imdb_ids = imdb_ids or {}
        self.names = names or {}
        self.tvdb_ids = tvdb_ids or {}
        self.wikidata_ids = wikidata_ids or {}
        self.wikidata_entities = wikidata_entities or {}
        self.person_calls = []
        self.wikidata_calls = []

    def get_person(self, tmdb_id, partial=None):
        self.person_calls.append((int(tmdb_id), partial))
        return SimpleNamespace(
            name=self.names.get(int(tmdb_id)),
            imdb_id=self.imdb_ids.get(int(tmdb_id)),
            tvdb_id=self.tvdb_ids.get(int(tmdb_id)),
            wikidata_id=self.wikidata_ids.get(int(tmdb_id)),
        )

    def get_wikidata_person_ids(self, wikidata_id):
        self.wikidata_calls.append(str(wikidata_id))
        return dict(self.wikidata_entities.get(str(wikidata_id)) or {})


def test_hybrid_movie_credits_use_tvdb_cast_tmdb_crew_and_omit_executive_producer():
    sync = EmbyPeopleSync(MagicMock(), MagicMock(), MagicMock())
    credits = SimpleNamespace(
        tmdb_id=299534,
        tvdb_id=148,
        credits_source="tvdb-cast/tmdb-crew",
        cast_source="tvdb",
        crew_source="tmdb",
        cast=[{"tvdb_id": 10, "name": "TVDb Actor", "character": "Hero", "order": 0}],
        crew=[
            {"id": 20, "name": "Executive", "job": "Executive Producer"},
            {"id": 25, "name": "Normal Producer", "job": "Producer"},
            {"id": 30, "name": "Composer", "job": "Original Music Composer"},
        ],
    )

    assert sync.stage_item({"Id": "movie-1", "Type": "Movie", "ProviderIds": {"Tmdb": "299534"}}, credits)

    plan = sync.plans[0]
    assert [(credit.tmdb_id, credit.person_type, credit.role) for credit in plan.credits] == [
        (-10, "Actor", "Hero"),
        (25, "Producer", None),
        (30, "Composer", "Filmmusik"),
    ]


def test_tvdb_cast_keeps_provider_sequence_even_when_sort_values_differ():
    sync = EmbyPeopleSync(MagicMock(), MagicMock(), MagicMock())
    credits = SimpleNamespace(
        tvdb_id=1935,
        credits_source="tvdb",
        cast=[
            {"tvdb_id": 310392, "name": "Colin Farrell", "character": "Marty Faranan", "order": 5},
            {"tvdb_id": 333915, "name": "Sam Rockwell", "character": "Billy Bickle", "order": 0},
            {"tvdb_id": 253775, "name": "Woody Harrelson", "character": "Charlie Costello", "order": 5},
        ],
        crew=[],
    )

    assert sync.stage_item({"Id": "movie-1", "Type": "Movie", "ProviderIds": {"Tvdb": "1935"}}, credits)

    assert [credit.name for credit in sync.plans[0].credits] == ["Colin Farrell", "Sam Rockwell", "Woody Harrelson"]


def test_show_credits_omit_normal_producer_but_keep_executive_producer():
    sync = EmbyPeopleSync(MagicMock(), MagicMock(), MagicMock())
    credits = SimpleNamespace(
        tvdb_id=121361,
        credits_source="tvdb",
        cast=[],
        crew=[
            {"tvdb_id": 20, "name": "Normal Producer", "person_type": "Producer", "role": None},
            {"tvdb_id": 30, "name": "Executive", "person_type": "Producer", "role": "Executive Producer"},
            {"tvdb_id": 40, "name": "Showrunner", "person_type": "Producer", "role": "Showrunner"},
        ],
    )

    assert sync.stage_item({"Id": "series-1", "Type": "Series", "ProviderIds": {"Tvdb": "121361"}}, credits)

    assert [(credit.tmdb_id, credit.person_type, credit.role) for credit in sync.plans[0].credits] == [(-30, "Producer", "Executive Producer"), (-40, "Producer", "Showrunner")]


class FakeTVDb:
    def __init__(self, external_ids=None, cache=None, expiration=30):
        self.external_ids = external_ids or {}
        self.people_calls = []
        self.cache = cache
        self.expiration = expiration

    def get_people_external_ids_bulk(self, tvdb_ids, progress_callback=None):
        normalized = [int(tvdb_id) for tvdb_id in tvdb_ids]
        self.people_calls.append(normalized)
        if progress_callback:
            progress_callback(len(normalized), len(normalized))
        return {tvdb_id: dict(self.external_ids.get(tvdb_id) or {}) for tvdb_id in normalized}


class FakeEmbyServer:
    def __init__(self, movie):
        self.system_info = {"Id": "server-a"}
        self.library_id = "library-a"
        self.friendlyName = "Test Emby"
        self.emby_server_url = "http://emby.test"
        self.items = {str(movie["Id"]): copy.deepcopy(movie)}
        self.next_person_id = 9000
        self.movie_update_count = 0
        self.refreshes = []
        self.bulk_calls = []
        self.people_payloads = []

    def get_item(self, item_id, force_refresh=False):
        item = self.items.get(str(item_id))
        return copy.deepcopy(item) if item else None

    def get_items_bulk(self, ids, fields=None, force_refresh=False):
        self.bulk_calls.append([str(item_id) for item_id in ids])
        return {str(item_id): copy.deepcopy(self.items[str(item_id)]) for item_id in ids if str(item_id) in self.items}

    def get_person_info_bulk(self, tmdb_ids, provider="Tmdb"):
        result = {}
        wanted = {str(tmdb_id) for tmdb_id in tmdb_ids}
        provider_name = str(provider).casefold()
        for item_id, item in self.items.items():
            if item.get("Type") != "Person":
                continue
            tmdb_id = next(
                (str(value) for key, value in (item.get("ProviderIds") or {}).items() if str(key).casefold() == provider_name),
                None,
            )
            if tmdb_id in wanted:
                result[int(tmdb_id)] = item_id
        return result

    def update_item(self, item_id, data):
        item_id = str(item_id)
        item = self.items[item_id]
        data = copy.deepcopy(data)
        data.pop("_ReplaceProviderIds", None)
        if item.get("Type") == "Movie" and "People" in data:
            self.people_payloads.append(copy.deepcopy(data["People"]))
            materialized = []
            for person in data["People"]:
                entry = copy.deepcopy(person)
                if not str(entry.get("Id") or "").isdigit():
                    existing_id = next(
                        (candidate_id for candidate_id, candidate in self.items.items() if candidate.get("Type") == "Person" and candidate.get("Name") == entry.get("Name")),
                        None,
                    )
                    if existing_id is None:
                        existing_id = str(self.next_person_id)
                        self.next_person_id += 1
                        self.items[existing_id] = {
                            "Id": existing_id,
                            "Name": entry["Name"],
                            "SortName": entry["Name"],
                            "Type": "Person",
                            "ProviderIds": {},
                            "LockedFields": [],
                        }
                    entry["Id"] = existing_id
                materialized.append(entry)
            data["People"] = materialized
            self.movie_update_count += 1
            item["Etag"] = f"etag-{self.movie_update_count}"
        item.update(data)
        return FakeResponse()

    def refresh_item(self, item_id, *, replace_all_metadata=True, replace_all_images=False):
        item_id = str(item_id)
        self.refreshes.append(
            {
                "item_id": item_id,
                "replace_all_metadata": replace_all_metadata,
                "replace_all_images": replace_all_images,
            }
        )
        if replace_all_images and item_id in self.items:
            image_number = sum(1 for refresh in self.refreshes if refresh["item_id"] == item_id)
            self.items[item_id]["ImageTags"] = {"Primary": f"image-{image_number}"}
        return True


class FakeMovie:
    tmdb_id = 315635
    cast = [
        {"id": 64796, "name": "Tom Holland", "character": "Spider Man", "order": 2},
        {"id": 1136406, "name": "Tom Holland", "character": "Spider Man", "order": 0},
        {"id": 1145610, "name": "Tom Holland", "character": "Spider Man", "order": 1},
    ]
    crew = [
        {"id": 287, "name": "Michael Giacchino", "job": "Original Music Composer"},
    ]


def make_cache(tmp_path):
    config_path = tmp_path / "config.yml"
    config_path.write_text("test: true\n", encoding="utf-8")
    return Cache(str(config_path), expiration=30)


def make_emby_cache(tmp_path):
    return EmbyCacheCoordinator(EmbyCacheDatabase(str(tmp_path), expiration=30))


def make_movie():
    return {
        "Id": "movie-1",
        "Name": "Spider-Man: Homecoming",
        "Type": "Movie",
        "Etag": "etag-0",
        "Overview": "Peter kehrt zurück und kämpft gegen einen Bösewicht.",
        "ProviderIds": {"Tmdb": "315635", "Imdb": "tt2250912"},
        "LockedFields": [],
        "People": [{"Id": "old-tom", "Name": "Tom Holland", "Type": "Actor", "Role": "Spider Man"}],
    }


def test_roman_number_and_name_normalization():
    assert roman_number(1) == "I"
    assert roman_number(4) == "IV"
    assert roman_number(10) == "X"
    assert normalize_person_name("  Tom   Holland ") == "tom holland"


def test_competing_tvdb_alias_uses_authoritative_crosswalk_not_lowest_id(tmp_path):
    cache = make_cache(tmp_path)
    try:
        sync = EmbyPeopleSync(
            FakeEmbyServer(make_movie()),
            FakeTMDb(),
            cache,
            tvdb=FakeTVDb({271811: {"tmdb_id": 151488}}),
        )
        lower = PersonIdentity(86390, "John Sebastian", "john sebastian", "John Sebastian", tvdb_id="271811", emby_id="100")
        higher = PersonIdentity(151488, "John Sebastian", "john sebastian", "John Sebastian (II)", name_index=2, tvdb_id="271811", emby_id="200")
        alias = PersonIdentity(-271811, "John Sebastian", "john sebastian", "John Sebastian", tvdb_id="271811", emby_id="200", canonical_id=151488)
        sync._identity_records = {86390: lower, 151488: higher, -271811: alias}
        sync.identities = {86390: lower, 151488: higher, -271811: higher}
        candidates = {
            (86390, -271811): {"100"},
            (151488, -271811): {"200"},
        }

        resolved, changed = sync._resolve_competing_alias_claims(candidates)

        assert not changed
        assert set(resolved) == {(151488, -271811)}
        assert alias.canonical_id == 151488
        assert sync.identities[-271811] is higher
    finally:
        cache.close()


def test_quarantined_same_identity_relationship_does_not_create_false_friend_index(tmp_path):
    cache = make_cache(tmp_path)
    try:
        cache.update_emby_person_identity(
            "server-a",
            192,
            "Morgan Freeman",
            "morgan freeman",
            "Morgan Freeman (I)",
            name_index=1,
            emby_id="100",
            imdb_id="nm0000151",
            tvdb_id="255114",
        )
        movie = make_movie()
        movie["People"] = [
            {
                "Id": "101",
                "Name": "Emby Duplicate Person 101",
                "Type": "Actor",
                "Role": "Det. Alex Cross",
            }
        ]
        server = FakeEmbyServer(movie)
        server.items["100"] = {
            "Id": "100",
            "Name": "Morgan Freeman (I)",
            "SortName": "Morgan Freeman (I)",
            "Type": "Person",
            "ProviderIds": {"Tmdb": "192", "Tvdb": "255114", "Imdb": "nm0000151"},
            "LockedFields": ["Name", "SortName"],
        }
        server.items["101"] = {
            "Id": "101",
            "Name": "Emby Duplicate Person 101",
            "SortName": "Emby Duplicate Person 101",
            "Type": "Person",
            "ProviderIds": {},
            "LockedFields": ["Name", "SortName"],
        }

        class Credits:
            tmdb_id = 1
            credits_source = "tmdb"
            cast = [{"id": 192, "name": "Morgan Freeman", "character": "Det. Alex Cross", "order": 0}]
            crew = []

        sync = EmbyPeopleSync(
            server,
            FakeTMDb(),
            cache,
            tvdb=FakeTVDb({255114: {"tmdb_id": 192, "imdb_id": "nm0000151"}}),
        )
        sync.stage_item(server.get_item("movie-1"), Credits())
        sync.finalize_discovery()
        sync._detect_false_friends(sync.plans)

        assert sync.identities[192].name_index is None
        assert sync.identities[192].display_name == "Morgan Freeman"
        cached = cache.query_emby_person_identities("server-a")[192]
        assert cached["name_index"] is None
        assert cached["display_name"] == "Morgan Freeman"
        assert cached["duplicate_emby_ids"] == ["101"]
    finally:
        cache.close()


def test_external_ids_are_unique_to_authoritatively_linked_tmdb_identity(tmp_path):
    cache = make_cache(tmp_path)
    try:
        sync = EmbyPeopleSync(FakeEmbyServer(make_movie()), FakeTMDb(), cache)
        lower = PersonIdentity(100, "Shared Identity", "shared identity", "Shared Identity", imdb_id="nm-shared", tvdb_id="500")
        higher = PersonIdentity(200, "Other Identity", "other identity", "Other Identity", imdb_id="nm-shared", tvdb_id="500")
        alias = PersonIdentity(-500, "Shared Identity", "shared identity", "Shared Identity", imdb_id="nm-shared", tvdb_id="500", canonical_id=200)
        records = {100: lower, 200: higher, -500: alias}

        sync._enforce_unique_external_ids(records)

        assert lower.imdb_id is None
        assert lower.tvdb_id is None
        assert higher.imdb_id == "nm-shared"
        assert higher.tvdb_id == "500"
        assert alias.canonical_id == 200
        assert sync._changed_identity_ids == {100}
    finally:
        cache.close()


def test_authoritative_tvdb_crosswalk_replaces_stale_cached_primary_id(tmp_path):
    cache = make_cache(tmp_path)
    try:
        tvdb = FakeTVDb(
            {
                260544: {"tmdb_id": 18022, "imdb_id": "nm0571160"},
                7909161: {},
            }
        )
        sync = EmbyPeopleSync(FakeEmbyServer(make_movie()), FakeTMDb({18022: "nm0571160"}), cache, tvdb=tvdb)
        canonical = PersonIdentity(
            18022,
            "Gina McKee",
            "gina mckee",
            "Gina McKee",
            imdb_id="nm0571160",
            tvdb_id="7909161",
            emby_id="139729",
        )
        correct_alias = PersonIdentity(-260544, "Gina McKee", "gina mckee", "Gina McKee", tvdb_id="260544")
        stale_alias = PersonIdentity(
            -7909161,
            "Gina McKee",
            "gina mckee",
            "Gina McKee",
            imdb_id="nm0571160",
            tvdb_id="7909161",
            emby_id="139729",
            emby_etag="stale-etag",
            emby_signature="stale-signature",
            canonical_id=None,
        )
        records = {18022: canonical, -260544: correct_alias, -7909161: stale_alias}

        sync._resolve_external_cross_source_identities(records)

        assert canonical.tvdb_id == "260544"
        assert correct_alias.canonical_id == 18022
        assert stale_alias.imdb_id is None
        assert stale_alias.canonical_id is None
        assert stale_alias.emby_id is None
        assert stale_alias.emby_etag is None
        assert stale_alias.emby_signature is None
        assert correct_alias.imdb_id == "nm0571160"
        assert stale_alias.canonical_id is None
        assert stale_alias.imdb_id is None
    finally:
        cache.close()


def test_authoritative_crosswalk_cleanup_is_stable_after_cache_restart(tmp_path):
    cache = make_cache(tmp_path)
    try:
        tvdb_data = {
            260544: {"tmdb_id": 18022, "imdb_id": "nm0571160"},
            7909161: {},
        }
        first = EmbyPeopleSync(
            FakeEmbyServer(make_movie()),
            FakeTMDb({18022: "nm0571160"}),
            cache,
            tvdb=FakeTVDb(tvdb_data),
        )
        records = {
            18022: PersonIdentity(
                18022,
                "Gina McKee",
                "gina mckee",
                "Gina McKee",
                imdb_id="nm0571160",
                tvdb_id="7909161",
                emby_id="139729",
            ),
            -260544: PersonIdentity(
                -260544,
                "Gina McKee",
                "gina mckee",
                "Gina McKee",
                tvdb_id="260544",
            ),
            -7909161: PersonIdentity(
                -7909161,
                "Gina McKee",
                "gina mckee",
                "Gina McKee",
                imdb_id="nm0571160",
                tvdb_id="7909161",
                emby_id="139729",
                canonical_id=18022,
            ),
        }
        first._resolve_external_cross_source_identities(records)
        first._identity_records = records
        first._persist_all_identities()

        restarted_records = {tmdb_id: PersonIdentity.from_cache(row) for tmdb_id, row in cache.query_emby_person_identities("server-a").items()}
        restarted_tvdb = FakeTVDb(tvdb_data)
        restarted = EmbyPeopleSync(
            FakeEmbyServer(make_movie()),
            FakeTMDb({18022: "nm0571160"}),
            cache,
            tvdb=restarted_tvdb,
        )
        restarted._resolve_external_cross_source_identities(restarted_records)

        assert restarted_records[18022].tvdb_id == "260544"
        assert restarted_records[-7909161].imdb_id is None
        assert restarted_records[-7909161].emby_id is None
        assert restarted_records[-7909161].canonical_id is None
        assert restarted._changed_identity_ids == set()
        assert restarted_tvdb.people_calls == []
    finally:
        cache.close()


def test_unavailable_tvdb_crosswalk_preserves_last_known_mapping(tmp_path):
    cache = make_cache(tmp_path)
    try:
        tvdb = FakeTVDb()
        tvdb.get_people_external_ids_bulk = MagicMock(return_value={})
        sync = EmbyPeopleSync(FakeEmbyServer(make_movie()), FakeTMDb(), cache, tvdb=tvdb)
        canonical = PersonIdentity(
            18022,
            "Gina McKee",
            "gina mckee",
            "Gina McKee",
            imdb_id="nm0571160",
            tvdb_id="260544",
        )
        alias = PersonIdentity(
            -260544,
            "Gina McKee",
            "gina mckee",
            "Gina McKee",
            imdb_id="nm0571160",
            tvdb_id="260544",
            canonical_id=18022,
        )
        records = {18022: canonical, -260544: alias}

        sync._resolve_external_cross_source_identities(records)

        assert canonical.tvdb_id == "260544"
        assert alias.canonical_id == 18022
        assert alias.imdb_id == "nm0571160"
    finally:
        cache.close()


def test_authoritative_tvdb_tmdb_link_discards_conflicting_tvdb_imdb_id(tmp_path):
    cache = make_cache(tmp_path)
    try:
        tvdb = FakeTVDb(
            {
                9130769: {
                    "tmdb_id": 2603963,
                    "imdb_id": "nm7955430",
                }
            }
        )
        tmdb = FakeTMDb({2603963: "nm10017518"})
        sync = EmbyPeopleSync(FakeEmbyServer(make_movie()), tmdb, cache, tvdb=tvdb)
        canonical = PersonIdentity(
            2603963,
            "Kosuke Echigoya",
            "kosuke echigoya",
            "Kosuke Echigoya",
            imdb_id="nm10017518",
        )
        alias = PersonIdentity(
            -9130769,
            "Kosuke Echigoya",
            "kosuke echigoya",
            "Kosuke Echigoya",
            imdb_id="nm7955430",
            tvdb_id="9130769",
        )
        records = {2603963: canonical, -9130769: alias}

        sync._resolve_external_cross_source_identities(records)
        sync._enforce_unique_external_ids(records)

        assert alias.canonical_id == 2603963
        assert canonical.tvdb_id == "9130769"
        assert canonical.imdb_id == "nm10017518"
        assert alias.imdb_id == "nm10017518"
        assert sync._changed_identity_ids == {-9130769, 2603963}
        assert tmdb.person_calls == [(2603963, "external_ids")]
    finally:
        cache.close()


def test_authoritative_tvdb_crosswalk_creates_missing_tmdb_identity(tmp_path):
    cache = make_cache(tmp_path)
    try:
        tvdb = FakeTVDb({378703: {"tmdb_id": 2749, "imdb_id": "nm0000015"}})
        tmdb = FakeTMDb({2749: "nm0000015"}, names={2749: "James Dean"})
        sync = EmbyPeopleSync(FakeEmbyServer(make_movie()), tmdb, cache, tvdb=tvdb)
        alias = PersonIdentity(
            -378703,
            "James Dean",
            "james dean",
            "James Dean",
            tvdb_id="378703",
            emby_id="233546",
        )
        records = {-378703: alias}

        sync._resolve_external_cross_source_identities(records)

        canonical = records[2749]
        assert canonical.base_name == "James Dean"
        assert canonical.imdb_id == "nm0000015"
        assert canonical.tvdb_id == "378703"
        assert canonical.emby_id == "233546"
        assert canonical.external_verified_at is not None
        assert alias.canonical_id == 2749
        assert alias.imdb_id == "nm0000015"
        assert tmdb.person_calls == [(2749, "external_ids")]
    finally:
        cache.close()


def test_verified_emby_ids_bridge_empty_tvdb_crosswalk_for_paul_webster(tmp_path):
    cache = make_cache(tmp_path)
    try:
        server = FakeEmbyServer(make_movie())
        server.items["2384219"] = {
            "Id": "2384219",
            "Name": "Paul Webster (II)",
            "SortName": "Paul Webster (II)",
            "Type": "Person",
            "ProviderIds": {
                "Tmdb": "2564664",
                "Tvdb": "276066",
                "Imdb": "nm0916988",
            },
            "LockedFields": ["Name", "SortName"],
            "Etag": "paul-actor",
        }
        producer = PersonIdentity(
            21378,
            "Paul Webster",
            "paul webster",
            "Paul Webster (I)",
            name_index=1,
            imdb_id="nm0916986",
            emby_id="2596035",
        )
        alias = PersonIdentity(
            -276066,
            "Paul Webster",
            "paul webster",
            "Paul Webster (II)",
            name_index=2,
            tvdb_id="276066",
            emby_id="2384219",
        )
        tmdb = FakeTMDb(
            {2564664: "nm0916988"},
            names={2564664: "Paul Webster"},
        )
        sync = EmbyPeopleSync(server, tmdb, cache, tvdb=FakeTVDb({276066: {}}))
        sync._identity_records = {21378: producer, -276066: alias}
        sync.identities = dict(sync._identity_records)

        sync._reconcile_verified_emby_identity_bridges({"2384219": server.get_item("2384219")})

        actor = sync._identity_records[2564664]
        assert actor.imdb_id == "nm0916988"
        assert actor.tvdb_id == "276066"
        assert actor.emby_id == "2384219"
        assert actor.display_name == "Paul Webster (II)"
        assert producer.imdb_id == "nm0916986"
        assert producer.display_name == "Paul Webster (I)"
        assert alias.canonical_id == 2564664
        assert alias.imdb_id == "nm0916988"
        assert sync.identities[-276066] is actor
        assert tmdb.person_calls == [(2564664, "external_ids")]
    finally:
        cache.close()


def test_repeated_item_relationships_bridge_tvdb_identity_without_primary_tvdb_id(tmp_path):
    cache = make_cache(tmp_path)
    try:
        server = FakeEmbyServer(make_movie())
        server.items["2589347"] = {
            "Id": "2589347",
            "Name": "Tedd Pierce (I)",
            "SortName": "Tedd Pierce (I)",
            "Type": "Person",
            "ProviderIds": {
                "Tmdb": "375782",
                "Imdb": "nm0682481",
            },
            "LockedFields": ["Name", "SortName"],
            "Etag": "tedd",
        }
        canonical = PersonIdentity(
            375782,
            "Tedd Pierce",
            "tedd pierce",
            "Tedd Pierce (I)",
            name_index=1,
            imdb_id="nm0682481",
            emby_id="2589347",
        )
        alias = PersonIdentity(
            -328191,
            "Tedd Pierce",
            "tedd pierce",
            "Tedd Pierce (II)",
            name_index=2,
            tvdb_id="328191",
        )
        tmdb = FakeTMDb(
            {375782: "nm0682481"},
            names={375782: "Tedd Pierce"},
        )
        sync = EmbyPeopleSync(server, tmdb, cache)
        sync._identity_records = {375782: canonical, -328191: alias}
        sync.identities = dict(sync._identity_records)

        sync._reconcile_verified_emby_identity_bridges(
            {"2589347": server.get_item("2589347")},
            [("2589347", -328191), ("2589347", -328191)],
        )

        assert alias.canonical_id == 375782
        assert alias.imdb_id == "nm0682481"
        assert canonical.tvdb_id == "328191"
        assert canonical.name_index is None
        assert canonical.display_name == "Tedd Pierce"
        assert sync.identities[-328191] is canonical
    finally:
        cache.close()


def test_single_item_relationship_does_not_bridge_tvdb_identity_by_name_alone(tmp_path):
    cache = make_cache(tmp_path)
    try:
        server = FakeEmbyServer(make_movie())
        server.items["700"] = {
            "Id": "700",
            "Name": "Same Name",
            "SortName": "Same Name",
            "Type": "Person",
            "ProviderIds": {"Tmdb": "100", "Imdb": "nm100"},
            "LockedFields": [],
        }
        alias = PersonIdentity(
            -500,
            "Same Name",
            "same name",
            "Same Name",
            tvdb_id="500",
        )
        sync = EmbyPeopleSync(
            server,
            FakeTMDb({100: "nm100"}, names={100: "Same Name"}),
            cache,
        )
        sync._identity_records = {-500: alias}
        sync.identities = dict(sync._identity_records)

        sync._reconcile_verified_emby_identity_bridges(
            {"700": server.get_item("700")},
            [("700", -500)],
        )

        assert 100 not in sync._identity_records
        assert alias.canonical_id is None
    finally:
        cache.close()


def test_unique_item_credit_merges_alexandra_kluge_and_refreshes_emby_metadata(tmp_path):
    cache = make_cache(tmp_path)
    try:
        cache.update_emby_person_identity(
            "server-a",
            1041578,
            "Alexandra Kluge",
            "alexandra kluge",
            "Alexandra Kluge (I)",
            name_index=1,
        )
        cache.update_emby_person_identity(
            "server-a",
            -8237646,
            "Alexandra Kluge",
            "alexandra kluge",
            "Alexandra Kluge (II)",
            name_index=2,
            tvdb_id="8237646",
            emby_id="2607563",
        )
        movie = make_movie()
        movie.update(
            {
                "Id": "2371987",
                "Name": "Abschied von gestern",
                "ProviderIds": {
                    "Tmdb": "89785",
                    "Tvdb": "151675",
                    "Imdb": "tt0060063",
                },
                "People": [
                    {
                        "Id": "2607563",
                        "Name": "Alexandra Kluge (II)",
                        "Type": "Actor",
                        "Role": "Anita G.",
                    }
                ],
            }
        )
        server = FakeEmbyServer(movie)
        server.items["2607563"] = {
            "Id": "2607563",
            "Name": "Alexandra Kluge (II)",
            "SortName": "Alexandra Kluge (II)",
            "Type": "Person",
            "ProviderIds": {"Tvdb": "8237646"},
            "LockedFields": ["Name", "SortName"],
            "ImageTags": {},
        }
        credits = SimpleNamespace(
            tmdb_id=89785,
            tvdb_id=151675,
            credits_source="tvdb-cast/tmdb-crew",
            cast_source="tvdb",
            crew_source="tmdb",
            cast=[
                {
                    "tvdb_id": 8237646,
                    "name": "Alexandra Kluge",
                    "character": "Anita G.",
                    "order": 0,
                }
            ],
            crew=[],
            identity_links=[
                {
                    "tmdb_id": 1041578,
                    "tvdb_id": 8237646,
                    "name": "Alexandra Kluge",
                    "role": "Anita G.",
                    "person_type": "Actor",
                }
            ],
        )
        sync = EmbyPeopleSync(
            server,
            FakeTMDb(
                {1041578: "nm0460177"},
                names={1041578: "Alexandra Kluge"},
                wikidata_ids={1041578: "Q92404"},
                wikidata_entities={
                    "Q92404": {
                        "wikidata_id": "Q92404",
                        "tmdb_id": 1041578,
                        "tvdb_id": "8237646",
                        "imdb_id": "nm0460177",
                    }
                },
            ),
            cache,
            tvdb=FakeTVDb({8237646: {}}),
        )

        assert sync.stage_item(server.get_item("2371987"), credits)
        summary = sync.apply()

        assert summary["failed"] == 0
        assert sync.identities[1041578] is sync.identities[-8237646]
        assert sync.identities[1041578].display_name == "Alexandra Kluge"
        assert sync.identities[1041578].emby_id == "2607563"
        assert server.get_item("2607563")["Name"] == "Alexandra Kluge"
        assert server.get_item("2607563")["ProviderIds"] == {
            "Tmdb": "1041578",
            "Imdb": "nm0460177",
            "Tvdb": "8237646",
            "Wikidata": "Q92404",
        }
        assert server.get_item("2607563")["ImageTags"]["Primary"]
        assert any(refresh["item_id"] == "2607563" and refresh["replace_all_metadata"] and refresh["replace_all_images"] for refresh in server.refreshes)
        relationship = server.get_item("2371987")["People"][0]
        assert relationship == {
            "Id": "2607563",
            "Name": "Alexandra Kluge",
            "Type": "Actor",
            "Role": "Anita G.",
        }
        cached = cache.query_emby_person_identities("server-a")
        assert cached[1041578]["imdb_id"] == "nm0460177"
        assert cached[1041578]["tvdb_id"] == "8237646"
        assert cached[1041578]["wikidata_id"] == "Q92404"
        assert cached[1041578]["emby_id"] == "2607563"
        assert cached[1041578]["name_index"] is None
        assert cached[-8237646]["canonical_id"] == 1041578
        assert cached[-8237646]["name_index"] is None

        cached_tmdb = FakeTMDb()
        second = EmbyPeopleSync(
            server,
            cached_tmdb,
            cache,
            tvdb=FakeTVDb(),
        )
        assert second.stage_item(server.get_item("2371987"), credits)
        second_summary = second.apply()

        assert second_summary["failed"] == 0
        assert second_summary["updated"] == 0
        assert cached_tmdb.person_calls == []
        assert cached_tmdb.wikidata_calls == []
    finally:
        cache.close()


def test_empty_tvdb_crosswalk_preserves_externally_verified_contextual_bridge(tmp_path):
    cache = make_cache(tmp_path)
    try:
        verified_at = "2026-07-26T08:00:00"
        canonical = PersonIdentity(
            2564664,
            "Paul Webster",
            "paul webster",
            "Paul Webster",
            imdb_id="nm0916988",
            tvdb_id="276066",
            external_verified_at=verified_at,
        )
        alias = PersonIdentity(
            -276066,
            "Paul Webster",
            "paul webster",
            "Paul Webster",
            imdb_id="nm0916988",
            tvdb_id="276066",
            canonical_id=2564664,
        )
        records = {2564664: canonical, -276066: alias}
        sync = EmbyPeopleSync(
            FakeEmbyServer(make_movie()),
            FakeTMDb(),
            cache,
            tvdb=FakeTVDb({276066: {}}),
        )

        sync._resolve_external_cross_source_identities(records)

        assert alias.canonical_id == 2564664
        assert alias.imdb_id == "nm0916988"
        assert canonical.tvdb_id == "276066"
    finally:
        cache.close()


def test_fresh_alias_consumes_newer_cached_tvdb_crosswalk_without_waiting_for_audit(tmp_path):
    cache = make_cache(tmp_path)
    try:
        cache.update_tvdb_people_external_ids(
            {
                531105: {
                    "tmdb_id": 1281060,
                    "imdb_id": "nm4808201",
                    "wikidata_id": None,
                }
            }
        )
        alias = PersonIdentity(
            -531105,
            "Christopher Monfette",
            "christopher monfette",
            "Christopher Monfette",
            imdb_id="nm4808201",
            tvdb_id="531105",
            emby_id="2514801",
            external_verified_at=datetime.now().isoformat(timespec="seconds"),
        )
        records = {-531105: alias}
        fake_tvdb = FakeTVDb(
            {
                531105: {
                    "tmdb_id": 1281060,
                    "imdb_id": "nm4808201",
                }
            },
            cache=cache,
        )
        sync = EmbyPeopleSync(
            FakeEmbyServer(make_movie()),
            FakeTMDb({1281060: "nm4808201"}, names={1281060: "Christopher Monfette"}),
            cache,
            tvdb=fake_tvdb,
        )
        sync._discovered_names[-531105] = "Christopher Monfette"

        sync._resolve_external_cross_source_identities(records)

        assert fake_tvdb.people_calls == [[531105]]
        assert alias.canonical_id == 1281060
        assert records[1281060].tvdb_id == "531105"
        assert records[1281060].imdb_id == "nm4808201"
        assert records[1281060].emby_id == "2514801"
    finally:
        cache.close()


def test_verified_emby_bridge_rejects_tmdb_imdb_mismatch(tmp_path):
    cache = make_cache(tmp_path)
    try:
        server = FakeEmbyServer(make_movie())
        server.items["2384219"] = {
            "Id": "2384219",
            "Name": "Paul Webster",
            "SortName": "Paul Webster",
            "Type": "Person",
            "ProviderIds": {
                "Tmdb": "2564664",
                "Tvdb": "276066",
                "Imdb": "nm-wrong",
            },
            "LockedFields": [],
        }
        alias = PersonIdentity(
            -276066,
            "Paul Webster",
            "paul webster",
            "Paul Webster",
            tvdb_id="276066",
            emby_id="2384219",
        )
        sync = EmbyPeopleSync(
            server,
            FakeTMDb({2564664: "nm0916988"}, names={2564664: "Paul Webster"}),
            cache,
        )
        sync._identity_records = {-276066: alias}
        sync.identities = dict(sync._identity_records)

        sync._reconcile_verified_emby_identity_bridges({"2384219": server.get_item("2384219")})

        assert 2564664 not in sync._identity_records
        assert alias.canonical_id is None
    finally:
        cache.close()


def test_disjoint_provider_ids_fail_safely_without_allocating_name_index(tmp_path):
    cache = make_cache(tmp_path)
    try:
        movie = make_movie()
        movie["People"] = [
            {
                "Id": "700",
                "Name": "Paul Webster",
                "Type": "GuestStar",
                "Role": "Agent Ron",
            }
        ]
        server = FakeEmbyServer(movie)
        server.items["700"] = {
            "Id": "700",
            "Name": "Paul Webster",
            "SortName": "Paul Webster",
            "Type": "Person",
            "ProviderIds": {"Tmdb": "21378", "Imdb": "nm0916986"},
            "LockedFields": [],
        }

        class TVDbCredits:
            tvdb_id = 189277
            credits_source = "tvdb"
            cast = [
                {
                    "tvdb_id": 276066,
                    "name": "Paul Webster",
                    "character": "Agent Ron",
                    "person_type": "GuestStar",
                    "order": 0,
                }
            ]
            crew = []

        sync = EmbyPeopleSync(server, FakeTMDb(), cache)
        sync.stage_item(server.get_item("movie-1"), TVDbCredits())
        sync.finalize_discovery()
        sync._detect_false_friends()

        identity = sync.identities[-276066]
        assert identity.name_index is None
        assert identity.display_name == "Paul Webster"
        assert -276066 in sync._identity_errors
        assert "no externally verified bridge" in str(sync._identity_errors[-276066])
    finally:
        cache.close()


def test_recovery_does_not_index_unresolved_cross_provider_identity(tmp_path):
    cache = make_cache(tmp_path)
    try:
        server = FakeEmbyServer(make_movie())
        server.items["700"] = {
            "Id": "700",
            "Name": "Tedd Pierce (I)",
            "SortName": "Tedd Pierce (I)",
            "Type": "Person",
            "ProviderIds": {"Tmdb": "375782", "Imdb": "nm0682481"},
            "LockedFields": ["Name", "SortName"],
        }
        identity = PersonIdentity(
            -328191,
            "Tedd Pierce",
            "tedd pierce",
            "Tedd Pierce",
            tvdb_id="328191",
        )
        sync = EmbyPeopleSync(server, FakeTMDb(), cache)
        sync._identity_records = {-328191: identity}
        sync.identities = {-328191: identity}
        actual_item = {
            "Id": "movie-1",
            "People": [
                {
                    "Id": "700",
                    "Name": "Tedd Pierce (I)",
                    "Type": "Actor",
                    "Role": "Announcer",
                }
            ],
        }
        desired_people = [
            {
                "Id": "Tedd Pierce",
                "Name": "Tedd Pierce",
                "Type": "Actor",
                "Role": "Announcer",
            }
        ]

        recovered = sync._recover_false_friend_people(
            "movie-1",
            desired_people,
            actual_item,
        )

        assert recovered is actual_item
        assert identity.name_index is None
        assert identity.display_name == "Tedd Pierce"
        assert -328191 in sync._identity_errors
    finally:
        cache.close()


def test_single_identity_store_persists_external_verification_timestamp(tmp_path):
    cache = make_cache(tmp_path)
    try:
        sync = EmbyPeopleSync(FakeEmbyServer(make_movie()), FakeTMDb(), cache)
        identity = PersonIdentity(
            2749,
            "James Dean",
            "james dean",
            "James Dean",
            imdb_id="nm0000015",
            tvdb_id="378703",
            external_verified_at="2026-07-26T08:00:00",
        )

        sync._store_identity(identity)

        cached = cache.query_emby_person_identities("server-a")[2749]
        assert cached["external_verified_at"] == "2026-07-26T08:00:00"
    finally:
        cache.close()


def test_stale_cached_locks_accept_no_response_when_live_person_is_already_unlocked(tmp_path):
    cache = make_cache(tmp_path)
    try:

        class NoOpUnlockServer(FakeEmbyServer):
            def update_item(self, item_id, data):
                if str(item_id) == "700" and data.get("LockedFields") == [] and set(data) == {"Id", "LockedFields"}:
                    return None
                return super().update_item(item_id, data)

        server = NoOpUnlockServer(make_movie())
        server.items["700"] = {
            "Id": "700",
            "Name": "Tedd Pierce",
            "SortName": "Tedd Pierce",
            "Type": "Person",
            "ProviderIds": {
                "Tmdb": "375782",
                "Imdb": "nm0682481",
                "Tvdb": "328191",
            },
            "LockedFields": [],
        }
        identity = PersonIdentity(
            375782,
            "Tedd Pierce",
            "tedd pierce",
            "Tedd Pierce",
            imdb_id="nm0682481",
            tvdb_id="328191",
            emby_id="700",
        )
        sync = EmbyPeopleSync(server, FakeTMDb(), cache)
        sync.identities = {375782: identity}
        sync._identity_records = dict(sync.identities)
        sync._resolved_person_items[375782] = {
            **server.get_item("700"),
            "Name": "Tedd Pierce (I)",
            "SortName": "Tedd Pierce (I)",
            "LockedFields": ["Name", "SortName"],
        }

        sync._ensure_person_metadata(identity)

        person = server.get_item("700")
        assert person["Name"] == "Tedd Pierce"
        assert person["LockedFields"] == []
        assert person["ProviderIds"]["Tvdb"] == "328191"
    finally:
        cache.close()


def test_emby_person_refresh_requests_full_metadata_and_image_replacement(monkeypatch):
    server = emby_server_module.EmbyServer.__new__(emby_server_module.EmbyServer)
    server.emby_server_url = "http://emby.test"
    server.api_key = "secret"
    server.seconds_between_requests = 0
    server.session = MagicMock()
    server.session.post.return_value = SimpleNamespace(status_code=204)
    server._ensure_http_session = MagicMock()
    server.invalidate_item = MagicMock()
    monkeypatch.setattr(emby_server_module.time, "sleep", MagicMock())

    assert server.refresh_item("9000", replace_all_metadata=True, replace_all_images=True)

    _, kwargs = server.session.post.call_args
    assert kwargs["params"] == {
        "api_key": "secret",
        "Recursive": "false",
        "MetadataRefreshMode": "FullRefresh",
        "ImageRefreshMode": "FullRefresh",
        "ReplaceAllMetadata": "true",
        "ReplaceAllImages": "true",
    }
    server.invalidate_item.assert_called_once_with("9000")


def test_collision_indices_follow_tmdb_id_order(tmp_path):
    cache = make_cache(tmp_path)
    try:
        server = FakeEmbyServer(make_movie())
        sync = EmbyPeopleSync(server, FakeTMDb(), cache)
        sync.stage_item(server.get_item("movie-1"), FakeMovie())
        sync.finalize_discovery()

        assert sync.identities[64796].display_name == "Tom Holland (I)"
        assert sync.identities[1136406].display_name == "Tom Holland (II)"
        assert sync.identities[1145610].display_name == "Tom Holland (III)"
        sync.apply()
        assert server.refreshes == []

        class LaterMovie:
            tmdb_id = 1
            cast = [{"id": 5, "name": "Tom Holland", "character": "Historian", "order": 0}]
            crew = []

        later = EmbyPeopleSync(server, FakeTMDb(), cache)
        later.stage_item(server.get_item("movie-1"), LaterMovie())
        later.finalize_discovery()

        assert later.identities[5].display_name == "Tom Holland (I)"
        assert later.identities[64796].display_name == "Tom Holland (II)"
        assert later.identities[1136406].display_name == "Tom Holland (III)"
        assert later.identities[1145610].display_name == "Tom Holland (IV)"
        later.apply()

        cached = cache.query_emby_person_identities("server-a")
        assert cached[5]["name_index"] == 1
        assert cached[5]["display_name"] == "Tom Holland (I)"
        assert cached[64796]["name_index"] == 2
        assert cached[64796]["emby_id"] == "9002"
        assert server.get_item("9002")["Name"] == "Tom Holland (II)"
        for tmdb_id, expected_index in ((1136406, "III"), (1145610, "IV")):
            person = server.get_item(cached[tmdb_id]["emby_id"])
            assert person["Name"] == f"Tom Holland ({expected_index})"
            assert person["ImageTags"]["Primary"] == "image-1"
    finally:
        cache.close()


def test_finalize_groups_people_identities_in_one_pass(tmp_path):
    cache = make_cache(tmp_path)
    try:
        server = FakeEmbyServer(make_movie())
        sync = EmbyPeopleSync(server, FakeTMDb(), cache)
        sync.stage_item(server.get_item("movie-1"), FakeMovie())
        original = sync._unique_identities
        calls = []

        def counted_unique_identities():
            calls.append(True)
            return original()

        sync._unique_identities = counted_unique_identities
        sync.finalize_discovery()

        assert len(calls) == 1
    finally:
        cache.close()


def test_composer_is_a_credit_not_a_separate_identity_type(tmp_path):
    cache = make_cache(tmp_path)
    try:
        server = FakeEmbyServer(make_movie())
        sync = EmbyPeopleSync(server, FakeTMDb(), cache)
        sync.stage_item(server.get_item("movie-1"), FakeMovie())

        composer = next(credit for credit in sync.plans[0].credits if credit.person_type == "Composer")
        assert composer.tmdb_id == 287
        assert composer.role == "Filmmusik"
        assert sync._discovered_names[287] == "Michael Giacchino"
    finally:
        cache.close()


def test_tvdb_show_credits_are_source_aware_and_keep_external_ids(tmp_path):
    cache = make_cache(tmp_path)
    try:
        server = FakeEmbyServer(make_movie())

        class TVDbShowCredits:
            credits_source = "tvdb"
            cast = [
                {
                    "tmdb_id": 64796,
                    "tvdb_id": 301234,
                    "imdb_id": "nm0276169",
                    "name": "Tom Holland",
                    "character": "Peter Parker",
                    "order": 0,
                }
            ]
            crew = []

        sync = EmbyPeopleSync(server, FakeTMDb(), cache)
        assert sync.stage_item(server.get_item("movie-1"), TVDbShowCredits())
        sync.finalize_discovery()

        assert sync.plans[0].credits_source == "tvdb"
        assert sync.identities[64796].imdb_id == "nm0276169"
        assert sync.identities[64796].tvdb_id == "301234"
    finally:
        cache.close()


def test_tvdb_only_person_identity_is_materialized_with_tvdb_provider(tmp_path):
    cache = make_cache(tmp_path)
    try:
        server = FakeEmbyServer(make_movie())

        class TVDbShowCredits:
            credits_source = "tvdb"
            tvdb_id = 250749
            tmdb_id = None
            cast = [
                {
                    "tvdb_id": 503820,
                    "name": "Rafael Lozano",
                    "character": "Marco Álvares",
                    "order": 0,
                }
            ]
            crew = []

        sync = EmbyPeopleSync(server, FakeTMDb(), cache)
        assert sync.stage_item(server.get_item("movie-1"), TVDbShowCredits())

        summary = sync.apply()

        assert summary["updated"] == 1
        identity = sync.identities[-503820]
        assert identity.provider == "Tvdb"
        assert identity.emby_id == "9000"
        assert server.get_item("9000")["ProviderIds"]["Tvdb"] == "503820"
        assert len(server.people_payloads) == 1
        assert server.people_payloads[0][0]["Id"] == server.people_payloads[0][0]["Name"] == "Rafael Lozano"
        assert set(server.people_payloads[0][0]) == {"Id", "Name", "Type", "Role"}
    finally:
        cache.close()


def test_apply_materializes_people_sets_provider_ids_and_preserves_overview(tmp_path):
    cache = make_emby_cache(tmp_path)
    try:
        server = FakeEmbyServer(make_movie())
        tmdb = FakeTMDb(
            {
                64796: "nm0276169",
                1136406: "nm4043618",
                1145610: "nm2286597",
                287: "nm0006133",
            }
        )
        original_overview = server.get_item("movie-1")["Overview"]
        sync = EmbyPeopleSync(server, tmdb, cache)
        sync.stage_item(server.get_item("movie-1"), FakeMovie())

        summary = sync.apply()

        assert summary == {"staged": 1, "updated": 1, "skipped": 0, "failed": 0, "created": 3}
        movie = server.get_item("movie-1")
        assert movie["Overview"] == original_overview
        assert server.movie_update_count == 1
        assert all(str(person["Id"]).isdigit() for person in movie["People"])
        assert [person["Name"] for person in movie["People"][:3]] == [
            "Tom Holland (II)",
            "Tom Holland (III)",
            "Tom Holland (I)",
        ]
        assert movie["People"][3]["Name"] == "Michael Giacchino"
        assert movie["People"][3]["Type"] == "Composer"
        assert movie["People"][3]["Role"] == "Filmmusik"
        assert all(person["Id"] == person["Name"] for person in server.people_payloads[0])
        assert all(set(person).issubset({"Id", "Name", "Type", "Role"}) for person in server.people_payloads[0])

        identities = cache.query_emby_person_identities("server-a")
        for tmdb_id, imdb_id in tmdb.imdb_ids.items():
            person = server.get_item(identities[tmdb_id]["emby_id"])
            assert person["ProviderIds"]["Tmdb"] == str(tmdb_id)
            if identities[tmdb_id]["name_index"] is not None:
                assert person["ProviderIds"]["Imdb"] == imdb_id
            assert person["Name"] == identities[tmdb_id]["display_name"]
            if identities[tmdb_id]["normalized_name"] == "tom holland":
                assert {"Name", "SortName"}.issubset(person["LockedFields"])
            else:
                assert not ({"Name", "SortName"} & set(person["LockedFields"]))
            assert "ImageTags" not in person

        assert server.refreshes == []

        second = EmbyPeopleSync(server, tmdb, cache)
        server.bulk_calls.clear()
        tmdb.person_calls.clear()
        second.stage_item(server.get_item("movie-1"), FakeMovie())
        second_summary = second.apply()
        assert second_summary["updated"] == 0
        assert second_summary["skipped"] == 1
        assert server.bulk_calls == []
        assert tmdb.person_calls == []
        assert server.get_item("movie-1")["Overview"] == original_overview
    finally:
        cache.close()


def test_existing_matching_person_fills_missing_ids_without_full_refresh(tmp_path):
    cache = make_cache(tmp_path)
    try:
        cache.update_emby_person_identity(
            "server-a",
            1056779,
            "Carlos González",
            "carlos gonzález",
            "Carlos González (II)",
            name_index=2,
            emby_id="9000",
        )
        movie = make_movie()
        movie["People"] = [{"Id": "9000", "Name": "Carlos González (II)", "Type": "Actor", "Role": "Self"}]
        server = FakeEmbyServer(movie)
        server.items["9000"] = {
            "Id": "9000",
            "Name": "Carlos González (II)",
            "SortName": "Carlos González (II)",
            "Type": "Person",
            "ProviderIds": {"Tmdb": "1056779"},
            "LockedFields": ["Name", "SortName"],
        }

        class MatchingCredits:
            tmdb_id = 1
            credits_source = "tmdb"
            cast = [{"id": 1056779, "name": "Carlos González", "character": "Self", "order": 0}]
            crew = []

        sync = EmbyPeopleSync(server, FakeTMDb({1056779: "nm1056779"}), cache)
        sync.stage_item(server.get_item("movie-1"), MatchingCredits())
        summary = sync.apply()

        assert summary["failed"] == 0
        person = server.get_item("9000")
        assert person["Name"] == "Carlos González (II)"
        assert person["SortName"] == "Carlos González (II)"
        assert person["ProviderIds"]["Tmdb"] == "1056779"
        assert person["ProviderIds"]["Imdb"] == "nm1056779"
        assert server.refreshes == []
    finally:
        cache.close()


def test_person_update_noop_response_is_verified_by_fresh_read(tmp_path):
    cache = make_cache(tmp_path)
    try:
        cache.update_emby_person_identity(
            "server-a",
            1056779,
            "Carlos GonzÃ¡lez",
            "carlos gonzÃ¡lez",
            "Carlos GonzÃ¡lez",
            emby_id="9000",
            imdb_id="nm1056779",
        )
        movie = make_movie()
        server = FakeEmbyServer(movie)
        server.items["9000"] = {
            "Id": "9000",
            "Name": "Carlos GonzÃ¡lez",
            "SortName": "Carlos GonzÃ¡lez",
            "Type": "Person",
            "Etag": "person-etag",
            "ProviderIds": {"Tmdb": "1056779"},
            "LockedFields": [],
        }

        original_update_item = server.update_item

        def apply_but_return_no_response(item_id, data):
            original_update_item(item_id, data)
            return None

        server.update_item = apply_but_return_no_response
        sync = EmbyPeopleSync(server, FakeTMDb(), cache)
        sync.finalize_discovery()
        identity = emby_people_module.PersonIdentity(
            tmdb_id=1056779,
            base_name="Carlos GonzÃ¡lez",
            normalized_name="carlos gonzÃ¡lez",
            display_name="Carlos GonzÃ¡lez",
            emby_id="9000",
            imdb_id="nm1056779",
        )
        sync.identities[identity.tmdb_id] = identity
        sync._identity_records[identity.tmdb_id] = identity

        sync._ensure_person_metadata(identity)

        assert server.get_item("9000")["ProviderIds"]["Imdb"] == "nm1056779"
        assert cache.query_emby_person_identities("server-a")[1056779]["emby_etag"] is None
    finally:
        cache.close()


def test_person_provider_id_key_casing_does_not_trigger_update(tmp_path):
    cache = make_cache(tmp_path)
    try:
        movie = make_movie()
        server = FakeEmbyServer(movie)
        server.items["9000"] = {
            "Id": "9000",
            "Name": "Bob Neuwirth",
            "SortName": "Bob Neuwirth",
            "Type": "Person",
            "Etag": "person-etag",
            "ProviderIds": {"tmdb": "1489", "imdb": "nm0627260"},
            "LockedFields": [],
        }
        update_calls = []
        original_update_item = server.update_item

        def track_update(item_id, data):
            update_calls.append((str(item_id), copy.deepcopy(data)))
            return original_update_item(item_id, data)

        server.update_item = track_update
        identity = emby_people_module.PersonIdentity(
            tmdb_id=1489,
            base_name="Bob Neuwirth",
            normalized_name="bob neuwirth",
            display_name="Bob Neuwirth",
            emby_id="9000",
            imdb_id="nm0627260",
        )
        sync = EmbyPeopleSync(server, FakeTMDb(), cache)
        sync.identities[identity.tmdb_id] = identity
        sync._identity_records[identity.tmdb_id] = identity

        sync._ensure_person_metadata(identity)

        assert update_calls == []
        assert identity.emby_etag is None
    finally:
        cache.close()


def test_cache_scopes_person_mapping_by_server(tmp_path):
    cache = make_cache(tmp_path)
    try:
        for server_id, emby_id in (("server-a", "10"), ("server-b", "20")):
            cache.update_emby_person_identity(
                server_id,
                1136406,
                "Tom Holland",
                "tom holland",
                "Tom Holland",
                emby_id=emby_id,
            )
        assert cache.query_emby_person_identities("server-a")[1136406]["emby_id"] == "10"
        assert cache.query_emby_person_identities("server-b")[1136406]["emby_id"] == "20"
    finally:
        cache.close()


def test_bulk_omitted_locked_fields_uses_verified_person_etag(tmp_path):
    cache = make_emby_cache(tmp_path)
    try:
        for tmdb_id, display_name, name_index, emby_id in (
            (100, "Same Name", None, "500"),
            (200, "Same Name (II)", 2, "600"),
        ):
            cache.update_emby_person_identity(
                "server-a",
                tmdb_id,
                "Same Name",
                "same name",
                display_name,
                name_index=name_index,
                emby_id=emby_id,
            )

        class BulkOmitsLockedFieldsServer(FakeEmbyServer):
            def __init__(self, movie):
                super().__init__(movie)
                self.direct_person_calls = []
                self.person_bulk_round = 0

            def get_item(self, item_id, force_refresh=False):
                item = super().get_item(item_id, force_refresh=force_refresh)
                if force_refresh and item and item.get("Type") == "Person":
                    self.direct_person_calls.append(str(item_id))
                    item["Etag"] = f"unstable-direct-{len(self.direct_person_calls)}"
                return item

            def get_items_bulk(self, ids, fields=None, force_refresh=False):
                items = super().get_items_bulk(ids, fields=fields, force_refresh=force_refresh)
                self.person_bulk_round += 1
                for item in items.values():
                    item.pop("LockedFields", None)
                    if item.get("Type") == "Person":
                        # Emby can change Person ETags because of derived
                        # relationship state even when identity metadata is
                        # byte-for-byte identical.
                        item["Etag"] = f"bulk-person-{self.person_bulk_round}"
                return items

        movie = make_movie()
        movie["People"] = [{"Id": "500", "Name": "Same Name (I)", "Type": "Actor", "Role": "Self"}]
        server = BulkOmitsLockedFieldsServer(movie)
        server.items["500"] = {
            "Id": "500",
            "Name": "Same Name (I)",
            "SortName": "Same Name (I)",
            "Type": "Person",
            "Etag": "person-etag-500",
            "ProviderIds": {"Tmdb": "100"},
            "LockedFields": ["Name", "SortName"],
        }
        server.items["600"] = {
            "Id": "600",
            "Name": "Same Name (II)",
            "SortName": "Same Name (II)",
            "Type": "Person",
            "Etag": "person-etag-600",
            "ProviderIds": {"Tmdb": "200"},
            "LockedFields": ["Name", "SortName"],
        }

        class Credits:
            tmdb_id = 1
            credits_source = "tmdb"
            cast = [{"id": 100, "name": "Same Name", "character": "Self", "order": 0}]
            crew = []

        first = EmbyPeopleSync(server, FakeTMDb(), cache)
        first.stage_item(server.get_item("movie-1"), Credits())
        first.finalize_discovery()
        first._detect_false_friends()

        assert server.direct_person_calls == ["500"]
        cached = cache.query_emby_person_identities("server-a")
        assert cached[100]["emby_etag"] is None
        assert cached[100]["emby_signature"] is None
        assert cached[200]["emby_etag"] is None
        assert cached[200]["emby_signature"] is None

        server.direct_person_calls.clear()
        second = EmbyPeopleSync(server, FakeTMDb(), cache)
        second.stage_item(server.get_item("movie-1"), Credits())
        second.finalize_discovery()
        second._detect_false_friends()

        assert server.direct_person_calls == ["500"]
        assert second._changed_identity_ids == set()
        cached = cache.query_emby_person_identities("server-a")
        assert cached[100]["emby_etag"] == "bulk-person-2"
        assert cached[200]["emby_etag"] is None
        assert cached[100]["emby_signature"]
        second_signature = cached[100]["emby_signature"]
        assert cached[200]["emby_signature"] is None

        server.direct_person_calls.clear()
        third = EmbyPeopleSync(server, FakeTMDb(), cache)
        third.stage_item(server.get_item("movie-1"), Credits())
        third.finalize_discovery()
        third._detect_false_friends()

        assert server.direct_person_calls == []
        assert third._changed_identity_ids == set()
        cached = cache.query_emby_person_identities("server-a")
        assert cached[100]["emby_etag"] == "bulk-person-3"
        assert cached[100]["emby_signature"] == second_signature
    finally:
        cache.close()


def test_changed_tmdb_credits_override_unchanged_emby_etag(tmp_path):
    cache = make_cache(tmp_path)
    try:
        server = FakeEmbyServer(make_movie())
        tmdb = FakeTMDb({42: "nm0000042"})
        first = EmbyPeopleSync(server, tmdb, cache)
        first.stage_item(server.get_item("movie-1"), FakeMovie())
        first.apply()
        unchanged_etag = server.get_item("movie-1")["Etag"]
        update_count = server.movie_update_count
        updated_person_ids = []
        original_update_item = server.update_item

        def tracked_update_item(item_id, data):
            if (server.items.get(str(item_id)) or {}).get("Type") == "Person":
                updated_person_ids.append(str(item_id))
            return original_update_item(item_id, data)

        server.update_item = tracked_update_item

        class ChangedMovie:
            tmdb_id = FakeMovie.tmdb_id
            cast = FakeMovie.cast + [{"id": 42, "name": "New Actor", "character": "Reporter", "order": 3}]
            crew = FakeMovie.crew

        second = EmbyPeopleSync(server, tmdb, cache)
        second.stage_item(server.get_item("movie-1"), ChangedMovie())
        assert second.plans[0].emby_etag == unchanged_etag

        summary = second.apply()

        assert summary["created"] == 0
        assert server.movie_update_count == update_count + 1
        new_actor = next(person for person in server.get_item("movie-1")["People"] if person["Name"] == "New Actor" and person["Role"] == "Reporter")
        assert set(updated_person_ids) == {new_actor["Id"]}
    finally:
        cache.close()


def test_changed_etag_uses_cached_people_without_person_lookup_when_credits_match(tmp_path):
    cache = make_cache(tmp_path)
    try:
        server = FakeEmbyServer(make_movie())
        tmdb = FakeTMDb()
        first = EmbyPeopleSync(server, tmdb, cache)
        first.stage_item(server.get_item("movie-1"), FakeMovie())
        first.apply()
        update_count = server.movie_update_count

        server.items["movie-1"]["Etag"] = "etag-external-change"
        server.bulk_calls.clear()
        second = EmbyPeopleSync(server, tmdb, cache)
        second.stage_item(server.get_item("movie-1"), FakeMovie())

        summary = second.apply()

        assert summary["skipped"] == 1
        assert summary["updated"] == 0
        assert server.bulk_calls == []
        assert server.movie_update_count == update_count

        server.bulk_calls.clear()
        third = EmbyPeopleSync(server, tmdb, cache)
        third.stage_item(server.get_item("movie-1"), FakeMovie())
        assert third.apply()["skipped"] == 1
        assert server.bulk_calls == []
    finally:
        cache.close()


def test_legacy_item_state_is_migrated_without_person_lookup_when_people_match(tmp_path):
    cache = make_cache(tmp_path)
    try:
        server = FakeEmbyServer(make_movie())
        first = EmbyPeopleSync(server, FakeTMDb(), cache)
        first.stage_item(server.get_item("movie-1"), FakeMovie())
        first.apply()

        with cache.connection as connection:
            connection.execute(
                """
                UPDATE emby_people_item_state
                SET sync_version = 5, applied_hash = 'legacy-hash', emby_etag = 'legacy-etag'
                WHERE server_id = ? AND item_id = ?
                """,
                ("server-a", "movie-1"),
            )

        server.bulk_calls.clear()
        update_count = server.movie_update_count
        second = EmbyPeopleSync(server, FakeTMDb(), cache)
        second.stage_item(server.get_item("movie-1"), FakeMovie())
        summary = second.apply()

        assert summary["skipped"] == 1
        assert summary["updated"] == 0
        assert server.bulk_calls == []
        assert server.movie_update_count == update_count
        state = cache.query_emby_people_item_states("server-a", ["movie-1"])["movie-1"]
        assert state["sync_version"] == 7
        assert state["applied_hash"] != "legacy-hash"
    finally:
        cache.close()


def test_person_metadata_change_does_not_invalidate_matching_item_relationship(tmp_path):
    cache = make_cache(tmp_path)
    try:
        server = FakeEmbyServer(make_movie())
        first = EmbyPeopleSync(server, FakeTMDb(), cache)
        first.stage_item(server.get_item("movie-1"), FakeMovie())
        first.apply()

        server.bulk_calls.clear()
        update_count = server.movie_update_count
        second = EmbyPeopleSync(server, FakeTMDb(), cache)
        second.stage_item(server.get_item("movie-1"), FakeMovie())
        second.finalize_discovery()
        second._changed_identity_ids.add(64796)
        summary = second.apply()

        assert summary["skipped"] == 1
        assert summary["updated"] == 0
        assert server.bulk_calls == []
        assert server.movie_update_count == update_count
    finally:
        cache.close()


def test_expired_person_audit_uses_one_bulk_check_without_item_update(tmp_path):
    cache = make_cache(tmp_path)
    try:
        server = FakeEmbyServer(make_movie())
        tmdb = FakeTMDb(
            {
                64796: "nm0276169",
                1136406: "nm4043618",
                1145610: "nm2286597",
                287: "nm0006133",
            }
        )
        first = EmbyPeopleSync(server, tmdb, cache)
        first.stage_item(server.get_item("movie-1"), FakeMovie())
        first.apply()
        update_count = server.movie_update_count

        for identity in cache.query_emby_person_identities("server-a").values():
            cache.update_emby_person_identity(
                "server-a",
                identity["tmdb_id"],
                identity["base_name"],
                identity["normalized_name"],
                identity["display_name"],
                name_index=identity["name_index"],
                emby_id=identity["emby_id"],
                imdb_id=identity["imdb_id"],
                tvdb_id=identity["tvdb_id"],
                verified_at="2000-01-01T00:00:00",
            )

        server.bulk_calls.clear()
        tmdb.person_calls.clear()
        second = EmbyPeopleSync(server, tmdb, cache)
        second.stage_item(server.get_item("movie-1"), FakeMovie())

        summary = second.apply()

        assert summary["skipped"] == 1
        assert summary["updated"] == 0
        assert server.bulk_calls == [["9000", "9001", "9002", "9003"]]
        assert sorted(tmdb_id for tmdb_id, _ in tmdb.person_calls) == [287, 64796, 1136406, 1145610]
        assert server.movie_update_count == update_count
    finally:
        cache.close()


def test_equivalent_emby_name_spelling_does_not_fail_or_update(tmp_path):
    cache = make_cache(tmp_path)
    try:
        movie = make_movie()
        movie["People"] = [{"Id": "277337", "Name": "Jay-Z", "Type": "Actor", "Role": "Self"}]
        server = FakeEmbyServer(movie)

        class AccentMovie:
            tmdb_id = FakeMovie.tmdb_id
            cast = [{"id": 84932, "name": "JAŸ-Z", "character": "Self", "order": 0}]
            crew = []

        sync = EmbyPeopleSync(server, FakeTMDb(), cache)
        sync.stage_item(server.get_item("movie-1"), AccentMovie())

        summary = sync.apply()

        assert summary["failed"] == 0
        assert summary["updated"] == 0
        assert summary["skipped"] == 1
        assert server.movie_update_count == 0
    finally:
        cache.close()


def test_false_friend_detection_matches_reordered_people_by_external_id(tmp_path):
    cache = make_cache(tmp_path)
    try:
        movie = make_movie()
        movie["People"] = [
            {"Id": "200", "Name": "Bob Example", "Type": "Actor", "Role": "Self"},
            {"Id": "100", "Name": "Alice Example", "Type": "Actor", "Role": "Self"},
        ]
        server = FakeEmbyServer(movie)
        server.items["100"] = {
            "Id": "100",
            "Name": "Alice Example",
            "SortName": "Alice Example",
            "Type": "Person",
            "ProviderIds": {"Tmdb": "1001"},
            "LockedFields": [],
        }
        server.items["200"] = {
            "Id": "200",
            "Name": "Bob Example",
            "SortName": "Bob Example",
            "Type": "Person",
            "ProviderIds": {"Tmdb": "2002"},
            "LockedFields": [],
        }

        class ReorderedCredits:
            tmdb_id = 1
            credits_source = "tmdb"
            cast = [
                {"id": 1001, "name": "Alice Example", "character": "Self", "order": 0},
                {"id": 2002, "name": "Bob Example", "character": "Self", "order": 1},
            ]
            crew = []

        sync = EmbyPeopleSync(server, FakeTMDb(), cache)
        sync.stage_item(server.get_item("movie-1"), ReorderedCredits())
        sync.finalize_discovery()
        sync._detect_false_friends()

        assert sync.identities[1001].display_name == "Alice Example"
        assert sync.identities[1001].name_index is None
        assert sync.identities[2002].display_name == "Bob Example"
        assert sync.identities[2002].name_index is None
    finally:
        cache.close()


def test_false_friend_external_id_conflict_is_indexed_once_for_multiple_movies(tmp_path):
    cache = make_cache(tmp_path)
    try:
        movie = make_movie()
        movie["People"] = [{"Id": "2523", "Name": "John Doe", "Type": "Actor", "Role": "Self"}]

        class FalseFriendEmbyServer(FakeEmbyServer):
            def update_item(self, item_id, data):
                payload = copy.deepcopy(data)
                people = payload.get("People")
                if people:
                    for entry in people:
                        if entry.get("Name") == "Carlos González" and entry.get("Id") == entry.get("Name"):
                            entry["Id"] = "2523"
                            entry["Name"] = "John Doe"
                return super().update_item(item_id, payload)

        server = FalseFriendEmbyServer(movie)
        second_movie = copy.deepcopy(movie)
        second_movie["Id"] = "movie-2"
        second_movie["Name"] = "Second Concert"
        server.items["movie-2"] = second_movie
        server.items["2523"] = {
            "Id": "2523",
            "Name": "John Doe",
            "SortName": "John Doe",
            "Type": "Person",
            "ProviderIds": {"Tmdb": "42746"},
            "LockedFields": [],
        }

        class CorrectedMovie:
            tmdb_id = FakeMovie.tmdb_id
            cast = [{"id": 1056779, "name": "Carlos González", "character": "Self", "order": 0}]
            crew = []

        sync = EmbyPeopleSync(server, FakeTMDb(), cache)
        sync.stage_item(server.get_item("movie-1"), CorrectedMovie())
        sync.stage_item(server.get_item("movie-2"), CorrectedMovie())

        summary = sync.apply()

        assert summary["failed"] == 0
        assert summary["updated"] == 2
        assert summary["created"] == 1
        assert server.movie_update_count == 2
        assert len(server.people_payloads) == 2
        assert server.people_payloads[0][0]["Id"] == server.people_payloads[0][0]["Name"] == "Carlos González (I)"
        assert server.people_payloads[1][0]["Id"] == server.people_payloads[1][0]["Name"] == "Carlos González (I)"
        assert set(server.people_payloads[1][0]) == {"Id", "Name", "Type", "Role"}
        assert server.get_item("movie-1")["People"][0]["Name"] == "Carlos González (I)"
        assert server.get_item("movie-2")["People"][0]["Name"] == "Carlos González (I)"
        corrected_id = server.get_item("movie-1")["People"][0]["Id"]
        assert server.get_item(corrected_id)["ProviderIds"]["Tmdb"] == "1056779"
        assert server.bulk_calls == [["2523"], ["9000"]]

        cached_identity = cache.query_emby_person_identities("server-a")[1056779]
        assert cached_identity["name_index"] == 1
        assert cached_identity["display_name"] == "Carlos González (I)"

        server.bulk_calls.clear()
        second_run = EmbyPeopleSync(server, FakeTMDb(), cache)
        second_run.stage_item(server.get_item("movie-1"), CorrectedMovie())
        second_run.stage_item(server.get_item("movie-2"), CorrectedMovie())
        second_summary = second_run.apply()
        assert second_summary["updated"] == 0
        assert second_summary["skipped"] == 2
        assert second_run.identities[1056779].display_name == "Carlos González (I)"
    finally:
        cache.close()


def test_repeated_false_friend_detection_keeps_existing_index_stable(tmp_path):
    cache = make_cache(tmp_path)
    try:
        sync = EmbyPeopleSync(FakeEmbyServer(make_movie()), FakeTMDb(), cache)
        identity = PersonIdentity(
            1056779,
            "Carlos González",
            "carlos gonzález",
            "Carlos González (XXVI)",
            name_index=26,
            emby_id="2523",
            emby_etag="old-etag",
            emby_signature="old-signature",
        )
        sync._identity_records = {identity.tmdb_id: identity}
        sync.identities = dict(sync._identity_records)

        assert sync._assign_false_friend_index(identity)
        assert identity.emby_id is None
        assert identity.name_index == 26
        assert identity.display_name == "Carlos González (XXVI)"
        sync._changed_identity_ids.clear()
        assert not sync._assign_false_friend_index(identity)
        assert identity.name_index == 26
        assert identity.display_name == "Carlos González (XXVI)"
        assert not sync._changed_identity_ids
    finally:
        cache.close()


def test_false_friend_with_same_name_is_detected_by_external_id(tmp_path):
    cache = make_cache(tmp_path)
    try:
        movie = make_movie()
        movie["People"] = [{"Id": "2523", "Name": "Carlos González", "Type": "Actor", "Role": "Self"}]
        server = FakeEmbyServer(movie)
        server.items["2523"] = {
            "Id": "2523",
            "Name": "Carlos González",
            "SortName": "Carlos González",
            "Type": "Person",
            "ProviderIds": {"Tmdb": "42746"},
            "LockedFields": [],
        }

        class CorrectedMovie:
            tmdb_id = FakeMovie.tmdb_id
            cast = [{"id": 1056779, "name": "Carlos González", "character": "Self", "order": 0}]
            crew = []

        sync = EmbyPeopleSync(server, FakeTMDb(), cache)
        sync.stage_item(server.get_item("movie-1"), CorrectedMovie())
        summary = sync.apply()

        assert summary["failed"] == 0
        assert sync.identities[1056779].display_name == "Carlos González (I)"
        assert server.get_item("movie-1")["People"][0]["Name"] == "Carlos González (I)"
        cached_identity = cache.query_emby_person_identities("server-a")[1056779]
        assert cached_identity["name_index"] == 1
    finally:
        cache.close()


def test_known_false_friend_is_reassigned_without_advancing_expected_name(tmp_path):
    cache = make_cache(tmp_path)
    try:
        cache.update_emby_person_identity(
            "server-a",
            100,
            "Same Name",
            "same name",
            "Same Name",
            emby_id="700",
            imdb_id="nm100",
        )
        cache.update_emby_person_identity(
            "server-a",
            200,
            "Same Name",
            "same name",
            "Same Name (II)",
            name_index=2,
            emby_id="600",
            imdb_id="nm200",
        )

        class RelationshipAwareEmbyServer(FakeEmbyServer):
            def update_item(self, item_id, data):
                item_id = str(item_id)
                old_name = (self.items.get(item_id) or {}).get("Name")
                response = super().update_item(item_id, data)
                new_name = (self.items.get(item_id) or {}).get("Name")
                if old_name != new_name:
                    for item in self.items.values():
                        for person in item.get("People") or []:
                            if str(person.get("Id") or "") == item_id:
                                person["Name"] = new_name
                return response

        movie = make_movie()
        movie["People"] = [{"Id": "500", "Name": "Same Name", "Type": "Actor", "Role": "Self"}]
        server = RelationshipAwareEmbyServer(movie)
        server.items["500"] = {
            "Id": "500",
            "Name": "Same Name",
            "SortName": "Same Name",
            "Type": "Person",
            "ProviderIds": {"Tmdb": "200", "Imdb": "nm200"},
            "LockedFields": [],
        }
        server.items["600"] = {
            "Id": "600",
            "Name": "Same Name (II)",
            "SortName": "Same Name (II)",
            "Type": "Person",
            "ProviderIds": {"Tmdb": "200", "Imdb": "nm200"},
            "LockedFields": ["Name", "SortName"],
        }
        server.items["700"] = {
            "Id": "700",
            "Name": "Same Name",
            "SortName": "Same Name",
            "Type": "Person",
            "ProviderIds": {"Tmdb": "100", "Imdb": "nm100"},
            "LockedFields": ["Name", "SortName"],
        }

        class Credits:
            tmdb_id = 1
            credits_source = "tmdb"
            cast = [{"id": 100, "name": "Same Name", "character": "Self", "order": 0}]
            crew = []

        sync = EmbyPeopleSync(server, FakeTMDb(), cache)
        sync.stage_item(server.get_item("movie-1"), Credits())
        summary = sync.apply()

        assert summary["failed"] == 0
        assert sync.identities[100].display_name == "Same Name (I)"
        assert sync.identities[100].emby_id == "700"
        assert sync.identities[200].display_name == "Same Name (II)"
        assert sync.identities[200].emby_id == "600"
        assert sync.identities[200].duplicate_emby_ids == {"500"}
        assert server.get_item("500")["Name"] == "Emby Duplicate Person 500"
        assert server.get_item("500")["SortName"] == "Emby Duplicate Person 500"
        assert server.get_item("500")["ProviderIds"] == {}
        assert {"Name", "SortName"}.issubset(server.get_item("500")["LockedFields"])
        assert server.get_item("movie-1")["People"][0]["Id"] == "700"
        cached = cache.query_emby_person_identities("server-a")
        assert cached[100]["name_index"] == 1
        assert cached[200]["name_index"] == 2
        assert cached[200]["emby_id"] == "600"
        assert cached[200]["duplicate_emby_ids"] == ["500"]
    finally:
        cache.close()


def test_explicit_external_id_conflict_detaches_stale_emby_person_mapping(tmp_path):
    cache = make_cache(tmp_path)
    try:
        cache.update_emby_person_identity(
            "server-a",
            72466,
            "Colin Farrell",
            "colin farrell",
            "Colin Farrell",
            emby_id="500",
            imdb_id="nm0268199",
            tvdb_id="310392",
        )
        cache.update_emby_person_identity(
            "server-a",
            1492649,
            "Colin Farrell",
            "colin farrell",
            "Colin Farrell (II)",
            name_index=2,
            emby_id="500",
            imdb_id="nm0268200",
            tvdb_id="9097153",
        )
        movie = make_movie()
        movie["People"] = [{"Id": "500", "Name": "Colin Farrell (II)", "Type": "Actor", "Role": "Self"}]
        server = FakeEmbyServer(movie)
        server.items["500"] = {
            "Id": "500",
            "Name": "Colin Farrell (II)",
            "SortName": "Colin Farrell (II)",
            "Type": "Person",
            "ProviderIds": {"Tmdb": "1492649", "Imdb": "nm0268200", "Tvdb": "9097153"},
            "LockedFields": ["Name", "SortName"],
        }

        class Credits:
            tmdb_id = 1
            credits_source = "tmdb"
            cast = [{"id": 72466, "name": "Colin Farrell", "character": "Self", "order": 0}]
            crew = []

        sync = EmbyPeopleSync(server, FakeTMDb(), cache)
        sync.stage_item(server.get_item("movie-1"), Credits())
        summary = sync.apply()

        assert summary["failed"] == 0
        assert sync.identities[72466].emby_id != "500"
        assert sync.identities[1492649].emby_id == "500"
        assert server.get_item("500")["ProviderIds"]["Tmdb"] == "1492649"
        assert server.get_item("movie-1")["People"][0]["Name"] == sync.identities[72466].display_name
    finally:
        cache.close()


def test_cross_source_reconciliation_rejects_live_id_with_conflicting_primary_ids(tmp_path):
    cache = make_cache(tmp_path)
    try:
        sync = EmbyPeopleSync(FakeEmbyServer(make_movie()), FakeTMDb(), cache)
        canonical = PersonIdentity(
            72466,
            "Colin Farrell",
            "colin farrell",
            "Colin Farrell",
            imdb_id="nm0268199",
            tvdb_id="310392",
        )
        alias = PersonIdentity(
            -310392,
            "Colin Farrell",
            "colin farrell",
            "Colin Farrell",
            imdb_id="nm0268199",
            tvdb_id="310392",
            canonical_id=72466,
        )
        sync._identity_records = {72466: canonical, -310392: alias}
        sync.identities = {72466: canonical, -310392: canonical}
        people = {
            "500": {
                "Id": "500",
                "Name": "Colin Farrell",
                "SortName": "Colin Farrell",
                "Type": "Person",
                "ProviderIds": {
                    "Tmdb": "1492649",
                    "Imdb": "nm0268200",
                    "Tvdb": "9097153",
                },
            }
        }

        sync._reconcile_cross_source_identities(people, [("500", 72466), ("500", -310392)])

        assert canonical.emby_id is None
        assert alias.emby_id is None
    finally:
        cache.close()


def test_kometa_managed_person_bridges_empty_tvdb_crosswalk_without_index(tmp_path):
    cache = make_cache(tmp_path)
    try:
        server = FakeEmbyServer(make_movie())
        server.items["138580"] = {
            "Id": "138580",
            "Name": "Gary Fleder (I)",
            "SortName": "Gary Fleder (I)",
            "Type": "Person",
            "ProviderIds": {
                "Tmdb": "5501",
                "Tvdb": "273491",
                "Imdb": "nm0001219",
            },
            "LockedFields": ["Name", "SortName"],
        }
        canonical = PersonIdentity(
            5501,
            "Gary Fleder",
            "gary fleder",
            "Gary Fleder (I)",
            name_index=1,
            imdb_id="nm0001219",
            emby_id="138580",
        )
        alias = PersonIdentity(
            -273491,
            "Gary Fleder",
            "gary fleder",
            "Gary Fleder (II)",
            name_index=2,
            tvdb_id="273491",
        )
        sync = EmbyPeopleSync(server, FakeTMDb(), cache)
        sync._identity_records = {5501: canonical, -273491: alias}
        sync.identities = dict(sync._identity_records)
        invalidate_calls = 0
        original_invalidate = sync._invalidate_identity_indexes

        def count_invalidate():
            nonlocal invalidate_calls
            invalidate_calls += 1
            original_invalidate()

        sync._invalidate_identity_indexes = count_invalidate

        sync._reconcile_cross_source_identities(
            {"138580": server.get_item("138580")},
            [("138580", 5501), ("138580", -273491)],
        )

        assert sync.identities[-273491] is canonical
        assert alias.canonical_id == 5501
        assert canonical.tvdb_id == "273491"
        assert canonical.name_index is None
        assert canonical.display_name == "Gary Fleder"
        assert invalidate_calls == 1
    finally:
        cache.close()


def test_matching_primary_id_keeps_emby_person_and_corrects_auxiliary_id(tmp_path):
    cache = make_cache(tmp_path)
    try:
        cache.update_emby_person_identity(
            "server-a",
            18022,
            "Gina McKee",
            "gina mckee",
            "Gina McKee",
            emby_id="500",
            imdb_id="nm0571160",
            tvdb_id="260544",
        )
        movie = make_movie()
        movie["People"] = [{"Id": "500", "Name": "Gina McKee", "Type": "Actor", "Role": "Self"}]
        server = FakeEmbyServer(movie)
        server.items["500"] = {
            "Id": "500",
            "Name": "Gina McKee",
            "SortName": "Gina McKee",
            "Type": "Person",
            "ProviderIds": {"Tmdb": "18022", "Imdb": "nm0571160", "Tvdb": "7909161"},
            "LockedFields": [],
        }

        class Credits:
            tmdb_id = 1
            credits_source = "tmdb"
            cast = [{"id": 18022, "name": "Gina McKee", "character": "Self", "order": 0}]
            crew = []

        sync = EmbyPeopleSync(
            server,
            FakeTMDb({18022: "nm0571160"}),
            cache,
            tvdb=FakeTVDb({260544: {"tmdb_id": 18022, "imdb_id": "nm0571160"}}),
        )
        sync.stage_item(server.get_item("movie-1"), Credits())
        summary = sync.apply()

        assert summary["failed"] == 0
        assert sync.identities[18022].emby_id == "500"
        assert server.get_item("500")["ProviderIds"] == {
            "Tmdb": "18022",
            "Imdb": "nm0571160",
            "Tvdb": "260544",
        }
    finally:
        cache.close()


def test_cached_false_friend_suffix_i_is_preserved(tmp_path):
    cache = make_cache(tmp_path)
    try:
        cache.update_emby_person_identity(
            "server-a",
            1056779,
            "Carlos González",
            "carlos gonzález",
            "Carlos González (I)",
            name_index=1,
            emby_id="9000",
        )
        server = FakeEmbyServer(make_movie())

        sync = EmbyPeopleSync(server, FakeTMDb(), cache)
        sync.finalize_discovery()

        assert sync.identities[1056779].name_index == 1
        assert sync.identities[1056779].display_name == "Carlos González (I)"
        cached_identity = cache.query_emby_person_identities("server-a")[1056779]
        assert cached_identity["name_index"] == 1
        assert cached_identity["display_name"] == "Carlos González (I)"
    finally:
        cache.close()


def test_cross_source_identity_removes_index_only_with_one_live_emby_person(tmp_path):
    cache = make_cache(tmp_path)
    try:
        cache.update_emby_person_identity(
            "server-a",
            239019,
            "Kit Harington",
            "kit harington",
            "Kit Harington (I)",
            name_index=1,
        )
        cache.update_emby_person_identity(
            "server-a",
            -247909,
            "Kit Harington",
            "kit harington",
            "Kit Harington (II)",
            name_index=2,
            emby_id="100",
            tvdb_id="247909",
        )
        server = FakeEmbyServer(make_movie())
        server.items["100"] = {
            "Id": "100",
            "Name": "Kit Harington",
            "Type": "Person",
            "ProviderIds": {"Tmdb": "239019", "Tvdb": "247909", "Imdb": "nm3229685"},
            "LockedFields": [],
        }

        class TMDbCredits:
            tmdb_id = 1
            credits_source = "tmdb"
            cast = [{"id": 239019, "name": "Kit Harington", "character": "Jon Snow", "order": 0}]
            crew = []

        class TVDbCredits:
            tvdb_id = 2
            tmdb_id = None
            credits_source = "tvdb"
            cast = [{"tvdb_id": 247909, "name": "Kit Harington", "character": "Jon Snow", "order": 0}]
            crew = []

        sync = EmbyPeopleSync(
            server,
            FakeTMDb({239019: "nm3229685"}),
            cache,
            tvdb=FakeTVDb({247909: {"tmdb_id": 239019, "imdb_id": "nm3229685"}}),
        )
        sync.stage_item(server.get_item("movie-1"), TMDbCredits())
        sync.stage_item(server.get_item("movie-1"), TVDbCredits())
        sync.finalize_discovery()
        sync._detect_false_friends()

        assert sync.identities[239019] is sync.identities[-247909]
        assert sync.identities[239019].display_name == "Kit Harington"
        assert sync.identities[239019].name_index is None
        cached = cache.query_emby_person_identities("server-a")
        assert cached[239019]["tvdb_id"] == "247909"
        assert cached[239019]["name_index"] is None
        assert cached[-247909]["canonical_id"] == 239019
        assert cached[-247909]["name_index"] is None
    finally:
        cache.close()


def test_external_tvdb_mapping_reuses_tvdb_only_emby_person_for_new_tmdb_credit(tmp_path):
    cache = make_cache(tmp_path)
    try:
        cache.update_emby_person_identity(
            "server-a",
            -501,
            "Jim Beam",
            "jim beam",
            "Jim Beam",
            tvdb_id="501",
        )
        server = FakeEmbyServer(make_movie())
        server.items["700"] = {
            "Id": "700",
            "Name": "Jim Beam",
            "SortName": "Jim Beam",
            "Type": "Person",
            "ProviderIds": {"Tvdb": "501"},
            "LockedFields": [],
        }
        tvdb = FakeTVDb({501: {"tmdb_id": 101, "imdb_id": "nm0000101"}})

        class TMDbCredits:
            tmdb_id = 1
            credits_source = "tmdb"
            cast = [{"id": 101, "name": "Jim Beam", "character": "Self", "order": 0}]
            crew = []

        sync = EmbyPeopleSync(server, FakeTMDb(), cache, tvdb=tvdb)
        sync.stage_item(server.get_item("movie-1"), TMDbCredits())
        summary = sync.apply()

        assert summary["failed"] == 0
        assert sync.identities[101] is sync.identities[-501]
        assert sync.identities[101].display_name == "Jim Beam"
        assert sync.identities[101].emby_id == "700"
        assert server.get_item("movie-1")["People"][0]["Id"] == "700"
        assert server.get_item("700")["ProviderIds"] == {
            "Tmdb": "101",
            "Imdb": "nm0000101",
            "Tvdb": "501",
        }
        assert tvdb.people_calls == [[501]]
    finally:
        cache.close()


def test_external_tvdb_mismatch_moves_existing_namesake_before_materializing_tmdb_person(tmp_path):
    cache = make_cache(tmp_path)
    try:
        cache.update_emby_person_identity(
            "server-a",
            -501,
            "Jim Beam",
            "jim beam",
            "Jim Beam",
            tvdb_id="501",
            emby_id="700",
        )
        server = FakeEmbyServer(make_movie())
        server.items["700"] = {
            "Id": "700",
            "Name": "Jim Beam",
            "SortName": "Jim Beam",
            "Type": "Person",
            "ProviderIds": {"Tvdb": "501"},
            "LockedFields": [],
        }
        tvdb = FakeTVDb({501: {"tmdb_id": 999}})

        class TMDbCredits:
            tmdb_id = 1
            credits_source = "tmdb"
            cast = [{"id": 101, "name": "Jim Beam", "character": "Self", "order": 0}]
            crew = []

        sync = EmbyPeopleSync(server, FakeTMDb(), cache, tvdb=tvdb)
        sync.stage_item(server.get_item("movie-1"), TMDbCredits())
        summary = sync.apply()

        assert summary["failed"] == 0
        assert sync.identities[101] is not sync.identities[-501]
        assert sync.identities[101].display_name == "Jim Beam (I)"
        assert sync.identities[-501].display_name == "Jim Beam (II)"
        assert server.get_item("700")["Name"] == "Jim Beam (II)"
        new_person_id = server.get_item("movie-1")["People"][0]["Id"]
        assert new_person_id != "700"
        assert server.get_item(new_person_id)["Name"] == "Jim Beam (I)"
        assert server.get_item(new_person_id)["ProviderIds"]["Tmdb"] == "101"
    finally:
        cache.close()


def test_cross_source_identity_consolidates_two_emby_people_on_best_existing_record(tmp_path):
    cache = make_cache(tmp_path)
    try:
        for source_id, index, emby_id in ((239019, 1, "101"), (-247909, 2, "100")):
            cache.update_emby_person_identity(
                "server-a",
                source_id,
                "Kit Harington",
                "kit harington",
                f"Kit Harington ({roman_number(index)})",
                name_index=index,
                emby_id=emby_id,
                tvdb_id="247909" if source_id < 0 else None,
            )

        class LockRespectingEmbyServer(FakeEmbyServer):
            def __init__(self, movie):
                super().__init__(movie)
                self.person_unlocks = []

            def update_item(self, item_id, data):
                item_id = str(item_id)
                payload = copy.deepcopy(data)
                item = self.items[item_id]
                if item.get("Type") == "Person":
                    if "LockedFields" in payload and "Name" not in payload:
                        self.person_unlocks.append((item_id, list(payload["LockedFields"])))
                    if {"Name", "SortName"} & set(item.get("LockedFields") or []):
                        payload.pop("Name", None)
                        payload.pop("SortName", None)
                return super().update_item(item_id, payload)

        server = LockRespectingEmbyServer(make_movie())
        server.items["movie-1"]["People"] = [{"Id": "101", "Name": "Kit Harington (I)", "Type": "Actor", "Role": "Jon Snow"}]
        second_movie = copy.deepcopy(server.items["movie-1"])
        second_movie["Id"] = "movie-2"
        second_movie["People"] = [{"Id": "100", "Name": "Kit Harington (II)", "Type": "Actor", "Role": "Jon Snow"}]
        server.items["movie-2"] = second_movie
        server.items["100"] = {
            "Id": "100",
            "Name": "Kit Harington (II)",
            "SortName": "Kit Harington (II)",
            "Type": "Person",
            "ProviderIds": {"Tmdb": "239019", "Tvdb": "247909", "Imdb": "nm3229685"},
            "LockedFields": ["Name", "SortName"],
        }
        server.items["101"] = {
            "Id": "101",
            "Name": "Kit Harington (I)",
            "SortName": "Kit Harington (I)",
            "Type": "Person",
            "ProviderIds": {"Tmdb": "239019"},
            "LockedFields": ["Name", "SortName"],
        }

        class TMDbCredits:
            tmdb_id = 1
            credits_source = "tmdb"
            cast = [{"id": 239019, "name": "Kit Harington", "character": "Jon Snow", "order": 0}]
            crew = []

        class TVDbCredits:
            tvdb_id = 2
            tmdb_id = None
            credits_source = "tvdb"
            cast = [{"tvdb_id": 247909, "name": "Kit Harington", "character": "Jon Snow", "order": 0}]
            crew = []

        sync = EmbyPeopleSync(
            server,
            FakeTMDb({239019: "nm3229685"}),
            cache,
            tvdb=FakeTVDb({247909: {"tmdb_id": 239019, "imdb_id": "nm3229685"}}),
        )
        sync.stage_item(server.get_item("movie-1"), TMDbCredits())
        sync.stage_item(server.get_item("movie-2"), TVDbCredits())
        summary = sync.apply()

        assert summary["failed"] == 0
        assert sync.identities[239019] is sync.identities[-247909]
        assert sync.identities[239019].name_index is None
        assert sync.identities[239019].emby_id == "100"
        assert server.get_item("100")["Name"] == "Kit Harington"
        assert set(server.get_item("100")["LockedFields"]) == {"Name", "SortName"}
        assert server.get_item("101")["Name"] == "Emby Duplicate Person 101"
        assert server.get_item("101")["ProviderIds"] == {}
        assert set(server.get_item("101")["LockedFields"]) == {"Name", "SortName"}
        assert ("100", []) in server.person_unlocks
        assert server.get_item("movie-1")["People"][0]["Id"] == "100"
        assert server.get_item("movie-2")["People"][0]["Id"] == "100"
        assert all(person["Id"] == person["Name"] == "Kit Harington" for payload in server.people_payloads for person in payload if person)
        cached = cache.query_emby_person_identities("server-a")
        assert cached[-247909]["canonical_id"] == 239019
        assert cached[239019]["emby_id"] == "100"
        assert cached[239019]["duplicate_emby_ids"] == ["101"]

        refresh_count = len(server.refreshes)
        second = EmbyPeopleSync(server, FakeTMDb(), cache)
        second.stage_item(server.get_item("movie-1"), TMDbCredits())
        second.stage_item(server.get_item("movie-2"), TVDbCredits())
        second_summary = second.apply()

        assert second_summary["failed"] == 0
        assert second_summary["updated"] == 0
        assert len(server.refreshes) == refresh_count
    finally:
        cache.close()


def test_same_external_identity_does_not_rewrite_item_to_canonical_emby_id(tmp_path):
    cache = make_cache(tmp_path)
    try:
        cache.update_emby_person_identity(
            "server-a",
            4690,
            "Christopher Walken",
            "christopher walken",
            "Christopher Walken",
            emby_id="100",
            imdb_id="nm0000686",
            tvdb_id="288073",
            duplicate_emby_ids=["101"],
        )
        cache.update_emby_person_identity(
            "server-a",
            -288073,
            "Christopher Walken",
            "christopher walken",
            "Christopher Walken",
            emby_id="100",
            imdb_id="nm0000686",
            tvdb_id="288073",
            canonical_id=4690,
        )

        class BulkOmitsLocksEmbyServer(FakeEmbyServer):
            def __init__(self, movie):
                super().__init__(movie)
                self.direct_get_ids = []

            def get_item(self, item_id, force_refresh=False):
                self.direct_get_ids.append(str(item_id))
                return super().get_item(item_id, force_refresh=force_refresh)

            def get_items_bulk(self, ids, fields=None, force_refresh=False):
                result = super().get_items_bulk(ids, fields=fields, force_refresh=force_refresh)
                for person in result.values():
                    person.pop("LockedFields", None)
                return result

        movie = make_movie()
        movie["People"] = [{"Id": "101", "Name": "Christopher Walken", "Type": "Actor", "Role": "Self"}]
        server = BulkOmitsLocksEmbyServer(movie)
        for person_id in ("100", "101"):
            server.items[person_id] = {
                "Id": person_id,
                "Name": "Christopher Walken",
                "SortName": "Christopher Walken",
                "ForcedSortName": "Christopher Walken",
                "Type": "Person",
                "ProviderIds": {"Tmdb": "4690", "Tvdb": "288073", "Imdb": "nm0000686"},
                "LockedFields": ["Name", "SortName"],
                "Etag": f"person-etag-{person_id}",
            }

        class WalkenCredits:
            tmdb_id = 1
            credits_source = "tmdb"
            cast = [{"id": 4690, "name": "Christopher Walken", "character": "Self", "order": 0}]
            crew = []

        first = EmbyPeopleSync(server, FakeTMDb(), cache)
        first.stage_item(server.get_item("movie-1"), WalkenCredits())
        first_summary = first.apply()

        assert first_summary["failed"] == 0
        assert first_summary["updated"] == 1
        assert first.identities[4690].emby_id == "100"
        assert server.get_item("101")["Name"] == "Emby Duplicate Person 101"
        assert server.get_item("101")["ProviderIds"] == {}
        assert server.get_item("movie-1")["People"][0]["Id"] == "100"
        assert server.movie_update_count == 1
        assert "101" in server.direct_get_ids
        assert cache.query_emby_person_identities("server-a")[4690]["emby_signature"]

        emby_people_module.logger.reset_mock()
        bulk_calls_before_second_run = len(server.bulk_calls)
        second = EmbyPeopleSync(server, FakeTMDb(), cache)
        second.stage_item(server.get_item("movie-1"), WalkenCredits())
        second_summary = second.apply()

        assert second_summary["failed"] == 0
        assert second_summary["updated"] == 0
        assert second.identities[4690].emby_id == "100"
        assert server.get_item("movie-1")["People"][0]["Id"] == "100"
        assert server.movie_update_count == 1
        assert len(server.bulk_calls) == bulk_calls_before_second_run
        assert not any("Christopher Walken" in str(call) and ("Identity Updated" in str(call) or "false friend" in str(call)) for call in emby_people_module.logger.method_calls)
    finally:
        cache.close()


def test_existing_cross_source_merge_unlocks_and_removes_stale_person_index(tmp_path):
    cache = make_cache(tmp_path)
    try:
        cache.update_emby_person_identity(
            "server-a",
            72118,
            "Anil Kapoor",
            "anil kapoor",
            "Anil Kapoor",
            emby_id="194571",
            imdb_id="nm0438463",
            tvdb_id="406293",
        )
        cache.update_emby_person_identity(
            "server-a",
            -406293,
            "Anil Kapoor",
            "anil kapoor",
            "Anil Kapoor",
            emby_id="194571",
            imdb_id="nm0438463",
            tvdb_id="406293",
            canonical_id=72118,
        )

        class LockRespectingEmbyServer(FakeEmbyServer):
            def update_item(self, item_id, data):
                item_id = str(item_id)
                payload = copy.deepcopy(data)
                item = self.items[item_id]
                if item.get("Type") == "Person" and {"Name", "SortName"} & set(item.get("LockedFields") or []):
                    payload.pop("Name", None)
                    payload.pop("SortName", None)
                return super().update_item(item_id, payload)

        movie = make_movie()
        movie["People"] = [{"Id": "194571", "Name": "Anil Kapoor (II)", "Type": "Actor", "Role": "Self"}]
        server = LockRespectingEmbyServer(movie)
        server.items["194571"] = {
            "Id": "194571",
            "Name": "Anil Kapoor (II)",
            "SortName": "Anil Kapoor (II)",
            "Type": "Person",
            "ProviderIds": {"Tmdb": "72118", "Tvdb": "406293", "Imdb": "nm0438463"},
            "LockedFields": ["Name", "SortName"],
        }

        class TMDbCredits:
            tmdb_id = 1
            credits_source = "tmdb"
            cast = [{"id": 72118, "name": "Anil Kapoor", "character": "Self", "order": 0}]
            crew = []

        sync = EmbyPeopleSync(server, FakeTMDb(), cache)
        sync.stage_item(server.get_item("movie-1"), TMDbCredits())
        summary = sync.apply()

        assert summary["failed"] == 0
        assert server.get_item("194571")["Name"] == "Anil Kapoor"
        assert server.get_item("194571")["SortName"] == "Anil Kapoor"
        assert server.get_item("194571")["LockedFields"] == []
        assert server.get_item("movie-1")["People"][0]["Name"] == "Anil Kapoor"
    finally:
        cache.close()


def test_primary_namesake_removes_index_and_repairs_stale_sort_name(tmp_path):
    cache = make_cache(tmp_path)
    try:
        cache.update_emby_person_identity(
            "server-a",
            106772,
            "Max Wright",
            "max wright",
            "Max Wright (I)",
            name_index=1,
            emby_id="160268",
            imdb_id="nm0942642",
            tvdb_id="258068",
        )
        cache.update_emby_person_identity(
            "server-a",
            1809773,
            "Max Wright",
            "max wright",
            "Max Wright (II)",
            name_index=2,
            imdb_id="nm3095031",
        )

        class LockRespectingEmbyServer(FakeEmbyServer):
            def update_item(self, item_id, data):
                item_id = str(item_id)
                payload = copy.deepcopy(data)
                item = self.items[item_id]
                if item.get("Type") == "Person" and {"Name", "SortName"} & set(item.get("LockedFields") or []):
                    payload.pop("Name", None)
                    payload.pop("SortName", None)
                return super().update_item(item_id, payload)

        movie = make_movie()
        movie["People"] = [{"Id": "160268", "Name": "Max Wright (I)", "Type": "Actor", "Role": "Willie Tanner"}]
        server = LockRespectingEmbyServer(movie)
        server.items["160268"] = {
            "Id": "160268",
            "Name": "Max Wright (I)",
            "SortName": "Max Wright (III)",
            "Type": "Person",
            "ProviderIds": {"Tmdb": "106772", "Tvdb": "258068", "Imdb": "nm0942642"},
            "LockedFields": ["Name", "SortName"],
        }

        class MaxWrightCredits:
            tmdb_id = 1
            credits_source = "tmdb"
            cast = [{"id": 106772, "name": "Max Wright", "character": "Willie Tanner", "order": 0}]
            crew = []

        sync = EmbyPeopleSync(server, FakeTMDb(), cache)
        sync.stage_item(server.get_item("movie-1"), MaxWrightCredits())
        summary = sync.apply()

        assert summary["failed"] == 0
        assert sync.identities[106772].name_index == 1
        assert sync.identities[106772].display_name == "Max Wright (I)"
        assert sync.identities[1809773].name_index == 2
        assert sync.identities[1809773].display_name == "Max Wright (II)"
        assert server.get_item("160268")["Name"] == "Max Wright (I)"
        assert server.get_item("160268")["SortName"] == "Max Wright (I)"
        assert server.get_item("160268")["LockedFields"] == ["Name", "SortName"]
        assert server.get_item("movie-1")["People"][0]["Name"] == "Max Wright (I)"
        assert server.refreshes == [
            {
                "item_id": "160268",
                "replace_all_metadata": True,
                "replace_all_images": True,
            }
        ]
    finally:
        cache.close()


def test_person_verification_accepts_diacritic_free_emby_sort_name(tmp_path):
    cache = make_cache(tmp_path)
    try:
        cache.update_emby_person_identity(
            "server-a",
            70369,
            "Javier Álvarez",
            "javier álvarez",
            "Javier Álvarez",
            emby_id="2595599",
        )
        cache.update_emby_person_identity(
            "server-a",
            170369,
            "Javier Álvarez",
            "javier álvarez",
            "Javier Álvarez (II)",
            name_index=2,
        )
        movie = make_movie()
        movie["People"] = [{"Id": "2595599", "Name": "Javier Álvarez", "Type": "Composer", "Role": "Filmmusik"}]
        server = FakeEmbyServer(movie)
        server.items["2595599"] = {
            "Id": "2595599",
            "Name": "Javier Álvarez",
            "SortName": "Javier Alvarez",
            "Type": "Person",
            "ProviderIds": {"Tmdb": "70369"},
            "LockedFields": ["Name", "SortName"],
        }

        class JavierCredits:
            tmdb_id = 1
            credits_source = "tmdb"
            cast = []
            crew = [{"id": 70369, "name": "Javier Álvarez", "job": "Original Music Composer"}]

        sync = EmbyPeopleSync(server, FakeTMDb(), cache)
        sync.stage_item(server.get_item("movie-1"), JavierCredits())
        sync.finalize_discovery()
        sync._detect_false_friends()
        sync._ensure_person_metadata(sync.identities[70369])

        assert server.get_item("2595599")["Name"] == "Javier Álvarez (I)"
        assert server.get_item("2595599")["SortName"] == "Javier Álvarez (I)"
        assert len(server.refreshes) == 1
        assert sync._sort_name_matches("Javier Álvarez", "Javier Alvarez")
        assert not sync._sort_name_matches("Javier Álvarez", "Javier Alvarez (III)")
    finally:
        cache.close()


def test_duplicate_quarantine_removes_real_diacritic_name_completely():
    server = FakeEmbyServer(make_movie())
    server.items["2451644"] = {
        "Id": "2451644",
        "Name": "Juan Fernández (I)",
        "SortName": "Juan Fernandez (I)",
        "Type": "Person",
        "ProviderIds": {"Tmdb": "2571347"},
        "LockedFields": ["Name", "SortName"],
    }
    identity = PersonIdentity(
        2571347,
        "Juan Fernández",
        "juan fernández",
        "Juan Fernández (I)",
        name_index=1,
        emby_id="2597503",
    )
    sync = EmbyPeopleSync(server, FakeTMDb(), None)

    person, changed = sync._quarantine_duplicate_person(identity, "2451644")

    assert changed
    assert person["Name"] == "Emby Duplicate Person 2451644"
    assert person["SortName"] == "Emby Duplicate Person 2451644"
    assert "Juan" not in person["Name"]
    assert "Fernández" not in person["Name"]
    assert person["ProviderIds"] == {}
    assert {"Name", "SortName"}.issubset(person["LockedFields"])


def test_duplicate_quarantine_migrates_suffix_name_away_from_real_name_prefix():
    server = FakeEmbyServer(make_movie())
    server.items["2589437"] = {
        "Id": "2589437",
        "Name": "John Forsythe [Emby Duplicate 2589437]",
        "SortName": "John Forsythe [Emby Duplicate 2589437]",
        "Type": "Person",
        "ProviderIds": {},
        "LockedFields": ["Name", "SortName"],
    }
    identity = PersonIdentity(
        24367,
        "John Forsythe",
        "john forsythe",
        "John Forsythe",
        emby_id="161360",
        duplicate_emby_ids={"2589437"},
    )
    sync = EmbyPeopleSync(server, FakeTMDb(), None)
    sync.identities[24367] = identity

    sync._maintain_duplicate_person_quarantines({"2589437": server.get_item("2589437")})

    migrated_name = server.get_item("2589437")["Name"]
    assert migrated_name == "Emby Duplicate Person 2589437"
    assert not migrated_name.startswith("John Forsythe")
    assert "John Forsythe" not in migrated_name


def test_canonical_person_of_another_identity_is_removed_from_duplicate_quarantine():
    server = FakeEmbyServer(make_movie())
    server.items["100"] = {
        "Id": "100",
        "Name": "First Person",
        "SortName": "First Person",
        "Type": "Person",
        "ProviderIds": {"Tmdb": "1"},
        "LockedFields": [],
    }
    server.items["200"] = {
        "Id": "200",
        "Name": "Second Person",
        "SortName": "Second Person",
        "Type": "Person",
        "ProviderIds": {"Tmdb": "2"},
        "LockedFields": [],
    }
    first = PersonIdentity(
        1,
        "First Person",
        "first person",
        "First Person",
        emby_id="100",
        duplicate_emby_ids={"200"},
    )
    second = PersonIdentity(
        2,
        "Second Person",
        "second person",
        "Second Person",
        emby_id="200",
    )
    sync = EmbyPeopleSync(server, FakeTMDb(), None)
    sync._identity_records = {1: first, 2: second}
    sync.identities = dict(sync._identity_records)

    sync._refresh_duplicate_identity_ids()
    sync._maintain_duplicate_person_quarantines({"200": server.get_item("200")})

    assert first.duplicate_emby_ids == set()
    assert server.get_item("200")["Name"] == "Second Person"
    assert sync._changed_identity_ids == {1}


def test_neutralized_canonical_person_is_restored_not_reclaimed_as_expected_duplicate():
    movie = make_movie()
    movie["People"] = [
        {
            "Id": "200",
            "Name": "Emby Duplicate Person 200",
            "Type": "Actor",
            "Role": "Expected Role",
        }
    ]
    server = FakeEmbyServer(movie)
    server.items["100"] = {
        "Id": "100",
        "Name": "Shared Person (I)",
        "SortName": "Shared Person (I)",
        "Type": "Person",
        "ProviderIds": {"Tmdb": "1", "Tvdb": "101", "Imdb": "nm0000001"},
        "LockedFields": ["Name", "SortName"],
    }
    server.items["200"] = {
        "Id": "200",
        "Name": "Emby Duplicate Person 200",
        "SortName": "Emby Duplicate Person 200",
        "Type": "Person",
        "ProviderIds": {},
        "LockedFields": ["Name", "SortName"],
    }
    expected = PersonIdentity(
        1,
        "Shared Person",
        "shared person",
        "Shared Person (I)",
        name_index=1,
        imdb_id="nm0000001",
        tvdb_id="101",
        emby_id="100",
    )
    canonical_owner = PersonIdentity(
        2,
        "Shared Person",
        "shared person",
        "Shared Person (II)",
        name_index=2,
        imdb_id="nm0000002",
        tvdb_id="202",
        emby_id="200",
    )
    plan = emby_people_module.ItemPeoplePlan(
        "movie-1",
        315635,
        "tmdb",
        "etag-0",
        server.get_item("movie-1"),
        [
            emby_people_module.PersonCredit(
                1,
                "Shared Person",
                "Actor",
                "Expected Role",
                0,
                imdb_id="nm0000001",
                tvdb_id="101",
            )
        ],
        "credits-hash",
    )
    sync = EmbyPeopleSync(server, FakeTMDb(), None)
    sync._identity_records = {1: expected, 2: canonical_owner}
    sync.identities = dict(sync._identity_records)
    sync._refresh_duplicate_identity_ids()

    sync._detect_false_friends([plan])

    assert expected.duplicate_emby_ids == set()
    assert canonical_owner.emby_id == "200"
    assert "200" not in sync._noncanonical_person_ids
    assert sync._canonical_person_owners["200"] is canonical_owner
    assert sync._changed_identity_ids == {2}

    sync._ensure_changed_people()

    restored = server.get_item("200")
    assert restored["Name"] == "Shared Person (II)"
    assert restored["SortName"] == "Shared Person (II)"
    assert restored["ProviderIds"] == {
        "Tmdb": "2",
        "Imdb": "nm0000002",
        "Tvdb": "202",
    }


def test_linked_duplicate_uses_one_time_canonical_routing_then_fast_skips(tmp_path):
    cache = make_cache(tmp_path)
    try:
        cache.update_emby_person_identity(
            "server-a",
            192,
            "Morgan Freeman",
            "morgan freeman",
            "Morgan Freeman (I)",
            name_index=1,
            emby_id="100",
            imdb_id="nm0000151",
            tvdb_id="255114",
            duplicate_emby_ids=["101"],
        )
        cache.update_emby_person_identity(
            "server-a",
            2714289,
            "Morgan Freeman",
            "morgan freeman",
            "Morgan Freeman (II)",
            name_index=2,
        )

        class StickyRelationshipEmbyServer(FakeEmbyServer):
            def __init__(self, movie):
                super().__init__(movie)
                self.person_names = []

            def update_item(self, item_id, data):
                item_id = str(item_id)
                payload = copy.deepcopy(data)
                item = self.items[item_id]
                if item.get("Type") == "Movie" and payload.get("People") and item.get("People"):
                    for person in payload["People"]:
                        if person.get("Name") == "Morgan Freeman (I)":
                            person["Id"] = "101"
                            person["Name"] = self.items["101"]["Name"]
                old_name = item.get("Name")
                response = super().update_item(item_id, payload)
                new_name = self.items[item_id].get("Name")
                if item.get("Type") == "Person" and old_name != new_name:
                    self.person_names.append((item_id, new_name))
                    for linked_item in self.items.values():
                        for linked_person in linked_item.get("People") or []:
                            if str(linked_person.get("Id") or "") == item_id:
                                linked_person["Name"] = new_name
                return response

        movie = make_movie()
        movie["People"] = [
            {
                "Id": "101",
                "Name": "Emby Duplicate Person 101",
                "Type": "Actor",
                "Role": "Det. Alex Cross",
            }
        ]
        server = StickyRelationshipEmbyServer(movie)
        server.items["100"] = {
            "Id": "100",
            "Name": "Morgan Freeman (I)",
            "SortName": "Morgan Freeman (I)",
            "Type": "Person",
            "ProviderIds": {"Tmdb": "192", "Tvdb": "255114", "Imdb": "nm0000151"},
            "LockedFields": ["Name", "SortName"],
        }
        server.items["101"] = {
            "Id": "101",
            "Name": "Emby Duplicate Person 101",
            "SortName": "Emby Duplicate Person 101",
            "Type": "Person",
            "ProviderIds": {},
            "LockedFields": ["Name", "SortName"],
        }

        class Credits:
            tmdb_id = 1
            credits_source = "tmdb"
            cast = [{"id": 192, "name": "Morgan Freeman", "character": "Det. Alex Cross", "order": 0}]
            crew = []

        first = EmbyPeopleSync(server, FakeTMDb(), cache)
        first.stage_item(server.get_item("movie-1"), Credits())
        first_summary = first.apply()

        assert first_summary["failed"] == 0
        assert server.get_item("movie-1")["People"][0]["Id"] == "100"
        assert server.get_item("movie-1")["People"][0]["Name"] == "Morgan Freeman (I)"
        assert server.get_item("movie-1")["People"][0]["Role"] == "Det. Alex Cross"
        assert server.people_payloads.count([]) == 1
        assert server.person_names == [
            ("100", "Kometa Canonical Person Tmdb 192 100"),
            ("100", "Morgan Freeman (I)"),
        ]
        assert all(set(person).issubset({"Id", "Name", "Type", "Role", "PrimaryImageTag"}) for people in server.people_payloads for person in people)

        payload_count = len(server.people_payloads)
        person_name_count = len(server.person_names)
        second = EmbyPeopleSync(server, FakeTMDb(), cache)
        second.stage_item(server.get_item("movie-1"), Credits())
        second_summary = second.apply()

        assert second_summary["failed"] == 0
        assert second_summary["updated"] == 0
        assert len(server.people_payloads) == payload_count
        assert len(server.person_names) == person_name_count
    finally:
        cache.close()


def test_matching_same_identity_duplicate_clears_item_before_canonical_routing(tmp_path):
    cache = make_cache(tmp_path)
    try:
        cache.update_emby_person_identity(
            "server-a",
            2771,
            "Jim Backus",
            "jim backus",
            "Jim Backus",
            emby_id="100",
            imdb_id="nm0000822",
            tvdb_id="260188",
        )

        class StickySameIdentityEmbyServer(FakeEmbyServer):
            def __init__(self, movie):
                super().__init__(movie)
                self.person_names = []
                self.person_name_searches = []

            def get_person_by_name(self, person_name):
                self.person_name_searches.append(person_name)
                return [copy.deepcopy(person) for person in self.items.values() if person.get("Type") == "Person" and person.get("Name") == person_name]

            def update_item(self, item_id, data):
                item_id = str(item_id)
                payload = copy.deepcopy(data)
                item = self.items[item_id]
                if item.get("Type") == "Movie" and payload.get("People") and item.get("People"):
                    for index, person in enumerate(payload["People"]):
                        if person.get("Name") == "Jim Backus":
                            person["Id"] = item["People"][index]["Id"]
                            person["Name"] = item["People"][index]["Name"]
                old_name = item.get("Name")
                response = super().update_item(item_id, payload)
                new_name = self.items[item_id].get("Name")
                if item.get("Type") == "Person" and old_name != new_name:
                    self.person_names.append((item_id, new_name))
                    for linked_item in self.items.values():
                        for linked_person in linked_item.get("People") or []:
                            if str(linked_person.get("Id") or "") == item_id:
                                linked_person["Name"] = new_name
                return response

        movie = make_movie()
        movie["People"] = [
            {
                "Id": "101",
                "Name": "Jim Backus (I)",
                "Type": "Actor",
                "Role": "Frank Stark",
            }
        ]
        server = StickySameIdentityEmbyServer(movie)
        provider_ids = {
            "Tmdb": "2771",
            "Tvdb": "260188",
            "Imdb": "nm0000822",
        }
        server.items["100"] = {
            "Id": "100",
            "Name": "Jim Backus",
            "SortName": "Jim Backus",
            "Type": "Person",
            "ProviderIds": dict(provider_ids),
            "LockedFields": [],
        }
        server.items["101"] = {
            "Id": "101",
            "Name": "Jim Backus (I)",
            "SortName": "Jim Backus (I)",
            "Type": "Person",
            "ProviderIds": dict(provider_ids),
            "LockedFields": ["Name", "SortName"],
        }

        class Credits:
            tmdb_id = 1
            credits_source = "tmdb"
            cast = [
                {
                    "id": 2771,
                    "name": "Jim Backus",
                    "character": "Frank Stark",
                    "order": 0,
                }
            ]
            crew = []

        sync = EmbyPeopleSync(server, FakeTMDb(), cache)
        waited_name_sets = []
        original_wait_for_person_name_index = sync._wait_for_person_name_index

        def track_waited_names(expected_names, timeout=15):
            waited_name_sets.append(dict(expected_names))
            return original_wait_for_person_name_index(expected_names, timeout=timeout)

        sync._wait_for_person_name_index = track_waited_names
        sync.stage_item(server.get_item("movie-1"), Credits())
        summary = sync.apply()

        assert summary["failed"] == 0
        assert server.get_item("movie-1")["People"][0]["Id"] == "100"
        assert server.get_item("movie-1")["People"][0]["Name"] == "Jim Backus"
        assert server.people_payloads.count([]) == 1
        assert server.get_item("101")["Name"] == "Emby Duplicate Person 101"
        assert "Kometa Canonical Person Tmdb 2771 100" in server.person_name_searches
        assert waited_name_sets == [{"Kometa Canonical Person Tmdb 2771 100": "100"}]
        cached = cache.query_emby_person_identities("server-a")[2771]
        assert cached["emby_id"] == "100"
        assert cached["duplicate_emby_ids"] == ["101"]
    finally:
        cache.close()


def test_person_name_wait_prefers_exact_resolver_when_search_omits_person(tmp_path):
    cache = make_cache(tmp_path)
    try:

        class ExactOnlyEmbyServer(FakeEmbyServer):
            def get_person_by_name(self, person_name):
                return []

            def get_person_by_exact_name(self, person_name):
                if person_name == "Ben Turner (II)":
                    return {
                        "Id": "2607018",
                        "Name": "Ben Turner (II)",
                        "Type": "Person",
                    }
                return None

        sync = EmbyPeopleSync(ExactOnlyEmbyServer(make_movie()), FakeTMDb(), cache)

        sync._wait_for_person_name_index({"Ben Turner (II)": "2607018"}, timeout=0.1)
    finally:
        cache.close()


def test_linked_same_identity_person_replaces_unpublished_cached_canonical(tmp_path):
    cache = make_cache(tmp_path)
    try:
        cache.update_emby_person_identity(
            "server-a",
            2785,
            "Jack Simmons",
            "jack simmons",
            "Jack Simmons (I)",
            name_index=1,
            emby_id="100",
            imdb_id="nm7479413",
            duplicate_emby_ids=["101"],
        )
        cache.update_emby_person_identity(
            "server-a",
            6346382,
            "Jack Simmons",
            "jack simmons",
            "Jack Simmons (II)",
            name_index=2,
            emby_id="200",
            imdb_id="nm0799779",
        )

        class UnpublishedCanonicalEmbyServer(FakeEmbyServer):
            def update_item(self, item_id, data):
                item_id = str(item_id)
                payload = copy.deepcopy(data)
                item = self.items[item_id]
                if item.get("Type") == "Movie" and payload.get("People") and item.get("People"):
                    payload["People"][0]["Id"] = item["People"][0]["Id"]
                    payload["People"][0]["Name"] = item["People"][0]["Name"]
                old_name = item.get("Name")
                response = super().update_item(item_id, payload)
                new_name = self.items[item_id].get("Name")
                if item.get("Type") == "Person" and old_name != new_name:
                    for linked_item in self.items.values():
                        for linked_person in linked_item.get("People") or []:
                            if str(linked_person.get("Id") or "") == item_id:
                                linked_person["Name"] = new_name
                return response

            def get_person_by_name(self, person_name):
                return [copy.deepcopy(person) for item_id, person in self.items.items() if item_id != "100" and person.get("Type") == "Person" and person.get("Name") == person_name]

        movie = make_movie()
        movie["People"] = [
            {
                "Id": "101",
                "Name": "Emby Duplicate Person 101",
                "Type": "Actor",
                "Role": "Frank Stark",
            }
        ]
        server = UnpublishedCanonicalEmbyServer(movie)
        server.items["100"] = {
            "Id": "100",
            "Name": "Jack Simmons (I)",
            "SortName": "Jack Simmons (I)",
            "Type": "Person",
            "ProviderIds": {"Tmdb": "2785", "Imdb": "nm7479413"},
            "LockedFields": ["Name", "SortName"],
        }
        server.items["101"] = {
            "Id": "101",
            "Name": "Emby Duplicate Person 101",
            "SortName": "Emby Duplicate Person 101",
            "Type": "Person",
            "ProviderIds": {},
            "LockedFields": ["Name", "SortName"],
        }
        server.items["200"] = {
            "Id": "200",
            "Name": "Jack Simmons (II)",
            "SortName": "Jack Simmons (II)",
            "Type": "Person",
            "ProviderIds": {"Tmdb": "6346382", "Imdb": "nm0799779"},
            "LockedFields": ["Name", "SortName"],
        }

        class Credits:
            tmdb_id = 1
            credits_source = "tmdb"
            cast = [
                {
                    "id": 2785,
                    "name": "Jack Simmons",
                    "character": "Frank Stark",
                    "order": 0,
                }
            ]
            crew = []

        sync = EmbyPeopleSync(server, FakeTMDb(), cache)
        sync.stage_item(server.get_item("movie-1"), Credits())
        summary = sync.apply()

        assert summary["failed"] == 0
        assert sync.identities[2785].emby_id == "101"
        assert sync.identities[2785].duplicate_emby_ids == {"100"}
        assert server.get_item("movie-1")["People"][0]["Id"] == "101"
        assert server.get_item("movie-1")["People"][0]["Name"] == "Jack Simmons (I)"
        assert server.get_item("100")["Name"] == "Emby Duplicate Person 100"
        assert server.get_item("101")["ProviderIds"] == {
            "Tmdb": "2785",
            "Imdb": "nm7479413",
        }
    finally:
        cache.close()


def test_canonical_numeric_route_is_not_rewritten_when_embedded_name_lags(tmp_path):
    cache = make_cache(tmp_path)
    try:
        cache.update_emby_person_identity(
            "server-a",
            2983,
            "Mark Strong",
            "mark strong",
            "Mark Strong",
            emby_id="100",
            duplicate_emby_ids=["101"],
        )
        cache.update_emby_person_identity(
            "server-a",
            107817,
            "Ben Turner",
            "ben turner",
            "Ben Turner (I)",
            name_index=1,
            emby_id="201",
        )
        cache.update_emby_person_identity(
            "server-a",
            1261071,
            "Ben Turner",
            "ben turner",
            "Ben Turner (II)",
            name_index=2,
            emby_id="200",
        )

        class LaggingNameEmbyServer(FakeEmbyServer):
            def update_item(self, item_id, data):
                payload = copy.deepcopy(data)
                if str(item_id) == "movie-1" and payload.get("People"):
                    for person in payload["People"]:
                        if person.get("Name") == "Mark Strong" and self.items["movie-1"].get("People"):
                            person["Id"] = "101"
                            person["Name"] = self.items["101"]["Name"]
                        elif person.get("Name") == "Ben Turner (II)":
                            person["Id"] = "200"
                            person["Name"] = "Ben Turner"
                return super().update_item(item_id, payload)

        movie = make_movie()
        movie["People"] = [
            {"Id": "101", "Name": "Emby Duplicate Person 101", "Type": "Actor", "Role": "Max Vernon"},
            {"Id": "200", "Name": "Ben Turner", "Type": "Actor", "Role": "Salim"},
        ]
        server = LaggingNameEmbyServer(movie)
        server.items["100"] = {
            "Id": "100",
            "Name": "Mark Strong",
            "SortName": "Mark Strong",
            "Type": "Person",
            "ProviderIds": {"Tmdb": "2983"},
            "LockedFields": ["Name", "SortName"],
        }
        server.items["101"] = {
            "Id": "101",
            "Name": "Emby Duplicate Person 101",
            "SortName": "Emby Duplicate Person 101",
            "Type": "Person",
            "ProviderIds": {},
            "LockedFields": ["Name", "SortName"],
        }
        server.items["200"] = {
            "Id": "200",
            "Name": "Ben Turner",
            "SortName": "Ben Turner",
            "Type": "Person",
            "ProviderIds": {"Tmdb": "1261071"},
            "LockedFields": ["Name", "SortName"],
        }
        server.items["201"] = {
            "Id": "201",
            "Name": "Ben Turner (I)",
            "SortName": "Ben Turner (I)",
            "Type": "Person",
            "ProviderIds": {"Tmdb": "107817"},
            "LockedFields": ["Name", "SortName"],
        }

        class Credits:
            tmdb_id = 1
            credits_source = "tmdb"
            cast = [
                {"id": 2983, "name": "Mark Strong", "character": "Max Vernon", "order": 0},
                {"id": 1261071, "name": "Ben Turner", "character": "Salim", "order": 1},
            ]
            crew = []

        sync = EmbyPeopleSync(server, FakeTMDb(), cache)
        sync._name_propagation_wait_seconds = 0
        sync.stage_item(server.get_item("movie-1"), Credits())
        summary = sync.apply()

        assert summary["failed"] == 0
        assert server.get_item("movie-1")["People"][0]["Id"] == "100"
        assert server.get_item("movie-1")["People"][1]["Id"] == "200"
        assert server.people_payloads.count([]) == 1
        assert server.people_payloads[-1][0]["Name"].startswith("Kometa Canonical Person Tmdb 2983")
    finally:
        cache.close()
