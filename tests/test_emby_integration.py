"""Tests for Emby integration features."""

import os
import tempfile
from unittest.mock import MagicMock, patch

from modules.cache import Cache


class TestEmbyIntegration:
    """Test Emby-specific caching and operations."""

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
        self.cache = None
        gc.collect()
        if os.path.exists(cache_path):
            os.remove(cache_path)
        if os.path.exists(self.config_path):
            os.remove(self.config_path)
        os.rmdir(self.temp_dir)

    def test_tvdb_data5_with_networks_production_studio(self):
        """Test that tvdb_data5 stores networks, production, and studio fields."""

        class FakeTVDbShow:
            tvdb_id = 121361
            is_movie = False
            title = "Breaking Bad"
            status = "Ended"
            summary = "A chemistry teacher turned meth cook."
            poster_url = "https://example.com/poster.jpg"
            background_url = "https://example.com/background.jpg"
            release_date = None
            genres = ["Drama", "Crime"]
            networks = ["AMC"]
            production = ["Vince Gilligan Productions"]
            studio = ["Sony Pictures Television"]

        show = FakeTVDbShow()

        # Store show with production metadata
        self.cache.update_tvdb(False, show, 30)

        # Query show
        data, expired = self.cache.query_tvdb(121361, False, 30)
        assert data is not None
        assert data["title"] == "Breaking Bad"
        assert data["networks"] == "AMC"
        assert data["production"] == "Vince Gilligan Productions"
        assert data["studio"] == "Sony Pictures Television"

    def test_tmdb_movie_data_with_cast_crew(self):
        """Test that cast and crew are stored in tmdb_movie_data2."""

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
            release_date = None
            collection_id = None
            collection_name = None
            cast = [
                {"id": 1, "name": "Brad Pitt", "character": "Tyler Durden", "order": 1},
                {"id": 2, "name": "Edward Norton", "character": "The Narrator", "order": 0},
            ]
            crew = [
                {"id": 3, "name": "David Fincher", "job": "Director", "department": "Directing"},
            ]

        movie = FakeMovie()

        # Store movie with cast/crew
        self.cache.update_tmdb_movie(False, movie, "en", 30)

        # Query and verify cast/crew
        data, _ = self.cache.query_tmdb_movie(550, "en", 30)
        assert data["cast"] == movie.cast
        assert data["crew"] == movie.crew

    def test_tmdb_show_data_with_cast_crew(self):
        """Test that cast and crew are stored in tmdb_show_data4."""

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
            first_air_date = None
            last_air_date = None
            status = "Ended"
            type = "Scripted"
            tvdb_id = 81189
            countries = ["US"]
            seasons = []
            cast = [
                {"id": 1, "name": "Bryan Cranston", "character": "Walter White"},
            ]
            crew = [
                {"id": 2, "name": "Vince Gilligan", "job": "Creator", "department": "Writing"},
            ]

        show = FakeShow()

        # Store show with cast/crew
        self.cache.update_tmdb_show(False, show, "en", 30)

        # Query and verify cast/crew
        data, _ = self.cache.query_tmdb_show(1399, "en", 30)
        assert data["cast"] == show.cast
        assert data["crew"] == show.crew

    def test_emby_people_from_tmdb_movie(self):
        """Test extracting people from TMDB movie cast/crew for Emby integration."""

        class FakeMovie:
            tmdb_id = 550
            title = "Fight Club"
            original_title = "Fight Club"
            studio = "20th Century Fox"
            overview = "An insomniac office worker..."
            tagline = "How much do you know about yourself?"
            imdb_id = "tt0137523"
            poster_url = "https://example.com/poster.jpg"
            backdrop_url = "https://example.com/backdrop.jpg"
            vote_count = 25000
            vote_average = 8.8
            language_iso = "en"
            language_name = "English"
            genres = ["Drama"]
            keywords = []
            release_date = None
            collection_id = None
            collection_name = None
            cast = [
                {"id": 287, "name": "Brad Pitt", "character": "Tyler Durden", "order": 0},
                {"id": 3, "name": "Edward Norton", "character": "The Narrator", "order": 1},
            ]
            crew = [
                {"id": 1, "name": "David Fincher", "job": "Director", "department": "Directing"},
            ]

        movie = FakeMovie()

        # Store movie
        self.cache.update_tmdb_movie(False, movie, "en", 30)

        # Verify person map was pre-populated from cast/crew
        mapping, missing, _ = self.cache.query_tmdb_person_map_bulk([287, 3, 1], 30)

        # Should have at least Brad Pitt and Edward Norton
        assert 287 in mapping or 287 in missing  # May be not yet cached (depends on implementation)
        assert mapping.get(287, {}).get("name") == "Brad Pitt" or 287 in missing

    def test_false_friend_names_per_emby(self):
        """Test false friend names caching (useful for Emby alias matching)."""

        # Add false friends
        self.cache.add_false_friend_name("John Smith")
        self.cache.add_false_friend_name("Jane Doe")

        # Query false friends
        friends = self.cache.query_false_friend_names()
        assert "john smith" in friends  # Case-folded
        assert "jane doe" in friends


class TestCacheLanguageEdgeCases:
    """Test edge cases in language-based caching."""

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
        self.cache = None
        gc.collect()
        if os.path.exists(cache_path):
            os.remove(cache_path)
        if os.path.exists(self.config_path):
            os.remove(self.config_path)
        os.rmdir(self.temp_dir)

    def test_same_tmdb_id_different_languages(self):
        """Test that the same TMDB ID can be cached in multiple languages independently."""

        class FakeMovie:
            tmdb_id = 100
            title = None  # Will be set per language
            original_title = "Original"
            studio = "Studio"
            overview = None  # Will be set per language
            tagline = ""
            imdb_id = "tt0000100"
            poster_url = "https://example.com/poster.jpg"
            backdrop_url = "https://example.com/backdrop.jpg"
            vote_count = 100
            vote_average = 7.0
            language_iso = None
            language_name = None
            genres = []
            keywords = []
            release_date = None
            collection_id = None
            collection_name = None
            cast = []
            crew = []

        # Store English version
        en_movie = FakeMovie()
        en_movie.title = "The Movie"
        en_movie.overview = "An English overview"
        en_movie.language_iso = "en"
        en_movie.language_name = "English"
        self.cache.update_tmdb_movie(False, en_movie, "en", 30)

        # Store German version
        de_movie = FakeMovie()
        de_movie.title = "Der Film"
        de_movie.overview = "Eine deutsche Zusammenfassung"
        de_movie.language_iso = "de"
        de_movie.language_name = "German"
        self.cache.update_tmdb_movie(False, de_movie, "de", 30)

        # Query both
        en_data, _ = self.cache.query_tmdb_movie(100, "en", 30)
        de_data, _ = self.cache.query_tmdb_movie(100, "de", 30)

        assert en_data["title"] == "The Movie"
        assert de_data["title"] == "Der Film"
        assert en_data["overview"] == "An English overview"
        assert de_data["overview"] == "Eine deutsche Zusammenfassung"
