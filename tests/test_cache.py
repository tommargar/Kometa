"""Tests for cache module, including new language-based and emby features."""

import json
import os
import sqlite3
import tempfile
from datetime import datetime, timedelta

from modules.cache import Cache


class TestCacheLanguageParameters:
    """Test language parameter handling in TMDB caching."""

    def setup_method(self):
        """Create temporary cache for each test."""
        self.temp_dir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.temp_dir, "test_config.yml")
        with open(self.config_path, "w") as f:
            f.write("test: true\n")
        self.cache = Cache(self.config_path, expiration=30)

    def teardown_method(self):
        """Clean up temporary files."""
        import gc
        cache_path = self.cache.cache_path
        self.cache.close()
        self.cache = None
        gc.collect()
        if os.path.exists(cache_path):
            os.remove(cache_path)
        if os.path.exists(self.config_path):
            os.remove(self.config_path)
        os.rmdir(self.temp_dir)

    def test_tmdb_movie_with_language(self):
        """Test that tmdb_movie cache stores data per language."""

        class FakeMovie:
            tmdb_id = 550
            title = "Fight Club"
            original_title = "Fight Club"
            studio = "20th Century Fox"
            overview = "An insomniac office worker and a devil-may-care soap maker form an underground fight club."
            tagline = "How much do you know about yourself?"
            imdb_id = "tt0137523"
            poster_url = "https://example.com/poster.jpg"
            backdrop_url = "https://example.com/backdrop.jpg"
            vote_count = 25000
            vote_average = 8.8
            language_iso = "en"
            language_name = "English"
            genres = ["Drama", "Thriller"]
            keywords = ["fight", "club"]
            release_date = datetime(1999, 10, 15)
            collection_id = None
            collection_name = None
            cast = [{"id": 1, "name": "Brad Pitt", "character": "Tyler Durden"}]
            crew = [{"id": 2, "name": "David Fincher", "job": "Director"}]

        movie = FakeMovie()

        # Store English version
        self.cache.update_tmdb_movie(False, movie, "en", 30)

        # Query English version
        data, expired = self.cache.query_tmdb_movie(550, "en", 30)
        assert data is not None
        assert data["title"] == "Fight Club"
        assert data["cast"] == movie.cast
        assert data["crew"] == movie.crew

        # German version should not exist
        data_de, _ = self.cache.query_tmdb_movie(550, "de", 30)
        assert data_de is None or data_de == {}

    def test_tmdb_show_with_language(self):
        """Test that tmdb_show cache stores data per language."""

        class FakeShow:
            tmdb_id = 1399
            title = "Breaking Bad"
            original_title = "Breaking Bad"
            studio = "AMC"
            overview = "A chemistry teacher turned meth cook."
            tagline = "You are welcome"
            imdb_id = "tt0903747"
            poster_url = "https://example.com/poster.jpg"
            backdrop_url = "https://example.com/backdrop.jpg"
            vote_count = 12000
            vote_average = 9.5
            language_iso = "en"
            language_name = "English"
            genres = ["Drama", "Crime"]
            keywords = ["meth", "teacher"]
            first_air_date = datetime(2008, 1, 20)
            last_air_date = datetime(2013, 9, 29)
            status = "Ended"
            type = "Scripted"
            tvdb_id = 81189
            countries = ["US"]
            seasons = []
            cast = [{"id": 1, "name": "Bryan Cranston", "character": "Walter White"}]
            crew = [{"id": 2, "name": "Vince Gilligan", "job": "Creator"}]

        show = FakeShow()

        # Store English version
        self.cache.update_tmdb_show(False, show, "en", 30)

        # Query English version
        data, expired = self.cache.query_tmdb_show(1399, "en", 30)
        assert data is not None
        assert data["title"] == "Breaking Bad"
        assert data.get("cast") == show.cast
        assert data.get("crew") == show.crew

    def test_tmdb_episode_with_language(self):
        """Test that tmdb_episode cache stores data per language."""

        class FakeEpisode:
            tmdb_id = 1399
            season_number = 1
            episode_number = 1
            title = "Pilot"
            air_date = datetime(2008, 1, 20)
            overview = "A high school chemistry teacher..."
            still_url = "https://example.com/still.jpg"
            vote_count = 5000
            vote_average = 8.2
            imdb_id = "tt0959621"
            tvdb_id = 349232

        episode = FakeEpisode()

        # Store English version
        self.cache.update_tmdb_episode(False, episode, "en", 30)

        # Query English version
        data, expired = self.cache.query_tmdb_episode(1399, 1, 1, "en", 30)
        assert data is not None
        assert data["title"] == "Pilot"

        # German version should not exist
        data_de, _ = self.cache.query_tmdb_episode(1399, 1, 1, "de", 30)
        assert data_de is None or data_de == {}


