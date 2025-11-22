import os
import re
import time
from datetime import datetime, timedelta
from urllib import parse
# from importlib.metadata import pass_none
from xml.etree.ElementTree import ParseError

import requests
from plexapi.exceptions import BadRequest, Unauthorized
from requests.exceptions import ConnectionError, ConnectTimeout

from modules import util, builder
from modules.emby_server import EmbyServer, Collection
from modules.library import Library
from modules.logs import WARNING
from modules.util import Failed
from urllib.parse import unquote, parse_qsl, parse_qs

logger = util.logger

builders = ["plex_all", "plex_watchlist", "plex_pilots", "plex_collectionless", "plex_search"]
library_types = ["movie", "show", "artist"]
search_translation = {
    "episode_actor": "episode.actor",
    "episode_title": "episode.title",
    "network": "show.network",
    "edition": "editionTitle",
    "critic_rating": "rating",
    "audience_rating": "audienceRating",
    "episode_critic_rating": "episode.rating",
    "episode_audience_rating": "episode.audienceRating",
    "user_rating": "userRating",
    "episode_user_rating": "episode.userRating",
    "content_rating": "contentRating",
    "episode_year": "episode.year",
    "release": "originallyAvailableAt",
    "show_unmatched": "show.unmatched",
    "episode_unmatched": "episode.unmatched",
    "episode_duplicate": "episode.duplicate",
    "added": "addedAt",
    "episode_added": "episode.addedAt",
    "episode_air_date": "episode.originallyAvailableAt",
    "plays": "viewCount",
    "episode_plays": "episode.viewCount",
    "last_played": "lastViewedAt",
    "episode_last_played": "episode.lastViewedAt",
    "unplayed": "unwatched",
    "episode_unplayed": "episode.unwatched",
    "dovi": "dovi",
    "subtitle_language": "subtitleLanguage",
    "audio_language": "audioLanguage",
    "progress": "inProgress",
    "episode_progress": "episode.inProgress",
    "unplayed_episodes": "show.unwatchedLeaves",
    "season_collection": "season.collection",
    "episode_collection": "episode.collection",
    "season_label": "season.label",
    "episode_label": "episode.label",
    "artist_title": "artist.title",
    "artist_user_rating": "artist.userRating",
    "artist_genre": "artist.genre",
    "artist_collection": "artist.collection",
    "artist_country": "artist.country",
    "artist_mood": "artist.mood",
    "artist_style": "artist.style",
    "artist_added": "artist.addedAt",
    "artist_last_played": "artist.lastViewedAt",
    "artist_unmatched": "artist.unmatched",
    "artist_label": "artist.label",
    "album_title": "album.title",
    "album_year": "album.year",
    "album_decade": "album.decade",
    "album_genre": "album.genre",
    "album_plays": "album.viewCount",
    "album_last_played": "album.lastViewedAt",
    "album_user_rating": "album.userRating",
    "album_critic_rating": "album.rating",
    "album_record_label": "album.studio",
    "album_mood": "album.mood",
    "album_style": "album.style",
    "album_format": "album.format",
    "album_type": "album.subformat",
    "album_collection": "album.collection",
    "album_added": "album.addedAt",
    "album_released": "album.originallyAvailableAt",
    "album_unmatched": "album.unmatched",
    "album_source": "album.source",
    "album_label": "album.label",
    "track_mood": "track.mood",
    "track_title": "track.title",
    "track_plays": "track.viewCount",
    "track_last_played": "track.lastViewedAt",
    "track_skips": "track.skipCount",
    "track_last_skipped": "track.lastSkippedAt",
    "track_user_rating": "track.userRating",
    "track_last_rated": "track.lastRatedAt",
    "track_added": "track.addedAt",
    "track_trash": "track.trash",
    "track_source": "track.source",
    "track_label": "track.label"
}
show_translation = {
    "title": "show.title",
    "country": "show.country",
    "studio": "show.studio",
    "rating": "show.rating",
    "audienceRating": "show.audienceRating",
    "userRating": "show.userRating",
    "contentRating": "show.contentRating",
    "year": "show.year",
    "originallyAvailableAt": "show.originallyAvailableAt",
    "unmatched": "show.unmatched",
    "genre": "show.genre",
    "collection": "show.collection",
    "actor": "show.actor",
    "addedAt": "show.addedAt",
    "viewCount": "show.viewCount",
    "lastViewedAt": "show.lastViewedAt",
    "resolution": "episode.resolution",
    "hdr": "episode.hdr",
    "subtitleLanguage": "episode.subtitleLanguage",
    "audioLanguage": "episode.audioLanguage",
    "trash": "episode.trash",
    "label": "show.label",
}
get_tags_translation = {"episode.actor": "actor"}
modifier_translation = {
    "": "", ".not": "!", ".is": "%3D", ".isnot": "!%3D", ".gt": "%3E%3E", ".gte": "%3E", ".lt": "%3C%3C", ".lte": "%3C",
    ".before": "%3C%3C", ".after": "%3E%3E", ".begins": "%3C", ".ends": "%3E", ".regex": "", ".rated": ""
}
attribute_translation = {
    "aspect": "aspectRatio",
    "channels": "audioChannels",
    "audio_codec": "audioCodec",
    "audio_profile ": "audioProfile",
    "video_codec": "videoCodec",
    "video_profile": "videoProfile",
    "resolution": "videoResolution",
    "record_label": "studio",
    "similar_artist": "similar",
    "actor": "actors",
    "audience_rating": "audienceRating",
    "collection": "collections",
    "content_rating": "contentRating",
    "country": "countries",
    "critic_rating": "rating",
    "director": "directors",
    "genre": "genres",
    "label": "labels",
    "producer": "producers",
    "composer": "composers",
    "release": "originallyAvailableAt",
    "originally_available": "originallyAvailableAt",
    "added": "addedAt",
    "last_played": "lastViewedAt",
    "plays": "viewCount",
    "user_rating": "userRating",
    "writer": "writers",
    "mood": "moods",
    "style": "styles",
    "episode_number": "episodeNumber",
    "season_number": "seasonNumber",
    "original_title": "originalTitle",
    "edition": "editionTitle",
    "runtime": "duration",
    "season_title": "parentTitle",
    "episode_count": "leafCount",
    "versions": "media"
}
method_alias = {
    "actors": "actor", "role": "actor", "roles": "actor",
    "show_actor": "actor", "show_actors": "actor", "show_role": "actor", "show_roles": "actor",
    "collections": "collection", "plex_collection": "collection",
    "show_collections": "collection", "show_collection": "collection",
    "content_ratings": "content_rating", "contentRating": "content_rating", "contentRatings": "content_rating",
    "countries": "country",
    "decades": "decade",
    "directors": "director",
    "genres": "genre",
    "labels": "label",
    "collection_minimum": "minimum_items",
    "playlist_minimum": "minimum_items",
    "save_missing": "save_report",
    "rating": "critic_rating",
    "show_user_rating": "user_rating",
    "video_resolution": "resolution",
    "tmdb_trending": "tmdb_trending_daily",
    "play": "plays", "show_plays": "plays", "show_play": "plays", "episode_play": "episode_plays",
    "originally_available": "release", "episode_originally_available": "episode_air_date",
    "episode_release": "episode_air_date", "episode_released": "episode_air_date",
    "show_originally_available": "release", "show_release": "release", "show_air_date": "release",
    "released": "release", "show_released": "release", "max_age": "release",
    "studios": "studio",
    "networks": "network",
    "producers": "producer",
    "composers": "composer",
    "writers": "writer",
    "years": "year", "show_year": "year", "show_years": "year",
    "show_title": "title", "filter": "filters",
    "seasonyear": "year", "isadult": "adult", "startdate": "start", "enddate": "end", "averagescore": "score",
    "minimum_tag_percentage": "min_tag_percent", "minimumtagrank": "min_tag_percent", "minimum_tag_rank": "min_tag_percent",
    "anilist_tag": "anilist_search", "anilist_genre": "anilist_search", "anilist_season": "anilist_search",
    "mal_producer": "mal_studio", "mal_licensor": "mal_studio",
    "trakt_recommended": "trakt_recommended_weekly", "trakt_watched": "trakt_watched_weekly", "trakt_collected": "trakt_collected_weekly",
    "collection_changes_webhooks": "changes_webhooks",
    "radarr_add": "radarr_add_missing", "sonarr_add": "sonarr_add_missing",
    "trakt_recommended_personal": "trakt_recommendations",
    "collection_level": "builder_level", "overlay_level": "builder_level",
}
modifier_alias = {".greater": ".gt", ".less": ".lt"}
date_sub_mods = {"s": "Seconds", "m": "Minutes", "h": "Hours", "d": "Days", "w": "Weeks", "o": "Months", "y": "Years"}
album_sorting_options = {"default": -1, "newest": 0, "oldest": 1, "name": 2}
episode_sorting_options = {"default": -1, "oldest": 0, "newest": 1}
keep_episodes_options = {"all": 0, "5_latest": 5, "3_latest": 3, "latest": 1, "past_3": -3, "past_7": -7, "past_30": -30}
delete_episodes_options = {"never": 0, "day": 1, "week": 7, "month": 30, "refresh": 100}
season_display_options = {"default": -1, "show": 0, "hide": 1}
episode_ordering_options = {"default": None, "tmdb_aired": "tmdbAiring", "tvdb_aired": "tvdbAiring", "tvdb_dvd": "tvdbDvd", "tvdb_absolute": "tvdbAbsolute"}
plex_languages = ["default", "ar-SA", "ca-ES", "cs-CZ", "da-DK", "de-DE", "el-GR", "en-AU", "en-CA", "en-GB", "en-US",
                  "es-ES", "es-MX", "et-EE", "fa-IR", "fi-FI", "fr-CA", "fr-FR", "he-IL", "hi-IN", "hu-HU", "id-ID",
                  "it-IT", "ja-JP", "ko-KR", "lt-LT", "lv-LV", "nb-NO", "nl-NL", "pl-PL", "pt-BR", "pt-PT", "ro-RO",
                  "ru-RU", "sk-SK", "sv-SE", "th-TH", "tr-TR", "uk-UA", "vi-VN", "zh-CN", "zh-HK", "zh-TW"]
