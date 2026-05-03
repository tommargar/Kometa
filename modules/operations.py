import math
import os
import re
from datetime import datetime, timedelta, timezone

from plexapi.exceptions import NotFound
from plexapi.video import Movie, Show

from modules import anidb, plex, util
from modules.util import Failed, LimitReached

logger = util.logger

meta_operations = [
    "mass_audience_rating_update",
    "mass_user_rating_update",
    "mass_critic_rating_update",
    "mass_episode_audience_rating_update",
    "mass_episode_user_rating_update",
    "mass_episode_critic_rating_update",
    "mass_genre_update",
    "mass_content_rating_update",
    "mass_originally_available_update",
    "mass_added_at_update",
    "mass_original_title_update",
    "mass_poster_update",
    "mass_background_update",
    "mass_studio_update",
]
name_display = {
    "audienceRating": "Audience Rating",
    "rating": "Critic Rating",
    "userRating": "User Rating",
    "CommunityRating": "Community Rating",  # Emby
    "CriticRating": "Tomatometer",  # Emby
    "CustomRating": "Custom Rating",  # Emby
    "originallyAvailableAt": "Originally Available Date",
    "addedAt": "Added At Date",
    "contentRating": "Content Rating",
}


def _item_batches(items_iterable, batch_size):
    for batch_num in range(0, math.ceil(len(items_iterable) / batch_size)):
        yield items_iterable[batch_num * batch_size : (batch_num + 1) * batch_size]


