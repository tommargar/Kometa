import os
import random
import re
import time
from datetime import datetime, timedelta, timezone
from urllib import parse
# from importlib.metadata import pass_none
from xml.etree.ElementTree import ParseError

import requests
from PIL import Image
from plexapi.audio import Artist, Album, Track
from plexapi.exceptions import BadRequest, Unauthorized
from plexapi.playlist import Playlist
from requests.exceptions import ConnectionError, ConnectTimeout

from modules import util, builder
from modules.emby_server import EmbyServer, Collection, FilterChoiceEmby, Movie, Show, Season, Episode
from modules.library import Library
from modules.logs import WARNING
from modules.poster import ImageData
from modules.util import Failed
from urllib.parse import unquote, parse_qsl, parse_qs, urlparse

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
    "filter": "filters",
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

        self.agent = None
        self.filter_items_cache = {}
        self.emby = params["emby"]
        self.url = self.emby["url"]
        # New and unused
        self.clean_bundles = params["emby"].get("clean_bundles", False)
        self.empty_trash = params["emby"].get("empty_trash", False)
        self.optimize = params["emby"].get("optimize", False)
        self._search_choices_cache = {}
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
                self.agent = "tv.plex.agents.series"
            elif s["CollectionType"] == 'movies':
                self.lib_type = "movie"
                self.agent = "tv.plex.agents.movie"
            if s["Name"] == params["name"]:
                self.Emby = s
                self.EmbyServer.library_id= self.Emby.get('Id')
                # print(s)
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

    def update_smart_collection(self, collection, uri_args):
        self.test_smart_filter(uri_args)

        new_items = self.fetchItems(uri_args)
        current_items = collection.items()
        add_items, remove_items, keep_items = self.calculate_add_remove_items(new_items, current_items)


        # return

        logger.info("")
        logger.separator(f"Syncing SmartEmby Collection {collection.title} {self.type}", space=False, border=False)
        logger.info("")


        total = len(add_items) + len(remove_items)
        spacing = len(str(total)) * 2 + 1

        # Prüfe auf hinzugefügte oder unveränderte Items
        for i, item in enumerate(add_items, 1):
            current_operation = "+"
            number_text = f"{i}/{total}"
            logger.info(
                f"{number_text:>{spacing}} | {collection.title} {self.type} | {current_operation} | {util.item_title(item)}")

        # Prüfe auf hinzugefügte oder unveränderte Items
        for i, item in enumerate(remove_items, 1):
            current_operation = "-"
            number_text = f"{i + len(add_items)}/{total}"
            logger.info(
                f"{number_text:>{spacing}} | {collection.title} {self.type} | {current_operation} | {util.item_title(item)}")

        if len(remove_items) >0:
            self.EmbyServer.add_remove_plex_object_from_collection(collection.title, remove_items, 'delete', collection_id = collection.ratingKey)
            # print(f"Removed {len(remove_items)} from Emby {collection.title} ")
        if len(add_items) >0:
            self.EmbyServer.add_remove_plex_object_from_collection(collection.title, add_items, 'add', collection_id = collection.ratingKey)
            # print(f"Added {len(add_items)} to Emby {collection.title} ")

        logger.exorcise()
        logger.info("")
        item_label = f"{self.type.capitalize()}{'s' if total > 1 else ''}"
        logger.info(f"{total} {item_label} Processed - Added {len(add_items)} {item_label} labels, Removed {len(remove_items)} {item_label} labels")

        # self._query(f"/library/collections/{collection.ratingKey}/items{utils.joinArgs({'uri': self.build_smart_filter(uri_args)})}", put=True)


    # Backend-agnostic helpers
    def get_seasons(self, show):
        if hasattr(show, "seasons"):
            return list(show.seasons)
        return []

    def delete(self, obj):
        if isinstance(obj, Collection):
            # print(f"EMBY DELETE: {obj}")
            self.EmbyServer.delete_collection(obj)
            return
        elif isinstance(obj, list) and len(obj) == 0:
            return
        else:
            logger.info(f"Failed to delete object {obj}")
            logger.stacktrace()
            return
            raise Failed(f"Plex Error: Failed to delete {obj.title}")

        # return
        try:
            return self.query(obj.delete)
        except Exception:
            logger.stacktrace()
            raise Failed(f"Plex Error: Failed to delete {obj.title}")

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

        def get_ids(rating_keys, *, is_episode=False):
            ids = []
            for rating_key in rating_keys:
                if hasattr(rating_key, "ratingKey"):
                    ids.append(rating_key.ratingKey)
                else:
                    ids.append(rating_key)
            if is_episode and any(not isinstance(rk, (int, str)) and not hasattr(rk, "episodeNumber") for rk in rating_keys):
                raise Failed("Emby Error: Episode batch edits require episode rating keys or episode objects")
            return ids

        def get_tag_values(emby_item, keys):
            values = set()
            for key in keys:
                for entry in emby_item.get(key) or []:
                    if isinstance(entry, dict):
                        name = entry.get("Name")
                        if name:
                            values.add(name)
                    elif entry:
                        values.add(entry)
            return values

        item_cache = {}
        item_updates = {}

        def get_emby_item(item_id):
            if item_id not in item_cache:
                emby_item = self.EmbyServer.get_item(item_id)
                if emby_item is None:
                    raise Failed(f"Emby Error: Unable to load item {item_id} for batch edit")
                item_cache[item_id] = emby_item
            return item_cache[item_id]

        def get_update_entry(item_id):
            if item_id not in item_updates:
                emby_item = get_emby_item(item_id)
                item_updates[item_id] = {
                    "update": {},
                    "locked_fields": list(emby_item.get("LockedFields") or []),
                    "provider_ids": dict(emby_item.get("ProviderIds") or {}),
                    "labels": {
                        "current": get_tag_values(emby_item, ["TagItems", "Tags"]),
                        "desired": None,
                    },
                    "genres": {
                        "current": get_tag_values(emby_item, ["GenreItems", "Genres"]),
                        "desired": None,
                    },
                }
            return item_updates[item_id]

        def update_field_state(field_map, edits, action, *, is_episode=False):
            for item_attr, rating_keys in sorted(edits.items()):
                if item_attr not in field_map:
                    raise Failed(f"Emby Error: Unsupported {action} batch edit for '{item_attr}'")
                item_field, clear_value = field_map[item_attr]
                items = self.load_list_from_cache(get_ids(rating_keys, is_episode=is_episode)) if not is_episode else rating_keys
                log_batch(item_attr, len(items), out_type=action, is_episode=is_episode)
                for item in items:
                    item_id = item.ratingKey if hasattr(item, "ratingKey") else item
                    entry = get_update_entry(item_id)
                    locked_fields = list(entry["locked_fields"])
                    if action in ["remove", "lock"] and item_field not in locked_fields:
                        locked_fields.append(item_field)
                    if action in ["reset", "unlock"]:
                        locked_fields = [f for f in locked_fields if f != item_field]
                    entry["locked_fields"] = locked_fields
                    if action in ["remove", "reset"]:
                        if item_field == "ProviderIds":
                            filtered_ids = {
                                k: v
                                for k, v in entry.get("provider_ids", {}).items()
                                if k.lower() != self.EmbyServer.CUSTOM_RATING_PROVIDER.lower()
                            }
                            entry["update"][item_field] = filtered_ids
                        else:
                            entry["update"][item_field] = clear_value

        field_map = {
            "audienceRating": ("CommunityRating", None),
            "rating": ("CriticRating", None),
            "userRating": ("ProviderIds", {}),
            "contentRating": ("OfficialRating", None),
            "studio": ("Studios", []),
            "originalTitle": ("OriginalTitle", None),
            "summary": ("Overview", None),
            "tagline": ("Taglines", []),
            "title": ("Name", None),
            "sortTitle": ("SortName", None),
            "originallyAvailableAt": ("PremiereDate", None),
            "addedAt": ("DateCreated", None),
        }

        def collect_tag_edits(edit_dict, tag_attribute):
            for tag_operation, batch_edits in edit_dict.items():
                for tag_value, rating_keys in sorted(batch_edits.items()):
                    items = self.load_list_from_cache(rating_keys)
                    log_batch(tag_attribute, len(items), display_value=tag_value, tag_type=tag_operation)
                    for item in items:
                        item_id = item.ratingKey if hasattr(item, "ratingKey") else item
                        entry = get_update_entry(item_id)
                        # Track the desired end state so the final payload can set all tags at once
                        if tag_attribute == "label":
                            tag_entry = entry["labels"]
                        elif tag_attribute == "genre":
                            tag_entry = entry["genres"]
                        else:
                            raise Failed(f"Emby Error: Unsupported tag attribute '{tag_attribute}'")

                        desired_tags = tag_entry.get("desired")
                        if desired_tags is None:
                            desired_tags = set(tag_entry["current"])
                            tag_entry["desired"] = desired_tags
                        if tag_operation == "add":
                            desired_tags.add(tag_value)
                        else:
                            desired_tags.discard(tag_value)
                        # Reassign to make the mutation explicit for later payload building
                        tag_entry["desired"] = desired_tags
        for tag_attribute, edit_dict in [("label", label_edits), ("genre", genre_edits)]:
            collect_tag_edits(edit_dict, tag_attribute)

        for item_attr, rt_edits in rating_edits.items():
            for new_rating, rating_keys in sorted(rt_edits.items()):
                rating_ids = get_ids(rating_keys)
                log_batch(item_attr, len(rating_ids), display_value=new_rating)
                for item_id in rating_ids:
                    entry = get_update_entry(item_id)
                    if item_attr == "audienceRating":
                        entry["update"]["CommunityRating"] = new_rating
                    elif item_attr == "rating":
                        entry["update"]["CriticRating"] = new_rating # if new_rating else None
                    elif item_attr == "userRating":
                        provider_ids = entry["update"].setdefault("ProviderIds", {})
                        provider_ids[self.EmbyServer.CUSTOM_RATING_PROVIDER] = new_rating

        for i, (new_rating, rating_keys) in enumerate(sorted(content_edits.items()), 1):
            log_batch("contentRating", len(rating_keys), display_value=new_rating)
            for item in self.load_list_from_cache(rating_keys):
                entry = get_update_entry(item.ratingKey if hasattr(item, "ratingKey") else item)
                entry["update"]["OfficialRating"] = new_rating

        for i, (new_studio, rating_keys) in enumerate(sorted(studio_edits.items()), 1):
            log_batch("studio", len(rating_keys), display_value=new_studio)
            for item in self.load_list_from_cache(rating_keys):
                item_id = item.ratingKey if hasattr(item, "ratingKey") else item
                entry = get_update_entry(item_id)
                entry["update"]["Studios"] = {"Name": new_studio}

        for i, (new_date, rating_keys) in enumerate(sorted(date_edits["originallyAvailableAt"].items()), 1):
            log_batch("originallyAvailableAt", len(rating_keys), display_value=new_date)
            for item in self.load_list_from_cache(rating_keys):
                entry = get_update_entry(item.ratingKey if hasattr(item, "ratingKey") else item)
                entry["update"]["PremiereDate"] = new_date

        for i, (new_date, rating_keys) in enumerate(sorted(date_edits["addedAt"].items()), 1):
            log_batch("addedAt", len(rating_keys), display_value=new_date)
            for item in self.load_list_from_cache(rating_keys):
                entry = get_update_entry(item.ratingKey if hasattr(item, "ratingKey") else item)
                entry["update"]["DateCreated"] = new_date

        update_field_state(field_map, remove_edits, "remove")
        update_field_state(field_map, reset_edits, "reset")
        update_field_state(field_map, lock_edits, "lock")
        update_field_state(field_map, unlock_edits, "unlock")

        for item_attr, ep_edits in ep_rating_edits.items():
            for new_rating, rating_keys in sorted(ep_edits.items()):
                episode_ids = get_ids(rating_keys, is_episode=True)
                log_batch(item_attr, len(episode_ids), display_value=new_rating, is_episode=True)
                for item_id in episode_ids:
                    entry = get_update_entry(item_id)
                    if item_attr == "audienceRating":
                        entry["update"]["CommunityRating"] = new_rating
                    elif item_attr == "rating":
                        entry["update"]["CriticRating"] = new_rating #* 10 if new_rating else None
                    elif item_attr == "userRating":
                        provider_ids = entry["update"].setdefault("ProviderIds", {})
                        provider_ids[self.EmbyServer.CUSTOM_RATING_PROVIDER] = new_rating

        update_field_state(field_map, ep_remove_edits, "remove", is_episode=True)
        update_field_state(field_map, ep_reset_edits, "reset", is_episode=True)
        update_field_state(field_map, ep_lock_edits, "lock", is_episode=True)
        update_field_state(field_map, ep_unlock_edits, "unlock", is_episode=True)

        for item_id, data in item_updates.items():
            update_payload = dict(data["update"])

            if data["locked_fields"] is not None:
                update_payload["LockedFields"] = data["locked_fields"]

            if data["labels"]["desired"] is not None:
                desired_labels = sorted(data["labels"]["desired"])
                if set(desired_labels) != data["labels"]["current"]:
                    update_payload.update({
                        "Tags": desired_labels,
                        "TagItems": desired_labels,
                    })

            if data["genres"]["desired"] is not None:
                desired_genres = sorted(data["genres"]["desired"])
                if set(desired_genres) != data["genres"]["current"]:
                    update_payload.update({
                        "Genres": desired_genres,
                        "GenreItems": desired_genres,
                    })

            provider_ids_payload = None
            if "ProviderIds" in update_payload:
                provider_ids_payload = dict(update_payload["ProviderIds"])
            elif data.get("provider_ids") is not None:
                provider_ids_payload = dict(data["provider_ids"])

            if provider_ids_payload is not None:
                if self.EmbyServer.CUSTOM_RATING_PROVIDER in provider_ids_payload:
                    normalized_rating = self.EmbyServer.normalize_custom_rating_input(
                        provider_ids_payload[self.EmbyServer.CUSTOM_RATING_PROVIDER]
                    )
                    if normalized_rating is None:
                        provider_ids_payload.pop(self.EmbyServer.CUSTOM_RATING_PROVIDER, None)
                    else:
                        provider_ids_payload[self.EmbyServer.CUSTOM_RATING_PROVIDER] = normalized_rating
                update_payload["ProviderIds"] = provider_ids_payload
            if update_payload:
                logger.info(f"Updating {item_id}: {update_payload}")
                self.EmbyServer.update_item(item_id, update_payload)

    def needs_collection_mode_update(self, collection, mode):
        return False

    def get_item_display_title(self, item_to_sort, sort=False):

        if isinstance(item_to_sort, Album):
            return f"{item_to_sort.artist().titleSort if sort else item_to_sort.parentTitle} Album {item_to_sort.titleSort if sort else item_to_sort.title}"
        elif isinstance(item_to_sort, Season):
            titleSort = None
            if sort:
                season = self.EmbyServer.get_item(item_to_sort.ratingKey)
                show = self.EmbyServer.get_item(season.get("SeriesId"))
                titleSort = show.get("SortName")
            return f"{titleSort if sort else item_to_sort.parentTitle} Season {item_to_sort.seasonNumber}"
        elif isinstance(item_to_sort, Episode):
            titleSort = None
            if sort:
                season = self.EmbyServer.get_item(item_to_sort.ratingKey)
                show = self.EmbyServer.get_item(season.get("SeriesId"))
                titleSort = show.get("SortName")
            # ToDo - Not working / tested
            return f"{item_to_sort.show().titleSort if sort else item_to_sort.grandparentTitle} {item_to_sort.seasonEpisode.upper()}"
        else:
            key = item_to_sort.ratingKey if not (isinstance(item_to_sort, int) or isinstance(item_to_sort, str)) else str(item_to_sort)
            # must bei str Id
            item = self.EmbyServer.get_item(key)
            return item.get("SortName") if sort else item.get("Name")

            return item_to_sort.titleSort if sort else item_to_sort.title


    def get_watchlist(self, sort=None, is_playlist=False):
        #ToDo: Will bug out, copy paste from Plex
        if is_playlist:
            libtype = None
        elif self.is_movie:
            libtype = "movie"
        else:
            libtype = "show"
        watchlist = self.account.watchlist(sort=watchlist_sorts[sort], libtype=libtype)
        ids = []
        for item in watchlist:
            tmdb_id = []
            tvdb_id = []
            imdb_id = []
            if self.config.Cache:
                cache_id, _, media_type, _ = self.config.Cache.query_guid_map(item.guid)
                if cache_id:
                    ids.extend([(t_id, "tmdb" if "movie" in media_type else "tvdb") for t_id in cache_id])
                    continue
            try:
                fin = False
                for guid_tag in item.guids:
                    url_parsed = urlparse(guid_tag.id)
                    if url_parsed.scheme == "tvdb":
                        if isinstance(item, Show):
                            ids.append((int(url_parsed.netloc), "tvdb"))
                            fin = True
                    elif url_parsed.scheme == "imdb":
                        imdb_id.append(url_parsed.netloc)
                    elif url_parsed.scheme == "tmdb":
                        if isinstance(item, Movie):
                            ids.append((int(url_parsed.netloc), "tmdb"))
                            fin = True
                        tmdb_id.append(int(url_parsed.netloc))
                    if fin:
                        break
                if fin:
                    continue
            except ConnectionError:
                continue
            if imdb_id and not tmdb_id:
                for imdb in imdb_id:
                    tmdb, tmdb_type = self.config.Convert.imdb_to_tmdb(imdb)
                    if tmdb:
                        tmdb_id.append(tmdb)
            if tmdb_id and isinstance(item, Show) and not tvdb_id:
                for tmdb in tmdb_id:
                    tvdb = self.config.Convert.tmdb_to_tvdb(tmdb)
                    if tvdb:
                        tvdb_id.append(tvdb)
            if isinstance(item, Show) and tvdb_id:
                ids.extend([(t_id, "tvdb") for t_id in tvdb_id])
            if isinstance(item, Movie) and tmdb_id:
                ids.extend([(t_id, "tmdb") for t_id in tmdb_id])
        return ids

    def parse_relative_date(self, relative_date_str):
        """
        Parses a relative date string like '-90d' and returns a datetime object.
        """
        match = re.match(r'(-?\d+)([dwmy])', relative_date_str)
        if not match:
            return None

        value, unit = match.groups()
        value = int(value)
        now = datetime.now()

        if unit == 'd':
            return now + timedelta(days=value)
        elif unit == 'w':
            return now + timedelta(weeks=value)
        elif unit == 'm':
            # Approximate a month as 30 days
            return now + timedelta(days=value * 30)
        elif unit == 'y':
            # Approximate a year as 365 days
            return now + timedelta(days=value * 365)
        else:
            return None

    def get_rating_keys(self, method, data, is_playlist=False):
        items = []
        if method == "plex_all":
            logger.info(f"Processing Plex All {data.capitalize()}s")
            items = self.get_all(builder_level=data)
        elif method == "plex_watchlist":
            logger.info(f"Processing Plex Watchlist")
            return self.get_watchlist(sort=data, is_playlist=is_playlist)
        elif method == "plex_pilots":
            logger.info(f"Processing Plex Pilot {data.capitalize()}s")
            items = []
            for item in self.get_all():
                try:
                    items.append(item.episode(season=1, episode=1))
                except Failed:
                    logger.warning(f"Plex Warning: {item.title} has no Season 1 Episode 1 ")
        elif method == "plex_search":
            logger.info(f"Processing {data[1]}")
            logger.trace(data[2])
            items = self.fetchItems(data[2])
        elif method == "plex_collectionless":
            good_collections = []
            logger.info(f"Processing Plex Collectionless")
            logger.info("")
            for col in self.get_all_collections():
                keep_collection = True
                for pre in data["exclude_prefix"]:
                    if col.title.startswith(pre) or (col.titleSort and col.titleSort.startswith(pre)):
                        keep_collection = False
                        logger.info(f"Excluded by Prefix Match: {col.title}")
                        break
                if keep_collection:
                    for ext in data["exclude"]:
                        if col.title == ext or (col.titleSort and col.titleSort == ext):
                            keep_collection = False
                            logger.info(f"Excluded by Exact Match: {col.title}")
                            break
                if keep_collection:
                    good_collections.append(col)
            logger.info("")
            logger.info("Collections Not Excluded (Items in these collections are not added to Collectionless)")
            for col in good_collections:
                logger.info(col.title)
            logger.info("")
            collection_indexes = [str(c.title).lower() for c in good_collections]
            all_items = self.get_all()
            for i, item in enumerate(all_items, 1):
                logger.ghost(f"Processing: {i}/{len(all_items)} {item.title}")
                add_item = True
                # item = self.reload(item)
                for collection in item.collections:
                    if str(collection.tag).lower() in collection_indexes:
                        add_item = False
                        break
                if add_item:
                    items.append(item)
            logger.info(f"Processed {len(all_items)} {self.type}s")
        else:
            raise Failed(f"Plex Error: Method {method} not supported")
        if not items:
            # raise Failed("Plex Error: No Items found in Plex")
            return[]
        return [(item.ratingKey, "ratingKey") for item in items]


    def item_has_year(self, item):
        return hasattr(item, "year") and item.year is not None