metadata_language_options = {lang.lower(): lang for lang in plex_languages}
metadata_language_options["default"] = None
use_original_title_options = {"default": -1, "no": 0, "yes": 1}
credits_detection_options = {"default": -1, "disabled": 0}
audio_language_options = {lang.lower(): lang for lang in plex_languages}
audio_language_options["en"] = "en"
subtitle_language_options = {lang.lower(): lang for lang in plex_languages}
subtitle_language_options["en"] = "en"
subtitle_mode_options = {"default": -1, "manual": 0, "foreign": 1, "always": 2}
collection_order_options = ["release", "alpha", "custom"]
collection_filtering_options = ["user", "admin"]
collection_mode_options = {
    "default": "default", "hide": "hide",
    "hide_items": "hideItems", "hideitems": "hideItems",
    "show_items": "showItems", "showitems": "showItems"
}
builder_level_show_options = ["episode", "season"]
builder_level_music_options = ["album", "track"]
builder_level_options = builder_level_show_options + builder_level_music_options
collection_mode_keys = {-1: "default", 0: "hide", 1: "hideItems", 2: "showItems"}
collection_order_keys = {0: "release", 1: "alpha", 2: "custom"}
item_advance_keys = {
    "item_album_sorting": ("albumSort", album_sorting_options),
    "item_episode_sorting": ("episodeSort", episode_sorting_options),
    "item_keep_episodes": ("autoDeletionItemPolicyUnwatchedLibrary", keep_episodes_options),
    "item_delete_episodes": ("autoDeletionItemPolicyWatchedLibrary", delete_episodes_options),
    "item_season_display": ("flattenSeasons", season_display_options),
    "item_episode_ordering": ("showOrdering", episode_ordering_options),
    "item_metadata_language": ("languageOverride", metadata_language_options),
    "item_use_original_title": ("useOriginalTitle", use_original_title_options),
    "item_credits_detection": ("enableCreditsMarkerGeneration", credits_detection_options),
    "item_audio_language": ("audioLanguage", audio_language_options),
    "item_subtitle_language": ("subtitleLanguage", subtitle_language_options),
    "item_subtitle_mode": ("subtitleMode", subtitle_mode_options)
}
new_plex_agents = ["tv.plex.agents.movie", "tv.plex.agents.series"]
and_searches = [
    "title.and", "studio.and", "actor.and", "audio_language.and", "collection.and",
    "content_rating.and", "country.and",  "director.and", "genre.and", "label.and",
    "network.and", "producer.and", "composer.and", "subtitle_language.and", "writer.and"
]
or_searches = [
    "title", "studio", "actor", "audio_language", "collection", "content_rating",
    "country", "director", "genre", "label", "network", "producer", "composer", "subtitle_language",
    "writer", "decade", "resolution", "year", "episode_title", "episode_year"
]
movie_only_searches = [
    "director", "director.not", "producer", "producer.not", "composer", "composer.not", "writer", "writer.not",
    "decade", "duplicate", "unplayed", "progress",
    "duration.gt", "duration.gte", "duration.lt", "duration.lte"
    "edition", "edition.not", "edition.is", "edition.isnot", "edition.begins", "edition.ends"
]
show_only_searches = [
    "network", "network.not",
    "season_collection", "season_collection.not",
    "episode_collection", "episode_collection.not",
    "season_label", "season_label.not",
    "episode_label", "episode_label.not",
    "episode_title", "episode_title.not", "episode_title.is", "episode_title.isnot", "episode_title.begins", "episode_title.ends",
    "episode_added", "episode_added.not", "episode_added.before", "episode_added.after",
    "episode_air_date", "episode_air_date.not",
    "episode_air_date.before", "episode_air_date.after",
    "episode_last_played", "episode_last_played.not", "episode_last_played.before", "episode_last_played.after",
    "episode_plays.gt", "episode_plays.gte", "episode_plays.lt", "episode_plays.lte",
    "episode_user_rating.gt", "episode_user_rating.gte", "episode_user_rating.lt", "episode_user_rating.lte", "episode_user_rating.rated",
    "episode_critic_rating.gt", "episode_critic_rating.gte", "episode_critic_rating.lt", "episode_critic_rating.lte", "episode_critic_rating.rated",
    "episode_audience_rating.gt", "episode_audience_rating.gte", "episode_audience_rating.lt", "episode_audience_rating.lte", "episode_audience_rating.rated",
    "episode_year", "episode_year.not", "episode_year.gt", "episode_year.gte", "episode_year.lt", "episode_year.lte",
    "unplayed_episodes", "episode_unplayed", "episode_duplicate", "episode_progress", "episode_unmatched", "show_unmatched",
]
string_attributes = ["title", "studio", "edition", "episode_title", "artist_title", "album_title", "album_record_label", "track_title"]
string_modifiers = ["", ".not", ".is", ".isnot", ".begins", ".ends"]
boolean_attributes = [
    "dovi", "hdr", "unmatched", "duplicate", "unplayed", "progress", "trash", "unplayed_episodes", "episode_unplayed",
    "episode_duplicate", "episode_progress", "episode_unmatched", "show_unmatched", "artist_unmatched", "album_unmatched", "track_trash"
]
tmdb_attributes = ["actor", "director", "producer", "composer", "writer"]
date_attributes = [
    "added", "episode_added", "release", "episode_air_date", "last_played", "episode_last_played",
    "artist_added", "artist_last_played", "album_last_played",
    "album_added", "album_released", "track_last_played", "track_last_skipped", "track_last_rated", "track_added"
]
date_modifiers = ["", ".not", ".before", ".after"]
year_attributes = ["decade", "year", "episode_year", "album_year", "album_decade"]
number_attributes = ["plays", "episode_plays", "album_plays", "track_plays", "track_skips"] + year_attributes
number_modifiers = [".gt", ".gte", ".lt", ".lte"]
float_attributes = [
    "user_rating", "episode_user_rating", "critic_rating", "episode_critic_rating", "audience_rating", "episode_audience_rating",
    "duration", "artist_user_rating", "album_user_rating", "album_critic_rating", "track_user_rating"
]
float_modifiers = number_modifiers + [".rated"]
search_display = {"added": "Date Added", "release": "Release Date", "hdr": "HDR", "progress": "In Progress", "episode_progress": "Episode In Progress"}
tag_attributes = [
    "actor", "episode_actor", "audio_language", "collection", "content_rating", "country", "director", "genre", "label", "season_label", "episode_label", "network",
    "producer", "composer", "resolution", "studio", "subtitle_language", "writer", "season_collection", "episode_collection", "edition",
    "artist_genre", "artist_collection", "artist_country", "artist_mood", "artist_label", "artist_style", "album_genre", "album_mood",
    "album_style", "album_format", "album_type", "album_collection", "album_source", "album_label", "track_mood", "track_source", "track_label"
]
tag_modifiers = ["", ".not", ".regex"]
no_not_mods = ["resolution", "decade", "album_decade"]
searches = boolean_attributes + \
               [f"{f}{m}" for f in string_attributes for m in string_modifiers] + \
               [f"{f}{m}" for f in tag_attributes + year_attributes for m in tag_modifiers if f not in no_not_mods or m != ".not"] + \
               [f"{f}{m}" for f in date_attributes for m in date_modifiers] + \
               [f"{f}{m}" for f in number_attributes for m in number_modifiers if f not in no_not_mods] + \
               [f"{f}{m}" for f in float_attributes for m in float_modifiers if f != "duration" or m != ".rated"]