class TestMediaPeopleCache:
    """Test unified media_people_cache for Plex and Emby."""

    def setup_method(self):
        """Create temporary cache for each test."""
        self.temp_dir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.temp_dir, "test_config.yml")
        with open(self.config_path, "w") as f:
            f.write("test: true\n")
        self.cache = Cache(self.config_path, expiration=30)

    def teardown_method(self):
        """Clean up temporary files."""
        import gc
        cache_path = self.cache.cache_path
        self.cache.close()
        self.cache = None
        gc.collect()
        if os.path.exists(cache_path):
            os.remove(cache_path)
        if os.path.exists(self.config_path):
            os.remove(self.config_path)
        os.rmdir(self.temp_dir)

    def test_plex_people_cache(self):
        """Test Plex people caching via query_plex_people/update_plex_people."""
        people_list = [
            {"id": 1, "name": "Actor One"},
            {"id": 2, "name": "Actor Two"},
        ]

        # Store Plex people
        self.cache.update_plex_people("plex_item_123", "cast", people_list, False)

        # Query Plex people
        cached, expired = self.cache.query_plex_people("plex_item_123", "cast")
        assert cached == people_list
        assert not expired

    def test_emby_people_cache(self):
        """Test Emby people caching via query_emby_people/update_emby_people."""
        people_list = [
            {"id": "emby_1", "name": "Director Name"},
        ]

        # Store Emby people
        self.cache.update_emby_people("emby_item_456", "directors", people_list, False)

        # Query Emby people
        cached, expired = self.cache.query_emby_people("emby_item_456", "directors")
        assert cached == people_list
        assert not expired

    def test_media_people_cache_unified(self):
        """Test unified media_people_cache with different sources."""
        plex_cast = [{"id": 1, "name": "Plex Actor"}]
        emby_cast = [{"id": 2, "name": "Emby Actor"}]

        # Store same item_id but different sources
        self.cache.update_media_people("item_123", "cast", plex_cast, False, source="plex")
        self.cache.update_media_people("item_123", "cast", emby_cast, False, source="emby")

        # Query separately
        plex_cached, _ = self.cache.query_media_people("item_123", "cast", source="plex")
        emby_cached, _ = self.cache.query_media_people("item_123", "cast", source="emby")

        assert plex_cached == plex_cast
        assert emby_cached == emby_cast


class TestTmdbPersonMap:
    """Test TMDb person mapping for Emby integration."""

    def setup_method(self):
        """Create temporary cache for each test."""
        self.temp_dir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.temp_dir, "test_config.yml")
        with open(self.config_path, "w") as f:
            f.write("test: true\n")
        self.cache = Cache(self.config_path, expiration=30)

    def teardown_method(self):
        """Clean up temporary files."""
        import gc
        cache_path = self.cache.cache_path
        self.cache.close()
        self.cache = None
        gc.collect()
        if os.path.exists(cache_path):
            os.remove(cache_path)
        if os.path.exists(self.config_path):
            os.remove(self.config_path)
        os.rmdir(self.temp_dir)

    def test_update_and_query_person_map(self):
        """Test storing and retrieving TMDb person mappings."""
        tmdb_id = 12345
        emby_id = "emby-person-abc"
        name = "Brad Pitt"
        alias = "Bradley Pitt"
        meta = {"bio": "American actor", "birthdate": "1963-12-18"}

        # Update person map
        self.cache.update_tmdb_person_map(False, tmdb_id, emby_id=emby_id, name=name, alias=alias, meta_patch=meta)

        # Query person map
        mapping, missing, expired = self.cache.query_tmdb_person_map_bulk([tmdb_id], 30)

        assert tmdb_id in mapping
        assert mapping[tmdb_id]["emby_id"] == emby_id
        assert mapping[tmdb_id]["name"] == name
        assert mapping[tmdb_id]["alias"] == alias
        assert mapping[tmdb_id]["meta"]["bio"] == "American actor"

    def test_bulk_person_map_query(self):
        """Test bulk querying of multiple TMDb persons."""
        persons = [
            (1, "Actor One", "emby-1"),
            (2, "Actor Two", "emby-2"),
            (3, "Actor Three", None),  # Mapped but without emby_id
        ]

        for tmdb_id, name, emby_id in persons:
            self.cache.update_tmdb_person_map(False, tmdb_id, emby_id=emby_id, name=name)

        # Query all
        tmdb_ids = [p[0] for p in persons]
        mapping, missing, expired = self.cache.query_tmdb_person_map_bulk(tmdb_ids, 30)

        assert len(mapping) == 3  # All 3 are cached
        assert len(missing) == 0  # None are missing
        assert mapping[1]["name"] == "Actor One"
        assert mapping[2]["emby_id"] == "emby-2"
        assert mapping[3]["name"] == "Actor Three"
        assert mapping[3]["emby_id"] is None