# ToDo - Untested, develop; use this with db cache instead of set_image_smart
    def _upload_image(self, item, image):
        upload_success = False
        try:
            if image.is_url and "theposterdb.com" in image.location:
                now = datetime.now()
                if self.config.tpdb_timer is not None:
                    while self.config.tpdb_timer + timedelta(seconds=6) > now:
                        time.sleep(1)
                        now = datetime.now()
                self.config.tpdb_timer = now
            if image.is_poster and image.is_url:
                upload_success = self.upload_poster(item, image, url=image.location)
            elif image.is_poster:
                upload_success = self.validate_image_size(image)
                if upload_success:
                    upload_success = self.upload_poster(item, image)
            elif image.is_background and image.is_url:
                upload_success = self.upload_background(item, image, url=image.location)
            elif image.is_background:
                upload_success = self.validate_image_size(image)
                if upload_success:
                    upload_success = self.upload_background(item, image)
            elif image.is_url:
                upload_success = self.upload_logo(item, image, url=image.location)
            else:
                upload_success = self.upload_logo(item, image)

            if upload_success:
                self.reload(item, force=True)
            return upload_success
        except BadRequest as e:
            item.refresh()
            raise Failed(e)

    def upload_images(self, item, poster=None, background=None, logo=None, overlay=False):
        poster_uploaded = False
        if poster is not None:
            try:
                emby_item = self.EmbyServer.get_item(item.ratingKey)
                emby_images = emby_item.get("ImageTags")
                emby_poster_compare = emby_images.get("Primary") if emby_images else None
                poster_key = f"{poster.compare}-{emby_poster_compare}" if emby_poster_compare else poster.compare
                image_compare = None
                if self.config.Cache:
                    _, image_compare, _ = self.config.Cache.query_image_map(item.ratingKey, self.image_table_name)
                if not image_compare or not emby_poster_compare or str(poster_key) != str(image_compare):
                    if overlay:
                        # self.reload(item, force=True)
                        if overlay and "Overlay" in [la.tag for la in self.item_labels(item)]:
                            item.removeLabel("Overlay")
                    poster_uploaded = self._upload_image(item, poster)
                    logger.info(f"Metadata: {poster.attribute} updated {poster.message}")
                elif self.show_asset_not_needed:
                    logger.info(f"Metadata: {poster.prefix}poster update not needed")
            except Failed:
                logger.stacktrace()
                logger.error(f"Metadata: {poster.attribute} failed to update {poster.message}")

        background_uploaded = False
        if background is not None:
            try:
                image_compare = None
                if self.config.Cache:
                    _, image_compare, _ = self.config.Cache.query_image_map(item.ratingKey, f"{self.image_table_name}_backgrounds")
                if not image_compare or str(background.compare) != str(image_compare):
                    background_uploaded = self._upload_image(item, background)
                    logger.info(f"Metadata: {background.attribute} updated {background.message}")
                elif self.show_asset_not_needed:
                    logger.info(f"Metadata: {background.prefix}background update not needed")
            except Failed:
                logger.stacktrace()
                logger.error(f"Metadata: {background.attribute} failed to update {background.message}")

        logo_uploaded = False
        if logo is not None:
            try:
                image_compare = None
                if self.config.Cache:
                    _, image_compare, _ = self.config.Cache.query_image_map(item.ratingKey, f"{self.image_table_name}_logos")
                if not image_compare or str(logo.compare) != str(image_compare):
                    logo_uploaded = self._upload_image(item, logo)
                    logger.info(f"Metadata: {logo.attribute} updated {logo.message}")
                elif self.show_asset_not_needed:
                    logger.info(f"Metadata: {logo.prefix}logo update not needed")
            except Failed:
                logger.stacktrace()
                logger.error(f"Metadata: {logo.attribute} failed to update {logo.message}")

        if self.config.Cache:

            if poster_uploaded:
                emby_item = self.EmbyServer.get_item(item.ratingKey, force_refresh=True)
                emby_images = emby_item.get("ImageTags")
                emby_poster_compare = emby_images.get("Primary") if emby_images else None
                poster_key = f"{poster.compare}-{emby_poster_compare}" if emby_poster_compare else poster.compare

                self.config.Cache.update_image_map(item.ratingKey, self.image_table_name, "", poster_key if poster else "")
            if background_uploaded:
                self.config.Cache.update_image_map(item.ratingKey, f"{self.image_table_name}_backgrounds", "", background.compare)
            if logo_uploaded:
                self.config.Cache.update_image_map(item.ratingKey, f"{self.image_table_name}_logos", "", logo.compare)

        return poster_uploaded, background_uploaded, logo_uploaded


    def _invalidate_metadata_caches(self, rating_key=None):
        """Clear search/filter caches for a specific item after metadata mutations."""
        if hasattr(self, "_search_choices_cache") and isinstance(self._search_choices_cache, dict):
            self._search_choices_cache.clear()
        if rating_key is not None and hasattr(self, "filter_items_cache") and isinstance(self.filter_items_cache, dict):
            self.filter_items_cache.pop(rating_key, None)
    def get_search_choices(self, search_name, title=True, name_pairs=False, person_list = None, tmdb_person_id = None):
        final_search = search_translation[search_name] if search_name in search_translation else search_name
        final_search = show_translation[final_search] if self.is_show and final_search in show_translation else final_search
        final_search = get_tags_translation[final_search] if final_search in get_tags_translation else final_search
        normalized_person_list = None
        if person_list:
            normalized_person_list = tuple(sorted(str(person).strip().lower() for person in person_list if person is not None))
        cache_key = (
            final_search,
            bool(title),
            bool(name_pairs),
            normalized_person_list,
            str(tmdb_person_id) if tmdb_person_id is not None else None,
        )
        if cache_key in self._search_choices_cache:
            cached_choices, cached_names = self._search_choices_cache[cache_key]
            return dict(cached_choices), list(cached_names)
        try:
            names = []
            seen_names = set()
            choices = {}
            use_title = title and final_search not in ["contentRating", "audioLanguage", "subtitleLanguage", "resolution"]
            tags_iter = self.get_tags(final_search, person_list = person_list, tmdb_person_id = tmdb_person_id)
            for choice in tags_iter:

                if choice.title not in seen_names:
                    seen_names.add(choice.title)
                    names.append((choice.title, choice.key) if name_pairs else choice.title)
                choices[choice.title] = choice.title if use_title else choice.key
                choices[choice.key] = choice.title if use_title else choice.key
                choices[choice.title.lower()] = choice.title if use_title else choice.key
                choices[choice.key.lower()] = choice.title if use_title else choice.key
            self._search_choices_cache[cache_key] = (dict(choices), list(names))
            return choices, names
        except Warning:
            logger.debug(f"Search Attribute: {final_search}")
            raise Failed(f"Emby Error: plex_search attribute: {search_name} not supported")

    def create_blank_collection(self, title):
        # Create a blank collection for Emby, add at least one title
        return self.EmbyServer.create_collection(title,[self._emby_all_items[0].ratingKey], self.Emby.get("Id"))



    def get_tags(self, tag, person_list=None, tmdb_person_id = None):
        if isinstance(tag, str):
            match = re.match(r'(?:([a-zA-Z]*)\.)?([a-zA-Z]+)', tag)
            if not match:
                raise BadRequest(f'Invalid filter field: {tag}')
            _libtype, tag = match.groups()
            libtype = _libtype or self.lib_type # e.g. show
            # libtype = _libtype or self.Plex.TYPE # e.g. show

            if not self.EmbyServer.is_in_filtertype(tag, libtype):
                raise Warning(f'Unknown filter field "{tag}" for libtype "{libtype}". ') from None
                try:
                    tag = next(f for f in self.Plex.listFilters(libtype) if f.filter == tag)
                except StopIteration:
                    available_filters = [f.filter for f in self.Plex.listFilters(libtype)]
                    raise NotFound(f'Unknown filter field "{tag}" for libtype "{libtype}". '
                                   f'Available filters: {available_filters}') from None
            my_search = tag
        else:
             my_search = tag.filter

        # tag: <FilteringFilter:/library/sections/8/:Labels>
        # items = {MediaContainer: 10} [<FilterChoice:284998:Overlay>, <FilterChoice:310126:Kometa>, <FilterChoice:310132:National-Film-Regist>, <FilterChoice:310150...-on-a-True-Stor>, <FilterChoice:310159:🎖Veteran's-Day-Movie>, <FilterChoice:310161:Seasonal>, <FilterChoice:310165:Top-Actors>
        #  TAG = {str} 'MediaContainer'
        #  TYPE = {NoneType} None
        #  allowSync = {int} 0
        #  augmentationKey = {NoneType} None
        #  identifier = {str} 'com.plexapp.plugins.library'
        #  key = {NoneType} None
        #  librarySectionID = {NoneType} None
        #  librarySectionTitle = {NoneType} None
        #  librarySectionUUID = {NoneType} None
        #  mediaTagPrefix = {str} '/system/bundle/media/flags/'
        #  mediaTagVersion = {str} '1727455477'
        #  offset = {NoneType} None
        #  size = {int} 10
        #  totalSize = {NoneType} None
        #  00 = {FilterChoice} <FilterChoice:284998:Overlay>
        #   TAG = {str} 'Directory'
        #   TYPE = {NoneType} None
        #   fastKey = {str} '/library/sections/8/all?label=284998'
        #   key = {str} '284998'
        #   thumb = {NoneType} None
        #   title = {str} 'Overlay'
        #   type = {NoneType} None
        #  01 = {FilterChoice} <FilterChoice:310126:Kometa>
        #   TAG = {str} 'Directory'
        #   TYPE = {NoneType} None
        #   fastKey = {str} '/library/sections/8/all?label=310126'
        #   key = {str} '310126'
        #   thumb = {NoneType} None
        #   title = {str} 'Kometa'
        #   type = {NoneType} None


        # my_items = self.EmbyServer.get_collections_filter_choices()

        # tag = {FilteringFilter} <FilteringFilter:/library/sections/8/:Actor>
        #  TAG = {str} 'Filter'
        #  TYPE = {NoneType} None
        #  filter = {str} 'actor'
        #  filterType = {str} 'string'
        #  key = {str} '/library/sections/8/actor'
        #  title = {str} 'Actor'
        #  type = {str} 'filter'



        emby_items = []

        if my_search in ['studio', 'show.studio']: # todo: differentiate between studio & network?
            labels = self.EmbyServer.get_emby_studios(self, self.Emby.get("Id"))

            for label in labels:
                key = str(label)
                title = f"{str(label)}"

                # Create a FilterChoiceEmby object
                filter_choice = FilterChoiceEmby(key=key, title=title)
                emby_items.append(filter_choice)
            return emby_items
        elif my_search in ['network', 'show.network']: # todo: differentiate between studio & network?
            labels = self.EmbyServer.get_emby_networks(self, self.Emby.get("Id"))

            for label in labels:
                key = str(label)
                title = f"{str(label)}"

                # Create a FilterChoiceEmby object
                filter_choice = FilterChoiceEmby(key=key, title=title)
                emby_items.append(filter_choice)
            return emby_items
        elif my_search in ['country']:
            labels = self.EmbyServer.get_emby_countries(self.Emby.get("Id"))

            for label in labels:
                key = str(label)
                title = f"{str(label)}"

                # Create a FilterChoiceEmby object
                filter_choice = FilterChoiceEmby(key=key, title=title)
                emby_items.append(filter_choice)
            return emby_items
        elif my_search in ['genre']:
            genres = self.EmbyServer.get_emby_genres(self.Emby.get("Id"))

            for genre in genres:
                key = str(genre)
                title = f"{str(genre)}"

                # Create a FilterChoiceEmby object
                filter_choice = FilterChoiceEmby(key=key, title=title)
                emby_items.append(filter_choice)
            return emby_items
        elif my_search in ['contentRating']:
            content_ratings = self.EmbyServer.get_official_age_ratings(self.Emby.get("Id"))

            for rating in content_ratings:
                key = rating.get("Name")
                if key:
                    # Create a FilterChoiceEmby object
                    filter_choice = FilterChoiceEmby(key=key, title=key)
                    emby_items.append(filter_choice)
            return emby_items
        elif my_search in ['label', 'show.label']:
            labels = self.EmbyServer.get_emby_item_tags(self, self.Emby.get("Id"), search_all=True)

            labels += self.EmbyServer.get_emby_countries(self.Emby.get("Id"))
            icon = '📺' if self.type == 'Show' else '🎥'
            name = self.name
            composed_name = f'{icon} {name} '
            for label in labels:
                key = str(label)
                title = f"{str(label)}"

                # Create a FilterChoiceEmby object
                filter_choice = FilterChoiceEmby(key=key, title=title)
                emby_items.append(filter_choice)
                if str(label).startswith(composed_name):
                    label_new = str(label).replace(composed_name, '')
                    key = str(label_new)
                    title = f"{str(label_new)}"
                    # Create a FilterChoiceEmby object
                    filter_choice = FilterChoiceEmby(key=key, title=title)
                    emby_items.append(filter_choice)

            return emby_items
        elif my_search in ['decade']:
            years = self.EmbyServer.get_years(self.Emby.get("Id"))
            dekaden_set = set()

            for y in years:
                jahr = int(y['Name'])
                dekade = (jahr // 10) * 10
                dekaden_set.add(dekade)

            dekaden_liste = sorted(dekaden_set)

            for dec in dekaden_liste:
                key = str(dec)
                title = f"{str(dec)}s"

                # Create a FilterChoiceEmby object
                filter_choice = FilterChoiceEmby(key=key, title=title)
                emby_items.append(filter_choice)
            return emby_items
        #             for decade in decades:
        #                 key = decade
        #                 title = f"{decade}s"
        # 0 = {FilterChoice} <FilterChoice:2020:2020s>
        # 1 = {FilterChoice} <FilterChoice:2010:2010s>

        # 0 = {FilterChoice} <FilterChoice:2020:2020s>
        #  TAG = {str} 'Directory'
        #  TYPE = {NoneType} None
        #  fastKey = {str} '/library/sections/8/all?decade=2020'
        #  key = {str} '2020'
        #  thumb = {NoneType} None
        #  title = {str} '2020s'
        #  type = {NoneType} None

        elif my_search in ["actor", "director", "writer", "producer", "composer"]:

            # short cut with proper tmdb id
            if tmdb_person_id and len(person_list) == 1:
                my_person = self.EmbyServer.get_person_info_bulk([tmdb_person_id], "tmdb")
                my_choice = FilterChoiceEmby(key=my_person.get(int(tmdb_person_id)), title=person_list[0], thumb=None)
                return [my_choice]

            emby_people = self.EmbyServer.get_people(my_search, person_list)

            for person in emby_people:
                key = person.get('Id')
                title = person.get('Name')
                prov_ids = person.get('ProviderIds')
                tmdb_id = prov_ids.get('Tmdb') if prov_ids else None

                # Construct the thumbnail URL
                thumb = None
                if 'ImageTags' in person and 'Primary' in person['ImageTags']:
                    server_url = self.EmbyServer.emby_server_url
                    image_tag = person['ImageTags']['Primary']
                    thumb = f"{server_url}/Items/{key}/Images/Primary?tag={image_tag}"

                # Create a FilterChoiceEmby object
                filter_choice = FilterChoiceEmby(key=key, title=title, thumb=thumb)
                emby_items.append(filter_choice)

            # if len(emby_items) > 0:
            return emby_items
        elif my_search in ['resolution']:
            my_dict = self.EmbyServer.get_resolutions()
            return my_dict
                # key = str(dec)
                # title = f"{str(dec)}s"
                #
                # # Create a FilterChoiceEmby object
                # filter_choice = FilterChoiceEmby(key=key, title=title)
                # emby_items.append(filter_choice)


        # Errors:
        # 'country'
        # country, region + continent not working

        raise Failed(f"Not implemented Emby search FilterChoice {tag}")

        items = self.Plex.findItems(self.Plex._server.query(tag.key), FilterChoice)

        #     {
        #       "Name": "Wolfgang Petersen",
        #       "ServerId": "37de8e11ee0748bea8d2080a13984949",
        #       "Id": "61041",
        #       "Type": "Person",
        #       "ImageTags": {
        #         "Primary": "ca733b3b975daa618201765a10805fe7"
        #       },
        #       "BackdropImageTags": []
        #     }

        # items_filter = object[FilterChoice]()
        # key: '/library/sections/8/label'
        # Plex:
        #         for elem in data:
        #             if self._checkAttrs(elem, **kwargs):
        #                 item = self._buildItemOrNone(elem, cls, initpath)
        #                 if item is not None:
        #                     items.append(item)
        #         return items

        if tag.key.endswith("/collection?type=4"): # no idea
            keys = [k.key for k in items]
            keys.extend([k.key for k in self.Plex.findItems(self.Plex._server.query(f"{tag.key[:-1]}3"), FilterChoice)])
            items = [i for i in self.Plex.findItems(self.Plex._server.query(tag.key[:-7]), FilterChoice) if i.key not in keys]
        return items

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

    def alter_collection(self, items, collection, smart_label_collection=False, add=True, collection_id = None):
        maintain_status = True
        locked_items = []
        unlocked_items = []
        if not smart_label_collection and maintain_status and self.agent in ["tv.plex.agents.movie", "tv.plex.agents.series"]:
            for item in items:
                # emby
                # item = self.reload(item)
                if next((f for f in item.fields if f.name == "collection"), None) is not None:
                    locked_items.append(item)
                else:
                    unlocked_items.append(item)
        else:
            locked_items = items

        for _items, locked in [(locked_items, True), (unlocked_items, False)]:
            if _items:
                # Smart Label Collection (verwende JSON-basierte Labels)
                if smart_label_collection:
                    if add:
                        # add / remove collection tag/label
                        # ToDo: Remove the tags for smart label collections
                        # if False:
                        for item in _items:
                            # Füge ein Label hinzu
                            self.EmbyServer.add_tags(item.ratingKey,[collection])
                            self._invalidate_metadata_caches(item.ratingKey)

                                # add_label(kometa_labels, item.ratingKey, collection)
                                # save_labels_to_file(file_path_kometa, kometa_labels)
                        rating_keys = [item.ratingKey for item in _items]
                        added = self.EmbyServer.add_to_collection(collection, rating_keys)
                        if not added:
                            self.EmbyServer.create_collection(collection, rating_keys, locked=locked, parent_id=self.Emby.get("Id"))


                    else:
                        for item in _items:
                            # Entferne ein Label (JSON-basiert)
                            self.EmbyServer.remove_tags(item.ratingKey, [collection])
                            self._invalidate_metadata_caches(item.ratingKey)
                            # remove_label(kometa_labels, item.ratingKey, collection)
                            # save_labels_to_file(file_path_kometa, kometa_labels)
                        self.EmbyServer.add_remove_plex_object_from_collection(collection, _items, 'delete')

                # Traditionelle Sammlungen (BoxSets in Emby)
                elif add:
                    rating_keys = [item.ratingKey for item in _items]
                    added = self.EmbyServer.add_to_collection(collection, rating_keys)
                    # Sammlung erstellen oder Medien hinzufügen
                    if not added:
                        self.EmbyServer.create_collection(collection, rating_keys, locked=locked, parent_id= self.Emby.get("Id"))
                    for rating_key in rating_keys:
                        self._invalidate_metadata_caches(rating_key)
                else:
                    # Tags entfernen und Sammlung löschen
                    for item in _items:
                        self.EmbyServer.remove_tags(item.ratingKey, [collection])
                        self._invalidate_metadata_caches(item.ratingKey)
                    # self.EmbyServer.remove_boxset(collection, collection_id)
                    self.EmbyServer.add_remove_plex_object_from_collection(collection, items, 'delete')

        # for _items, locked in [(locked_items, True), (unlocked_items, False)]:
        #     if _items:
        #         # self.Plex.batchMultiEdits(_items)
        #         if smart_label_collection:
        #             self.query_data(self.Plex.addLabel if add else self.Plex.removeLabel, collection)
        #         elif add:
        #             self.Plex.addCollection(collection, locked=locked)
        #         else:
        #             self.EmbyServer.remove_kometa_tags_from_collection(collection)
        #             self.Plex.removeCollection(collection, locked=locked)
        #         # self.Plex.saveMultiEdits()

    def calculate_add_remove_items(self, new_items, current_items):
        """
        Berechnet die Listen von Items, die hinzugefügt (add_items) oder entfernt (remove_items) werden müssen.

        :param new_items: Liste der neuen Items (z. B. aus fetchItems)
        :param current_items: Liste der aktuellen Items in der Collection
        :return: Tuple (add_items, remove_items)
        """
        # Extrahiere die ratingKeys für schnellen Vergleich
        new_keys = {item.ratingKey for item in new_items}
        current_keys = {item.ratingKey for item in current_items}

        # Berechne Items, die hinzugefügt werden müssen (in new_items, aber nicht in current_items)
        add_items = [item for item in new_items if item.ratingKey not in current_keys]

        # Berechne Items, die entfernt werden müssen (in current_items, aber nicht in new_items)
        remove_items = [item for item in current_items if item.ratingKey not in new_keys]

        keep_items = [item for item in current_items if item.ratingKey in new_keys]

        return add_items, remove_items, keep_items


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

    def _can_use_emby_cache(self, emby_query_params):
        """Return True when the local cache can satisfy the requested filters."""
        if not emby_query_params:
            return False

        if emby_query_params.get("Ids"):
            return False

        if "PersonIds" in emby_query_params:
            include_types = emby_query_params.get("IncludeItemTypes", "")
            if include_types:
                include_person = any(
                    t.strip().lower() == "person" for t in include_types.split(",") if t.strip()
                )
                if include_person:
                    return False

        supported_keys = {
            "Recursive",
            "Fields",
            "IncludeItemTypes",
            "ParentId",
            "Years",
            "Genres",
            "Studios",
            "Tags",
            "OfficialRatings",
            "MinCriticRating",
            "MaxCriticRating",
            "MinCommunityRating",
            "MaxCommunityRating",
            "MinCustomRating",
            "MaxCustomRating",
            "MinPremiereDate",
            "MaxPremiereDate",
            "Limit",
            "SortBy",
            "SortOrder",
            "PersonIds",
            "PersonTypes",
        }

        for key, value in emby_query_params.items():
            if key.startswith("_"):
                continue
            if key not in supported_keys and value not in (None, ""):
                return False

        return True



    def _to_aware_utc(self, dt: datetime | None) -> datetime | None:
        from datetime import datetime
        if dt is None:
            return None
        if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)



    # @retry(stop=stop_after_attempt(6), wait=wait_fixed(10),
    #        retry=retry_if_not_exception_type((BadRequest, NotFound, Unauthorized)))

    def _parse_emby_datetime(self, value):
        """Parse ISO timestamps from Emby and ALWAYS return UTC-aware datetimes (or None)."""
        if not value:
            return None

        # Bereits datetime?
        if isinstance(value, datetime):
            return self._to_aware_utc(value)

        s = str(value).strip()

        # Emby nutzt häufig 'Z' -> UTC
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"

        # Emby liefert teils 7+ Nachkommastellen. Kürzen auf 6 (Python-Mikrosekunden).
        try:
            # Offset-Position finden (falls vorhanden)
            off_idx = max(s.rfind("+"), s.rfind("-"))
            time_part, offset = (s[:off_idx], s[off_idx:]) if off_idx > 10 else (s, "")
            if "." in time_part:
                base, frac = time_part.split(".", 1)
                frac = "".join(c for c in frac if c.isdigit())
                frac = (frac + "000000")[:6]
                time_part = f"{base}.{frac}"
            s_norm = f"{time_part}{offset}"

            dt = datetime.fromisoformat(s_norm)
            return self._to_aware_utc(dt)
        except Exception:
            # Fallbacks – liefern naiv, darum danach _to_aware_utc
            for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
                try:
                    dt = datetime.strptime(s.replace("Z", ""), fmt)
                    return self._to_aware_utc(dt)
                except Exception:
                    continue
            return None

    def find_item_assets(self, item, item_asset_directory=None, asset_directory=None, folder_name=None):
        poster = None
        background = None
        logo = None

        if asset_directory is None:
            asset_directory = self.asset_directory

        is_top_level = isinstance(item, (Movie, Artist, Show, Collection, Playlist, str))
        if isinstance(item, Album):
            prefix = f"{item.parentTitle} Album {item.title}'s "
            file_name = item.title
        elif isinstance(item, Season):
            prefix = f"{item.parentTitle} Season {item.seasonNumber}'s "
            file_name = f"Season{'0' if not item.seasonNumber or item.seasonNumber < 10 else ''}{item.seasonNumber}"
        elif isinstance(item, Episode):
            prefix = f"{item.grandparentTitle} {item.seasonEpisode.upper()}'s "
            file_name = item.seasonEpisode.upper()
        else:
            prefix = f"{item if isinstance(item, str) else item.title}'s "
            file_name = "poster"

        if not item_asset_directory:
            if isinstance(item, (Movie, Artist, Album, Show, Episode, Season)):
                if isinstance(item, (Episode, Season)):
                    starting = item.show()
                elif isinstance(item, (Album, Track)):
                    starting = item.artist()
                else:
                    starting = item
                emby_item = self.EmbyServer.get_item(starting.ratingKey)
                emby_path_media = emby_item.get('Path', None)
                # directory_path = os.path.dirname(emby_path_media)
                # starting.locations.append(directory_path)

                if not emby_path_media:
                    raise Failed(f"Asset Warning: No video filepath found for {item.title}")
                path_test = str(emby_path_media)
                if not os.path.dirname(path_test):
                    path_test = path_test.replace("\\", "/")
                folder_name = os.path.basename(os.path.dirname(path_test) if isinstance(starting, Movie) else path_test)
            elif isinstance(item, (Collection, Playlist)):
                folder_name = item.title
            else:
                folder_name = item
            folder_name, _ = util.validate_filename(folder_name)

        if not self.asset_folders:
            file_name = folder_name if file_name == "poster" else f"{folder_name}_{file_name}"

        if not item_asset_directory:
            for ad in asset_directory:
                if self.asset_folders:
                    if os.path.isdir(os.path.join(ad, folder_name)):
                        item_asset_directory = os.path.join(ad, folder_name)
                    else:
                        for n in range(1, self.asset_depth + 1):
                            new_path = ad
                            for i in range(1, n + 1):
                                new_path = os.path.join(new_path, "*")
                            matches = util.glob_filter(os.path.join(new_path, folder_name))
                            if len(matches) > 0:
                                item_asset_directory = os.path.abspath(matches[0])
                                break
                else:
                    matches = util.glob_filter(os.path.join(ad, f"{file_name}.*"))
                    if len(matches) > 0:
                        item_asset_directory = ad
                if item_asset_directory:
                    break
            if not item_asset_directory:
                if self.asset_folders:
                    if self.create_asset_folders and asset_directory:
                        item_asset_directory = os.path.join(asset_directory[0], folder_name)
                        os.makedirs(item_asset_directory, exist_ok=True)
                        logger.warning(f"Asset Warning: Asset Directory Not Found and Created: {item_asset_directory}")
                    else:
                        raise Failed(f"Asset Warning: Unable to find asset folder: '{folder_name}'")
                return None, None, None, item_asset_directory, folder_name

        poster_filter = os.path.join(item_asset_directory, f"{file_name}.*")
        background_filter = os.path.join(item_asset_directory, "background.*" if file_name == "poster" else f"{file_name}_background.*")
        logo_filter = os.path.join(item_asset_directory, "logo.*" if file_name == "poster" else f"{file_name}_logo.*")

        poster_matches = util.glob_filter(poster_filter)
        if len(poster_matches) > 0:
            poster = ImageData("asset_directory", os.path.abspath(poster_matches[0]), prefix=prefix, is_url=False)

        background_matches = util.glob_filter(background_filter)
        if len(background_matches) > 0:
            background = ImageData("asset_directory", os.path.abspath(background_matches[0]), prefix=prefix, image_type="background", is_url=False)

        logo_matches = util.glob_filter(logo_filter)
        if len(logo_matches) > 0:
            logo = ImageData("asset_directory", os.path.abspath(logo_matches[0]), prefix=prefix, image_type="logo", is_url=False)

        if is_top_level and self.asset_folders and self.dimensional_asset_rename and (not poster or not background):
            for file in util.glob_filter(os.path.join(item_asset_directory, "*.*")):
                if file.lower().endswith((".png", ".jpg", ".jpeg", "webp")) and not re.match(r"s\d+e\d+|season\d+", os.path.basename(file).lower()):
                    try:
                        with Image.open(file) as image:
                            _w, _h = image.size
                        if not poster and _h >= _w:
                            new_path = os.path.join(os.path.dirname(file), f"poster{os.path.splitext(file)[1].lower()}")
                            os.rename(file, new_path)
                            poster = ImageData("asset_directory", os.path.abspath(new_path), prefix=prefix, is_url=False)
                        elif not background and _w > _h:
                            new_path = os.path.join(os.path.dirname(file), f"background{os.path.splitext(file)[1].lower()}")
                            os.rename(file, new_path)
                            background = ImageData("asset_directory", os.path.abspath(new_path), prefix=prefix, image_type="background", is_url=False)
                        if poster and background:
                            break
                    except OSError:
                        logger.error(f"Asset Error: Failed to open image: {file}")

        return poster, background, logo, item_asset_directory, folder_name

    def get_ids(self, item):
        provider_ids = self.EmbyServer.get_provider_ids(item)
        emby_imdb_id = provider_ids[0]
        emby_tvdb_id = provider_ids[1]
        emby_tmdb_id = provider_ids[2]

        return emby_tmdb_id, emby_tvdb_id, emby_imdb_id

    def _filter_emby_native_items(self, items, query_params):
        """Apply supported Emby filters to a list of cached native items."""
        if items is None:
            return None

        filtered = list(items)

        def get_year(item):
            year = item.get("ProductionYear")
            if isinstance(year, int):
                return str(year)
            if isinstance(year, str) and year.isdigit():
                return year
            premiere = item.get("PremiereDate")
            premiere_date = self._parse_emby_datetime(premiere)
            if premiere_date:
                return str(premiere_date.year)
            return None

        def get_tags(item):
            raw_tags = item.get("Tags") or []
            if raw_tags:
                return set(raw_tags)
            tag_items = item.get("TagItems") or []
            return {tag.get("Name") for tag in tag_items if tag.get("Name")}

        def matches_person(item, person_ids, person_roles=None):
            people = item.get("People")
            if not isinstance(people, list):
                return None
            for person in people:
                pid = person.get("Id")
                if pid is not None and str(pid) in person_ids:
                    if person_roles:
                        person_type = person.get("Type")
                        if isinstance(person_type, str) and person_type.lower() in person_roles:
                            return True
                    else:
                        return True
            return False

        include_item_types = query_params.get("IncludeItemTypes")
        if include_item_types:
            allowed_types = {t.strip() for t in include_item_types.split(",") if t.strip()}
            if allowed_types:
                filtered = [item for item in filtered if item.get("Type") in allowed_types]

        years_value = query_params.get("Years")
        if years_value:
            allowed_years = {y.strip() for y in years_value.split(",") if y.strip()}
            if allowed_years:
                hydrated_items = []
                for item in filtered:
                    item_year = get_year(item)
                    if item_year is None:
                        return None
                    hydrated_items.append((item, item_year))
                filtered = [item for item, item_year in hydrated_items if item_year in allowed_years]

        min_premiere = self._parse_emby_datetime(query_params.get("MinPremiereDate"))
        if min_premiere:
            filtered = [
                item
                for item in filtered
                if (premiere := self._parse_emby_datetime(item.get("PremiereDate"))) and premiere >= min_premiere
            ]

        max_premiere = self._parse_emby_datetime(query_params.get("MaxPremiereDate"))
        if max_premiere:
            filtered = [
                item
                for item in filtered
                if (premiere := self._parse_emby_datetime(item.get("PremiereDate"))) and premiere <= max_premiere
            ]

        if any(
            key in query_params
            for key in (
                "MaxCriticRating",
                "MaxCommunityRating",
                "MaxCustomRating",
                "MinCriticRating",
                "MinCommunityRating",
                "MinCustomRating",
            )
        ):
            updated_results = []
            for item in filtered:
                keep_item = True
                custom_rating_value = 0
                if "MaxCustomRating" in query_params or "MinCustomRating" in query_params:
                    rating = self.EmbyServer.get_custom_rating_from_item(item)
                    if rating is not None:
                        custom_rating_value = rating
                if "MaxCriticRating" in query_params:
                    critic_rating = int(float(item.get("CriticRating", 0)))
                    max_rating = int(query_params.get("MaxCriticRating"))
                    if critic_rating > max_rating or critic_rating == 0:
                        keep_item = False

                if "MaxCommunityRating" in query_params:
                    community_rating = float(item.get("CommunityRating", 0))
                    max_rating = float(query_params.get("MaxCommunityRating"))
                    if community_rating > max_rating or community_rating == 0:
                        keep_item = False

                if "MaxCustomRating" in query_params:
                    max_rating = float(query_params.get("MaxCustomRating"))
                    if custom_rating_value > max_rating or custom_rating_value == 0:
                        keep_item = False

                if "MinCriticRating" in query_params:
                    critic_rating = int(float(item.get("CriticRating", 0)))
                    min_rating = int(query_params.get("MinCriticRating"))
                    if critic_rating < min_rating or critic_rating == 0:
                        keep_item = False

                if "MinCommunityRating" in query_params:
                    community_rating = float(item.get("CommunityRating", 0))
                    min_rating = float(query_params.get("MinCommunityRating"))
                    if community_rating < min_rating or community_rating == 0:
                        keep_item = False

                if "MinCustomRating" in query_params:
                    min_rating = float(query_params.get("MinCustomRating"))
                    if custom_rating_value < min_rating or custom_rating_value == 0:
                        keep_item = False

                if keep_item:
                    updated_results.append(item)

            filtered = updated_results

        if "Studios" in query_params:
            studio_value = query_params["Studios"]
            if isinstance(studio_value, str):
                studio_names = [s.strip() for s in studio_value.split(",") if s.strip()]
            else:
                studio_names = [str(s).strip() for s in studio_value if str(s).strip()]
            if studio_names:
                studio_filter_results = []
                for item in filtered:
                    for studio in item.get("Studios", []) or []:
                        if studio.get("Name") in studio_names:
                            studio_filter_results.append(item)
                            break
                filtered = studio_filter_results

        if "Genres" in query_params:
            genres_value = query_params["Genres"]
            if isinstance(genres_value, str):
                source_genres = genres_value.split(",")
            else:
                source_genres = genres_value
            requested_genres = {str(g).strip() for g in source_genres if str(g).strip()}
            if requested_genres:
                filtered = [
                    item
                    for item in filtered
                    if requested_genres.issubset({str(g) for g in item.get("Genres", []) if g})
                ]

        if "Tags" in query_params:
            requested_tags = {tag.strip() for tag in query_params["Tags"].split("|") if tag.strip()}
            if requested_tags:
                filtered = [
                    item
                    for item in filtered
                    if (tags := get_tags(item)) and requested_tags.intersection(tags)
                ]

        if "OfficialRatings" in query_params:
            allowed_ratings = {r.strip() for r in query_params["OfficialRatings"].split("|") if r.strip()}
            if allowed_ratings:
                filtered = [
                    item
                    for item in filtered
                    if item.get("OfficialRating") in allowed_ratings
                ]

        resolution_filters = query_params.get("_Resolutions")
        require_hdr = query_params.get("_RequireHdr")
        if resolution_filters or require_hdr:
            allowed_ids = None
            if resolution_filters:
                allowed_ids = set()
                for res_key in resolution_filters:
                    allowed_ids.update(str(i) for i in self.EmbyServer.media_by_resolution.get(res_key, []))
            if require_hdr:
                hdr_ids = set()
                for hdr_key in ["dvhdr", "dvhdrplus", "hdr", "plus"]:
                    hdr_ids.update(str(i) for i in self.EmbyServer.media_by_resolution.get(hdr_key, []))
                allowed_ids = hdr_ids if allowed_ids is None else allowed_ids.intersection(hdr_ids)
            if allowed_ids is not None:
                filtered = [item for item in filtered if str(item.get("Id")) in allowed_ids]

        if "PersonIds" in query_params:
            person_ids = {pid.strip() for pid in query_params["PersonIds"].split(",") if pid.strip()}
            person_roles = None
            if "PersonTypes" in query_params:
                raw_roles = query_params["PersonTypes"]
                if isinstance(raw_roles, str):
                    parsed_roles = {role.strip().lower() for role in raw_roles.split(",") if role.strip()}
                else:
                    parsed_roles = {str(role).strip().lower() for role in raw_roles if str(role).strip()}
                if parsed_roles:
                    person_roles = parsed_roles
            person_matches = []
            for item in filtered:
                match = matches_person(item, person_ids, person_roles)
                if match is None:
                    return None
                if match:
                    person_matches.append(item)
            filtered = person_matches

        sort_key = query_params.get("SortBy")
        if sort_key:
            reverse_order = query_params.get("SortOrder", "Ascending").lower() == "descending"
            if sort_key.lower() == "random":
                random.shuffle(filtered)
            else:
                def _normalize_rating(value):
                    if isinstance(value, (int, float)) and not isinstance(value, bool):
                        return float(value)
                    if isinstance(value, str):
                        match = re.search(r"[-+]?\d*\.?\d+", value)
                        if match:
                            try:
                                return float(match.group())
                            except (TypeError, ValueError):
                                return None
                        return None
                    if isinstance(value, dict):
                        for sub_value in value.values():
                            normalized = _normalize_rating(sub_value)
                            if normalized is not None:
                                return normalized
                        return None
                    if isinstance(value, (list, tuple, set)):
                        for sub_value in value:
                            normalized = _normalize_rating(sub_value)
                            if normalized is not None:
                                return normalized
                        return None
                    return None

                def sort_value(item):
                    value = item.get(sort_key)
                    if sort_key in ["PremiereDate", "DateCreated"]:
                        value = self._parse_emby_datetime(value) or datetime.min
                    elif value is None and sort_key == "Name":
                        value = item.get("SortName") or item.get("Name") or ""
                    elif isinstance(sort_key, str) and "rating" in sort_key.lower():
                        value = _normalize_rating(value)
                    return value

                try:
                    sortable = []
                    none_bucket = []
                    for item in filtered:
                        value = sort_value(item)
                        if value is None:
                            none_bucket.append(item)
                        else:
                            sortable.append((value, item))
                    sortable.sort(key=lambda pair: pair[0], reverse=reverse_order)
                    filtered = [item for _, item in sortable] + none_bucket
                except Exception as e:
                    logger.error(f"Error during local sorting by {sort_key}: {e}")
                    return None

        limit_value = query_params.get("Limit")
        if limit_value:
            try:
                limit_int = int(limit_value)
                if limit_int >= 0:
                    filtered = filtered[:limit_int]
            except (TypeError, ValueError):
                logger.error(f"Invalid Limit value for local filtering: {limit_value}")
                return None

        return filtered

    def search(self, title=None, sort=None, maxresults=None, libtype=None, **kwargs):
        # print(title)
        if libtype == "collection":
            lib_id = self.Emby.get("Id")

            return self.EmbyServer.get_boxsets_from_library(title, library_id=lib_id)

            # return self.EmbyServer.search(title=title, sort=sort, maxresults=maxresults, libtype=libtype, **kwargs)
            pass
        else:
            pass
            # print(f"EMBY style: {self.EmbyServer.search(title=title, sort=sort, maxresults=maxresults, libtype=libtype, **kwargs)}")
            # print(f"plex_search:{title} - {sort} - {maxresults} - {libtype} - {kwargs}")
            # print(self.EmbyServer.search(title=title, sort=sort, maxresults=maxresults, libtype=libtype, **kwargs))
            # print(self.Plex.search(title=title, sort=sort, maxresults=maxresults, libtype=libtype, **kwargs))

        # plex_search: IMDb Lowest Rated - None - None - collection - {}
        # [ < Collection: 204155:IMDb - Lowest - Rated >]

        return self.EmbyServer.search(title=title, sort=sort, maxresults=maxresults, libtype=libtype, **kwargs)


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

    def get_collection_name_and_items(self, collection, smart_label_collection):
        name = collection.title if isinstance(collection, (Collection, Playlist)) else str(collection)
        return name, self.get_collection_items(collection, smart_label_collection)


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

        logger.info(f"Loading All {builder_level.capitalize()}s from Emby Library: {self.Emby.get('Name')}")

        include_item_types = ["Movie", "Series", "MusicArtist"]
        if builder_type == "movie":
            include_item_types = ["Movie"]
        elif builder_type == "show":
            include_item_types = ["Series"]
        elif builder_type == "season":
            include_item_types = ["Season"]
        elif builder_type == "artist":
            include_item_types = ["MusicArtist"]

        items_data = self.EmbyServer.get_items(
            params={"ParentId": self.Emby.get("Id")},
            fields="Budget,Chapters,DateCreated,Genres,HomePageUrl,IndexOptions,MediaStreams,Overview,ParentId,Path,People,ProductionYear,PremiereDate,ProviderIds,PrimaryImageAspectRatio,Revenue,SortName,Studios,Taglines,CriticRating,CommunityRating,OfficialRating,Tags,TagItems",
            include_item_types=include_item_types,
            getAll=True
        ) or []

        self.EmbyServer.cache_filenames(items_data)

        logger.info(f"Loaded {len(items_data)} {builder_level.capitalize()}s from Emby")
        self._emby_all_items_native = items_data
        if native:
            return items_data
        plex_items= self.EmbyServer.convert_emby_to_plex(items_data)

        # Emby Path Fix
        path_map = {i['Id']: i.get('Path') for i in items_data}
        for item in plex_items:
            if str(item.ratingKey) in path_map and path_map[str(item.ratingKey)]:
                item.locations = [path_map[str(item.ratingKey)]]

        # if builder_level in [None, "show", "artist", "movie"]:
        self._emby_all_items = plex_items
        return plex_items

    def get_all_collections(self, label=None):

        lib_id = self.Emby.get("Id")
        return self.EmbyServer.get_boxsets_from_library(library_id = lib_id, label=label, native=True)

    def get_actor_id(self, name):

        return self.EmbyServer.get_actor_id(name)

        results = self.Plex.hubSearch(name)
        for result in results:
            if isinstance(result, Role) and result.librarySectionID == self.Plex.key and result.tag == name:
                return result.id

    def fetch_item(self, item):
        if isinstance(item, (Movie, Show, Season, Episode, Artist, Album, Track)):
            return self.reload(item)
        key = int(item)
        if key in self.cached_items:
            return self.reload(self.cached_items[key][0])
        try:
            current = self.fetchItem(key)
            if isinstance(current, (Movie, Show, Season, Episode, Artist, Album, Track)):
                return self.reload(current)
        except (BadRequest, Warning) as e:
            logger.trace(e)
        raise Failed(f"Emby Error: Item {item} not found")

    def get_all_native(self, builder_level=None, load = False):
        return self.get_all(builder_level, load, native=True)

    def get_native_emby_item(self, emby_item_id):
        return self.EmbyServer.get_item(emby_item_id)
        pass

    def smart_filter(self, collection):
        smart_filter = self.get_collection(collection).content
        return smart_filter[smart_filter.index("?"):]

    def get_provider_ids(self, item):
        return self.EmbyServer.get_provider_ids(item)

    def get_collection_items(self, collection, smart_label_collection):
        # print(f"{collection} - {smart_label_collection}")

        if smart_label_collection:
            my_collection= None
            if hasattr(collection, 'ratingKey'):
                my_collection = collection.ratingKey
            else:
                my_collection:str = self.EmbyServer.get_collection_id(collection if isinstance(collection, str) else collection.title )
            if my_collection:
                return self.EmbyServer.get_items_in_boxset(my_collection)
            return []

            # self.create_blank_collection(collection)
            # my_collection: str = self.EmbyServer.get_collection_id(collection)
            # return self.EmbyServer.get_items_in_boxset(my_collection)

            return self.search(label=collection.title if isinstance(collection, Collection) else str(collection))
        elif isinstance(collection, (Collection, Playlist)):
            if collection.smart:
                return self.fetchItems(self.smart_filter(collection))
            else:
                my_items = self.EmbyServer.get_items_in_boxset(collection.ratingKey)
                # my_return = self.query(collection.items)
                return my_items
        elif isinstance(collection, str):
            mycol = self.EmbyServer.get_collection_id(collection)
            if mycol:
                my_items = self.EmbyServer.get_items_in_boxset(mycol)
                return self.EmbyServer.convert_emby_to_plex(my_items)

        return []


    def image_update(self, item, image, tmdb=None, title=None, poster=True):
        text = f"{f'{title} ' if title else ''}{'Poster' if poster else 'Background'}"
        attr = self.mass_poster_update["source"] if poster else self.mass_background_update["source"]
        if attr == "lock":
            self.query(item.lockPoster if poster else item.lockArt)
            logger.info(f"{text} | Locked")
        elif attr == "unlock":
            self.query(item.unlockPoster if poster else item.unlockArt)
            logger.info(f"{text} | Unlocked")
        else:
            location = "the Assets Directory" if image else ""
            image_url = False if image else True
            image = image.location if image else None
            if not image:
                if attr == "tmdb" and tmdb:
                    image = tmdb
                    location = "TMDb"
                if not image:
                    images = item.posters() if poster else item.arts()
                    temp_image = next((p for p in images), None)
                    if temp_image:
                        if temp_image.key.startswith("/"):
                            image = f"{self.url}{temp_image.key}&X-Plex-Token={self.token}"
                        else:
                            image = temp_image.key
                        location = "Plex"
            if image:
                logger.info(f"{text} | Reset from {location}")
                if poster:
                    try:
                        self.upload_poster(item, image, url=image_url)
                    except BadRequest as e:
                        logger.stacktrace()
                        logger.error(f"Plex Error: {e}")
                else:
                    try:
                        self.upload_background(item, image, url=image_url)
                    except BadRequest as e:
                        logger.stacktrace()
                        logger.error(f"Plex Error: {e}")
                # todo: check for file, no overlay in tags
                # if poster and "Overlay" in self.item_labels(item):
                if poster and "Overlay" in [la.tag for la in self.item_labels(item)]:
                    logger.info(self.edit_tags("label", item, remove_tags="Overlay", do_print=False))
            else:
                logger.warning(f"{text} | No Reset Image Found")

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
        # todo: set dirty
        return item
        pass
    def upload_poster(self, item, image, url=""):
        if url:
            return self.EmbyServer.set_image_smart(item.ratingKey, url, image_type="Primary")
        else:
            return self.EmbyServer.set_image_smart(item.ratingKey, image.location, image_type="Primary")

    def create_smart_collection(self, title, smart_type, uri_args, ignore_blank_results, minimum = None):

        collection_id = self.EmbyServer.get_collection_id(title)
        if collection_id:
            return collection_id

        if not ignore_blank_results:
            self.test_smart_filter(uri_args)

        # no smart collections in emby, using regular one
        my_items = self.fetchItems(uri_args)

        if minimum and minimum > len(my_items):
            return None


        return self.EmbyServer.create_smart_collection(title, smart_type, my_items, ignore_blank_results, self.Emby.get("Id"))
        # print(f"{smart_type} - {uri_args}")


        args = {
            "type": smart_type,
            "title": title,
            "smart": 1,
            "sectionId": self.Plex.key,
            "uri": self.build_smart_filter(uri_args)
        }
        self._query(f"/library/collections{utils.joinArgs(args)}", post=True)


    def upload_poster_overlay(self, item, image_temp_path, url=False):
        # Not actually uploading anything to Emby, just saving the overlay png
        file_extension = image_temp_path.split('.')[-1]
        file_name = f"{item.ratingKey}.{file_extension}"
        # todo: config path

        export_file = os.path.join(self.overlay_destination_folder, file_name)

        try:
            # Erstellen des Zielverzeichnisses, falls es nicht existiert
            destination_dir = os.path.dirname(self.overlay_destination_folder)
            if not os.path.exists(destination_dir):
                os.makedirs(destination_dir)

            # Kopiere die Datei zum Ziel
            import shutil
            shutil.copy2(image_temp_path, export_file)
            # print(f"Datei erfolgreich kopiert: {image_temp_path} -> {export_file}")
        except Exception as e:
            # print(f"Fehler beim Kopieren der Datei: {e}")
            raise
    def upload_background(self, item, image, url=""):
        if url:
            return self.EmbyServer.set_image_smart(item.ratingKey, url, image_type="Backdrop")
            # item.uploadArt(url=image)
        else:
            return self.EmbyServer.set_image_smart(item.ratingKey, image.location, image_type="Backdrop")
            # item.uploadArt(filepath=image)
    def upload_logo(self, item, image, url=""):
        if url:
            return self.EmbyServer.set_image_smart(item.ratingKey, url, image_type="ClearLogo")
        else:
            return self.EmbyServer.set_image_smart(item.ratingKey, image.location, image_type="ClearLogo")

    def upload_theme(self, collection, url=None, filepath=None):
        return
        key = f"/library/metadata/{collection.ratingKey}/themes"
        if url:
            self.PlexServer.query(f"{key}?url={quote_plus(url)}", method=self.PlexServer._session.post)
        elif filepath:
            self.PlexServer.query(key, method=self.PlexServer._session.post, data=open(filepath, 'rb').read())
    def check_filters(self, item, filters_in, current_time):
        for filter_method, filter_data in filters_in:
            filter_attr, modifier, filter_final = self.split(filter_method)
            if self.check_filter(item, filter_attr, modifier, filter_final, filter_data, current_time) is False:
                return False
        return True

    def check_filter(self, item, filter_attr, modifier, filter_final, filter_data, current_time):
        filter_actual = attribute_translation[filter_attr] if filter_attr in attribute_translation else filter_attr
        if item.ratingKey in self.filter_items_cache:
            emby_item = self.filter_items_cache[item.ratingKey]
        else:
            emby_item = self.EmbyServer.get_item(item.ratingKey)
            self.filter_items_cache[item.ratingKey] = emby_item
        if isinstance(item, Movie):
            item_type = "movie"
        elif isinstance(item, Show):
            item_type = "show"
        elif isinstance(item, Season):
            item_type = "season"
        elif isinstance(item, Episode):
            item_type = "episode"
        elif isinstance(item, Artist):
            item_type = "artist"
        elif isinstance(item, Album):
            item_type = "album"
        elif isinstance(item, Track):
            item_type = "track"
        else:
            return True
        # item = self.reload(item)
        if filter_attr not in builder.filters[item_type]:
            return True
        elif filter_attr in builder.date_filters:
            if util.is_date_filter(getattr(item, filter_actual), modifier, filter_data, filter_final, current_time):
                return False
        elif filter_attr in builder.string_filters:
            #ToDo: most of the stuff for proprietary Emby item not integrated yet
            values = []
            if filter_attr == "audio_track_title":
                for media in item.media:
                    for part in media.parts:
                        values.extend([a.extendedDisplayTitle for a in part.audioStreams() if a.extendedDisplayTitle])
            elif filter_attr == "subtitle_track_title":
                for media in item.media:
                    for part in media.parts:
                        values.extend([a.extendedDisplayTitle for a in part.subtitleStreams() if a.extendedDisplayTitle])
            elif filter_attr in ["audio_codec", "audio_profile", "video_codec", "video_profile"]:
                for media in item.media:
                    attr = getattr(media, filter_actual)
                    if attr and attr not in values:
                        values.append(attr)
            elif filter_attr in ["filepath", "folder"]:
                values = [emby_item.get("Path")]
                # values = [loc for loc in item.locations if loc]
            else:
                test_value = getattr(item, filter_actual)
                values = [test_value] if test_value else []
            if util.is_string_filter(values, modifier, filter_data):
                return False
        elif filter_attr in builder.boolean_filters:
            filter_check = False
            # logger.info(f"filter attribute: {filter_attr}")
            if filter_attr == "has_collection":
                filter_check = len(item.collections) > 0
            elif filter_attr == "has_edition": # no edition in Emby, filename regex
                filter_check = True if item.editionTitle else False
            elif filter_attr == "has_stinger":
                filter_check = False
                if item.ratingKey in self.movie_rating_key_map and self.movie_rating_key_map[item.ratingKey] in self.config.mediastingers:
                    filter_check = True
            elif filter_attr == "has_overlay":
                if os.path.exists(os.path.join(self.overlay_destination_folder, item.ratingKey, ".png")):
                    filter_check = True
                for label in self.item_labels(item):
                    if label.tag.lower().endswith(" overlay") or label.tag.lower() == "overlay":
                        filter_check = True
                        break
            elif filter_attr == "has_dolby_vision":
                media_sources = emby_item.get("MediaSources", [])
                # logger.warning(f"My media: {media_sources}")

                for media in media_sources:
                    media_streams = media.get("MediaStreams", [])
                    for stream in media_streams:
                        if stream.get("VideoRange") == "DolbyVision":
                            # logger.warning("Found Dolby Vision!")
                            filter_check = True
                            break

                # for media in item.media:
                #     for part in media.parts:
                #         for stream in part.videoStreams():
                #             if stream.DOVIPresent:
                #                 filter_check = True
                #                 break
            if util.is_boolean_filter(filter_data, filter_check):
                return False
        elif filter_attr == "history":
            my_date = emby_item.get("PremiereDate")
            item_date = datetime.fromisoformat(my_date.replace("Z", "+00:00"))
            # item_date = item.originallyAvailableAt
            if item_date is None:
                return False
            elif filter_data == "day":
                if item_date.month != current_time.month or item_date.day != current_time.day:
                    return False
            elif filter_data == "month":
                if item_date.month != current_time.month:
                    return False
            else:
                date_match = False
                for i in range(filter_data):
                    check_date = current_time - timedelta(days=i)
                    if item_date.month == check_date.month and item_date.day == check_date.day:
                        date_match = True
                if date_match is False:
                    return False
        elif filter_attr in ["seasons", "episodes", "albums", "tracks"]:
            if filter_attr == "seasons":
                sub_items = item.seasons()
            elif filter_attr == "albums":
                sub_items = item.albums()
            elif filter_attr == "tracks":
                sub_items = item.tracks()
            else:
                episodes = self.EmbyServer.get_items({"ParentId": item.ratingKey}, include_item_types="Episode")
                sub_items = self.EmbyServer.convert_emby_to_plex(episodes)
                # sub_items = item.episodes()
            filters_in = []
            percentage = 60
            for sub_atr, sub_data in filter_data.items():
                if sub_atr == "percentage":
                    percentage = sub_data
                else:
                    filters_in.append((sub_atr, sub_data))
            failure_threshold = len(sub_items) * ((100 - percentage) / 100)
            failures = 0
            for sub_item in sub_items:
                if self.check_filters(sub_item, filters_in, current_time) is False:
                    failures += 1
                if failures > failure_threshold:
                    return False
        elif (filter_attr != "year" and filter_attr in builder.number_filters) or modifier in [".gt", ".gte", ".lt", ".lte", ".count_gt", ".count_gte", ".count_lt", ".count_lte"]:
            test_number = []
            if filter_attr in ["channels", "height", "width", "aspect"]:
                test_number = 0
                for media in item.media:
                    attr = getattr(media, filter_actual)
                    if attr and attr > test_number:
                        test_number = attr
            elif filter_attr == "stinger_rating":
                test_number = None
                if item.ratingKey in self.movie_rating_key_map and self.movie_rating_key_map[item.ratingKey] in self.config.mediastingers:
                    test_number = self.config.mediastingers[self.movie_rating_key_map[item.ratingKey]]
            elif filter_attr == "versions":
                test_number = len(item.media)
            elif filter_attr == "audio_language":
                for media in item.media:
                    for part in media.parts:
                        test_number.extend([a.language for a in part.audioStreams()])
            elif filter_attr == "subtitle_language":
                for media in item.media:
                    for part in media.parts:
                        test_number.extend([s.language for s in part.subtitleStreams()])
            elif filter_attr == "duration":
                test_number = getattr(item, filter_actual)
                if test_number:
                    test_number /= 60000
            else:
                test_number = getattr(item, filter_actual)
            if modifier in [".count_gt", ".count_gte", ".count_lt", ".count_lte"]:
                test_number = len(test_number) if test_number else 0
                modifier = f".{modifier[7:]}"
            if test_number is None or util.is_number_filter(test_number, modifier, filter_data):
                return False
        else:
            attrs = []
            if filter_attr in ["resolution", "audio_language", "subtitle_language"]:
                for media in item.media:
                    if filter_attr == "resolution":
                        attrs.append(media.videoResolution)
                    for part in media.parts:
                        if filter_attr == "audio_language":
                            for a in part.audioStreams():
                                attrs.extend([a.language, a.languageCode])
                        if filter_attr == "subtitle_language":
                            for s in part.subtitleStreams():
                                attrs.extend([s.language, s.languageCode])
            elif filter_attr in ["content_rating", "year", "rating"]:
                attrs = [getattr(item, filter_actual)]
            elif filter_attr in ["actor", "country", "director", "genre", "label", "producer", "composer", "writer",
                                 "collection", "network"]:
                attrs = [attr.tag for attr in getattr(item, filter_actual)]
            else:
                raise Failed(f"Filter Error: filter: {filter_final} not supported")
            if modifier == ".regex":
                has_match = False
                for reg in filter_data:
                    pattern = re.compile(reg)
                    for name in attrs:
                        if name is None:
                            continue
                        if pattern.search(str(name)):
                            has_match = True
                            break
                    if has_match:
                        break
                if has_match is False:
                    return False
            elif (not list(set(filter_data) & set(attrs)) and modifier == "") \
                    or (list(set(filter_data) & set(attrs)) and modifier == ".not"):
                return False
        return True