class Operations:
    def __init__(self, config, library):
        self.config = config
        self.library = library

    def run_operations(self):
        # ToDo: Batch critic rating items not counted in total, leadin to (40/10)
        operation_start = datetime.now()
        logger.info("")
        logger.separator(f"{self.library.name} Library Operations")
        logger.info("")
        logger.debug(f"Assets For All: {self.library.assets_for_all}")
        logger.debug(f"Delete Collections: {self.library.delete_collections}")
        logger.debug(f"Show Unmanaged Collections: {self.library.show_unmanaged}")
        logger.debug(f"Show Unconfigured Collections: {self.library.show_unconfigured}")
        logger.debug(f"Mass Genre Update: {self.library.mass_genre_update}")
        logger.debug(f"Mass Audience Rating Update: {self.library.mass_audience_rating_update}")
        logger.debug(f"Mass Critic Rating Update: {self.library.mass_critic_rating_update}")
        logger.debug(f"Mass User Rating Update: {self.library.mass_user_rating_update}")
        logger.debug(f"Mass Episode Audience Rating Update: {self.library.mass_episode_audience_rating_update}")
        logger.debug(f"Mass Episode Critic Rating Update: {self.library.mass_episode_critic_rating_update}")
        logger.debug(f"Mass Episode User Rating Update: {self.library.mass_episode_user_rating_update}")
        logger.debug(f"Mass Content Rating Update: {self.library.mass_content_rating_update}")
        logger.debug(f"Mass Original Title Update: {self.library.mass_original_title_update}")
        logger.debug(f"Mass Originally Available Update: {self.library.mass_originally_available_update}")
        logger.debug(f"Mass Added At Update: {self.library.mass_added_at_update}")
        logger.debug(f"Mass IMDb Parental Labels: {self.library.mass_imdb_parental_labels}")
        logger.debug(f"Mass Poster Update: {self.library.mass_poster_update}")
        logger.debug(f"Mass Background Update: {self.library.mass_background_update}")
        logger.debug(f"Mass Collection Mode Update: {self.library.mass_collection_mode}")
        logger.debug(f"Split Duplicates: {self.library.split_duplicates}")
        logger.debug(f"Radarr Add All Existing: {self.library.radarr_add_all_existing}")
        logger.debug(f"Radarr Remove by Tag: {self.library.radarr_remove_by_tag}")
        logger.debug(f"Sonarr Add All Existing: {self.library.sonarr_add_all_existing}")
        logger.debug(f"Sonarr Remove by Tag: {self.library.sonarr_remove_by_tag}")
        logger.debug(f"Update Blank Track Titles: {self.library.update_blank_track_titles}")
        logger.debug(f"Update Remove Title Parentheses: {self.library.remove_title_parentheses}")
        logger.debug(f"Genre Mapper: {self.library.genre_mapper}")
        logger.debug(f"Content Rating Mapper: {self.library.content_rating_mapper}")
        logger.debug(f"Metadata Backup: {self.library.metadata_backup}")
        logger.debug(f"Item Operation: {self.library.items_library_operation}")
        logger.debug(f"Plex Bulk Edit Batch Size: {self.library.plex_bulk_edit_batch_size}")
        logger.debug("")

        def should_be_deleted(col_in, labels_in, configured_in, managed_in, less_in):
            if all((x is None for x in [configured_in, managed_in, less_in])):
                return False

            less_check = True
            if less_in is not None:
                col_count = col_in.childCount if col_in.childCount is not None else 0
                less_check = col_count < less_in
                logger.trace(f"{col_in.title} - collection size: {col_count} < less: {less_in}, DELETE: {less_check}")

            managed_check = True
            if managed_in is not None:
                is_managed = "PMM" in labels_in or "Kometa" in labels_in
                managed_check = managed_in == is_managed
                logger.trace(f"{col_in.title} - collection managed: {is_managed} vs managed: {managed_in}, DELETE: {managed_check}")

            configured_check = True
            if configured_in is not None:
                is_configured = col_in.title in self.library.collections
                configured_check = configured_in == is_configured
                logger.trace(f"{col_in.title} - collection configured: {is_configured} vs configured: {configured_in}, DELETE: {configured_check}")

            return all((less_check, managed_check, configured_check))

        items = []

        if self.library.split_duplicates:
            items = self.library.search(**{"duplicate": True})
            for item in items:
                item.split()
                logger.info(f"{item.title[:25]:<25} | Splitting")

        if self.library.update_blank_track_titles:
            tracks = self.library.get_all(builder_level="track")
            num_edited = 0
            for i, track in enumerate(tracks, 1):
                logger.ghost(f"Processing Track: {i}/{len(tracks)} {track.title}")
                if not track.title and track.titleSort:
                    # track.editTitle(track.titleSort)
                    self.library.EmbyServer.editItemTitle(track.ratingKey, track.titleSort)

                    num_edited += 1
                    logger.info(f"Track: {track.titleSort} was updated with sort title")
            logger.info(f"{len(tracks)} Tracks Processed; {num_edited} Blank Track Titles Updated")

        if self.library.items_library_operation:
            if self.library.assets_for_all and not self.library.asset_directory:
                logger.error("Asset Error: No Asset Directory for Assets For All")

            items = self.library.get_all()
            total_items = len(items)

            # Lookup-Tabelle aus bereits geladenen nativen Emby-Items für Studio-Check (O(1), kein HTTP-Call)
            _emby_native_by_id = {}
            if hasattr(self.library, "EmbyServer") and hasattr(self.library, "_emby_all_items_native"):
                native_list = self.library._emby_all_items_native or []
                _emby_native_by_id = {str(it.get("Id")): it for it in native_list if isinstance(it, dict) and it.get("Id")}

            radarr_adds = []
            sonarr_adds = []
            label_edits = {"add": {}, "remove": {}}
            rating_edits = {"audienceRating": {}, "rating": {}, "userRating": {}, "CriticRating": {}, "CommunityRating": {}}
            genre_edits = {"add": {}, "remove": {}}
            content_edits = {}
            studio_edits = {}
            date_edits = {"originallyAvailableAt": {}, "addedAt": {}}
            remove_edits = {}
            reset_edits = {}
            lock_edits = {}
            unlock_edits = {}
            ep_rating_edits = {"audienceRating": {}, "rating": {}, "userRating": {}}
            ep_tmdb_id_edits = {}
            ep_remove_edits = {}
            ep_reset_edits = {}
            ep_lock_edits = {}
            ep_unlock_edits = {}

            people_alias_revert = []
            emby_payload = {}  # populated but not yet consumed; kept for future use

            if self.library.mc_type == "emby" and self.library.mass_cast_and_crew_update:
                self.library.EmbyServer.cleanup_stuck_aliases()

            trakt_user_ratings = None

            def trakt_ratings():
                nonlocal trakt_user_ratings
                if trakt_user_ratings is None:
                    trakt_user_ratings = self.config.Trakt.user_ratings(self.library.is_movie)
                if not trakt_user_ratings:
                    raise Failed
                return trakt_user_ratings

            for i, item in enumerate(items, 1):
                logger.info("")
                logger.info(f"({i}/{total_items}) {item.title}")
                # try:
                #     item = self.library.reload(item)
                # except Failed as e:
                #     logger.error(e)
                #     continue
                emby_item = self.library.EmbyServer.get_item(item.ratingKey)

                current_labels = [la.tag for la in self.library.item_labels(item)] if self.library.label_operations else []

                if self.library.assets_for_all and self.library.asset_directory:
                    self.library.find_and_upload_assets(item, current_labels)

                locked_fields = [f.name for f in item.fields if f.locked]

                tmdb_id, tvdb_id, imdb_id = self.library.get_ids(item)
                # Use list instead of string for item edits log
                item_edits = []
                do_cast_update = True

                if self.library.remove_title_parentheses:
                    if not any([f.name == "title" and f.locked for f in item.fields]) and item.title.endswith(")"):
                        new_title = re.sub(" \\(\\w+\\)$", "", item.title)
                        # item.editTitle(new_title)
                        self.library.EmbyServer.editItemTitle(item.ratingKey, new_title)
                        item_edits.append(f"\nUpdated Title: {item.title[:25]:<25} | {new_title}")
                if self.library.mass_imdb_parental_labels:
                    # Emby: not tested
                    try:
                        if self.library.mass_imdb_parental_labels == "remove":
                            parental_labels = []
                        else:
                            parental_guide = self.config.IMDb.parental_guide(imdb_id)
                            parental_labels = [f"{k}:{v}" for k, v in parental_guide.items() if v and v not in util.parental_levels[self.library.mass_imdb_parental_labels]]
                        add_labels = [la for la in parental_labels if la not in current_labels]
                        remove_labels = [la for la in current_labels if la in util.parental_labels and la not in parental_labels]
                        for label_list, edit_type in [(add_labels, "add"), (remove_labels, "remove")]:
                            if label_list:
                                for label in label_list:
                                    if label not in label_edits[edit_type]:
                                        label_edits[edit_type][label] = []
                                    label_edits[edit_type][label].append(item.ratingKey)
                                item_edits.append(f"\n{edit_type.capitalize()} IMDb Parental Labels (Batched) | {', '.join(label_list)}")
                    except Failed:
                        pass
                location_path = None
                if item.locations:
                    first_location = item.locations[0]
                    if isinstance(first_location, str) and first_location:
                        location_path = first_location
                if not location_path:
                    emby_stub_path = getattr(item, "_emby_path", None)
                    if isinstance(emby_stub_path, str) and emby_stub_path:
                        location_path = emby_stub_path
                if not location_path and emby_item:
                    location_path = emby_item.get("Path")
                    if not location_path:
                        media_sources = emby_item.get("MediaSources") or []
                        for source in media_sources:
                            location_path = source.get("Path") or source.get("Name") or source.get("FileName")
                            if location_path:
                                break

                if location_path and not isinstance(location_path, str):
                    try:
                        location_path = os.fspath(location_path)
                    except TypeError:
                        location_path = str(location_path)

                if location_path:
                    path = os.path.dirname(location_path) if self.library.is_movie else location_path
                    if self.library.Radarr and self.library.radarr_add_all_existing and tmdb_id:
                        path = path.replace(self.library.Radarr.plex_path, self.library.Radarr.radarr_path)
                        path = path[:-1] if path.endswith(("/", "\\")) else path
                        radarr_adds.append((tmdb_id, path))
                    if self.library.Sonarr and self.library.sonarr_add_all_existing and tvdb_id:
                        path = path.replace(self.library.Sonarr.plex_path, self.library.Sonarr.sonarr_path)
                        path = path[:-1] if path.endswith(("/", "\\")) else path
                        sonarr_adds.append((tvdb_id, path))

                _trakt_ratings = None

                def trakt_ratings():
                    nonlocal _trakt_ratings
                    if _trakt_ratings is None:
                        _trakt_ratings = self.config.Trakt.user_ratings(self.library.is_movie)
                    if not _trakt_ratings:
                        raise Failed
                    return _trakt_ratings

                _tmdb_obj = None

                def tmdb_obj():
                    nonlocal _tmdb_obj
                    if _tmdb_obj is None:
                        _tmdb_obj = False
                        try:
                            _item = self.config.TMDb.get_item(item, tmdb_id, tvdb_id, imdb_id, is_movie=self.library.is_movie)
                            if _item:
                                _tmdb_obj = _item
                        except Failed as err:
                            logger.error(str(err))
                    if not _tmdb_obj:
                        raise Failed
                    return _tmdb_obj

                _omdb_obj = None

                def omdb_obj():
                    nonlocal _omdb_obj
                    if _omdb_obj is None:
                        _omdb_obj = False
                        if self.config.OMDb.limit is not False:
                            logger.error("Daily OMDb Limit Reached")
                        elif not imdb_id:
                            logger.info(f"No IMDb ID for Guid: {item.guid}")
                        else:
                            try:
                                _omdb_obj = self.config.OMDb.get_omdb(imdb_id)
                            except Failed as err:
                                logger.error(str(err))
                            except Exception:
                                logger.error(f"IMDb ID: {imdb_id}")
                                raise
                    if not _omdb_obj:
                        raise Failed
                    return _omdb_obj

                _tvdb_obj = None

                def tvdb_obj():
                    nonlocal _tvdb_obj
                    if _tvdb_obj is None:
                        _tvdb_obj = False
                        if tvdb_id:
                            try:
                                _tvdb_obj = self.config.TVDb.get_tvdb_obj(tvdb_id, is_movie=self.library.is_movie)
                            except Failed as err:
                                logger.error(str(err))
                        else:
                            logger.info(f"No TVDb ID for Guid: {item.guid}")
                    if not _tvdb_obj:
                        raise Failed
                    return _tvdb_obj

                _mdb_obj = None

                def mdb_obj():
                    nonlocal _mdb_obj
                    if _mdb_obj is None:
                        _mdb_obj = False
                        # Reworked so that the code still gets the data from cache if limit reached
                        # if self.config.MDBList.limit is False:
                        if self.library.is_show and tvdb_id:
                            try:
                                _mdb_obj = self.config.MDBList.get_series(tvdb_id)
                            except LimitReached as err:
                                logger.debug(err)
                            except Failed as err:
                                logger.error(str(err))
                            except Exception:
                                logger.trace(f"TVDb ID: {tvdb_id}")
                                raise
                        if self.library.is_movie and tmdb_id:
                            try:
                                _mdb_obj = self.config.MDBList.get_movie(tmdb_id)
                            except LimitReached as err:
                                logger.debug(err)
                            except Failed as err:
                                logger.error(str(err))
                            except Exception:
                                logger.trace(f"TMDb ID: {tmdb_id}")
                                raise
                        if imdb_id and not _mdb_obj:
                            try:
                                _mdb_obj = self.config.MDBList.get_imdb(imdb_id)
                            except LimitReached as err:
                                logger.debug(err)
                            except Failed as err:
                                logger.error(str(err))
                            except Exception:
                                logger.trace(f"IMDb ID: {imdb_id}")
                                raise
                        if not _mdb_obj:
                            logger.warning(f"No MdbItem for {item.title} (Guid: {item.guid})")
                    if not _mdb_obj:
                        raise Failed
                    return _mdb_obj

                anidb_id = None

                def get_anidb_id():
                    temp_id = self.config.Convert.ids_to_anidb(self.library, item.ratingKey, tvdb_id, imdb_id, tmdb_id)
                    return temp_id if temp_id else False

                _anidb_obj = None

                def anidb_obj():
                    nonlocal anidb_id, _anidb_obj
                    if _anidb_obj is None:
                        _anidb_obj = False
                        if anidb_id is None:
                            anidb_id = get_anidb_id()
                        if anidb_id:
                            try:
                                _anidb_obj = self.config.AniDB.get_anime(anidb_id)
                            except Failed as err:
                                logger.error(str(err))
                        else:
                            logger.warning(f"No AniDB ID for Guid: {item.guid}")
                    if not _anidb_obj:
                        raise Failed
                    return _anidb_obj

                _mal_obj = None

                def mal_obj():
                    nonlocal anidb_id, _mal_obj
                    if _mal_obj is None:
                        _mal_obj = False
                        if anidb_id is None:
                            anidb_id = get_anidb_id()
                        mal_id = None
                        if item.ratingKey in self.library.reverse_mal:
                            mal_id = self.library.reverse_mal[item.ratingKey]
                        elif not anidb_id:
                            logger.warning(f"Convert Warning: No AniDB ID to Convert to MyAnimeList ID for Guid: {item.guid}")
                        else:
                            try:
                                mal_id = self.config.Convert.anidb_to_mal(anidb_id)
                            except Failed as err:
                                logger.warning(f"{err} of Guid: {item.guid}")
                        if mal_id:
                            try:
                                _mal_obj = self.config.MyAnimeList.get_anime(mal_id)
                            except Failed as err:
                                logger.error(str(err))
                    if not _mal_obj:
                        raise Failed
                    return _mal_obj

                for attribute, item_attr in [(self.library.mass_audience_rating_update, "audienceRating"), (self.library.mass_critic_rating_update, "rating"), (self.library.mass_user_rating_update, "userRating")]:
                    if attribute:
                        current = getattr(item, item_attr)
                        for option in attribute:
                            if option in ["lock", "remove"]:
                                if option == "remove" and current:
                                    if item_attr not in remove_edits:
                                        remove_edits[item_attr] = []
                                    remove_edits[item_attr].append(item.ratingKey)
                                    item_edits.append(f"Remove {name_display[item_attr]} (Batched)")
                                elif item_attr not in locked_fields:
                                    if item_attr not in lock_edits:
                                        lock_edits[item_attr] = []
                                    lock_edits[item_attr].append(item.ratingKey)
                                    item_edits.append(f"Lock {name_display[item_attr]} (Batched)")
                                break
                            elif option in ["unlock", "reset"]:
                                if option == "reset" and current:
                                    if item_attr not in reset_edits:
                                        reset_edits[item_attr] = []
                                    reset_edits[item_attr].append(item.ratingKey)
                                    item_edits.append(f"Reset {name_display[item_attr]} (Batched)")
                                elif item_attr in locked_fields:
                                    if item_attr not in unlock_edits:
                                        unlock_edits[item_attr] = []
                                    unlock_edits[item_attr].append(item.ratingKey)
                                    item_edits.append(f"Unlock {name_display[item_attr]} (Batched)")
                                break
                            else:
                                try:
                                    if option == "tmdb":
                                        found_rating = tmdb_obj().vote_average  # noqa
                                    elif option == "imdb":
                                        found_rating = self.config.IMDb.get_rating(imdb_id)
                                    elif option == "trakt":
                                        found_rating = self.config.Trakt.get_rating(imdb_id, self.library.is_movie)
                                    elif option == "trakt_user":
                                        _ratings = trakt_ratings()
                                        _id = tmdb_id if self.library.is_movie else tvdb_id
                                        if _id in _ratings:
                                            found_rating = _ratings[_id]
                                        else:
                                            raise Failed
                                    elif str(option).startswith("plex"):
                                        ratings = self.library.get_ratings(item)
                                        try:
                                            found_rating = ratings[option]  # noqa
                                        except KeyError:
                                            found_rating = None
                                    elif str(option).startswith("omdb"):
                                        omdb_item = omdb_obj()
                                        if option == "omdb_metascore":
                                            found_rating = omdb_item.metacritic_rating / 10 if omdb_item.metacritic_rating else None  # noqa
                                        elif option == "omdb_tomatoes":
                                            found_rating = omdb_item.rotten_tomatoes / 10 if omdb_item.rotten_tomatoes else None  # noqa
                                        else:
                                            found_rating = omdb_item.imdb_rating  # noqa
                                    elif str(option).startswith("mdb"):
                                        mdb_item = mdb_obj()
                                        if option == "mdb_average":
                                            found_rating = mdb_item.average / 10 if mdb_item.average else None  # noqa
                                        elif option == "mdb_imdb":
                                            found_rating = mdb_item.imdb_rating if mdb_item.imdb_rating else None  # noqa
                                        elif option == "mdb_metacritic":
                                            found_rating = mdb_item.metacritic_rating / 10 if mdb_item.metacritic_rating else None  # noqa
                                        elif option == "mdb_metacriticuser":
                                            found_rating = mdb_item.metacriticuser_rating if mdb_item.metacriticuser_rating else None  # noqa
                                        elif option == "mdb_trakt":
                                            found_rating = mdb_item.trakt_rating / 10 if mdb_item.trakt_rating else None  # noqa
                                        elif option == "mdb_tomatoes":
                                            found_rating = mdb_item.tomatoes_rating / 10 if mdb_item.tomatoes_rating else None  # noqa
                                        elif option == "mdb_tomatoesaudience":
                                            found_rating = mdb_item.tomatoesaudience_rating / 10 if mdb_item.tomatoesaudience_rating else None  # noqa
                                        elif option == "mdb_tmdb":
                                            found_rating = mdb_item.tmdb_rating / 10 if mdb_item.tmdb_rating else None  # noqa
                                        elif option == "mdb_letterboxd":
                                            found_rating = mdb_item.letterboxd_rating * 2 if mdb_item.letterboxd_rating else None  # noqa
                                        elif option == "mdb_myanimelist":
                                            found_rating = mdb_item.myanimelist_rating if mdb_item.myanimelist_rating else None  # noqa
                                        else:
                                            found_rating = mdb_item.score / 10 if mdb_item.score else None  # noqa
                                    elif option == "anidb_rating":
                                        found_rating = anidb_obj().rating  # noqa
                                    elif option == "anidb_average":
                                        found_rating = anidb_obj().average  # noqa
                                    elif option == "anidb_score":
                                        found_rating = anidb_obj().score  # noqa
                                    elif option == "mal":
                                        found_rating = mal_obj().score  # noqa
                                    else:
                                        found_rating = option
                                    if not found_rating:
                                        logger.info(f"No {option} {name_display[item_attr]} Found")
                                        raise Failed
                                    multiplier = 10 if item_attr == "rating" else 1
                                    if item_attr == "rating":
                                        found_rating = f"{(float(found_rating) * multiplier)}"
                                    found_rating = f"{float(found_rating):.1f}" if item_attr != "rating" else str(int(float(found_rating)))
                                    # found_rating = f"{found_rating:.1f}"
                                    try:
                                        current *= multiplier
                                    except:
                                        pass
                                    if current is not None and str(f"{current}") == str(f"{float(found_rating):.1f}"):
                                        continue
                                    rating_key = item_attr
                                    payload_key = rating_key
                                    log_display_key = item_attr
                                    match item_attr:
                                        case "rating":
                                            payload_key = "CriticRating"
                                        case "userRating":
                                            payload_key = "ProviderIds"
                                        case "audienceRating":
                                            payload_key = "CommunityRating"
                                    # Plex
                                    # {'audienceRating': {'6.7': [2391406], '7.3': [2391407]},
                                    #  'rating': {'5.8': [2391406]}, 'userRating': {}}

                                    # Emby
                                    # 'CommunityRating' = {float}
                                    # 7.402
                                    # 'CustomRating' = {str}
                                    # '6.8'
                                    # 'CriticRating' = {int}
                                    # 69
                                    if rating_key not in rating_edits:
                                        rating_edits[rating_key] = {}
                                    if found_rating not in rating_edits[rating_key]:
                                        rating_edits[rating_key][found_rating] = []
                                    rating_edits[rating_key][found_rating].append(item.ratingKey)
                                    if item.ratingKey not in emby_payload:
                                        emby_payload[item.ratingKey] = {}
                                    if payload_key == "ProviderIds":
                                        provider_key = self.library.EmbyServer.CUSTOM_RATING_PROVIDER
                                        provider_ids = emby_payload[item.ratingKey].setdefault("ProviderIds", {})
                                        provider_ids[provider_key] = found_rating
                                    elif payload_key:
                                        emby_payload[item.ratingKey][payload_key] = found_rating
                                    display_label = name_display.get(log_display_key, log_display_key)
                                    item_edits.append(f"Update {display_label} (Batched) | {found_rating}")
                                    do_cast_update = True
                                    # break
                                except Failed:
                                    continue

                if self.library.mass_genre_update or self.library.genre_mapper:
                    new_genres = []
                    extra_option = None
                    if self.library.mass_genre_update:
                        # Flatten list of lists from config
                        options = [item for sublist in self.library.mass_genre_update for item in (sublist if isinstance(sublist, list) else [sublist])]
                        merge_genres = "merge" in options
                        for option in options:
                            if option == "merge":
                                continue
                            if option in ["lock", "unlock", "remove", "reset"]:
                                extra_option = option
                                break
                            try:
                                temp_genres = []
                                if option == "tmdb":
                                    temp_genres = tmdb_obj().genres  # noqa
                                elif option == "imdb":
                                    temp_genres = self.config.IMDb.get_genres(imdb_id)
                                elif option == "omdb":
                                    temp_genres = omdb_obj().genres  # noqa
                                elif option == "tvdb":
                                    temp_genres = tvdb_obj().genres  # noqa
                                elif str(option) in anidb.weights:
                                    temp_genres = [str(t).title() for t, w in anidb_obj().tags.items() if w >= anidb.weights[str(option)]]  # noqa
                                elif option == "mal":
                                    temp_genres = mal_obj().genres  # noqa
                                elif option == "mal_all":
                                    temp_genres = mal_obj().genres + mal_obj().explicit_genres + mal_obj().themes + mal_obj().demographics
                                else:
                                    # BUGFIX: Ensure single string options are treated as a list containing that string
                                    temp_genres = [option]
                                if temp_genres:
                                    temp_genres = [g for g in temp_genres if str(g).strip()]
                                if not temp_genres:
                                    logger.info(f"No {option} Genres Found")
                                    raise Failed
                                new_genres.extend(temp_genres)
                                if not merge_genres:
                                    break
                            except Failed:
                                continue
                        if merge_genres and new_genres:
                            new_genres = list(dict.fromkeys(new_genres))

                    item_genres = emby_item.get("Genres", []) if emby_item else []
                    if not new_genres and extra_option not in ["remove", "reset"]:
                        new_genres = item_genres
                    if self.library.genre_mapper:
                        mapped_genres = []
                        for genre in new_genres:
                            if genre in self.library.genre_mapper:
                                if self.library.genre_mapper[genre]:
                                    mapped_genres.append(self.library.genre_mapper[genre])
                            else:
                                mapped_genres.append(genre)
                        new_genres = mapped_genres

                    if new_genres:
                        new_genres = list(dict.fromkeys(new_genres))

                    item_genres_lower = {g.lower() for g in item_genres}
                    new_genres_lower = {g.lower() for g in new_genres}
                    _add = [g for g in new_genres if g.lower() not in item_genres_lower]
                    _remove = [g for g in item_genres if g.lower() not in new_genres_lower]

                    for genre_list, edit_type in [(_add, "add"), (_remove, "remove")]:
                        if genre_list:
                            for g in genre_list:
                                if g not in genre_edits[edit_type]:
                                    genre_edits[edit_type][g] = []
                                genre_edits[edit_type][g].append(item.ratingKey)
                            item_edits.append(f"{edit_type.capitalize()} Genres (Batched) | {', '.join(genre_list)}")
                    if extra_option in ["unlock", "reset"] and ("genre" in locked_fields or _add or _remove):
                        if "genre" not in unlock_edits:
                            unlock_edits["genre"] = []
                        unlock_edits["genre"].append(item.ratingKey)
                        item_edits.append("Unlock Genre (Batched)")
                    elif extra_option in ["lock", "remove"] and "genre" not in locked_fields and not _add and not _remove:
                        if "genre" not in lock_edits:
                            lock_edits["genre"] = []
                        lock_edits["genre"].append(item.ratingKey)
                        item_edits.append("Lock Genre (Batched)")

                    if new_genres != item_genres:
                        my_new_data = {"Genres": new_genres, "GenreItems": new_genres}
                        if item.ratingKey not in emby_payload:
                            emby_payload[item.ratingKey] = my_new_data
                        else:
                            emby_payload[item.ratingKey].update(my_new_data)

                        do_cast_update = True

                if self.library.mass_content_rating_update or self.library.content_rating_mapper:
                    new_rating = None
                    extra_option = None
                    if self.library.mass_content_rating_update:
                        for option in self.library.mass_content_rating_update:
                            if option in ["lock", "unlock", "remove", "reset"]:
                                extra_option = option
                                break
                            try:
                                if option == "omdb":
                                    new_rating = omdb_obj().content_rating  # noqa
                                elif option == "mdb":
                                    _rating = mdb_obj().content_rating  # noqa
                                    new_rating = _rating if _rating else None
                                elif str(option).startswith("mdb_commonsense"):
                                    _rating = mdb_obj().commonsense  # noqa
                                    if not _rating:
                                        new_rating = None
                                    elif option == "mdb_commonsense0":
                                        new_rating = str(_rating).rjust(2, "0")
                                    else:
                                        new_rating = _rating
                                elif str(option).startswith("mdb_age_rating"):
                                    _rating = mdb_obj().age_rating  # noqa
                                    if not _rating:
                                        new_rating = None
                                    elif option == "mdb_age_rating0":
                                        new_rating = str(_rating).rjust(2, "0")
                                    else:
                                        new_rating = _rating
                                elif option == "mal":
                                    new_rating = mal_obj().rating  # noqa
                                else:
                                    new_rating = option
                                if new_rating is None:
                                    logger.info(f"No {option} Content Rating Found")
                                    raise Failed
                                else:
                                    new_rating = str(new_rating)
                                    break
                            except Failed:
                                continue

                    is_none = False
                    do_lock = False
                    do_unlock = False
                    current_rating = item.contentRating
                    if not new_rating:
                        new_rating = current_rating
                    if self.library.content_rating_mapper:
                        if new_rating in self.library.content_rating_mapper:
                            new_rating = self.library.content_rating_mapper[new_rating]
                            if not new_rating:
                                is_none = True
                    if extra_option == "reset":
                        if current_rating:
                            if "contentRating" not in reset_edits:
                                reset_edits["contentRating"] = []
                            reset_edits["contentRating"].append(item.ratingKey)
                            item_edits.append("Reset Content Rating (Batched)")
                        elif "contentRating" in locked_fields:
                            do_unlock = True
                    elif extra_option == "remove" or is_none:
                        if current_rating:
                            if "contentRating" not in remove_edits:
                                remove_edits["contentRating"] = []
                            remove_edits["contentRating"].append(item.ratingKey)
                            item_edits.append("Remove Content Rating (Batched)")
                        elif "contentRating" not in locked_fields:
                            do_lock = True
                    elif new_rating and new_rating != current_rating:
                        if new_rating not in content_edits:
                            content_edits[new_rating] = []
                        content_edits[new_rating].append(item.ratingKey)
                        item_edits.append(f"Update Content Rating (Batched) | {new_rating}")
                        do_lock = False

                    if extra_option == "lock" or do_lock:
                        if "contentRating" not in lock_edits:
                            lock_edits["contentRating"] = []
                        lock_edits["contentRating"].append(item.ratingKey)
                        item_edits.append("\nLock Content Rating (Batched)")
                    elif extra_option == "unlock" or do_unlock:
                        if "contentRating" not in unlock_edits:
                            unlock_edits["contentRating"] = []
                        unlock_edits["contentRating"].append(item.ratingKey)
                        item_edits.append("Unlock Content Rating (Batched)")

                if self.library.mass_original_title_update:
                    current_original = item.originalTitle
                    for option in self.library.mass_original_title_update:
                        if option in ["lock", "remove"]:
                            if option == "remove" and current_original:
                                if "originalTitle" not in remove_edits:
                                    remove_edits["originalTitle"] = []
                                remove_edits["originalTitle"].append(item.ratingKey)
                                item_edits.append("Remove Original Title (Batched)")
                            elif "originalTitle" not in locked_fields:
                                if "originalTitle" not in lock_edits:
                                    lock_edits["originalTitle"] = []
                                lock_edits["originalTitle"].append(item.ratingKey)
                                item_edits.append("Lock Original Title (Batched)")
                            break
                        elif option in ["unlock", "reset"]:
                            if option == "reset" and current_original:
                                if "originalTitle" not in reset_edits:
                                    reset_edits["originalTitle"] = []
                                reset_edits["originalTitle"].append(item.ratingKey)
                                item_edits.append("Reset Original Title (Batched)")
                            elif "originalTitle" in locked_fields:
                                if "originalTitle" not in unlock_edits:
                                    unlock_edits["originalTitle"] = []
                                unlock_edits["originalTitle"].append(item.ratingKey)
                                item_edits.append("Unlock Original Title (Batched)")
                            break
                        else:
                            try:
                                if option == "anidb":
                                    new_original_title = anidb_obj().main_title  # noqa
                                elif option == "anidb_official":
                                    new_original_title = anidb_obj().official_title  # noqa
                                elif option == "mal":
                                    new_original_title = mal_obj().title  # noqa
                                elif option == "mal_english":
                                    new_original_title = mal_obj().title_english  # noqa
                                elif option == "mal_japanese":
                                    new_original_title = mal_obj().title_japanese  # noqa
                                else:
                                    new_original_title = option
                                if not new_original_title:
                                    logger.info(f"No {option} Original Title Found")
                                    raise Failed
                                if str(current_original) != str(new_original_title):
                                    item.editOriginalTitle(new_original_title)
                                    item_edits.append(f"Updated Original Title | {new_original_title}")
                                break
                            except Failed:
                                continue

                if self.library.mass_studio_update:
                    current_studio = item.studio
                    current_studio_str = str(current_studio).strip() if current_studio else ""
                    current_studio_names = set()
                    native = _emby_native_by_id.get(str(item.ratingKey))
                    if native:
                        for s in native.get("Studios") or []:
                            n = s.get("Name") if isinstance(s, dict) else s
                            if n:
                                current_studio_names.add(str(n).strip().lower())
                    for option in self.library.mass_studio_update:
                        if option in ["lock", "remove"]:
                            if option == "remove" and current_studio:
                                if "studio" not in remove_edits:
                                    remove_edits["studio"] = []
                                remove_edits["studio"].append(item.ratingKey)
                                item_edits.append("\nRemove Studio (Batched)")
                            elif "studio" not in locked_fields:
                                if "studio" not in lock_edits:
                                    lock_edits["studio"] = []
                                lock_edits["studio"].append(item.ratingKey)
                                item_edits.append("\nLock Studio (Batched)")
                            break
                        elif option in ["unlock", "reset"]:
                            if option == "reset" and current_studio:
                                if "studio" not in reset_edits:
                                    reset_edits["studio"] = []
                                reset_edits["studio"].append(item.ratingKey)
                                item_edits.append("\nReset Studio (Batched)")
                            elif "studio" in locked_fields:
                                if "studio" not in unlock_edits:
                                    unlock_edits["studio"] = []
                                unlock_edits["studio"].append(item.ratingKey)
                                item_edits.append("\nUnlock Studio (Batched)")
                            break
                        else:
                            try:
                                if option == "tmdb":
                                    new_studio = tmdb_obj().studio  # noqa
                                elif option == "anidb":
                                    new_studio = anidb_obj().studio  # noqa
                                elif option == "mal":
                                    new_studio = mal_obj().studio  # noqa
                                else:
                                    new_studio = option
                                if not new_studio:
                                    logger.info(f"No {option} Studio Found")
                                    raise Failed
                                new_studio_str = str(new_studio).strip()
                                new_studio_lower = new_studio_str.lower()
                                already_set = current_studio_str.lower() == new_studio_lower or new_studio_lower in current_studio_names
                                if not already_set:
                                    if new_studio not in studio_edits:
                                        studio_edits[new_studio] = []
                                    studio_edits[new_studio].append(item.ratingKey)
                                    item_edits.append(f"Update Studio (Batched) | {new_studio}")
                                break
                            except Failed:
                                continue

                for attribute, item_attr in [(self.library.mass_originally_available_update, "originallyAvailableAt"), (self.library.mass_added_at_update, "addedAt")]:
                    if attribute:
                        current = getattr(item, item_attr)
                        if current:
                            current = current.strftime("%Y-%m-%d")
                        for option in attribute:
                            if option in ["lock", "remove"]:
                                if option == "remove" and current:
                                    if item_attr not in remove_edits:
                                        remove_edits[item_attr] = []
                                    remove_edits[item_attr].append(item.ratingKey)
                                    item_edits.append(f"\nRemove {name_display[item_attr]} (Batched)")
                                elif item_attr not in locked_fields:
                                    if item_attr not in lock_edits:
                                        lock_edits[item_attr] = []
                                    lock_edits[item_attr].append(item.ratingKey)
                                    item_edits.append(f"\nLock {name_display[item_attr]} (Batched)")
                                break
                            elif option in ["unlock", "reset"]:
                                if option == "reset" and current:
                                    if item_attr not in reset_edits:
                                        reset_edits[item_attr] = []
                                    reset_edits[item_attr].append(item.ratingKey)
                                    item_edits.append(f"\nReset {name_display[item_attr]} (Batched)")
                                elif item_attr in locked_fields:
                                    if item_attr not in unlock_edits:
                                        unlock_edits[item_attr] = []
                                    unlock_edits[item_attr].append(item.ratingKey)
                                    item_edits.append(f"\nUnlock {name_display[item_attr]} (Batched)")
                                break
                            else:
                                try:
                                    if option == "tmdb":
                                        new_date = tmdb_obj().release_date if self.library.is_movie else tmdb_obj().first_air_date  # noqa
                                    elif option == "omdb":
                                        new_date = omdb_obj().released  # noqa
                                    elif option == "tvdb":
                                        new_date = tvdb_obj().release_date  # noqa
                                    elif option == "mdb":
                                        new_date = mdb_obj().released  # noqa
                                    elif option == "mdb_digital":
                                        new_date = mdb_obj().released_digital  # noqa
                                    elif option == "anidb":
                                        new_date = anidb_obj().released  # noqa
                                    elif option == "mal":
                                        new_date = mal_obj().aired  # noqa
                                    else:
                                        new_date = option
                                    if not new_date:
                                        logger.info(f"No {option} {name_display[item_attr]} Found")
                                        raise Failed
                                    new_date = new_date.strftime("%Y-%m-%d")
                                    if current != new_date:
                                        if new_date not in date_edits[item_attr]:
                                            date_edits[item_attr][new_date] = []
                                        date_edits[item_attr][new_date].append(item.ratingKey)
                                        item_edits.append(f"Update {name_display[item_attr]} (Batched) | {new_date}")
                                    break
                                except Failed:
                                    continue
                if self.library.mc_type == "emby" and self.library.mass_cast_and_crew_update and tmdb_id:
                    try:
                        tmdb_item = tmdb_obj()
                    except Failed:
                        tmdb_item = None

                    if tmdb_item and emby_item is not None:

                        def contains_non_latin(text: str, allow_greek=False, allow_cyrillic=False) -> bool:
                            """True, wenn der Text Zeichen enthält, die nicht in lateinischen/erlaubten Blöcken liegen."""
                            import unicodedata

                            if not text:
                                return False
                            for ch in text:
                                cp = ord(ch)
                                # Basic Latin + Latin-1 + Latin Extended
                                if 0x0000 <= cp <= 0x024F or 0x1E00 <= cp <= 0x1EFF or 0x2C60 <= cp <= 0x2C7F or 0xA720 <= cp <= 0xA7FF or 0xAB30 <= cp <= 0xAB6F:
                                    continue
                                # Ziffern, Leerzeichen, Satzzeichen
                                if ch.isdigit() or ch.isspace() or unicodedata.category(ch).startswith("P"):
                                    continue
                                # Optional: Griechisch
                                if allow_greek and (0x0370 <= cp <= 0x03FF or 0x1F00 <= cp <= 0x1FFF):
                                    continue
                                # Optional: Kyrillisch
                                if allow_cyrillic and (0x0400 <= cp <= 0x04FF or 0x0500 <= cp <= 0x052F or 0x2DE0 <= cp <= 0x2DFF or 0xA640 <= cp <= 0xA69F):
                                    continue
                                return True
                            return False

                        tmdb_title = tmdb_item.title
                        emby_title = emby_item.get("Name")

                        all_cast_string = "".join([c.get("name", "") for c in tmdb_item.cast]) if tmdb_item.cast else ""
                        if contains_non_latin(tmdb_title) or contains_non_latin(all_cast_string):
                            #     #     pass
                            #     #     # ToDo: need to ensure EN title if lang from config tmdb has no translation
                            _tmdb_obj = None
                            tmdb_item = tmdb_obj(ignore_cache=True)
                            tmdb_title = tmdb_item.title

                        my_cast = tmdb_item.cast
                        my_crew = tmdb_item.crew

                        # This will get the title in the language set in config -> tmdb
                        # with foreign titles, emby will only scrape the original title;
                        # as I don't speak Chinese, I want EN title

                        if tmdb_id == "46043":
                            pass

                        if tmdb_title != emby_title:

                            if contains_non_latin(emby_title) and not contains_non_latin(tmdb_title):
                                edits = {"Name": tmdb_title}
                                self.library.EmbyServer.update_item(emby_item.get("Id"), edits)
                                item_edits.append(f"Changed title from '{emby_title}' to '{tmdb_title}'")
                                pass
                        # ToDo: needs good trigger + option
                        if my_cast:
                            # try:
                            # has_edits, people_edits = emby_people.sync_people(emby_item, my_cast, my_crew)

                            has_edits, people_edits, new_people_renames = self.library.EmbyServer.sync_people(emby_item, my_cast, my_crew)
                            if has_edits:
                                item_edits.append(people_edits)
                            if new_people_renames:
                                people_alias_revert.extend(new_people_renames)

                        # except Exception as e:
                        #     logger.error(e)
                        #     pass
                    # Title and case updates end

                    # tick("Emby genres updated", min_ms=5)

                if len(item_edits) > 0:
                    # logger.info(f"{item_edits[1:]}")
                    logger.info(f"{item_edits}")
                else:
                    logger.info("No Item Edits")

                if self.library.mass_poster_update or self.library.mass_background_update:
                    try:
                        new_poster, new_background, logo, item_dir, name = self.library.find_item_assets(item)
                    except Failed:
                        new_poster, new_background, logo, item_dir, name = None, None, None, None, None
                    try:
                        tmdb_item = tmdb_obj()
                    except Failed:
                        tmdb_item = None

                    if self.library.mass_poster_update:
                        source = self.library.mass_poster_update["source"]
                        ignore_locked = self.library.mass_poster_update["ignore_locked"]
                        ignore_overlays = self.library.mass_poster_update.get("ignore_overlays")
                        thumb_locked = any(f.name == "thumb" and f.locked for f in item.fields)
                        # labels same as above but without None check
                        labels = current_labels  # [la.tag for la in self.library.item_labels(item)]
                        has_overlay_label = "Overlay" in labels

                        # Bypass ignore_locked and ignore_overlays checks if the source is "unlock" or "lock"
                        if source in ["unlock", "lock"]:
                            self.library.poster_update(item, new_poster, tmdb=tmdb_item.poster_url if tmdb_item else None, title=item.title)  # noqa
                        elif ignore_locked and thumb_locked:
                            # Skip processing if ignore_locked is True and thumb is locked
                            pass
                        elif ignore_overlays and has_overlay_label:
                            # Skip processing if ignore_overlays is True and Overlay label is found
                            pass
                        else:
                            self.library.poster_update(item, new_poster, tmdb=tmdb_item.poster_url if tmdb_item else None, title=item.title)  # noqa

                    if self.library.mass_background_update:
                        source = self.library.mass_background_update["source"]
                        ignore_locked = self.library.mass_background_update["ignore_locked"]
                        ignore_overlays = self.library.mass_background_update["ignore_overlays"]
                        art_locked = any(f.name == "art" and f.locked for f in item.fields)

                        if source in ["unlock", "lock"]:
                            self.library.background_update(item, new_background, tmdb=tmdb_item.backdrop_url if tmdb_item else None, title=item.title)  # noqa

                        elif not (ignore_locked and art_locked):
                            self.library.background_update(item, new_background, tmdb=tmdb_item.backdrop_url if tmdb_item else None, title=item.title)  # noqa

                    if self.library.is_show and (
                        (self.library.mass_poster_update and (self.library.mass_poster_update["seasons"] or self.library.mass_poster_update["episodes"]))
                        or (self.library.mass_background_update and (self.library.mass_background_update["seasons"] or self.library.mass_background_update["episodes"]))
                    ):
                        real_show = None
                        try:
                            real_show = tmdb_item.load_show() if tmdb_item else None  # noqa
                        except Failed as e:
                            logger.error(e)
                        tmdb_seasons = {s.season_number: s for s in real_show.seasons} if real_show else {}
                        for season in self.library.get_seasons(item):
                            if (self.library.mass_poster_update and self.library.mass_poster_update["seasons"]) or (self.library.mass_background_update and self.library.mass_background_update["seasons"]):
                                try:
                                    season_poster, season_background, _, _, _ = self.library.find_item_assets(season, item_asset_directory=item_dir, folder_name=name)
                                except Failed:
                                    season_poster = None
                                    season_background = None
                                season_title = f"S{season.seasonNumber} {season.title}"
                                tmdb_poster = tmdb_seasons[season.seasonNumber].poster_url if season.seasonNumber in tmdb_seasons else None
                                if self.library.mass_poster_update and self.library.mass_poster_update["seasons"]:
                                    self.library.poster_update(season, season_poster, tmdb=tmdb_poster, title=season_title if season else None)
                                if self.library.mass_background_update and self.library.mass_background_update["seasons"]:
                                    self.library.background_update(season, season_background, title=season_title if season else None)

                            if (self.library.mass_poster_update and self.library.mass_poster_update["episodes"]) or (self.library.mass_background_update and self.library.mass_background_update["episodes"]):
                                tmdb_episodes = {}
                                if season.seasonNumber in tmdb_seasons:
                                    for episode in tmdb_seasons[season.seasonNumber].episodes:
                                        episode._partial = False
                                        try:
                                            tmdb_episodes[episode.episode_number] = episode
                                        except NotFound:
                                            logger.error(f"TMDb Error: An Episode of Season {season.seasonNumber} was Not Found")

                                for episode in self.library.get_episodes(season):
                                    try:
                                        episode = self.library.reload(episode)
                                    except Failed:
                                        logger.error(f"S{season.seasonNumber}E{episode.episodeNumber} {episode.title} Failed to Reload from Plex")
                                        continue
                                    try:
                                        episode_poster, episode_background, _, _, _ = self.library.find_item_assets(episode, item_asset_directory=item_dir, folder_name=name)
                                    except Failed:
                                        episode_poster = None
                                        episode_background = None
                                    episode_title = f"S{season.seasonNumber}E{episode.episodeNumber} {episode.title}"
                                    tmdb_poster = tmdb_episodes[episode.episodeNumber].still_url if episode.episodeNumber in tmdb_episodes else None
                                    if self.library.mass_poster_update and self.library.mass_poster_update["episodes"]:
                                        self.library.poster_update(episode, episode_poster, tmdb=tmdb_poster, title=episode_title if episode else None)
                                    if self.library.mass_background_update and self.library.mass_background_update["episodes"]:
                                        self.library.background_update(episode, episode_background, title=episode_title if episode else None)

                episode_ops = [(self.library.mass_episode_audience_rating_update, "audienceRating"), (self.library.mass_episode_critic_rating_update, "rating"), (self.library.mass_episode_user_rating_update, "userRating")]

                if any([x is not None for x, _ in episode_ops]):

                    if any(["imdb" in x for x, _ in episode_ops if x]) and not imdb_id:
                        logger.info(f"No IMDb ID for Guid: {item.guid}")

                    all_episodes = []
                    if self.library.EmbyServer:
                        for season in self.library.get_seasons(item):
                            all_episodes.extend(self.library.get_episodes(season))
                    else:
                        all_episodes = item.episodes()

                    for ep in all_episodes:
                        item_edits = []
                        ep = self.library.reload(ep)
                        item_title = self.library.get_item_display_title(ep)
                        logger.info("")
                        logger.info(f"{item_title}")

                        for attribute, item_attr in episode_ops:
                            if attribute:
                                current = getattr(ep, item_attr)
                                for option in attribute:
                                    if option in ["lock", "remove"]:
                                        if option == "remove" and current:
                                            if item_attr not in ep_remove_edits:
                                                ep_remove_edits[item_attr] = []
                                            ep_remove_edits[item_attr].append(ep)
                                            item_edits.append(f"\nRemove {name_display[item_attr]} (Batched)")
                                        elif item_attr not in locked_fields:
                                            if item_attr not in ep_lock_edits:
                                                ep_lock_edits[item_attr] = []
                                            ep_lock_edits[item_attr].append(ep)
                                            item_edits.append(f"\nLock {name_display[item_attr]} (Batched)")
                                        break
                                    elif option in ["unlock", "reset"]:
                                        if option == "reset" and current:
                                            if item_attr not in ep_reset_edits:
                                                ep_reset_edits[item_attr] = []
                                            ep_reset_edits[item_attr].append(ep)
                                            item_edits.append(f"\nReset {name_display[item_attr]} (Batched)")
                                        elif item_attr in locked_fields:
                                            if item_attr not in ep_unlock_edits:
                                                ep_unlock_edits[item_attr] = []
                                            ep_unlock_edits[item_attr].append(ep)
                                            item_edits.append(f"\nUnlock {name_display[item_attr]} (Batched)")
                                        break
                                    else:
                                        try:
                                            try:
                                                tmdb_item = tmdb_obj()
                                            except Failed:
                                                tmdb_item = None
                                            found_rating = None
                                            if str(option).startswith("plex"):
                                                ratings = self.library.get_ratings(ep)
                                                try:
                                                    found_rating = ratings[option]  # noqa
                                                except KeyError:
                                                    found_rating = None
                                            if tmdb_item and option == "tmdb":
                                                try:
                                                    found_rating = self.config.TMDb.get_episode(tmdb_item.tmdb_id, ep.seasonNumber, ep.episodeNumber).vote_average  # noqa
                                                except Failed as er:
                                                    logger.error(er)
                                            elif imdb_id and option == "imdb":
                                                found_rating = self.config.IMDb.get_episode_rating(imdb_id, ep.seasonNumber, ep.episodeNumber)
                                            elif imdb_id and option == "trakt":
                                                found_rating = self.config.Trakt.get_episode_rating(imdb_id, ep.seasonNumber, ep.episodeNumber)
                                            else:
                                                try:
                                                    found_rating = float(option)
                                                except ValueError:
                                                    pass
                                            if not found_rating:
                                                logger.info(f"  No {option} {name_display[item_attr]} Found")
                                                raise Failed
                                            multiplier = 10 if item_attr == "rating" else 1
                                            if item_attr == "rating":
                                                found_rating = f"{(float(found_rating) * multiplier)}"
                                            found_rating = f"{float(found_rating):.1f}" if item_attr != "rating" else str(int(float(found_rating)))
                                            if str(current) != found_rating:
                                                if found_rating not in ep_rating_edits[item_attr]:
                                                    ep_rating_edits[item_attr][found_rating] = []
                                                ep_rating_edits[item_attr][found_rating].append(ep)
                                                item_edits.append(f"Update {name_display[item_attr]} (Batched) | {found_rating}")
                                            break
                                        except Failed:
                                            continue

                        if len(item_edits) > 0:
                            # Output list as lines
                            logger.info("Item Edits: %s", "\n".join(item_edits))

            logger.info("")
            logger.separator("Batch Updates", space=False, border=False)
            logger.info("")

            self.library.apply_batch_operations(
                label_edits=label_edits,
                genre_edits=genre_edits,
                rating_edits=rating_edits,
                content_edits=content_edits,
                studio_edits=studio_edits,
                date_edits=date_edits,
                remove_edits=remove_edits,
                reset_edits=reset_edits,
                lock_edits=lock_edits,
                unlock_edits=unlock_edits,
                ep_rating_edits=ep_rating_edits,
                ep_remove_edits=ep_remove_edits,
                ep_reset_edits=ep_reset_edits,
                ep_lock_edits=ep_lock_edits,
                ep_unlock_edits=ep_unlock_edits,
                ep_tmdb_id_edits=ep_tmdb_id_edits,
                name_display=name_display,
            )

            if hasattr(self.library, "EmbyServer"):

                def collect_updated_ids(obj):
                    ids = []
                    if isinstance(obj, dict):
                        for v in obj.values():
                            if isinstance(v, list):
                                for item in v:
                                    if hasattr(item, "ratingKey"):
                                        ids.append(item.ratingKey)
                                    elif isinstance(item, (int, str)):
                                        ids.append(item)
                            elif isinstance(v, dict):
                                ids.extend(collect_updated_ids(v))
                    elif isinstance(obj, list):
                        for item in obj:
                            ids.extend(collect_updated_ids(item))
                    return ids

                updated_ids = collect_updated_ids(
                    {
                        "label": label_edits,
                        "genre": genre_edits,
                        "rating": rating_edits,
                        "content": content_edits,
                        "studio": studio_edits,
                        "date": date_edits,
                        "remove": remove_edits,
                        "reset": reset_edits,
                        "lock": lock_edits,
                        "unlock": unlock_edits,
                        "ep_rating": ep_rating_edits,
                        "ep_remove": ep_remove_edits,
                        "ep_reset": ep_reset_edits,
                        "ep_lock": ep_lock_edits,
                        "ep_unlock": ep_unlock_edits,
                    }
                )
                for item_id in set(updated_ids):
                    idx = next((i for i, it in enumerate(items) if str(it.ratingKey) == str(item_id)), None)
                    if idx is not None:
                        refreshed = self.library.EmbyServer.get_item(item_id, force_refresh=True)
                        if refreshed:
                            items[idx] = self.library.EmbyServer.convert_emby_to_plex([refreshed])[0]

            if hasattr(self.library, "EmbyServer"):
                modified_ids = set()

                def collect_ids(obj):
                    if isinstance(obj, dict):
                        for v in obj.values():
                            collect_ids(v)
                    elif isinstance(obj, list):
                        for v in obj:
                            if hasattr(v, "ratingKey"):
                                try:
                                    modified_ids.add(int(v.ratingKey))
                                except (ValueError, TypeError):
                                    pass
                            else:
                                try:
                                    modified_ids.add(int(v))
                                except (ValueError, TypeError):
                                    pass

                collect_ids(label_edits)
                collect_ids(genre_edits)
                collect_ids(rating_edits)
                collect_ids(content_edits)
                collect_ids(studio_edits)
                collect_ids(date_edits)
                collect_ids(remove_edits)
                collect_ids(reset_edits)
                collect_ids(lock_edits)
                collect_ids(unlock_edits)
                collect_ids(ep_rating_edits)
                collect_ids(ep_remove_edits)
                collect_ids(ep_reset_edits)
                collect_ids(ep_lock_edits)
                collect_ids(ep_unlock_edits)

                if ep_tmdb_id_edits:
                    modified_ids.update([int(k) for k in ep_tmdb_id_edits.keys()])

                if modified_ids:
                    if not hasattr(self.library.EmbyServer, "dirty_items"):
                        self.library.EmbyServer.dirty_items = set()
                    self.library.EmbyServer.dirty_items.update(modified_ids)

            # Revert any pre-existing aliases detected by sync_people Phase E
            if people_alias_revert and len(people_alias_revert) > 1:
                dedup = []
                seen = set()  # Key pro Aktion
                for emby_pid, (alias_name, clean_name, tid) in people_alias_revert:
                    key = (str(emby_pid), str(alias_name))
                    if key in seen:
                        continue
                    seen.add(key)
                    dedup.append((str(emby_pid), (alias_name, clean_name, tid)))

                # 2) Stabil sortieren (erst nach alias, dann PID), damit das Log hübsch ist
                dedup.sort(key=lambda t: (t[1][0].casefold(), t[0]))
                _size = len(dedup)
            else:
                _size = len(people_alias_revert)
                dedup = people_alias_revert

            for i, (emby_pid, (alias_name, clean_name, tid)) in enumerate(dedup, 1):
                try:
                    logger.info(f"Reverting People Name {i}/{_size} | {alias_name} -> {clean_name}")
                    self.library.EmbyServer.update_item(emby_pid, {"Id": emby_pid, "Name": clean_name, "ProviderIds": {"Tmdb": tid}})
                    # log["aliases_reverted"].append((emby_pid, clean_name))
                except Exception:
                    pass

            if self.library.Radarr and self.library.radarr_add_all_existing:
                logger.info("")
                logger.separator(f"Radarr Add All Existing: {len(radarr_adds)} Movies", space=False, border=False)
                logger.info("")
                try:
                    self.library.Radarr.add_tmdb(radarr_adds)
                except Failed as e:
                    logger.error(e)

            if self.library.Sonarr and self.library.sonarr_add_all_existing:
                logger.info("")
                logger.separator(f"Sonarr Add All Existing: {len(sonarr_adds)} Shows", space=False, border=False)
                logger.info("")
                try:
                    self.library.Sonarr.add_tvdb(sonarr_adds)
                except Failed as e:
                    logger.error(e)

            logger.info("")

        if self.library.radarr_remove_by_tag:
            logger.info("")
            logger.separator(f"Radarr Remove {len(self.library.radarr_remove_by_tag)} Movies with Tags: {', '.join(self.library.radarr_remove_by_tag)}", space=False, border=False)
            logger.info("")
            self.library.Radarr.remove_all_with_tags(self.library.radarr_remove_by_tag)
        if self.library.sonarr_remove_by_tag:
            logger.info("")
            logger.separator(f"Sonarr Remove {len(self.library.sonarr_remove_by_tag)} Shows with Tags: {', '.join(self.library.sonarr_remove_by_tag)}", space=False, border=False)
            logger.info("")
            self.library.Sonarr.remove_all_with_tags(self.library.sonarr_remove_by_tag)

        if self.library.delete_collections or self.library.show_unmanaged or self.library.show_unconfigured or self.library.assets_for_all or self.library.mass_collection_mode:
            logger.info("")
            logger.separator("Collection Operations", space=False, border=False)
            logger.info("")

            if self.library.delete_collections:
                logger.info("")
                logger.separator("Deleting Collections", space=False, border=False)
                logger.info("")

            less = self.library.delete_collections["less"] if self.library.delete_collections and self.library.delete_collections["less"] is not None else None
            managed = self.library.delete_collections["managed"] if self.library.delete_collections else None
            configured = self.library.delete_collections["configured"] if self.library.delete_collections else None
            ignore_smart = self.library.delete_collections["ignore_empty_smart_collections"] if self.library.delete_collections else True
            unmanaged_collections = []
            unconfigured_collections = []
            all_collections = self.library.get_all_collections()
            for i, col in enumerate(all_collections, 1):
                logger.ghost(f"Reading Collection: {i}/{len(all_collections)} {col.title}")
                col = self.library.reload(col, force=True)
                labels = [la.tag for la in self.library.item_labels(col)]

                if should_be_deleted(col, labels, configured, managed, None if col.smart and ignore_smart else less):
                    try:
                        self.library.delete(col)
                        logger.info(f"{col.title} Deleted")
                    except Failed as e:
                        logger.error(e)
                else:
                    if "PMM" not in labels and "Kometa" not in labels:
                        unmanaged_collections.append(col)
                    if col.title not in self.library.collection_names:
                        unconfigured_collections.append(col)

            if self.library.show_unmanaged and len(unmanaged_collections) > 0:
                logger.info("")
                logger.separator(f"Unmanaged Collections in {self.library.name} Library", space=False, border=False)
                logger.info("")
                for col in unmanaged_collections:
                    logger.info(col.title)
                logger.info("")
                logger.info(f"{len(unmanaged_collections)} Unmanaged Collection{'s' if len(unmanaged_collections) > 1 else ''}")
            elif self.library.show_unmanaged:
                logger.info("")
                logger.separator(f"No Unmanaged Collections in {self.library.name} Library", space=False, border=False)
                logger.info("")

            if self.library.show_unconfigured and len(unconfigured_collections) > 0:
                logger.info("")
                logger.separator(f"Unconfigured Collections in {self.library.name} Library", space=False, border=False)
                logger.info("")
                for col in unconfigured_collections:
                    logger.info(col.title)
                logger.info("")
                logger.info(f"{len(unconfigured_collections)} Unconfigured Collection{'s' if len(unconfigured_collections) > 1 else ''}")
            elif self.library.show_unconfigured:
                logger.info("")
                logger.separator(f"No Unconfigured Collections in {self.library.name} Library", space=False, border=False)
                logger.info("")

            if self.library.assets_for_all_collections and len(unconfigured_collections) > 0:
                logger.info("")
                logger.separator(f"Unconfigured Collection Assets Check for {self.library.name} Library", space=False, border=False)
                logger.info("")
                for col in unconfigured_collections:
                    try:
                        poster, background, _, item_dir, name = self.library.find_item_assets(col)
                        if poster or background:
                            self.library.upload_images(col, poster=poster, background=background)
                        elif self.library.show_missing_assets:
                            logger.warning(f"Asset Warning: No poster or background found in an assets folder for '{name}'")
                    except Failed as e:
                        logger.warning(e)

            if self.library.mass_collection_mode:
                logger.info("")
                logger.separator(f"Unconfigured Mass Collection Mode to {self.library.mass_collection_mode} for {self.library.name} Library", space=False, border=False)
                logger.info("")
                for col in unconfigured_collections:
                    if int(col.collectionMode) not in plex.collection_mode_keys or plex.collection_mode_keys[int(col.collectionMode)] != self.library.mass_collection_mode:
                        self.library.collection_mode_query(col, self.library.mass_collection_mode)
                        logger.info(f"{col.title} Collection Mode Updated")

        if self.library.metadata_backup:
            logger.info("")
            logger.separator(f"Metadata Backup for {self.library.name} Library", space=False, border=False)
            logger.info("")
            logger.info(f"Metadata Backup Path: {self.library.metadata_backup['path']}")
            logger.info("")
            yaml = None
            if os.path.exists(self.library.metadata_backup["path"]):
                try:
                    yaml = self.config.Requests.file_yaml(self.library.metadata_backup["path"])
                except Failed as e:
                    logger.error(e)
                    filename, file_extension = os.path.splitext(self.library.metadata_backup["path"])
                    i = 1
                    while os.path.exists(f"{filename}{i}{file_extension}"):
                        i += 1
                    os.rename(self.library.metadata_backup["path"], f"{filename}{i}{file_extension}")
                    logger.error(f"Backup failed to load saving copy to {filename}{i}{file_extension}")
            if not yaml:
                yaml = self.config.Requests.file_yaml(self.library.metadata_backup["path"], create=True)
            if "metadata" not in yaml.data or not isinstance(yaml.data["metadata"], dict):
                yaml.data["metadata"] = {}
            special_names = {}
            for mk, mv in yaml.data["metadata"].items():
                if mv and "title" in mv:
                    special_names[mv["title"]] = mk
                    if "year" in mv:
                        special_names[f"{mv['title']} ({mv['year']})"] = mk
            if not items:
                items = self.library.get_all(load=True)
            total_items = len(items)
            titles = []
            year_titles = []
            for item in items:
                titles.append(item.title)
                if self.library.item_has_year(item):
                    year_titles.append(f"{item.title} ({item.year})")
            for i, item in enumerate(items, 1):
                logger.ghost(f"({i}/{total_items}) {item.title}")
                map_key, attrs = self.library.get_locked_attributes(item, titles, year_titles)
                if map_key in special_names:
                    map_key = special_names[map_key]
                og_dict = yaml.data["metadata"][map_key] if map_key in yaml.data["metadata"] and yaml.data["metadata"][map_key] and isinstance(yaml.data["metadata"][map_key], dict) else {}
                if attrs or (self.library.metadata_backup["add_blank_entries"] and not og_dict):

                    def loop_dict(looping, dest_dict):
                        if not looping:
                            return None
                        for lk, lv in looping.items():
                            if isinstance(lv, dict) and lk in dest_dict and dest_dict[lk] and isinstance(dest_dict[lk], dict):
                                dest_dict[lk] = loop_dict(lv, dest_dict[lk])
                            else:
                                dest_dict[lk] = lv
                        return dest_dict

                    yaml.data["metadata"][map_key] = loop_dict(attrs, og_dict)
            logger.exorcise()
            yaml.save()
            logger.info(f"{len(yaml.data['metadata'])} {self.library.type}{'s' if len(yaml.data['metadata']) > 1 else ''} Backed Up")

        operation_run_time = str(datetime.now() - operation_start).split(".")[0]
        logger.info("")
        logger.separator(f"Finished {self.library.name} Library Operations\nOperations Run Time: {operation_run_time}")
        return operation_run_time