music_searches = [a for a in searches if a.startswith(("artist", "album", "track"))]
movie_sorts = {
    "title.asc": "titleSort", "title.desc": "titleSort%3Adesc",
    "year.asc": "year", "year.desc": "year%3Adesc",
    "originally_available.asc": "originallyAvailableAt", "originally_available.desc": "originallyAvailableAt%3Adesc",
    "release.asc": "originallyAvailableAt", "release.desc": "originallyAvailableAt%3Adesc",
    "critic_rating.asc": "rating", "critic_rating.desc": "rating%3Adesc",
    "audience_rating.asc": "audienceRating", "audience_rating.desc": "audienceRating%3Adesc",
    "user_rating.asc": "userRating",  "user_rating.desc": "userRating%3Adesc",
    "content_rating.asc": "contentRating", "content_rating.desc": "contentRating%3Adesc",
    "duration.asc": "duration", "duration.desc": "duration%3Adesc",
    "progress.asc": "viewOffset", "progress.desc": "viewOffset%3Adesc",
    "plays.asc": "viewCount", "plays.desc": "viewCount%3Adesc",
    "added.asc": "addedAt", "added.desc": "addedAt%3Adesc",
    "viewed.asc": "lastViewedAt", "viewed.desc": "lastViewedAt%3Adesc",
    "resolution.asc": "mediaHeight", "resolution.desc": "mediaHeight%3Adesc",
    "bitrate.asc": "mediaBitrate", "bitrate.desc": "mediaBitrate%3Adesc",
    "random": "random"
}
show_sorts = {
    "title.asc": "titleSort", "title.desc": "titleSort%3Adesc",
    "year.asc": "year", "year.desc": "year%3Adesc",
    "originally_available.asc": "originallyAvailableAt", "originally_available.desc": "originallyAvailableAt%3Adesc",
    "episode_originally_available.asc": "episode.originallyAvailableAt", "episode_originally_available.desc": "episode.originallyAvailableAt%3Adesc",
    "release.asc": "originallyAvailableAt", "release.desc": "originallyAvailableAt%3Adesc",
    "episode_release.asc": "episode.originallyAvailableAt", "episode_release.desc": "episode.originallyAvailableAt%3Adesc",
    "critic_rating.asc": "rating", "critic_rating.desc": "rating%3Adesc",
    "audience_rating.asc": "audienceRating", "audience_rating.desc": "audienceRating%3Adesc",
    "user_rating.asc": "userRating",  "user_rating.desc": "userRating%3Adesc",
    "content_rating.asc": "contentRating", "content_rating.desc": "contentRating%3Adesc",
    "unplayed.asc": "unviewedLeafCount", "unplayed.desc": "unviewedLeafCount%3Adesc",
    "episode_added.asc": "episode.addedAt", "episode_added.desc": "episode.addedAt%3Adesc",
    "added.asc": "addedAt", "added.desc": "addedAt%3Adesc",
    "viewed.asc": "lastViewedAt", "viewed.desc": "lastViewedAt%3Adesc",
    "random": "random"
}
season_sorts = {
    "season.asc": "season.index%2Cseason.titleSort", "season.desc": "season.index%3Adesc%2Cseason.titleSort",
    "show.asc": "show.titleSort%2Cindex", "show.desc": "show.titleSort%3Adesc%2Cindex",
    "user_rating.asc": "userRating",  "user_rating.desc": "userRating%3Adesc",
    "added.asc": "addedAt", "added.desc": "addedAt%3Adesc",
    "random": "random"
}
episode_sorts = {
    "title.asc": "titleSort", "title.desc": "titleSort%3Adesc",
    "show.asc": "show.titleSort%2Cseason.index%3AnullsLast%2Cepisode.index%3AnullsLast%2Cepisode.originallyAvailableAt%3AnullsLast%2Cepisode.titleSort%2Cepisode.id",
    "show.desc": "show.titleSort%3Adesc%2Cseason.index%3AnullsLast%2Cepisode.index%3AnullsLast%2Cepisode.originallyAvailableAt%3AnullsLast%2Cepisode.titleSort%2Cepisode.id",
    "year.asc": "year", "year.desc": "year%3Adesc",
    "originally_available.asc": "originallyAvailableAt", "originally_available.desc": "originallyAvailableAt%3Adesc",
    "episode_originally_available.asc": "episode.originallyAvailableAt", "episode_originally_available.desc": "episode.originallyAvailableAt%3Adesc",
    "release.asc": "originallyAvailableAt", "release.desc": "originallyAvailableAt%3Adesc",
    "episode_release.asc": "episode.originallyAvailableAt", "episode_release.desc": "episode.originallyAvailableAt%3Adesc",
    "critic_rating.asc": "rating", "critic_rating.desc": "rating%3Adesc",
    "audience_rating.asc": "audienceRating", "audience_rating.desc": "audienceRating%3Adesc",
    "user_rating.asc": "userRating",  "user_rating.desc": "userRating%3Adesc",
    "duration.asc": "duration", "duration.desc": "duration%3Adesc",
    "progress.asc": "viewOffset", "progress.desc": "viewOffset%3Adesc",
    "plays.asc": "viewCount", "plays.desc": "viewCount%3Adesc",
    "added.asc": "addedAt", "added.desc": "addedAt%3Adesc",
    "viewed.asc": "lastViewedAt", "viewed.desc": "lastViewedAt%3Adesc",
    "resolution.asc": "mediaHeight", "resolution.desc": "mediaHeight%3Adesc",
    "bitrate.asc": "mediaBitrate", "bitrate.desc": "mediaBitrate%3Adesc",
    "random": "random"
}
artist_sorts = {
    "title.asc": "titleSort", "title.desc": "titleSort%3Adesc",
    "user_rating.asc": "userRating",  "user_rating.desc": "userRating%3Adesc",
    "added.asc": "addedAt", "added.desc": "addedAt%3Adesc",
    "played.asc": "lastViewedAt", "played.desc": "lastViewedAt%3Adesc",
    "plays.asc": "viewCount", "plays.desc": "viewCount%3Adesc",
    "random": "random"
}
album_sorts = {
    "title.asc": "titleSort", "title.desc": "titleSort%3Adesc",
    "album_artist.asc": "artist.titleSort%2Calbum.titleSort%2Calbum.index%2Calbum.id%2Calbum.originallyAvailableAt",
    "album_artist.desc": "artist.titleSort%3Adesc%2Calbum.titleSort%2Calbum.index%2Calbum.id%2Calbum.originallyAvailableAt",
    "year.asc": "year", "year.desc": "year%3Adesc",
    "originally_available.asc": "originallyAvailableAt", "originally_available.desc": "originallyAvailableAt%3Adesc",
    "release.asc": "originallyAvailableAt", "release.desc": "originallyAvailableAt%3Adesc",
    "critic_rating.asc": "rating", "critic_rating.desc": "rating%3Adesc",
    "user_rating.asc": "userRating",  "user_rating.desc": "userRating%3Adesc",
    "added.asc": "addedAt", "added.desc": "addedAt%3Adesc",
    "played.asc": "lastViewedAt", "played.desc": "lastViewedAt%3Adesc",
    "plays.asc": "viewCount", "plays.desc": "viewCount%3Adesc",
    "random": "random"
}
track_sorts = {
    "title.asc": "titleSort", "title.desc": "titleSort%3Adesc",
    "album_artist.asc": "artist.titleSort%2Calbum.titleSort%2Calbum.year%2Ctrack.absoluteIndex%2Ctrack.index%2Ctrack.titleSort%2Ctrack.id",
    "album_artist.desc": "artist.titleSort%3Adesc%2Calbum.titleSort%2Calbum.year%2Ctrack.absoluteIndex%2Ctrack.index%2Ctrack.titleSort%2Ctrack.id",
    "artist.asc": "originalTitle", "artist.desc": "originalTitle%3Adesc",
    "album.asc": "album.titleSort", "album.desc": "album.titleSort%3Adesc",
    "user_rating.asc": "userRating",  "user_rating.desc": "userRating%3Adesc",
    "duration.asc": "duration", "duration.desc": "duration%3Adesc",
    "plays.asc": "viewCount", "plays.desc": "viewCount%3Adesc",
    "added.asc": "addedAt", "added.desc": "addedAt%3Adesc",
    "played.asc": "lastViewedAt", "played.desc": "lastViewedAt%3Adesc",
    "rated.asc": "lastRatedAt", "rated.desc": "lastRatedAt%3Adesc",
    "popularity.asc": "ratingCount", "popularity.desc": "ratingCount%3Adesc",
    "bitrate.asc": "mediaBitrate", "bitrate.desc": "mediaBitrate%3Adesc",
    "random": "random"
}
sort_types = {
    "movie": ("title.asc", 1, movie_sorts),
    "show": ("title.asc", 2, show_sorts),
    "season": ("season.asc", 3, season_sorts),
    "episode": ("title.asc", 4, episode_sorts),
    "artist": ("title.asc", 8, artist_sorts),
    "album": ("title.asc", 9, album_sorts),
    "track": ("title.asc", 10, track_sorts)
}
watchlist_sorts = {
    "added.asc": "watchlistedAt:asc", "added.desc": "watchlistedAt:desc",
    "title.asc": "titleSort:asc", "title.desc": "titleSort:desc",
    "release.asc": "originallyAvailableAt:asc", "release.desc": "originallyAvailableAt:desc",
    "critic_rating.asc": "rating:asc", "critic_rating.desc": "rating:desc",
}

