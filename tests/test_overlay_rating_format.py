import modules.builder  # noqa: F401 - initializes the plex/builder import cycle

from modules.overlays import format_overlay_rating


def test_emby_tmdb_critic_percent_keeps_0_to_100_scale():
    assert format_overlay_rating(70, "critic_rating", "%", emby=True) == "70"


def test_emby_trakt_user_literal_percent_converts_0_to_10_scale():
    assert format_overlay_rating(9, "user_rating", emby=True, literal_percent=True) == "90"


def test_emby_imdb_audience_always_has_one_decimal_place():
    assert format_overlay_rating(9, "audience_rating", emby=True) == "9.0"


def test_rating_source_percentage_uses_0_to_10_scale():
    assert format_overlay_rating(7, "tmdb_rating", "%", emby=True) == "70"


def test_plex_critic_rating_remains_on_0_to_10_scale():
    assert format_overlay_rating(7, "critic_rating", "%", emby=False) == "70"
