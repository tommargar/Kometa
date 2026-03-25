import os, re
from datetime import datetime
from modules import plex, util, overlay
from modules.builder import CollectionBuilder
from modules.poster import ImageData
from modules.util import Failed, FilterFailed, NotScheduled, LimitReached
from num2words import num2words
from plexapi.exceptions import BadRequest
from plexapi.video import Season, Episode
from PIL import Image, ImageFilter

logger = util.logger

class Overlays:
    def __init__(self, config, library):
        from modules.config import ConfigFile # avoid circle import
        self.config : ConfigFile = config
        self.cache = self.config.Cache
        self.library = library
        self.overlays = []

    def run_overlays(self):
        overlay_start = datetime.now()
        logger.info("")
        logger.separator(f"{self.library.name} Library Overlays")
        logger.info("")
        os.makedirs(self.library.overlay_backup, exist_ok=True)

        key_to_overlays = {}
        properties = {}
        if not self.library.remove_overlays:
            emby_server = getattr(self.library, "EmbyServer", None)
            if emby_server and emby_server.dirty_items:
                logger.info("")
                logger.separator(f"Refreshing {len(emby_server.dirty_items)} Dirty Items for the {self.library.name} Library")
                logger.info("")
                for i, item_id in enumerate(list(emby_server.dirty_items), 1):
                    logger.ghost(f"Refreshing {i}/{len(emby_server.dirty_items)} Item ID: {item_id}")
                    emby_server.get_item(item_id, force_refresh=True)
                logger.info("")
            key_to_overlays, properties = self.compile_overlays()
        ignore_list = [rk for rk in key_to_overlays]

        if self.library.mc_type == "plex":
            # I guess this block contains Plex legacy stuff
            old_overlays = [la for la in self.library.Plex.listFilterChoices("label") if str(la.title).lower().endswith(" overlay")]
            if old_overlays:
                logger.separator(f"Removing Old Overlays for the {self.library.name} Library")
                logger.info("")
                for old_overlay in old_overlays:
                    label_items = self.get_overlay_items(label=old_overlay)
                    if label_items:
                        logger.info("")
                        logger.separator(f"Removing {old_overlay.title}")
                        logger.info("")
                        for i, item in enumerate(label_items, 1):
                            item_title = self.library.get_item_display_title(item)
                            logger.ghost(f"Restoring {old_overlay.title}: {i}/{len(label_items)} {item_title}")
                            self.remove_overlay(item, item_title, old_overlay.title, [
                                os.path.join(self.library.overlay_folder, old_overlay.title[:-8], f"{item.ratingKey}.png")
                            ])
                logger.info("")

        # emby_image_manager = Emby_Image_Manager(self.library.name)
        if False: # ToDo: Remove overlays not working as expected - deactiveted
            remove_overlays = self.get_overlay_items(ignore=ignore_list)
            if self.library.is_show:
                remove_overlays.extend(self.get_overlay_items(libtype="episode", ignore=ignore_list))
                remove_overlays.extend(self.get_overlay_items(libtype="season", ignore=ignore_list))
            elif self.library.is_music:
                remove_overlays.extend(self.get_overlay_items(libtype="album", ignore=ignore_list))

            if remove_overlays:
                logger.separator(f"Removing {'All ' if self.library.remove_overlays else ''}Overlays for the {self.library.name} Library")
                for i, item in enumerate(remove_overlays, 1):
                    item_title = self.library.get_item_display_title(item)
                    logger.ghost(f"Restoring: {i}/{len(remove_overlays)} {item_title}")
                    self.remove_overlay(item, item_title, "Overlay", [
                        os.path.join(self.library.overlay_destination_folder, f"{item}.png"),
                        os.path.join(self.library.overlay_destination_folder, f"{item}.jpg"),
                        os.path.join(self.library.overlay_destination_folder, f"{item}.webp")
                    ])
                logger.exorcise()
            else:
                logger.separator(f"No Overlays to Remove for the {self.library.name} Library")
            logger.info("")
        if not self.library.remove_overlays:
            logger.separator(f"{'Re-' if self.library.reapply_overlays else ''}Applying Overlays for the {self.library.name} Library")
            logger.info("")

            _trakt_ratings = None
            def trakt_ratings():
                nonlocal _trakt_ratings
                if _trakt_ratings is None:
                    _trakt_ratings = self.config.Trakt.user_ratings(self.library.is_movie)
                if not _trakt_ratings:
                    raise Failed
                return _trakt_ratings

            total_keys = len(key_to_overlays)
            for i, (over_key, (item, over_names)) in enumerate(sorted(key_to_overlays.items(), key=lambda io: self.library.get_item_display_title(io[1][0], sort=True)), 1):
                emby_server = getattr(self.library, "EmbyServer", None)
                native_emby_data = None
                item_id = None
                if emby_server and getattr(item, "ratingKey", None):
                    item_id = getattr(item, "ratingKey", None)
                    try:
                        item_id_int = int(item_id)
                    except (TypeError, ValueError):
                        item_id_int = None

                    force_refresh = bool(self.library.overlay_refresh_emby_items)
                    perform_refresh = force_refresh or (item_id_int is not None and item_id_int in emby_server.dirty_items)

                    if perform_refresh:
                        refreshed_data = emby_server.get_item(item_id, force_refresh=force_refresh or item_id_int in emby_server.dirty_items)
                        if refreshed_data:
                            native_emby_data = refreshed_data
                            refreshed_items = emby_server.convert_emby_to_plex([refreshed_data])
                            if refreshed_items:
                                item = refreshed_items[0]
                                key_to_overlays[over_key] = (item, over_names)

                item_title = self.library.get_item_display_title(item)

                try:
                    logger.ghost(f"Overlaying: ({i}/{total_keys}) {item_title}")
                    image_compare = None
                    overlay_compare = None
                    poster = None
                    if self.cache:
                        image, image_compare, overlay_compare = self.cache.query_image_map(item.ratingKey, f"{self.library.image_table_name}_overlays")
                    # self.library.reload(item, force=True)
                    # not needed here for Emby

                    overlay_compare = [] if overlay_compare is None else util.get_list(overlay_compare, split="|")
                    my_overlay_path = ""
                    if self.library.mc_type == "emby":
                        my_overlay_path = os.path.join(self.library.overlay_destination_folder, f"{item.ratingKey}.{self.library.overlay_artwork_filetype}")
                        has_overlay = os.path.exists(my_overlay_path)
                    else:
                        has_overlay = any(
                            [item_tag.tag.lower() == "overlay" for item_tag in self.library.item_labels(item)])

                    special_text_cache = self.cache.query_overlay_special_text(item.ratingKey) if self.cache else {}
                    compare_names = {properties[ov].get_overlay_compare(): ov for ov in over_names}
                    overlay_change = self.get_overlay_change_reason(item, over_names, properties, overlay_compare,compare_names, has_overlay, special_text_cache)

                    blur_num = 0
                    applied_names = []
                    queue_overlays = {}
                    for over_name in over_names:
                        current_overlay = properties[over_name]
                        if current_overlay.name.startswith("blur"):
                            logger.info(over_name)
                            blur_test = int(re.search("\\(([^)]+)\\)", current_overlay.name).group(1))
                            if blur_test > blur_num:
                                blur_num = blur_test
                        elif current_overlay.queue_name:
                            if current_overlay.queue not in queue_overlays:
                                queue_overlays[current_overlay.queue] = {}
                            if current_overlay.weight in queue_overlays[current_overlay.queue]:
                                raise Failed("Overlay Error: Overlays in a queue cannot have the same weight")
                            queue_overlays[current_overlay.queue][current_overlay.weight] = over_name
                        else:
                            applied_names.append(over_name)

                    try:
                        #todo: for Emby transparent PNG, ignore existing poster files
                        poster, background, logo, item_dir, name = self.library.find_item_assets(item)
                        if self.library.EmbyServer:
                            poster :ImageData = ImageData("asset_directory", my_overlay_path if has_overlay else "", is_url=False)
                        # poster = ImageData("asset_directory", emby_poster.get("Path"), is_url=False, compare=poster_compare)
                        # background = ImageData("asset_directory", emby_thumb.get("Path"), compare=emby_item.get("ImageTags").get("Thumb"))
                        item_dir = os.path.dirname(poster.location)
                        name = str(item_dir).split('\\')[-1]

                        # poster, background, item_dir, name = self.library.find_item_assets(item)

                        if not poster and self.library.assets_for_all:
                            if (isinstance(item, Episode) and self.library.show_missing_episode_assets) or \
                                    (isinstance(item, Season) and self.library.show_missing_season_assets) or \
                                    (not isinstance(item, (Episode, Season)) and self.library.show_missing_assets):
                                if self.library.asset_folders:
                                    logger.warning(f"Asset Warning: No poster found for '{item_title}' in the assets folder '{item_dir}'")
                                else:
                                    logger.warning(f"Asset Warning: No poster '{name}' found in the assets folders")
                        # if background:
                        #     self.library.upload_images(item, background=background)
                        # if logo:
                        #     self.library.upload_images(item, logo=logo)
                    except Failed as e:
                        if self.library.assets_for_all and self.library.show_missing_assets:
                            logger.warning(e)

                    changed_image = False
                    poster_compare = None
                    if self.library.mc_type == "emby" and has_overlay:
                        try:
                            if str(os.stat(my_overlay_path).st_size) != str(image_compare):
                                changed_image = True
                        except FileNotFoundError:
                            changed_image = True
                    elif poster:
                        poster_compare = poster.compare
                        if poster.compare and str(poster.compare) != str(image_compare):
                            changed_image = True

                    if self.library.reapply_overlays or overlay_change or changed_image:
                        try:
                            if not self.library.reapply_overlays and changed_image:
                                logger.trace(f"  Overlay Reason: New image detected")
                            if overlay_change:
                                logger.trace(f"  Overlay Reason: Overlay changed {overlay_change}")

                            # canvas_width = emby_poster.get('Width', 0)
                            # canvas_height = emby_poster.get('Height',0)
                            canvas_width, canvas_height = overlay.get_canvas_size(item)
                            if self.library.mc_type == "emby" and isinstance(item, Episode):
                                try:
                                    emby_item = emby_server.get_item(item.ratingKey)
                                    if emby_item and "PrimaryImageAspectRatio" in emby_item:
                                        aspect_ratio = float(emby_item["PrimaryImageAspectRatio"])
                                        canvas_height = 1080
                                        canvas_width = int(canvas_height * aspect_ratio)
                                except Exception:
                                    pass

                            # todo if filetype png / emby overlay
                            # canvas_width, canvas_height = overlay.get_canvas_size(item)
                            new_poster = Image.new("RGBA", (canvas_width, canvas_height), (0, 0, 0, 0))
                            with (new_poster):
                            # with Image.open(poster.location if poster else has_original) as new_poster:
                                exif_tags = new_poster.getexif()
                                exif_tags[0x04bc] = "overlay"
                                # new_poster = new_poster.convert("RGBA").resize((canvas_width, canvas_height), Image.LANCZOS)
                                # new_poster = new_poster.convert("RGB").resize((canvas_width, canvas_height), Image.Resampling.LANCZOS)

                                if blur_num > 0:
                                    new_poster = new_poster.filter(ImageFilter.GaussianBlur(blur_num))

                                def get_text(text_overlay):
                                    nonlocal native_emby_data
                                    fresh_emby_item = None
                                    full_text = text_overlay.name[5:-1]
                                    # Detect Provider based on Overlay Image Path
                                    provider = None
                                    if text_overlay.path:
                                        lower_path = text_overlay.path.lower()
                                        if "letterboxd" in lower_path: provider = "letterboxd"
                                        elif "imdb" in lower_path: provider = "imdb"
                                        elif "tmdb" in lower_path: provider = "tmdb"
                                        elif "rotten" in lower_path or "rt_" in lower_path: provider = "rt"
                                        elif "metacritic" in lower_path: provider = "metacritic"
                                        elif "trakt" in lower_path: provider = "trakt"

                                    for format_var in overlay.vars_by_type[text_overlay.level]:
                                        if f"<<{format_var}" in full_text and format_var == "originally_available[":
                                            mod = re.search("<<originally_available\\[(.+)]>>", full_text).group(1)
                                            format_var = "originally_available"
                                        elif f"<<{format_var}>>" in full_text and format_var.endswith(tuple(m for m in overlay.double_mods)):
                                            mod = format_var[-2:]
                                            format_var = format_var[:-2]
                                        elif f"<<{format_var}>>" in full_text and format_var.endswith(tuple(m for m in overlay.single_mods)):
                                            mod = format_var[-1]
                                            format_var = format_var[:-1]
                                        elif f"<<{format_var}>>" in full_text:
                                            mod = ""
                                        else:
                                            continue
                                        if format_var == "show_title":
                                            actual_attr = "parentTitle" if text_overlay.level == "season" else "grandparentTitle"
                                        elif format_var in plex.attribute_translation:
                                            actual_attr = plex.attribute_translation[format_var]
                                        else:
                                            actual_attr = format_var
                                        if format_var == "bitrate":
                                            actual_value = None
                                            # ToDo
                                            for media in item.media:
                                                current = int(media.bitrate)
                                                if actual_value is None:
                                                    actual_value = current
                                                    if mod == "":
                                                        break
                                                elif mod == "H" and current > actual_value:
                                                    actual_value = current
                                                elif mod == "L" and current < actual_value:
                                                    actual_value = current
                                        elif format_var == "user_rating" and emby_server:
                                            actual_attr = plex.attribute_translation.get(format_var, format_var)
                                            actual_value = None
                                            if item_id is not None:
                                                if fresh_emby_item is None:
                                                    if native_emby_data:
                                                        fresh_emby_item = native_emby_data
                                                    else:
                                                        fresh_emby_item = emby_server.get_item(item_id, force_refresh=True)
                                                        native_emby_data = fresh_emby_item
                                                native_item = fresh_emby_item
                                                if native_item:
                                                    actual_value = emby_server.get_custom_rating_from_item(native_item, raw=True)
                                                    if actual_value in [None, ""] and native_item.get("ProviderIds") and "CustomRating" in native_item["ProviderIds"]:
                                                        actual_value = native_item["ProviderIds"]["CustomRating"]
                                                    if actual_value is None and native_item.get("CustomRating"):
                                                        logger.warning(
                                                            "Overlay Warning: Emby custom ratings must be provided via "
                                                            "ProviderIds['CustomRating']; the CustomRating field is ignored."
                                                        )
                                            if actual_value is None and hasattr(item, actual_attr):
                                                actual_value = getattr(item, actual_attr)
                                            # if actual_value is not None and "%" in full_text:
                                            #     try:
                                            #         actual_value = float(actual_value) / 10
                                            #     except (ValueError, TypeError):
                                            #         pass
                                        elif format_var in overlay.rating_sources:
                                            found_rating = None
                                            try:
                                                tmdb_id, tvdb_id, imdb_id = self.library.get_ids(item)
                                                if format_var == "tmdb_rating":
                                                    item_to_id = item.show() if isinstance(item, (Season, Episode)) else item
                                                    t_tmdb_id, t_tvdb_id, t_imdb_id = self.library.get_ids(item_to_id)
                                                    _item = self.config.TMDb.get_item(item_to_id, t_tmdb_id, t_tvdb_id, t_imdb_id, is_movie=self.library.is_movie)
                                                    if _item:
                                                        if isinstance(item, Episode):
                                                            found_rating = self.config.TMDb.get_episode(_item.tmdb_id, item.seasonNumber, item.episodeNumber).vote_average
                                                        elif isinstance(item, Season):
                                                            for season in _item.seasons:
                                                                if item.seasonNumber == season.season_number:
                                                                    found_rating = season.average
                                                                    break
                                                        else:
                                                            found_rating = _item.vote_average
                                                    else:
                                                        raise Failed(f"No TMDb ID for Guid: {item.guid}")
                                                elif format_var == "imdb_rating":
                                                    if isinstance(item, Episode):
                                                        found_rating = self.config.IMDb.get_episode_rating(imdb_id, item.seasonNumber, item.episodeNumber)
                                                    else:
                                                        found_rating = self.config.IMDb.get_rating(imdb_id)
                                                elif format_var == "trakt_user_rating":
                                                    _ratings = trakt_ratings()
                                                    _id = tmdb_id if self.library.is_movie else tvdb_id
                                                    if _id in _ratings:
                                                        found_rating = _ratings[_id]
                                                    else:
                                                        raise Failed("No Trakt User Rating Found")
                                                elif format_var == "trakt_rating":
                                                    if self.config.Trakt:
                                                        found_rating = self.config.Trakt.get_rating(imdb_id, self.library.is_movie)
                                                    else:
                                                        raise Failed("No Trakt Rating Found")
                                                elif str(format_var).startswith("mdb"):
                                                    mdb_item = None
                                                    if self.config.MDBList.limit is False and not self.config.MDBList.limit:
                                                        if self.library.is_show and tvdb_id:
                                                            try:
                                                                use_tvdb_id = tvdb_id
                                                                if isinstance(item, (Season, Episode)):
                                                                    _, use_tvdb_id, _ = self.library.get_ids(item.show())
                                                                mdb_item = self.config.MDBList.get_series(use_tvdb_id)
                                                            except LimitReached as err:
                                                                logger.debug(err)
                                                            except Failed as err:
                                                                logger.error(str(err))
                                                            except Exception as e:
                                                                logger.trace(f"TVDb ID: {tvdb_id}")
                                                                logger.debug(f"MDBList Error: {e}")
                                                        if self.library.is_movie and tmdb_id:
                                                            try:
                                                                mdb_item = self.config.MDBList.get_movie(tmdb_id)
                                                            except LimitReached as err:
                                                                logger.debug(err)
                                                            except Failed as err:
                                                                logger.error(str(err))
                                                            except Exception as e:
                                                                logger.trace(f"TMDb ID: {tmdb_id}")
                                                                logger.debug(f"MDBList Error: {e}")
                                                        if imdb_id and not mdb_item:
                                                            try:
                                                                mdb_item = self.config.MDBList.get_imdb(imdb_id)
                                                            except LimitReached as err:
                                                                logger.debug(err)
                                                            except Failed as err:
                                                                logger.error(str(err))
                                                            except Exception as e:
                                                                logger.trace(f"IMDb ID: {imdb_id}")
                                                                logger.debug(f"MDBList Error: {e}")
                                                        if not mdb_item and self.config.MDBList.limit:
                                                            logger.warning(f"Overlay Warning: MDBList Limit Reached, skipping {format_var} for {item.title}")
                                                        elif not mdb_item:
                                                            # Don't raise Failed, just log warning to allow cache update
                                                            logger.warning(f"Overlay Warning: No MdbItem for {item.title} (Guid: {item.guid})")
                                                    if format_var == "mdb_average_rating":
                                                        found_rating = mdb_item.average / 10 if mdb_item.average else None
                                                    elif format_var == "mdb_imdb_rating":
                                                        found_rating = mdb_item.imdb_rating if mdb_item.imdb_rating else None
                                                    elif format_var == "mdb_metacritic_rating":
                                                        found_rating = mdb_item.metacritic_rating / 10 if mdb_item.metacritic_rating else None
                                                    elif format_var == "mdb_metacriticuser_rating":
                                                        found_rating = mdb_item.metacriticuser_rating if mdb_item.metacriticuser_rating else None
                                                    elif format_var == "mdb_trakt_rating":
                                                        found_rating = mdb_item.trakt_rating / 10 if mdb_item.trakt_rating else None
                                                    elif format_var == "mdb_tomatoes_rating":
                                                        found_rating = mdb_item.tomatoes_rating / 10 if mdb_item.tomatoes_rating else None
                                                    elif format_var == "mdb_tomatoesaudience_rating":
                                                        found_rating = mdb_item.tomatoesaudience_rating / 10 if mdb_item.tomatoesaudience_rating else None
                                                    elif format_var == "mdb_tmdb_rating":
                                                        found_rating = mdb_item.tmdb_rating / 10 if mdb_item.tmdb_rating else None
                                                    elif format_var == "mdb_letterboxd_rating":
                                                        found_rating = mdb_item.letterboxd_rating * 2 if mdb_item.letterboxd_rating else None
                                                    elif format_var == "mdb_myanimelist_rating":
                                                        found_rating = mdb_item.myanimelist_rating if mdb_item.myanimelist_rating else None
                                                    else:
                                                        found_rating = mdb_item.score / 10 if mdb_item.score else None
                                                elif str(format_var).startswith("omdb"):
                                                    if self.config.OMDb.limit is not False:
                                                        raise Failed("Daily OMDb Limit Reached")
                                                    elif not imdb_id:
                                                        raise Failed(f"No IMDb ID for Guid: {item.guid}")
                                                    else:
                                                        try:
                                                            omdb_obj = self.config.OMDb.get_omdb(imdb_id, True)
                                                            if format_var == "omdb_metascore_rating":
                                                                found_rating = omdb_obj.metacritic_rating / 10 if omdb_obj.metacritic_rating else None
                                                            elif format_var == "omdb_tomatoes_rating":
                                                                found_rating = omdb_obj.rotten_tomatoes / 10 if omdb_obj.rotten_tomatoes else None
                                                            else:
                                                                found_rating = omdb_obj.imdb_rating if omdb_obj.imdb_rating else None
                                                        except Exception:
                                                            logger.error(f"Cannot retrieve {format_var} for: {imdb_id}")
                                                            raise
                                                elif str(format_var).startswith(("anidb", "mal")):
                                                    anidb_id = self.config.Convert.ids_to_anidb(self.library, item.ratingKey, tvdb_id, imdb_id, tmdb_id)

                                                    if str(format_var).startswith("anidb"):
                                                        if anidb_id:
                                                            anidb_obj = self.config.AniDB.get_anime(anidb_id)
                                                            if format_var == "anidb_rating_rating":
                                                                found_rating = anidb_obj.rating
                                                            elif format_var == "anidb_average_rating":
                                                                found_rating = anidb_obj.average
                                                            elif format_var == "anidb_score_rating":
                                                                found_rating = anidb_obj.score
                                                        else:
                                                            raise Failed(f"No AniDB ID for Guid: {item.guid}")
                                                    else:
                                                        if item.ratingKey in self.library.reverse_mal:
                                                            mal_id = self.library.reverse_mal[item.ratingKey]
                                                        elif not anidb_id:
                                                            raise Failed(f"Convert Warning: No AniDB ID to Convert to MyAnimeList ID for Guid: {item.guid}")
                                                        else:
                                                            try:
                                                                mal_id = self.config.Convert.anidb_to_mal(anidb_id)
                                                            except Failed as errr:
                                                                raise Failed(f"{errr} of Guid: {item.guid}")
                                                        if mal_id:
                                                            found_rating = self.config.MyAnimeList.get_anime(mal_id).score
                                                elif str(format_var).startswith("plex"):
                                                    ratings = self.library.get_ratings(item)
                                                    rating_key = format_var.replace("_rating", "")
                                                    try:
                                                        found_rating = ratings[rating_key] # noqa
                                                    except KeyError:
                                                        found_rating = None
                                            except Failed as err:
                                                logger.error(err)
                                            if found_rating:
                                                actual_value = found_rating
                                                logger.trace(f"{format_var}: {actual_value}")
                                            elif not self.config.MDBList.limit:
                                                logger.warning(f"Overlay Warning: No {format_var} found for {item_title}")
                                        elif format_var == "runtime" and text_overlay.level in ["show", "season", "artist", "album"]:
                                            if hasattr(item, "duration") and item.duration:
                                                actual_value = item.duration
                                            else:
                                                sub_items = item.episodes() if text_overlay.level in ["show", "season"] else item.tracks()
                                                sub_items = [ep.duration for ep in sub_items if hasattr(ep, "duration") and ep.duration]
                                                actual_value = sum(sub_items) / len(sub_items)
                                        elif format_var == "total_runtime":
                                            # ToDo
                                            sub_items = item.episodes() if text_overlay.level in ["show", "season"] else item.tracks()
                                            sub_items = [ep.duration for ep in sub_items if hasattr(ep, "duration") and ep.duration]
                                            actual_value = sum(sub_items)
                                        else:
                                            # match actual_attr:
                                            #     case 'rating':
                                            actual_value = None
                                            if emby_server and item_id and format_var in ["critic_rating", "audience_rating", "user_rating"]:
                                                if fresh_emby_item is None:
                                                    if native_emby_data:
                                                        fresh_emby_item = native_emby_data
                                                    else:
                                                        fresh_emby_item = emby_server.get_item(item_id, force_refresh=True)
                                                        native_emby_data = fresh_emby_item
                                                native_item = fresh_emby_item
                                                if native_item:
                                                    if format_var == "audience_rating":
                                                        actual_value = native_item.get("CommunityRating")
                                                    elif format_var == "critic_rating":
                                                        actual_value = native_item.get("CriticRating")
                                                    elif format_var == "user_rating":
                                                        actual_value = emby_server.get_custom_rating_from_item(native_item, raw=True)

                                            if actual_value is None:
                                                actual_value = getattr(item, actual_attr, None)

                                            if actual_value is None and emby_server and item_id:
                                                native_item = emby_server.get_item(item_id)
                                                if native_item:
                                                    if format_var == "audience_rating":
                                                        actual_value = native_item.get("CommunityRating")
                                                    elif format_var == "critic_rating":
                                                        actual_value = native_item.get("CriticRating")
                                                    elif format_var == "user_rating":
                                                        actual_value = emby_server.get_custom_rating_from_item(native_item,
                                                                                                           raw=True)
                                                if actual_value is None and fresh_emby_item is None:
                                                    if native_emby_data:
                                                        fresh_emby_item = native_emby_data
                                                    else:
                                                        fresh_emby_item = emby_server.get_item(item_id, force_refresh=True)
                                                        native_emby_data = fresh_emby_item
                                                    native_item = fresh_emby_item
                                                    if native_item:
                                                        if format_var == "audience_rating":
                                                            actual_value = native_item.get("CommunityRating")
                                                        elif format_var == "critic_rating":
                                                            actual_value = native_item.get("CriticRating")
                                                        elif format_var == "user_rating":
                                                            actual_value = emby_server.get_custom_rating_from_item(native_item, raw=True)



                                            if actual_value is None:
                                                raise Failed(f"Overlay Warning: No {full_text} found")

                                            if format_var == "versions":
                                                actual_value = len(actual_value)
                                        if self.cache:
                                            cache_store = actual_value.strftime("%Y-%m-%d") if format_var in overlay.date_vars else actual_value
                                            self.cache.update_overlay_special_text(item.ratingKey, format_var, cache_store)

                                        # Apply Provider Specific Scaling if using generic rating variables
                                        if provider and actual_value is not None and format_var in ["critic_rating"]:
                                            try:
                                                val = float(actual_value)
                                                # if provider == "letterboxd":
                                                #     actual_value = val / 2 if format_var != "critic_rating" else val / 20 # Scale 0-10 to 0-5
                                                if format_var == "critic_rating" and provider not in ["tmdb", "trakt", "rt", "metacritic"]:
                                                    actual_value = val / 10
                                            except (ValueError, TypeError):
                                                pass

                                        sub_value = None
                                        if format_var == "originally_available":
                                            if mod:
                                                sub_value = "<<originally_available\\[(.+)]>>"
                                                final_value = actual_value.strftime(mod)
                                            else:
                                                final_value = actual_value.strftime("%Y-%m-%d")
                                        elif format_var in ["runtime", "total_runtime"]:
                                            if mod == "H":
                                                final_value = int((actual_value / 60000) // 60)
                                            elif mod == "M":
                                                final_value = int((actual_value / 60000) % 60)
                                            else:
                                                final_value = int(actual_value / 60000)
                                        elif provider in ["tmdb", "rt", "metacritic", "trakt"]:
                                            # Ensure percentage is calculated from the scaled value
                                            val = float(actual_value)
                                            if format_var != "critic_rating":
                                                val *= 10
                                            final_value = int(val)
                                        elif mod == "%":
                                            final_value = int(float(actual_value) * 10)
                                        elif mod == "#":
                                            actual_value = f"{float(actual_value):.1f}"
                                            final_value = actual_value[:-2] if actual_value.endswith(".0") else actual_value
                                        elif mod == "/":
                                            final_value = f"{float(actual_value) / 2:.1f}"
                                        elif mod == "W":
                                            final_value = num2words(int(actual_value))
                                        elif mod == "WU":
                                            final_value = num2words(int(actual_value)).upper()
                                        elif mod == "WL":
                                            final_value = num2words(int(actual_value)).lower()
                                        elif mod == "0":
                                            final_value = f"{int(actual_value):02}"
                                        elif mod == "00":
                                            final_value = f"{int(actual_value):03}"
                                        elif mod == "U":
                                            final_value = str(actual_value).upper()
                                        elif mod == "L":
                                            final_value = str(actual_value).lower()
                                        elif mod == "P":
                                            final_value = str(actual_value).title()
                                        elif format_var in overlay.rating_sources or format_var == "audience_rating":
                                            final_value = f"{float(actual_value):.1f}"
                                        else:
                                            final_value = actual_value
                                        if sub_value:
                                            full_text = re.sub(sub_value, str(final_value), full_text)
                                        else:
                                            full_text = full_text.replace(f"<<{format_var}{mod}>>", str(final_value))
                                    return str(full_text)

                                for over_name in applied_names:
                                    current_overlay = properties[over_name]
                                    if current_overlay.name.startswith("text"):
                                        if "<<" in current_overlay.name:
                                            image_box = current_overlay.image.size if current_overlay.image else None
                                            try:
                                                overlay_image, addon_box = current_overlay.get_backdrop((canvas_width, canvas_height), box=image_box, text=get_text(current_overlay))
                                            except Failed as e:
                                                logger.warning(f"  {e}")
                                                continue
                                            new_poster.paste(overlay_image, (0, 0), overlay_image)
                                        else:
                                            overlay_image, addon_box = current_overlay.get_canvas(item)
                                            new_poster.paste(overlay_image, (0, 0), overlay_image)
                                        if current_overlay.image:
                                            new_poster.paste(current_overlay.image, addon_box, current_overlay.image)
                                    elif current_overlay.name == "backdrop":
                                        overlay_image, _ = current_overlay.get_canvas(item)
                                        new_poster.paste(overlay_image, (0, 0), overlay_image)
                                    else:
                                        if current_overlay.has_coordinates():
                                            overlay_image, overlay_box = current_overlay.get_canvas(item)
                                            if overlay_image is not None:
                                                new_poster.paste(overlay_image, (0, 0), overlay_image)
                                            new_poster.paste(current_overlay.image, overlay_box, current_overlay.image)
                                        else:
                                            new_poster = new_poster.resize(current_overlay.image.size, Image.Resampling.LANCZOS)
                                            new_poster.paste(current_overlay.image, (0, 0), current_overlay.image)
                                            new_poster = new_poster.resize((canvas_width, canvas_height), Image.Resampling.LANCZOS)

                                for queue, weights in queue_overlays.items():
                                    cords = self.library.queues[queue]
                                    sorted_weights = sorted(weights.items(), reverse=True)
                                    for o, cord in enumerate(cords):
                                        if len(sorted_weights) <= o:
                                            break
                                        over_name = sorted_weights[o][1]
                                        current_overlay = properties[over_name]
                                        if current_overlay.name.startswith("text"):
                                            image_box = current_overlay.image.size if current_overlay.image else None
                                            try:
                                                overlay_image, addon_box = current_overlay.get_backdrop((canvas_width, canvas_height), box=image_box, text=get_text(current_overlay), new_cords=cord)
                                            except Failed as e:
                                                logger.warning(f"  {e}")
                                                continue
                                            new_poster.paste(overlay_image, (0, 0), overlay_image)
                                            if current_overlay.image:
                                                new_poster.paste(current_overlay.image, addon_box, current_overlay.image)
                                        else:
                                            if current_overlay.has_back:
                                                overlay_image, overlay_box = current_overlay.get_backdrop((canvas_width, canvas_height), box=current_overlay.image.size, new_cords=cord)
                                                new_poster.paste(overlay_image, (0, 0), overlay_image)
                                            else:
                                                overlay_box = current_overlay.get_coordinates((canvas_width, canvas_height), box=current_overlay.image.size, new_cords=cord)
                                            new_poster.paste(current_overlay.image, overlay_box, current_overlay.image)

                                if isinstance(item, Episode) and getattr(self.library, "overlay_show_logo", False):
                                    try:
                                        show_item = item.show()
                                        _, _, show_logo, _, _ = self.library.find_item_assets(show_item)
                                        if show_logo:
                                            with Image.open(show_logo.location) as logo_img:
                                                logo_img = logo_img.convert("RGBA")
                                                l_w, l_h = logo_img.size
                                                ratio = min(canvas_width * 0.3 / l_w, canvas_height * 0.3 / l_h)
                                                new_l_w = int(l_w * ratio)
                                                new_l_h = int(l_h * ratio)
                                                logo_img = logo_img.resize((new_l_w, new_l_h), Image.Resampling.LANCZOS)
                                                padding = int(canvas_width * 0.02)
                                                box = (padding, canvas_height - new_l_h - padding)
                                                new_poster.paste(logo_img, box, logo_img)
                                    except Exception as e:
                                        logger.warning(f"  Overlay Warning: Failed to apply show logo: {e}")

                                # ext = "webp" if self.library.overlay_artwork_filetype.startswith("webp") else self.library.overlay_artwork_filetype
                                ext = "webp" if self.library.overlay_artwork_filetype.startswith("webp") else self.library.overlay_artwork_filetype
                                temp = os.path.join(self.library.overlay_folder, f"temp.{ext}")
                                if self.library.overlay_artwork_quality and self.library.overlay_artwork_filetype in ["jpg", "webp_lossy"]:
                                    new_poster.save(temp, exif=exif_tags, quality=self.library.overlay_artwork_quality)
                                elif self.library.overlay_artwork_filetype == "webp_lossless":
                                    new_poster.save(temp, exif=exif_tags, lossless=True)
                                else:
                                    new_poster.save(temp, exif=exif_tags)
                                # self.library.upload_poster(item, temp)
                                self.library.upload_poster_overlay(item, temp)
                                # self.library.edit_tags("label", item, add_tags=["Overlay"], do_print=False)
                                # poster_compare =  poster.compare if poster else item.thumb

                                poster_compare = os.stat(temp).st_size

                            logger.info(f"  Overlays Applied {item_id}: {', '.join(over_names)}")
                        except (OSError, BadRequest, SyntaxError) as e:
                            logger.stacktrace()
                            raise Failed(f"  Overlay Error: {e}")
                    else:
                        logger.info(f"  Overlay Update Not Needed {item_id} (Current Overlays: {', '.join(over_names)})")

                    if self.cache and poster_compare:
                        self.cache.update_image_map(item.ratingKey, f"{self.library.image_table_name}_overlays", item.thumb, poster_compare, overlay='|'.join(sorted(compare_names)))
                except Failed as e:
                    logger.error(f"  {e}\n  Overlays Attempted on {item_title}: {', '.join(over_names)}")
                except Exception as e:
                    logger.info(e)
                    logger.info(type(e))
                    logger.stacktrace()
                    logger.info("")
                    logger.error(f"Overlays Attempted on {item_title}: {', '.join(over_names)}")
        logger.exorcise()
        for _, over in properties.items():
            if over.image:
                over.image.close()
        overlay_run_time = str(datetime.now() - overlay_start).split('.')[0]
        logger.info("")
        logger.separator(f"Finished {self.library.name} Library Overlays\nOverlays Run Time: {overlay_run_time}")
        return overlay_run_time

    def get_overlay_change_reason(self, item, over_names, properties, overlay_compare, compare_names, has_overlay, special_text_cache):
        if not has_overlay:
            return "No Overlay Label"

        for oc in overlay_compare:
            if oc not in compare_names:
                # Check for path mismatch (Drive letter vs UNC)
                match_found = False
                for cn in compare_names:
                    if oc == cn:
                        match_found = True
                        break
                    oc_norm = oc.replace("\\", "/")
                    cn_norm = cn.replace("\\", "/")
                    prefix_len = 0
                    for i in range(min(len(oc_norm), len(cn_norm))):
                        if oc_norm[i] != cn_norm[i]:
                            break
                        prefix_len = i
                    if prefix_len > 10:
                        oc_rest = oc_norm[prefix_len:]
                        cn_rest = cn_norm[prefix_len:]
                        if "/" in oc_rest and "/" in cn_rest and oc_rest.rsplit("/", 1)[-1] == cn_rest.rsplit("/", 1)[-1]:
                            match_found = True
                            break
                if not match_found:
                    return f"{oc} not in {compare_names}"

        for compare_name, original_name in compare_names.items():
            if compare_name not in overlay_compare:
                match_found = False
                for oc in overlay_compare:
                    if compare_name == oc:
                        match_found = True
                        break
                    cn_norm = compare_name.replace("\\", "/")
                    oc_norm = oc.replace("\\", "/")
                    prefix_len = 0
                    for i in range(min(len(cn_norm), len(oc_norm))):
                        if cn_norm[i] != oc_norm[i]:
                            break
                        prefix_len = i
                    if prefix_len > 10:
                        cn_rest = cn_norm[prefix_len:]
                        oc_rest = oc_norm[prefix_len:]
                        if "/" in cn_rest and "/" in oc_rest and cn_rest.rsplit("/", 1)[-1] == oc_rest.rsplit("/", 1)[-1]:
                            match_found = True
                            break
                if not match_found:
                    return f"{compare_name} not in {overlay_compare}"
            if properties[original_name].updated:
                return f"{properties[original_name].updated}"

        if self.cache:
            for over_name in over_names:
                current_overlay = properties[over_name]
                if not current_overlay.name.startswith("text") or "<<" not in current_overlay.name:
                    continue

                full_text = current_overlay.name[5:-1]
                matches = re.findall(r"<<([^<>]+)>>", full_text)
                if not matches:
                    continue

                expected_vars = set()
                for match in matches:
                    base_var = match
                    if base_var.startswith("originally_available["):
                        base_var = "originally_available"
                    else:
                        stripped = False
                        for mod in overlay.double_mods:
                            if base_var.endswith(mod):
                                base_var = base_var[:-len(mod)]
                                stripped = True
                                break
                        if not stripped:
                            for mod in overlay.single_mods:
                                if base_var.endswith(mod):
                                    base_var = base_var[:-len(mod)]
                                    break
                    expected_vars.add(base_var)

                for format_var in expected_vars:
                    cached_value = special_text_cache.get(format_var) if special_text_cache else None
                    if cached_value is None:
                        return f"Missing Special Text Cache for {format_var}"

                    real_value = None
                    if format_var == "show_title":
                        attr = "parentTitle" if current_overlay.level == "season" else "grandparentTitle"
                        real_value = getattr(item, attr, None)
                    elif format_var == "runtime" and current_overlay.level in ["show", "season", "artist", "album"]:
                        if hasattr(item, "duration") and item.duration:
                            real_value = item.duration
                        else:
                            sub_items = item.episodes() if current_overlay.level in ["show", "season"] else item.tracks()
                            durations = [getattr(ep, "duration", None) for ep in sub_items]
                            durations = [d for d in durations if d]
                            if durations:
                                real_value = sum(durations) / len(durations)
                    elif format_var == "total_runtime":
                        sub_items = item.episodes() if current_overlay.level in ["show", "season"] else item.tracks()
                        durations = [getattr(ep, "duration", None) for ep in sub_items]
                        durations = [d for d in durations if d]
                        if durations:
                            real_value = sum(durations)
                    else:
                        actual_attr = plex.attribute_translation.get(format_var, format_var)
                        if hasattr(item, actual_attr):
                            real_value = getattr(item, actual_attr)

                    if real_value is None:
                        # Warnung: Externe Ratings (TMDB, Trakt, etc.) werden in dieser Prüf-Funktion
                        # aktuell nicht neu abgerufen. Änderungen an diesen Werten werden hier ignoriert.
                        continue

                    if format_var == "versions":
                        real_value = len(real_value) if isinstance(real_value, (list, tuple, set)) else real_value

                    try:
                        if format_var in overlay.float_vars:
                            real_value = float(real_value)
                            compare_value = float(cached_value)
                            # Divide by 10 to not trigger false updates. emby 0-100, plex 0-10
                            if self.library.mc_type == "emby" and format_var == "critic_rating":
                                compare_value /= 10
                        elif format_var in overlay.int_vars:
                            real_value = int(real_value)
                            compare_value = int(cached_value)
                        else:
                            compare_value = cached_value
                        if format_var in overlay.date_vars and hasattr(real_value, "strftime"):
                            real_value = real_value.strftime("%Y-%m-%d")
                    except (TypeError, ValueError):
                        continue

                    if real_value != compare_value:
                        return f"Special Text Changed from {cached_value} to {real_value}"
        return ""

    def compile_overlays(self):
        key_to_item = {}
        properties = {}
        overlay_groups = {}
        key_to_overlays = {}

        for overlay_file in self.library.overlay_files:
            for k, v in overlay_file.overlays.items():
                try:
                    builder = CollectionBuilder(self.config, overlay_file, k, v, library=self.library, overlay=True)
                    logger.info("")

                    logger.separator(f"Gathering Items for {k} Overlay", space=False, border=False)

                    prop_name = builder.overlay.mapping_name
                    properties[prop_name] = builder.overlay

                    builder.display_filters()

                    for method, value in builder.builders:
                        logger.debug("")
                        logger.debug(f"Builder: {method}: {value}")
                        logger.info("")
                        try:
                            builder.filter_and_save_items(builder.gather_ids(method, value))
                        except Failed as e:
                            if builder.ignore_blank_results:
                                logger.info("")
                                logger.warning(e)
                            else:
                                raise Failed(e)

                    added_titles = []
                    if builder.found_items:
                        for item in builder.found_items:
                            if builder.limit and len(added_titles) >= builder.limit:
                                break
                            key_to_item[item.ratingKey] = item
                            added_titles.append(item)
                            if item.ratingKey not in properties[prop_name].keys:
                                properties[prop_name].keys.append(item.ratingKey)
                    if added_titles:
                        logger.info(f"{len(added_titles)} Items found for {prop_name}")
                        logger.trace(f"Titles Found: {[self.library.get_item_display_title(a) for a in added_titles]}")
                    else:
                        logger.warning(f"No Items found for {prop_name}")
                    logger.info("")
                except NotScheduled as e:
                    logger.info(e)
                except FilterFailed:
                    pass
                except Failed as e:
                    logger.stacktrace()
                    logger.error(e)
                    logger.info("")
                except Exception as e:
                    logger.stacktrace()
                    logger.error(f"Unknown Error: {e}")
                    logger.info("")

        logger.separator(f"Overlay Operation for the {self.library.name} Library")
        logger.debug("")
        logger.debug(f"Remove Overlays: {self.library.remove_overlays}")
        logger.debug(f"Reapply Overlays: {self.library.reapply_overlays}")
        logger.debug(f"Reset Overlays: {self.library.reset_overlays}")
        logger.debug("")
        logger.separator(f"Number of Items Per Overlay", space=False, border=False)
        logger.debug("")

        longest = 7
        for overlay_name in properties:
            if len(overlay_name) > longest:
                longest = len(overlay_name)

        logger.debug(f"{'Overlay':^{longest}} | Number |")
        logger.debug(f"{logger.separating_character * longest} | {logger.separating_character * 6} |")
        for overlay_name, over_obj in properties.items():
            logger.debug(f"{overlay_name:<{longest}} | {len(over_obj.keys):^6} |")
        logger.debug("")

        for overlay_name, over_obj in properties.items():
            if over_obj.group:
                if over_obj.group not in overlay_groups:
                    overlay_groups[over_obj.group] = {}
                overlay_groups[over_obj.group][overlay_name] = over_obj.weight

        for overlay_name, over_obj in properties.items():
            for over_key in over_obj.keys:
                if over_key not in key_to_overlays:
                    key_to_overlays[over_key] = (key_to_item[over_key], [])
                key_to_overlays[over_key][1].append(overlay_name)

        for over_key, (item, over_names) in key_to_overlays.items():
            group_status = {}
            for over_name in over_names:
                for suppress_name in properties[over_name].suppress:
                    if suppress_name in over_names:
                        key_to_overlays[over_key][1].remove(suppress_name)
            for over_name in over_names:
                for overlay_group, group_names in overlay_groups.items():
                    if over_name in group_names:
                        if overlay_group not in group_status:
                            group_status[overlay_group] = []
                        group_status[overlay_group].append(over_name)
            for gk, gv in group_status.items():
                if len(gv) > 1:
                    final = None
                    for v in gv:
                        if final is None or overlay_groups[gk][v] > overlay_groups[gk][final]:
                            final = v
                    for v in gv:
                        if final != v:
                            key_to_overlays[over_key][1].remove(v)
        return key_to_overlays, properties

    def get_overlay_items(self, label="Overlay", libtype=None, ignore=None):
        if self.library.mc_type == "plex":
            items = self.library.search(label=label, libtype=libtype)
            return items if not ignore else [o for o in items if o.ratingKey not in ignore]

        if libtype:
            emby_items = self.library.EmbyServer.get_items(include_item_types=[libtype], params={"ParentId": self.library.EmbyServer.library_id, "Recursive": "True"})
        else:
            emby_items = self.library.EmbyServer.get_items(params={"ParentId": self.library.EmbyServer.library_id, "Recursive": "True"},include_item_types =["Series,Movie,Season,Episode"])
        all_emby_ids = [item.get("Id") for item in emby_items]
        all_emby_ids = all_emby_ids if not ignore else [o for o in all_emby_ids if o not in ignore]
        return all_emby_ids

    def remove_overlay(self, item, item_title, label, locations):
        #todo: delete overlay png from Emby plugin folder
        try:
            poster, _, _, _, _ = self.library.find_item_assets(item)
        except Failed:
            poster = None
        is_url = False
        poster_location = None
        if poster:
            poster_location = poster.location
        elif any([os.path.exists(loc) for loc in locations]):
            poster_location = next((loc for loc in locations if os.path.exists(loc)))
        # if not poster_location:
        #     is_url = True
        #     try:
        #         poster_location = self.library.item_posters(item)
        #     except Failed:
        #         pass
        if poster_location:
            # self.library.upload_poster(item, poster_location, url=is_url)
            self.library.edit_tags("label", item, remove_tags=[label], do_print=False)
            for loc in locations:
                if os.path.exists(loc):
                    os.remove(loc)
        else:
            logger.error(f"No Poster found to restore for {item_title}")