MAX_IMAGE_SIZE = 10480000  # a little less than 10MB

class Emby(Library):
    def __init__(self, config, params):
        super().__init__(config, params)

        self.filter_items_cache = {}
        self.emby = params["emby"]
        self.url = self.emby["url"]
        # New and unused
        self.clean_bundles = params["emby"].get("clean_bundles", False)
        self.empty_trash = params["emby"].get("empty_trash", False)
        self.optimize = params["emby"].get("optimize", False)
        # unused end
        self.session = self.config.Requests.session # init?
        if self.emby["verify_ssl"] is False and self.config.Requests.global_ssl is True:
            logger.debug("Overriding verify_ssl to False for Emby connection")
            self.session = self.config.Requests.create_session(verify_ssl=False)
        if self.emby["verify_ssl"] is True and self.config.Requests.global_ssl is False:
            logger.debug("Overriding verify_ssl to True for Emby connection")
            self.session = self.config.Requests.create_session()
        self.emby_api_key = self.emby["api_key"]
        self.emby_user_id = self.emby["user_id"]
        self.overlay_destination_folder = self.emby["overlay_destination_folder"]
        self.timeout = self.emby["timeout"]
        logger.secret(self.url)
        logger.secret(self.emby_api_key)
        logger.secret(self.emby_user_id)
        self.EmbyServer = None
        try:
            self.EmbyServer = EmbyServer(self.url, self.emby_user_id, self.emby_api_key, config, params["name"])
            # timeout not set - self.timeout
            logger.info(f"Connected to server {self.EmbyServer.friendlyName} version {self.EmbyServer.version}")
            logger.info(f"Running on {self.EmbyServer.platform} version {self.EmbyServer.platformVersion}")
            # srv_settings = self.EmbyServer.settings
            # try:
            #     db_cache = srv_settings.get("DatabaseCacheSize")
            #     logger.info(f"Plex DB cache setting: {db_cache.value} MB")
            #     if self.plex["db_cache"] and self.plex["db_cache"] != db_cache.value:
            #         db_cache.set(self.plex["db_cache"])
            #         self.PlexServer.settings.save()
            #         logger.info(f"Plex DB Cache updated to {self.plex['db_cache']} MB")
            # except NotFound:
            #     logger.info(f"Plex DB cache setting: Unknown")
            # try:
            #     chl_num = srv_settings.get("butlerUpdateChannel").value
            #     if chl_num == "16":
            #         uc_str = f"Public update channel."
            #     elif chl_num == "8":
            #         uc_str = f"PlexPass update channel."
            #     else:
            #         uc_str = f"Unknown update channel: {chl_num}."
            # except NotFound:
            #     uc_str = f"Unknown update channel."
            # TODO - subscription info
            # logger.info(f"PlexPass: {self.EmbyServer.myPlexSubscription} on {uc_str}")

            # try:
            #     logger.info(f"Scheduled maintenance running between {srv_settings.get('butlerStartHour').value}:00 and {srv_settings.get('butlerEndHour').value}:00")
            # except NotFound:
            #     logger.info("Scheduled maintenance times could not be found")
        except Unauthorized:
            logger.info(f"Emby Error: Emby connection attempt returned 'Unauthorized'")
            raise Failed("Emby Error: Emby API key is invalid")
        except ConnectTimeout:
            raise Failed(f"Emby Error: Emby did not respond within the {self.timeout}-second timeout.")
        except ValueError as e:
            logger.info(f"Emby Error: Emby connection attempt returned 'ValueError'")
            logger.stacktrace()
            raise Failed(f"Emby Error: {e}")
        except (ConnectionError, ParseError):
            logger.info(f"Emby Error: Emby connection attempt returned 'ConnectionError' or 'ParseError'")
            logger.stacktrace()
            raise Failed("Emby Error: Plex URL is probably invalid")

        self.Emby = None

        emby_library_names = []
        # print(params)
        self.lib_type = None
        for s in self.EmbyServer.get_libraries():
            # print(s)
            emby_library_names.append(s["Name"])
            if s["CollectionType"] == 'tvshows':
                self.lib_type = "show"
            elif s["CollectionType"] == 'movies':
                self.lib_type = "movie"
            if s["Name"] == params["name"]:
                self.Emby = s
                self.EmbyServer.library_id= self.Emby.get('Id')
                print(s)
                break
        # print(emby_library_names)
        if not self.Emby:
            raise Failed(f"Emby Error: Emby Library '{params['name']}' not found. Options: {emby_library_names}")
        # --------------

        self.type = self.Emby.get("CollectionType", "")
        # Entferne das 's', wenn self.type 'movies' oder 'shows' ist

        # Now, find out the library type
        collection_type = self.Emby.get("CollectionType", "").lower()
        if collection_type == "movies":
            self.emby_type = "Movie"
        elif collection_type == "tvshows":
            self.emby_type = "Show"
        elif collection_type == "music":
            self.emby_type = "Artist"
        else:
            self.emby_type = "Other"
        self.type= self.emby_type
        # print(f"Collection type is: '{collection_type}'")
        # coll = Collection()
        if self.emby_type.lower() not in library_types:
            raise Failed(f"Emby Error: Emby Library must be a Movies, TV Shows, or Music library")



        # print(f"EMBY Library type: {self.type}")
        # print(self.type)
        self._users = []
        self.emby_users = []
        self._all_items = []
        self._emby_all_items = []
        self._emby_all_items_native = []
        self._account = None

        # source_setting = next((s for s in self.Plex.settings() if s.id in ["ratingsSource"]), None)
        # Todo
        # print(f"Checkie: {source_setting}")
        # Checkie: <Setting:ratingsSource:rottentomatoes>
        # Checkie: <Setting:ratingsSource:imdb>
        # Checkie: <Setting:ratingsSource:themoviedb>
        self.ratings_source = "N/A" # lets' use RT
        # self.ratings_source = source_setting.enumValues[source_setting.value] if source_setting else "N/A"

        self.is_movie = self.emby_type == "Movie"
        self.is_show = self.emby_type == "Show"
        self.is_music = self.emby_type == "Artist"
        self.is_other = self.emby_type == "Other"

        # todo: needed for Emby?
        if self.is_other and self.type == "Movie":
            self.type = "Video"
        if not self.is_music and self.update_blank_track_titles:
            self.update_blank_track_titles = False
            logger.error(f"update_blank_track_titles library operation only works with music libraries")

        logger.info(f"Connected to library {params['name']}")
        logger.info(f"Type: {self.type}")
        logger.info(f"Ratings Source: {self.ratings_source}")

    # Backend-agnostic helpers
    def get_seasons(self, show):
        if hasattr(show, "seasons"):
            return list(show.seasons)
        return []

    def get_episodes(self, season):
        if hasattr(season, "episodes"):
            return list(season.episodes)
        return []

    def load_from_cache(self, rating_key):
        if rating_key in self.cached_items:
            item, _ = self.cached_items[rating_key]
            return item

    def load_list_from_cache(self, rating_keys):
        item_list = []
        for rating_key in rating_keys:
            item = self.load_from_cache(rating_key)
            if item:
                item_list.append(item)
        return item_list

    def get_ratings(self, item):
        return {}

    def apply_batch_operations(self, *, label_edits, genre_edits, rating_edits,
                               content_edits, studio_edits, date_edits, remove_edits,
                               reset_edits, lock_edits, unlock_edits, ep_rating_edits,
                               ep_remove_edits, ep_reset_edits, ep_lock_edits,
                               ep_unlock_edits, name_display):

        def log_batch(display_attr, total_count, display_value=None, out_type=None, tag_type=None, is_episode=False):
            logger.info(
                f"Batch {name_display.get(display_attr, display_attr.capitalize())} Update: "
                f"{f'{out_type.capitalize()} ' if out_type else ''}"
                f"{f'Adding {display_value} to ' if tag_type == 'add' else f'Removing {display_value} from ' if tag_type == 'remove' else ''}"
                f"{total_count} {'Episode' if is_episode else 'Movie' if self.is_movie else 'Show'}"
                f"{'s' if total_count != 1 else ''}"
                f"{'' if out_type or tag_type else f' updated to {display_value}'}"
            )

        for i, (new_rating, rating_keys) in enumerate(sorted(content_edits.items()), 1):
            log_batch("contentRating", len(rating_keys), display_value=new_rating)
            self.EmbyServer.multiEditField(self.load_list_from_cache(rating_keys), "contentRating", new_rating)

        for i, (new_studio, rating_keys) in enumerate(sorted(studio_edits.items()), 1):
            log_batch("studio", len(rating_keys), display_value=new_studio)
            self.EmbyServer.multiEditField(self.load_list_from_cache(rating_keys), "studio", new_studio)

        for i, (new_date, rating_keys) in enumerate(sorted(date_edits["originallyAvailableAt"].items()), 1):
            log_batch("originallyAvailableAt", len(rating_keys), display_value=new_date)
            self.EmbyServer.multiEditField(self.load_list_from_cache(rating_keys), "originallyAvailableAt", new_date)

        for i, (new_date, rating_keys) in enumerate(sorted(date_edits["addedAt"].items()), 1):
            log_batch("addedAt", len(rating_keys), display_value=new_date)
            self.EmbyServer.multiEditField(self.load_list_from_cache(rating_keys), "addedAt", new_date)

        if any([label_edits, genre_edits, rating_edits, remove_edits, reset_edits, lock_edits, unlock_edits, ep_rating_edits, ep_remove_edits, ep_reset_edits, ep_lock_edits, ep_unlock_edits]):
            logger.debug("Some batch operations are not yet supported for Emby backends.")

    def needs_collection_mode_update(self, collection, mode):
        return False

    def item_has_year(self, item):
        return hasattr(item, "year") and item.year is not None
