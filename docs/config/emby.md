---
hide:
  - toc
---
# Emby Configuration

Kometa supports Emby as an alternative media server to Plex. This guide details how to configure Kometa to interact with your Emby server.

## Enabling Emby

To switch Kometa from Plex (default) to Emby, you must set the `server_type` attribute in the global `settings` block of your configuration file.

```yaml
settings:
  server_type: emby
```

## Emby Connection Settings

The `emby` block contains the connection details for your Emby server. This can be defined globally (applied to all libraries) or within specific library definitions.

```yaml
emby:
  url: http://192.168.1.10:8096
  api_key: YOUR_EMBY_API_KEY
  user_id: YOUR_EMBY_USER_ID
  overlay_destination_folder: /mnt/share/overlays
  timeout: 60
  verify_ssl: true
```

| Attribute | Description & Values |
| :--- | :--- |
| `url` | **Description:** The URL of your Emby server.<br>**Required:** Yes<br>**Values:** URL (e.g., `http://localhost:8096`) |
| `api_key` | **Description:** The API key generated in Emby for Kometa.<br>**Required:** Yes<br>**Values:** String |
| `user_id` | **Description:** The User ID of the Emby user Kometa will use to access the library.<br>**Required:** Yes<br>**Values:** String |
| `overlay_destination_folder` | **Description:** The local path or network share where Kometa will save generated overlay images. This folder must be accessible by the Emby server (specifically the Emby Overlay Plugin).<br>**Required:** Yes (if using Overlays)<br>**Values:** Path |
| `timeout` | **Description:** The timeout in seconds for requests to the Emby API.<br>**Default:** `60`<br>**Values:** Integer |
| `verify_ssl` | **Description:** Whether to verify SSL certificates when connecting to Emby.<br>**Default:** `true`<br>**Values:** `true` or `false` |
| `db_cache` | **Description:** The size of the database cache.<br>**Values:** Integer |

## Emby Specific Settings

There are additional settings available in the `settings` block that are specific to Emby integration.

| Attribute | Description & Values |
| :--- | :--- |
| `overlay_refresh_emby_items` | **Description:** When enabled, Kometa will trigger a refresh of items in Emby after generating overlays. This helps Emby pick up the new images immediately.<br>**Default:** `false`<br>**Values:** `true` or `false` |
| `overlay_artwork_filetype` | **Description:** Controls the file format of the generated overlay images. Using `png` is recommended for Emby to ensure transparency is preserved.<br>**Default:** `webp_lossy`<br>**Values:** `jpg`, `png`, `webp_lossy`, `webp_lossless` |

## Integration Specifics & Limitations

### Overlays
Unlike the Plex integration where overlays are uploaded directly to the server as poster art, the Emby integration works differently due to API limitations or design choices.

*   **File-Based Overlays:** Kometa generates the overlay images and saves them to the directory specified in `overlay_destination_folder`. The format is controlled by `overlay_artwork_filetype`.
*   **Emby Plugin:** An Emby plugin (e.g., `ImageEnhancer`) is required on the Emby server side. This plugin monitors the folder specified in `overlay_destination_folder` and applies the overlays to the items in Emby's UI.
*   **Matching:** The images are typically named using the internal Emby Item ID to ensure the plugin applies the correct overlay to the correct item.

### Libraries
When configuring libraries in `config.yml`, you define them similarly to Plex libraries, but ensure the `emby` connection details are present (either globally or per library).

```yaml
libraries:
  Movies:
    library_name: Movies
    emby:
      url: http://192.168.1.10:8096
      api_key: ...
      ...
```

!!! important "One Server per Library"
    Each library is bound to exactly **one** media server instance. A single library definition cannot span multiple Emby (or Plex) servers simultaneously.

    Mixed configurations are supported at the **config level** — you can have some libraries pointing to a Plex server and others pointing to an Emby server within the same `config.yml`. However, you cannot configure a single library to be backed by more than one server at once.

### Authentication
Emby authentication requires both an `api_key` and a `user_id`. This differs from Plex which uses a token.

### Operations
Some Plex-specific operations (like `mass_collection_mode`) might not be applicable or behave differently in Emby.

### Smart Collections / Auto Collections
Emby does not support "Smart Collections" (collections dynamically updated by the server based on search criteria) like Plex does.

*   **Static Only:** All collections created by Kometa in Emby are "static". This means Kometa determines which items belong in the collection at runtime and adds them. The collection will not automatically update on the Emby server when new media is added; Kometa must run again to update the collection.
*   **Builders:** Builders that rely on Plex's smart filtering capabilities (e.g., `smart_filter`, `plex_search`) are not available or will behave differently (likely performing a one-time search and adding items statically).

### Sorting
In Plex, you can assign a specific Sort Title to an item within a collection, allowing for custom ordering inside that collection without affecting the item's placement in the main library. In Emby, the Sort Name is a global attribute of the item itself. Consequently, it is not possible to have a custom sort order for a collection that differs from the global library sort order, as changing the sort name affects the item everywhere.

### Composer Collections
Emby allows for the creation of collections based on Composers. This functionality is exclusive to the Emby integration within Kometa.