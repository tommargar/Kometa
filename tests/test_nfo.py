from xml.etree import ElementTree

from modules.nfo import NfoIndex


def test_nfo_index_writes_and_skips_unchanged_movie(tmp_path):
    library = tmp_path / "movies"
    movie_dir = library / "Arrival (2016) [tmdb-329865]"
    movie_dir.mkdir(parents=True)
    video = movie_dir / "Arrival (2016).mkv"
    video.write_bytes(b"video")
    metadata = {"Arrival": {"title": "Arrival", "year": 2016, "summary": "First plot", "genre": ["Drama", "Science Fiction"]}}

    with NfoIndex(tmp_path / "index.db", [library]) as index:
        first = index.apply(metadata)
        second = index.apply(metadata)

    assert first["written"] == 1
    assert second["written"] == 0
    root = ElementTree.parse(movie_dir / "movie.nfo").getroot()
    assert root.findtext("title") == "Arrival"
    assert root.findtext("tmdbid") == "329865"
    assert [node.text for node in root.findall("genre")] == ["Drama", "Science Fiction"]


def test_nfo_merge_preserves_unknown_jellyfin_fields(tmp_path):
    library = tmp_path / "movies"
    movie_dir = library / "Alien (1979)"
    movie_dir.mkdir(parents=True)
    (movie_dir / "Alien.mkv").write_bytes(b"video")
    (movie_dir / "movie.nfo").write_text("<movie><title>Alien</title><customrating>keep</customrating><genre>Old</genre></movie>", encoding="utf-8")

    with NfoIndex(tmp_path / "index.db", [library]) as index:
        result = index.apply({"Alien": {"title": "Alien", "year": 1979, "genre": ["Horror"]}})

    root = ElementTree.parse(movie_dir / "movie.nfo").getroot()
    assert result["written"] == 1
    assert root.findtext("customrating") == "keep"
    assert [node.text for node in root.findall("genre")] == ["Horror"]


def test_nfo_matches_kometa_mapping_id(tmp_path):
    library = tmp_path / "movies"
    movie_dir = library / "Blade Runner"
    movie_dir.mkdir(parents=True)
    (movie_dir / "Blade Runner.mkv").write_bytes(b"video")
    (movie_dir / "movie.nfo").write_text('<movie><title>Blade Runner</title><uniqueid type="imdb" default="true">tt0083658</uniqueid></movie>', encoding="utf-8")

    with NfoIndex(tmp_path / "index.db", [library]) as index:
        result = index.apply({"entry": {"mapping_id": "tt0083658", "title": "Blade Runner: Final Cut"}})

    assert result["written"] == 1
    assert ElementTree.parse(movie_dir / "movie.nfo").getroot().findtext("title") == "Blade Runner: Final Cut"