# ToDo - Untested, develop; use this with db cache instead of set_image_smart
    def _upload_image(self, item, image):
        upload_success = True
        try:
            if image.is_url and "theposterdb.com" in image.location:
                now = datetime.now()
                if self.config.tpdb_timer is not None:
                    while self.config.tpdb_timer + timedelta(seconds=6) > now:
                        time.sleep(1)
                        now = datetime.now()
                self.config.tpdb_timer = now
            if image.is_poster and image.is_url:
                self.upload_poster(item, url=image.location)
            elif image.is_poster:
                upload_success = self.validate_image_size(image)
                if upload_success:
                    self.upload_poster(item, image.location)
            elif image.is_background and image.is_url:
                item.uploadArt(url=image.location)
            elif image.is_background:
                upload_success = self.validate_image_size(image)
                if upload_success:
                    item.uploadArt(filepath=image.location)
            elif image.is_url:
                item.uploadLogo(url=image.location)
            else:
                item.uploadLogo(filepath=image.location)
            self.reload(item, force=True)
            return upload_success
        except BadRequest as e:
            item.refresh()
            raise Failed(e)

    def edit_tags(self, attr, obj, add_tags=None, remove_tags=None, sync_tags=None, do_print=True, locked=True,
                  is_locked=None):

        display = ""
        final = ""
        attribute_translation[attr] if attr in attribute_translation else attr
        "similar" if attr == "similar_artist" else attr
        attr_display = attr.replace("_", " ").title()

        if add_tags or remove_tags or sync_tags is not None:
            _add_tags = add_tags if add_tags else []
            _remove_tags = remove_tags if remove_tags else []
            _sync_tags = sync_tags if sync_tags else []

            if attr == "label":
                _item_tags = self.EmbyServer.get_emby_item_tags(obj, self.Emby.get("Id"), from_cache=False)
            elif attr == "genre":
                _item_tags = self.EmbyServer.get_emby_item_genres(obj, self.Emby.get("Id"), from_cache=False)
            else:
                pass

            _add = [t for t in _add_tags + _sync_tags if t not in _item_tags]
            _remove = [t for t in _item_tags if (sync_tags is not None and t not in _sync_tags) or t in _remove_tags]

            # Berechne die finalen Tags
            final_tags = sorted(set([t for t in _item_tags if t not in _remove] + _add))
            final_tags = sorted(set(final_tags))  # Entferne eventuelle Duplikate
            if final_tags != sorted(set(_item_tags)):
                if attr == "label":
                    self.EmbyServer.set_tags(obj.ratingKey, final_tags)
                elif attr == "genre":
                    self.EmbyServer.set_genres(obj.ratingKey, final_tags)
                else:
                    raise WARNING(f"edit_tags: I won't edit {attr} with {final_tags}")

            if _add:
                display += f"+{', +'.join(_add)}"
            if _remove:
                if display:
                    display += ", "
                display += f"-{', -'.join(_remove)}"
            if is_locked is not None and not display and is_locked != locked:
                # self.edit_query(obj, {f"{actual}.locked": 1 if locked else 0})
                # todo: add emby locked?
                display = "Locked" if locked else "Unlocked"
            final = f"{obj.title[:25]:<25} | {attr_display} | {display}" if display else display
            if do_print and final:
                logger.info(final)
        return final[28:] if final else final

        # if add_tags and not remove_tags and not None:
        #     self.EmbyServer.add_tags(obj.ratingKey, add_tags)
        #     return
        raise WARNING(
            f"EMBY EDIT TAGS: {self} - {attr} - {obj} - {add_tags} - {remove_tags} - {sync_tags} - {locked} - {is_locked}")

        display = ""
        final = ""
        key = attribute_translation[attr] if attr in attribute_translation else attr
        actual = "similar" if attr == "similar_artist" else attr
        attr_display = attr.replace("_", " ").title()
        if add_tags or remove_tags or sync_tags is not None:
            _add_tags = add_tags if add_tags else []
            _remove_tags = remove_tags if remove_tags else []
            _sync_tags = sync_tags if sync_tags else []
            try:
                obj = self.reload(obj)
                _item_tags = [item_tag.tag for item_tag in getattr(obj, key)]
            except BadRequest:
                _item_tags = []
            _add = [t for t in _add_tags + _sync_tags if t not in _item_tags]
            _remove = [t for t in _item_tags if (sync_tags is not None and t not in _sync_tags) or t in _remove_tags]
            if _add:
                self.tag_edit(obj, actual, _add, locked=locked)
                display += f"+{', +'.join(_add)}"
            if _remove:
                self.tag_edit(obj, actual, _remove, locked=locked, remove=True)
                if display:
                    display += ", "
                display += f"-{', -'.join(_remove)}"
            if is_locked is not None and not display and is_locked != locked:
                self.edit_query(obj, {f"{actual}.locked": 1 if locked else 0})
                display = "Locked" if locked else "Unlocked"
            final = f"{obj.title[:25]:<25} | {attr_display} | {display}" if display else display
            if do_print and final:
                logger.info(final)
        return final[28:] if final else final

    def find_poster_url(self, item):
        pass
    def smart_label_check(self, label):

        # print(f"Smart Label: {label}")
        tags = self.EmbyServer.get_emby_item_tags(self, self.Emby.get("Id"), search_all=True,from_cache=False)
        #
        if label in tags:
            return True
        logger.trace(f"Label not found in Emby. Options: {tags}")
        return False

        labels = [la.title for la in self.get_tags("label")] # noqa
        labels += self.EmbyServer.get_emby_countries(self.Emby.get("Id"))
        if label in labels:
            return True
        logger.trace(f"Label not found in Plex. Options: {labels}")
        return False

    def parse_qs(data):
        return parse.parse_qs(data)

    def split(self, text):
        attribute, modifier = os.path.splitext(str(text).lower())
        attribute = method_alias[attribute] if attribute in method_alias else attribute
        modifier = modifier_alias[modifier] if modifier in modifier_alias else modifier

        if attribute == "add_to_arr":
            attribute = "radarr_add_missing" if self.is_movie else "sonarr_add_missing"
        elif attribute in ["arr_tag", "arr_folder"]:
            attribute = f"{'rad' if self.is_movie else 'son'}{attribute}"
        elif attribute in builder.date_attributes and modifier in [".gt", ".gte"]:
            modifier = ".after"
        elif attribute in builder.date_attributes and modifier in [".lt", ".lte"]:
            modifier = ".before"
        final = f"{attribute}{modifier}"
        if text != final:
            logger.warning(f"Collection Warning: {text} attribute will run as {final}")
        return attribute, modifier, final


    def fetchItems(self, uri_args):
        """
        Fetch items from Plex or Emby based on the provided URI arguments.
        Supports decade-based filtering for Emby and correctly handles episodes.
        """
        is_show= False
        additional_person_search = []
        # Parse the URI arguments
        plus_replace = str(uri_args).replace('+', '%2B')

        args = parse_qs(plus_replace.lstrip('?'))

        # Default-Datenstruktur für mehrere Instanzen
        from collections import defaultdict
        param_values = defaultdict(list)
        for key, values in args.items():
            for value in values:
                param_values[unquote(key)].append(unquote(value))

        # Initialize Emby API query parameters
        emby_query_params = {}
        unknown_params = {}
        emby_query_params["Recursive"] = "true"
        if "or" in args:
            pass

        # Initialize 'Years' list and item types
        years_list = []
        item_types = set()

        # Process 'type' parameter
        type_values = args.get('type', [])
        for type_value in type_values:
            if type_value == '1':
                item_types.add('Movie')
            elif type_value == '2':
                item_types.add('Series')
            elif type_value == '18':
                item_types.add('BoxSet')  # Assuming 'BoxSet' for collections
            else:
                raise Failed(f"Unknown type value: {type_value} {uri_args}")

        # Process each parameter
        for key, values in param_values.items():
            for value in values:
                key_decoded = unquote(key)
                value_decoded = unquote(value)

                # Detect 'episode.' or 'show.' fields for item types
                # if key_decoded.startswith('episode.'):
                #     item_types = {"Episode"}

                # Handle parameters with comparison operators in the key
                match = re.match(r'([\w\.]+)([<>]{1,2}=?)(.*)', key_decoded)
                if match:
                    field, operator, _ = match.groups()
                    field = field.strip()
                    operator = operator.strip()
                    operand = value_decoded.strip()

                    if field in ["rating","show.rating"]:
                        emby_query_params["Fields"]= "CommunityRating,CriticRating,ProviderIds"
                        if operator in ['>', '>=']:
                            emby_query_params['MinCriticRating'] = int(float(operand) * 10)
                        elif operator in ['<', '<=','<<']:
                            emby_query_params['MaxCriticRating'] = int(float(operand) * 10)
                        else:
                            raise Failed(f"Unknown operator {operator} for {field}")
                    elif field in ["audienceRating", "show.audienceRating"]:
                        emby_query_params["Fields"]= "CommunityRating,CriticRating,ProviderIds"
                        if operator in ['>', '>=']:
                            emby_query_params['MinCommunityRating'] = operand
                        elif operator in ['<', '<=','<<']:
                            emby_query_params['MaxCommunityRating'] = operand
                        else:
                            raise Failed(f"Unknown operator {operator} for {field}")
                    elif field in ["userRating", "show.userRating"]:
                        emby_query_params["Fields"]= "CommunityRating,CriticRating,ProviderIds"
                        if operator in ['>', '>=']:
                            emby_query_params['MinCustomRating'] = operand
                        elif operator in ['<', '<=','<<']:
                            emby_query_params['MaxCustomRating'] = operand
                        else:
                            raise Failed(f"Unknown operator {operator} for {field}")
                    elif field.endswith('originallyAvailableAt'):
                        if field.startswith("episode"): # look for episodes recently aired to get to the show
                            is_show = True
                            item_types.add("Episode")
                            # item_types = {"Series"}

                        date_value = self.parse_relative_date(operand)
                        if date_value:
                            if operator in ['>>', '>=', '>>=']:
                                emby_query_params['MinPremiereDate'] = date_value.isoformat()
                            elif operator in ['<<', '<=', '<<=']:
                                emby_query_params['MaxPremiereDate'] = date_value.isoformat()
                            else:
                                unknown_params['operator'] = operator
                        else:
                            print(f"Unable to parse date value: {operand}")
                    else:
                        unknown_params[key_decoded] = value_decoded
                else:
                    # Process regular parameters
                    if key_decoded in ['type', 'and']:
                        pass  # Already handled above
                    elif key_decoded in ['studio=', 'studio', 'show.studio', 'show.studio=']: # todo add newtwork here for later
                        # Handle multiple studios
                        # if 'Studios' not in emby_query_params:
                        #     emby_query_params['Studios'] = []
                        # is this working correctly?
                        if "Studios" in emby_query_params:
                            emby_query_params["Studios"].append(value_decoded)
                        else:
                            emby_query_params['Studios']= [value_decoded]
                    elif key_decoded in ['show.network']: # todo add newtwork here for later
                        # TODO: Use Emby Studio for Studios and Networks. Too much work with auto updates.
                        if "Studios" in emby_query_params:
                            # emby_query_params["Studios"].append(f"📡 {value_decoded}")
                            emby_query_params["Studios"].append(f"{value_decoded}")
                        else:
                            # emby_query_params['Studios']= [f"📡 {value_decoded}"]
                            emby_query_params['Studios']= [f"{value_decoded}"]
                    elif key_decoded == 'country':

                        if 'Ids' not in emby_query_params:
                            emby_query_params['Ids'] = []

                        for it, val in self.EmbyServer.production_search.items():
                            if value in val:
                                emby_query_params['Ids'].append(it)
                            elif f"{self.name} {value_decoded}" in val:
                                emby_query_params['Ids'].append(it)

                        # emby_query_params['Ids'].append(encode_tags_to_uri(emby_item_ids))


                        # e_items = []
                        # for id in emby_item_ids:
                        #     e_items.append(self.EmbyServer.get_item(id))
                        #
                        # # mn = self.EmbyServer.get_items({'Ids': emby})
                        # mn = self.EmbyServer.convert_emby_to_plex(e_items)
                        # # todo: add sort order etc.
                        # return mn

                    elif key_decoded == 'genre':
                        if "Genres" not in emby_query_params:
                            emby_query_params['Genres'] = [value_decoded]
                            emby_query_params["Recursive"]= "true"

                        else:
                            emby_query_params['Genres'].append(value_decoded)
                    elif key_decoded == 'limit':
                        emby_query_params['Limit'] = value_decoded
                    elif key_decoded == 'show.contentRating' or key_decoded == 'contentRating':
                        if "OfficialRatings" not in emby_query_params:
                            emby_query_params['OfficialRatings'] = [value_decoded]
                        else:
                            emby_query_params['OfficialRatings'].append(value_decoded)
                    elif key_decoded in ['label', 'show.label']:
                        # Handle multiple labels
                        icon = '📺' if self.type == 'Show' else '🎥'
                        name = self.name
                        composed_name = f'{icon} {name} '
                        if 'Tags' not in emby_query_params:
                            emby_query_params['Tags'] = []
                        emby_query_params['Tags'].append(f'{composed_name}{value_decoded}')
                        emby_query_params['Tags'].append(f'{value_decoded}')
                    elif key_decoded in ['actor', 'director', 'writer', 'producer', 'composer', 'show.actor']:
                        # Handle multiple persons
                        # item_types.add("Person")
                        if 'PersonIds' not in emby_query_params:
                            emby_query_params['PersonIds'] = []
                        if 'PersonTypes' not in emby_query_params:
                            emby_query_params['PersonTypes'] = []
                        if key_decoded.startswith('show.'):
                            key_decoded = key_decoded.split('.')[1]
                        emby_query_params['PersonIds'].append(value_decoded)
                        emby_query_params['PersonTypes'].append(key_decoded)
                        additional_person_search.append(value_decoded) # Emby item id
                    elif key_decoded == 'sort':
                        sort_parts = value_decoded.split(':')
                        sort_field, sort_order = (sort_parts[0], sort_parts[1]) if len(sort_parts) == 2 else (
                        value_decoded, 'asc')

                        if sort_field == 'audienceRating':
                            emby_query_params['SortBy'] = 'CommunityRating'
                        elif sort_field in ['title', 'titleSort']:
                            emby_query_params['SortBy'] = 'Name'
                        elif sort_field == 'originallyAvailableAt':
                            emby_query_params['SortBy'] = 'PremiereDate'
                        elif sort_field == 'rating':
                            emby_query_params['SortBy'] = 'CriticRating'
                        elif sort_field == 'random':
                            emby_query_params['SortBy'] = 'Random'
                        elif sort_field in ['addedAt', 'episode.addedAt']:
                            emby_query_params['SortBy'] = 'DateCreated'
                        else:
                            unknown_params['sort_field'] = sort_field

                        emby_query_params['SortOrder'] = 'Descending' if sort_order.lower() == 'desc' else 'Ascending'
                    elif key_decoded == 'decade':
                        decade = int(value_decoded)
                        years_list.extend(str(year) for year in range(decade, decade + 10))
                    elif key_decoded in ('year', 'show.year', 'episode.year'):
                        if value_decoded.isdigit():
                            years_list.append(value_decoded)
                    elif key_decoded in ['resolution']:
                        index_key = value_decoded
                        lower_index = index_key.lower()
                        if lower_index == "hd":
                            index_key = "720p"
                            lower_index = "720p"
                        elif lower_index not in ["4k"] and not lower_index.endswith("p"):
                            index_key = f"{index_key}p"
                            lower_index = index_key.lower()
                        normalized_key = lower_index
                        if normalized_key == "4k":
                            normalized_key = "4k"
                        media_by_resolutions = getattr(self.EmbyServer, "media_by_resolution", None)
                        if not media_by_resolutions or normalized_key not in media_by_resolutions:
                            get_resolutions = getattr(self.EmbyServer, "get_resolutions", None)
                            if callable(get_resolutions):
                                get_resolutions()
                                media_by_resolutions = getattr(self.EmbyServer, "media_by_resolution", None)
                        if not media_by_resolutions or normalized_key not in media_by_resolutions:
                            logger.warning(
                                "Emby BETA: resolution '%s' is not cached; skipping filter",
                                value_decoded,
                            )
                            continue
                        emby_query_params.setdefault("_Resolutions", set()).add(normalized_key)
                    elif key_decoded == 'hdr':
                        if value_decoded == "1":
                            emby_query_params['_RequireHdr'] = True

                    else:
                        if key_decoded not in ["pop", "push", "or"]:
                            unknown_params[key_decoded] = value_decoded

        # resolution:
        # {'resolution': '4k'}
        # {'resolution': '4k', 'hdr': '1'}
        # {'resolution': '1080'}
        # {'resolution': 'HD'}
        # {'resolution': '576'}
        # {'resolution': '480'}

        # retrieves all media
        # 📺 Serien CBS
        # 📺 Serien Max
        # if '📺 Serien Sky' in emby_query_params.get('Tags', []):
        #     pass

        # Combine multi-value parameters
        if 'Ids' in emby_query_params:
            emby_query_params['Ids'] = ','.join(emby_query_params['Ids'])
        if 'Studios' in emby_query_params:
            emby_query_params['Studios'] = ','.join(emby_query_params['Studios'])
        if 'Tags' in emby_query_params:
            emby_query_params['Tags'] = '|'.join(emby_query_params['Tags'])
        if 'PersonIds' in emby_query_params:
            emby_query_params['PersonIds'] = ','.join(emby_query_params['PersonIds'])
        if 'PersonTypes' in emby_query_params:
            emby_query_params['PersonTypes'] = ','.join(set(emby_query_params['PersonTypes']))
        if 'OfficialRatings' in emby_query_params:
            emby_query_params['OfficialRatings'] = '|'.join(set(emby_query_params['OfficialRatings']))


        # Set 'Years' parameter if years_list is not empty
        if years_list:
            emby_query_params['Years'] = ','.join(years_list)

        # Set IncludeItemTypes in query params
        if item_types:
            emby_query_params['IncludeItemTypes'] = ','.join(item_types)

        emby_query_params['ParentId'] = self.Emby.get("Id")

        needs_resolution_filter = bool(
            emby_query_params.get("_Resolutions") or emby_query_params.get("_RequireHdr")
        )
        if needs_resolution_filter:
            media_by_resolutions = getattr(self.EmbyServer, "media_by_resolution", None)
            if not media_by_resolutions:
                get_resolutions = getattr(self.EmbyServer, "get_resolutions", None)
                if callable(get_resolutions):
                    get_resolutions()

        if unknown_params:
            logger.error(f"Emby BETA: unknown parameters: {unknown_params}")
            # |     1 | Unknown parameter: {'duplicate': '1'} ?type=1&sort=titleSort&duplicate=1
            raise Failed(f"Unknown parameter: {unknown_params} {uri_args}")

        # Query Emby API to get items matching criteria
        # if re.search("Miramax",uri_args):
        #     pass


        items = None
        if self._can_use_emby_cache(emby_query_params):
            self.get_all_native(builder_level=self.type.lower())
            native_source = self._emby_all_items_native or []
            filtered_items = self._filter_emby_native_items(list(native_source), emby_query_params)
            if filtered_items is not None:
                items = filtered_items

        if items is None:
            api_query_params = {k: v for k, v in emby_query_params.items() if not k.startswith('_')}
            items = self.EmbyServer.get_items(api_query_params)
            if items is None:
                items = []
            filtered_items = self._filter_emby_native_items(list(items), emby_query_params)
            if filtered_items is not None:
                items = filtered_items

        all_shows = None
        if is_show:
            all_shows= []
            # only the show is requestes
            for item in items:
                my_id = item.get("SeriesId")
                my_series = self.EmbyServer.get_item(my_id)
                all_shows.append(my_series)

        if all_shows:
            my_output= self.EmbyServer.convert_emby_to_plex(all_shows)
        else:
            my_output= self.EmbyServer.convert_emby_to_plex(items)
        # Convert Emby items to Plex format
        # Used for Emby to retrieve the person and add to collection
        if additional_person_search:
            people = []
            for add_p in additional_person_search:
                if not add_p.isdigit():
                    continue
                person = self.EmbyServer.get_item(add_p)
                people.append(person)
            plex_person = self.EmbyServer.convert_emby_to_plex(people, False)
            if plex_person:
                my_output.extend(plex_person)
            else:
                logger.warning(f"Additional person search was requested, result unclear: {additional_person_search} => {plex_person}")
        return my_output


    def test_smart_filter(self, uri_args):
        logger.debug(f"Smart Collection Test: {uri_args}")
        test_items = self.fetchItems(uri_args)
        if len(test_items) < 1:
            raise Failed(f"Plex Error: No items for smart filter: {uri_args}")

    def get_collection(self, data, force_search=False, debug=True):
        if isinstance(data, Collection):
            return data
        elif isinstance(data, int) and not force_search:
            return self.fetchItem(data)
        else:
            # lib_id = self.Emby.get("Id")
            # my_cols = self.EmbyServer.get_boxsets_from_library(str(data), library_id=lib_id )
            # my_col = self.EmbyServer.get_boxsets_from_library(str(data))
            col_id= self.EmbyServer.get_collection_id(str(data))
            if col_id:
                emby_col = self.EmbyServer.get_item(col_id)
                return self.EmbyServer.convert_emby_to_plex([emby_col])[0]

            # Rest fails
            raise Failed(f"Emby Error: Collection {data} not found")
            if col_id:
                my_cols = self.EmbyServer.get_boxset_by_title(str(data))
            # print(my_cols)
            if len(my_cols) > 0:
                return  my_cols[0]

            if debug:
                logger.debug("")
                for d in my_cols:
                    logger.debug(f"Found: {d.title}")
                logger.debug(f"Looking for: {data}")

            # return empty list
            # return None
            raise Failed(f"Emby Error: Collection {data} not found")

    def fetchItem(self, data):
        item = self.EmbyServer.get_item(data)
        return self.EmbyServer.convert_emby_to_plex([item])[0]

    def get_all(self, builder_level=None, load=False, native = False):
        """
        Retrieves all items from the library, optionally filtering by builder_level.

        Parameters:
            builder_level (str): The level to build (e.g., 'movie', 'show', 'artist').
            load (bool): Whether to reload the items.

        Returns:
            list: A list of all items.
        """
        # print(builder_level)
        # if not native and load and builder_level in [None, "show", "artist", "movie"]:
        #     self._emby_all_items = []
        #     self._emby_all_items_native = []
        if not native and self._emby_all_items and builder_level in [None, "show", "artist", "movie"]:
            return self._emby_all_items
        if native and self._emby_all_items_native and builder_level in [None, "show", "artist", "movie"]:
            return self._emby_all_items_native

        # builder_type = builder_level.lower() if builder_level else self.Plex.TYPE

        builder_type = builder_level.lower() if builder_level else self.type.lower()
        if not builder_level:
            builder_level = self.type.lower()

        logger.info(f"Loading All {builder_level.capitalize()}s from Library: {self.Emby.get('Name')}")

        items = []
        start_index = 0
        limit = 250
        total_record_count = 1
        include_item_types = []
        # print(builder_type)
        # Bestimmung der Typen für die Abfrage
        # ToDo: Add more builder_types
        if builder_type == "movie":
            include_item_types = ["Movie"]
        elif builder_type == "show":
            include_item_types = ["Series"]
        elif builder_type == "season":
            include_item_types = ["Season"]
        elif builder_type == "artist":
            include_item_types = ["MusicArtist"]
        else:
            logger.warning(f"builder type not supported by 'emby_get_all' - {builder_type}")
            include_item_types = ["Movie", "Series", "MusicArtist"]
        items_data =[]
        while start_index < total_record_count:
            # Abfrage der Hauptdaten
            params = {
                "Recursive": "true",
                "IncludeItemTypes": ",".join(include_item_types),
                "StartIndex": start_index,
                "Limit": limit,
                "ParentId": self.Emby.get("Id"),
                "Fields": "Budget,Chapters,DateCreated,Genres,HomePageUrl,IndexOptions,MediaStreams,Overview,ParentId,Path,People,ProductionYear,PremiereDate,ProviderIds,PrimaryImageAspectRatio,Revenue,SortName,Studios,Taglines,CriticRating,CommunityRating,OfficialRating,Tags,TagItems",
            }

            endpoint = f"{self.url}/emby/Users/{self.emby_user_id}/Items"
            response = requests.get(endpoint, headers=self.EmbyServer.headers, params=params)
            # response = self.session.get(endpoint, headers=self.EmbyServer.headers, params=params)
            response.raise_for_status()
            data = response.json()


            # print(data)

            # Gesamtdatensätze und Fortschritt verfolgen
            items_data += data.get("Items", [])
            total_record_count = data.get("TotalRecordCount", 0)
            start_index += limit
            logger.ghost(
                f"Loaded: {start_index if start_index < total_record_count else total_record_count}/{total_record_count}")

        self.EmbyServer.cache_filenames(items_data)

        logger.info(f"Loaded {len(items_data)} {builder_level.capitalize()}s from Emby")
        self._emby_all_items_native = items_data
        if native:
            # for item in items_data:
            #     for people in item.get("People", []):
            #         params = {
            #             "Recursive": "true",
            #             "IncludeItemTypes": "People",
            #             "Ids": people.get('Id'),
            #             "Fields": "ProviderIds",
            #         }
            #         response = self.session.get(endpoint, headers=self.emby_headers, params=params)
            #         prov_ids = response.json().get('Items', [])[0].get('ProviderIds')
            #         if prov_ids:
            #             tmdb_id = prov_ids.get('Tmdb', None)
            #             if tmdb_id:
            #                 people['tmdb_id'] = tmdb_id
            return items_data
        plex_items= self.EmbyServer.convert_emby_to_plex(items_data)
        # if builder_level in [None, "show", "artist", "movie"]:
        self._emby_all_items = plex_items
        return plex_items

    def get_all_collections(self, label=None):

        lib_id = self.Emby.get("Id")
        return self.EmbyServer.get_boxsets_from_library(library_id = lib_id, label=label, native=True)


    def get_all_native(self, builder_level=None, load=False):
        # todo remove
        pass
    def get_native_emby_item(self, emby_item_id):
        # todo remove
        pass

    def get_provider_ids(self, item):
        return self.EmbyServer.get_provider_ids(item)

    def image_update(self, item, image, tmdb=None, title=None, poster=True):
        pass
    def item_labels(self, item):
        try:
            # Prüfe, ob das Plex/Emby-Objekt ein `ratingKey` hat
            rating_key = getattr(item, "ratingKey", None)
            if not rating_key:
                raise Failed(f"Item: {getattr(item, 'title', 'Unknown')} does not have a valid ratingKey.")

            # Hole die Labels/Tags vom Emby-Server
            tags = self.EmbyServer.get_emby_item_tags(item, self.Emby.get("Id"))

            # Wrappe jeden Tag in ein Objekt mit Attribut .tag
            class Label:
                def __init__(self, tag):
                    self.tag = tag

            return [Label(t) for t in tags]

        except BadRequest:
            raise Failed(f"Item: {item.title} Labels failed to load")

    def item_posters(self, item, providers=None):
        pass
    def notify(self, text, collection=None, critical=True):
        pass
    def notify_delete(self, message):
        pass
    def reload(self, item, force=False):
        return item
        pass
    def upload_poster(self, item, image, url=False):
        pass
    def upload_poster_overlay(self, item, image, url=False):
        pass
    def upload_background(self, item, image, url=False):
        pass