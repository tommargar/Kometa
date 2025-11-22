from modules.library import Library
from modules.util import Failed


class Jellyfin(Library):
    def __init__(self, config, params):
        super().__init__(config, params)
        self.is_jellyfin = True

    def notify(self, text, collection=None, critical=True):
        raise Failed("Jellyfin backend not implemented")

    def notify_delete(self, message):
        raise Failed("Jellyfin backend not implemented")

    def _upload_image(self, item, image):
        raise Failed("Jellyfin backend not implemented")

    def upload_poster(self, item, image, url=False):
        raise Failed("Jellyfin backend not implemented")

    def upload_poster_overlay(self, item, image, url=False):
        raise Failed("Jellyfin backend not implemented")

    def image_update(self, item, image, tmdb=None, title=None, poster=True):
        raise Failed("Jellyfin backend not implemented")

    def reload(self, item, force=False):
        return item

    def edit_tags(self, attr, obj, add_tags=None, remove_tags=None, sync_tags=None, do_print=True, locked=True, is_locked=None):
        raise Failed("Jellyfin backend not implemented")

    def item_labels(self, item):
        return []

    def find_poster_url(self, item):
        raise Failed("Jellyfin backend not implemented")

    def item_posters(self, item, providers=None):
        return []

    def get_all(self, builder_level=None, load=False):
        return []

    def get_seasons(self, show):
        return []

    def get_episodes(self, season):
        return []

    def get_native_emby_item(self, emby_item_id):
        return None

    def get_all_native(self, builder_level=None, load=False):
        return []

    def get_ratings(self, item):
        return {}

    def apply_batch_operations(self, *, label_edits, genre_edits, rating_edits,
                               content_edits, studio_edits, date_edits, remove_edits,
                               reset_edits, lock_edits, unlock_edits, ep_rating_edits,
                               ep_remove_edits, ep_reset_edits, ep_lock_edits,
                               ep_unlock_edits, name_display):
        raise Failed("Jellyfin batch operations not implemented")

    def needs_collection_mode_update(self, collection, mode):
        return False

    def item_has_year(self, item):
        return hasattr(item, "year") and item.year is not None

    def get_provider_ids(self, item):
        return {}

    def get_all_collections(self):
        return []

    def search(self, **kwargs):
        return []
