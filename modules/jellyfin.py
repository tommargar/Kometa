from modules.library import Library
from modules.util import Failed


class Jellyfin(Library):
    """
    The Jellyfin class handles all interaction with the Jellyfin Media Server.
    It inherits from the Library class and provides methods to fetch items,
    update metadata, manage collections, and handle images.
    
    This class is currently a placeholder for future implementation.
    """
    def __init__(self, config, params):
        """
        Initialize the Jellyfin library connection.
        
        TODO:
        - Parse configuration parameters (URL, API Key, User ID).
        - Establish connection to Jellyfin Server (using requests or a client library).
        - Validate connection and library existence.
        """
        super().__init__(config, params)
        self.is_jellyfin = True

    def notify(self, text, collection=None, critical=True):
        """
        Send a notification message to the configured notification services.
        
        :param text: The message text.
        :param collection: The collection name involved (optional).
        :param critical: Boolean indicating if the notification is critical.
        """
        raise Failed("Jellyfin backend not implemented")

    def notify_delete(self, message):
        """
        Send a notification about item deletion.
        
        :param message: The deletion message.
        """
        raise Failed("Jellyfin backend not implemented")

    def _upload_image(self, item, image):
        """
        Internal method to upload an image to a Jellyfin item.
        
        TODO:
        - Implement API call to /Items/{Id}/Images/{Type}
        - Handle different image types (Primary, Backdrop, etc.)
        """
        raise Failed("Jellyfin backend not implemented")

    def upload_poster(self, item, image, url=False):
        """
        Upload a poster (Primary image) to a Jellyfin item.
        
        :param item: The item object.
        :param image: The ImageData object or URL string.
        :param url: Boolean indicating if 'image' is a URL.
        """
        raise Failed("Jellyfin backend not implemented")

    def upload_poster_overlay(self, item, image, url=False):
        """
        Upload a poster overlay. In Jellyfin/Emby, this usually means uploading a new Primary image
        that contains the overlay, as they don't support separate overlay layers like Plex might.
        
        :param item: The item object.
        :param image: Path to the overlay image.
        """
        raise Failed("Jellyfin backend not implemented")

    def image_update(self, item, image, tmdb=None, title=None, poster=True):
        """
        Update the image of an item, handling locking/unlocking if necessary.
        
        :param item: The item object.
        :param image: The new image to set.
        :param tmdb: Optional TMDb ID for fallback/lookup.
        :param title: Title of the item.
        :param poster: True for poster, False for background.
        """
        raise Failed("Jellyfin backend not implemented")

    def reload(self, item, force=False):
        """
        Reload the item metadata from the server.
        
        :param item: The item object to reload.
        :param force: Force refresh from server.
        :return: The reloaded item object.
        """
        return item

    def edit_tags(self, attr, obj, add_tags=None, remove_tags=None, sync_tags=None, do_print=True, locked=True, is_locked=None):
        """
        Edit tags (Tags, Genres, etc.) for an item.
        
        :param attr: The attribute to edit (e.g., 'label', 'genre').
        :param obj: The item object.
        :param add_tags: List of tags to add.
        :param remove_tags: List of tags to remove.
        :param sync_tags: List of tags to sync (overwrite).
        """
        raise Failed("Jellyfin backend not implemented")

    def item_labels(self, item):
        """
        Get the labels (Tags) for an item.
        
        :param item: The item object.
        :return: List of labels/tags.
        """
        return []

    def find_poster_url(self, item):
        """
        Find the URL of the poster for an item (e.g. from TMDb).
        
        :param item: The item object.
        :return: URL string.
        """
        raise Failed("Jellyfin backend not implemented")

    def item_posters(self, item, providers=None):
        """
        Get available posters for an item from the server.
        
        :param item: The item object.
        :param providers: List of providers to check.
        :return: List of available poster URLs/objects.
        """
        return []

    def get_all(self, builder_level=None, load=False):
        """
        Get all items from the library.
        
        :param builder_level: The level to fetch (movie, show, season, episode, artist, album).
        :param load: Boolean to force reload.
        :return: List of item objects.
        """
        return []

    def get_seasons(self, show):
        """
        Get seasons for a show.
        
        :param show: The show object.
        :return: List of season objects.
        """
        return []

    def get_episodes(self, season):
        """
        Get episodes for a season.
        
        :param season: The season object.
        :return: List of episode objects.
        """
        return []

    def get_native_item(self, item_id):
        """
        Get the raw JSON response for an item from Jellyfin.

        :param item_id: The Jellyfin Item ID.
        :return: JSON dict.
        """
        return None

    def get_all_native(self, builder_level=None, load=False):
        """
        Get all items as raw JSON objects.
        """
        return []

    def get_ratings(self, item):
        """
        Get ratings for an item (e.g. CommunityRating, CriticRating).
        
        :param item: The item object.
        :return: Dictionary of ratings.
        """
        return {}

    def apply_batch_operations(self, *, label_edits, genre_edits, rating_edits,
                               content_edits, studio_edits, date_edits, remove_edits,
                               reset_edits, lock_edits, unlock_edits, ep_rating_edits,
                               ep_remove_edits, ep_reset_edits, ep_lock_edits,
                               ep_unlock_edits, name_display):
        """
        Apply batch operations to multiple items.
        Jellyfin might not support batch editing natively, so this might need to iterate
        over items and update them individually.
        """
        raise Failed("Jellyfin batch operations not implemented")

    def needs_collection_mode_update(self, collection, mode):
        """
        Check if a collection needs its display mode updated.
        """
        return False

    def get_provider_ids(self, item):
        """
        Get provider IDs (IMDb, TMDb, TVDb) for an item.
        
        :param item: The item object.
        :return: Dictionary or list of IDs.
        """
        return {}

    def get_all_collections(self):
        """
        Get all collections in the library.
        """
        return []

    def search(self, **kwargs):
        """
        Search the library for items matching the criteria.
        
        :param kwargs: Search arguments (title, etc.).
        :return: List of matching items.
        """
        return []
