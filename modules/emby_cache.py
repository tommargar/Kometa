import hashlib
import json
import os
import random
import re
import sqlite3
import threading
from contextlib import closing
from datetime import datetime, timedelta

from modules import util

logger = util.logger


class EmbyCacheCoordinator:
    """Run-wide Emby cache coordinator backed by per-server databases."""

    def __init__(self, persistent_cache):
        self.persistent_cache = persistent_cache
        self._lock = threading.RLock()
        self._db_lock = threading.Lock()
        self._servers = {}
        self._libraries = {}

    @staticmethod
    def server_key(server_url, server_id=None):
        server_id = str(server_id or "").strip()
        return server_id if server_id else str(server_url or "").strip().rstrip("/").lower()

    def bind_server(self, server_key):
        server_key = str(server_key)
        with self._lock:
            return self._servers.setdefault(
                server_key,
                {
                    "items": {},
                    "fields": {},
                    "timestamps": {},
                    "validated_etags": {},
                    "converted": {},
                    "dirty": set(),
                    "cached_person_names": {},
                    "person_name_cache": {},
                    "bulk_person_cache": {},
                    "people_index": {},
                    "cached_tmdb_ids": {},
                    "roman_name_cache": {},
                    "people_cache": {},
                    "people_lib_cache": {},
                    "person_name_fix_cache": set(),
                    "false_friend_names": None,
                },
            )

    def bind_library(self, server_key, library_id):
        key = (str(server_key), str(library_id))
        with self._lock:
            return self._libraries.setdefault(
                key,
                {
                    "views": {},
                    "object_cache": {},
                    "filter_cache": {},
                    "search_choices_cache": {},
                },
            )

    def hydrate(self, server_key, item_ids):
        state = self.bind_server(server_key)
        missing = [str(item_id) for item_id in item_ids if str(item_id) not in state["items"]]
        if not missing:
            return
        with self._db_lock:
            records = self.persistent_cache.query_emby_items(server_key, missing)
        with self._lock:
            for item_id, record in records.items():
                state["items"][item_id] = record["data"]
                state["fields"][item_id] = set(record["fields"])
                state["timestamps"].setdefault(item_id, 0.0)

    def item_record(self, server_key, item_id):
        state = self.bind_server(server_key)
        item_id = str(item_id)
        data = state["items"].get(item_id)
        if data is None:
            return None
        return {
            "etag": data.get("Etag"),
            "fields": set(state["fields"].get(item_id, set(data))),
            "data": data,
        }

    @staticmethod
    def _merge_record(old_data, old_fields, item, declared_fields):
        item = dict(item or {})
        incoming_etag = item.get("Etag")
        old_etag = old_data.get("Etag") if old_data else None
        etag_changed = bool(incoming_etag and old_etag and incoming_etag != old_etag)
        if not old_data or etag_changed:
            return item, set(declared_fields or []) | set(item), etag_changed
        merged = dict(old_data)
        merged.update(item)
        return merged, set(old_fields or []) | set(declared_fields or []) | set(item), etag_changed

    def upsert_items(self, server_key, items, declared_fields=None, persist=True):
        state = self.bind_server(server_key)
        records = {}
        with self._lock:
            for item in items or []:
                item_id = str((item or {}).get("Id") or "")
                if not item_id:
                    continue
                merged, fields, etag_changed = self._merge_record(
                    state["items"].get(item_id),
                    state["fields"].get(item_id),
                    item,
                    declared_fields,
                )
                state["items"][item_id] = merged
                state["fields"][item_id] = fields
                state["timestamps"][item_id] = datetime.now().timestamp()
                if merged.get("Etag"):
                    state["validated_etags"][item_id] = merged["Etag"]
                if etag_changed:
                    state["converted"].pop(item_id, None)
                records[item_id] = {"etag": merged.get("Etag"), "fields": fields, "data": merged}
        if persist and records:
            with self._db_lock:
                self.persistent_cache.update_emby_items(server_key, records)
        return records

    def commit_library_snapshot(self, server_key, library_id, view_key, items, declared_fields=None):
        state = self.bind_server(server_key)
        records = {}
        for item in items or []:
            item_id = str((item or {}).get("Id") or "")
            if not item_id:
                continue
            merged, fields, _ = self._merge_record(
                state["items"].get(item_id),
                state["fields"].get(item_id),
                item,
                declared_fields,
            )
            records[item_id] = {"etag": merged.get("Etag"), "fields": fields, "data": merged}

        with self._db_lock:
            self.persistent_cache.replace_emby_library_snapshot(server_key, library_id, view_key, records)

        library_state = self.bind_library(server_key, library_id)
        with self._lock:
            previous_ids = set(library_state["views"].get(view_key, []))
            current_ids = list(records)
            changed_ids = set()
            for item_id, record in records.items():
                if (state["items"].get(item_id) or {}).get("Etag") != record["etag"]:
                    changed_ids.add(item_id)
                state["items"][item_id] = record["data"]
                state["fields"][item_id] = set(record["fields"])
                state["timestamps"][item_id] = datetime.now().timestamp()
                if record["etag"]:
                    state["validated_etags"][item_id] = record["etag"]
            removed_ids = previous_ids - set(current_ids)
            added_ids = set(current_ids) - previous_ids
            for item_id in changed_ids | removed_ids:
                state["converted"].pop(item_id, None)
                library_state["object_cache"].pop(item_id, None)
                library_state["filter_cache"].pop(item_id, None)
            library_state["views"][view_key] = current_ids
            if changed_ids or removed_ids or added_ids:
                library_state["search_choices_cache"].clear()
            referenced_ids = {
                str(view_item_id)
                for (bound_server, _), bound_library in self._libraries.items()
                if bound_server == str(server_key)
                for view_ids in bound_library["views"].values()
                for view_item_id in view_ids
            }
            for item_id in removed_ids:
                if item_id not in referenced_ids:
                    state["items"].pop(item_id, None)
                    state["fields"].pop(item_id, None)
                    state["timestamps"].pop(item_id, None)
                    state["validated_etags"].pop(item_id, None)
        return [records[item_id]["data"] for item_id in current_ids]

    def library_view(self, server_key, library_id, view_key):
        state = self.bind_server(server_key)
        library_state = self.bind_library(server_key, library_id)
        with self._lock:
            if view_key not in library_state["views"]:
                return None
            ids = list(library_state["views"][view_key])
            if any(item_id not in state["items"] for item_id in ids):
                return None
            return [state["items"][item_id] for item_id in ids]

    def invalidate_library_views(self, server_key, library_id):
        library_state = self.bind_library(server_key, library_id)
        with self._lock:
            library_state["views"].clear()
            library_state["object_cache"].clear()
            library_state["filter_cache"].clear()
            library_state["search_choices_cache"].clear()

    def library_item_ids(self, server_key, library_id):
        library_state = self.bind_library(server_key, library_id)
        with self._lock:
            return {
                str(item_id)
                for view_ids in library_state["views"].values()
                for item_id in view_ids
            }

    def invalidate_item(self, server_key, item_id, dirty=True):
        state = self.bind_server(server_key)
        item_id = str(item_id)
        try:
            item_id_int = int(item_id)
        except (TypeError, ValueError):
            item_id_int = None
        with self._lock:
            state["items"].pop(item_id, None)
            state["fields"].pop(item_id, None)
            state["timestamps"].pop(item_id, None)
            state["validated_etags"].pop(item_id, None)
            state["converted"].pop(item_id, None)
            if dirty and item_id_int is not None:
                state["dirty"].add(item_id_int)
            for (bound_server, _), library_state in self._libraries.items():
                if bound_server == str(server_key):
                    library_state["object_cache"].pop(item_id, None)
                    library_state["filter_cache"].pop(item_id, None)
        with self._db_lock:
            self.persistent_cache.delete_emby_item(server_key, item_id)

    def query_tmdb_person_map_bulk(self, server_key, tmdb_ids, expiration):
        with self._db_lock:
            return self.persistent_cache.query_tmdb_person_map_bulk(server_key, tmdb_ids, expiration)

    def update_tmdb_person_map(self, server_key, expired, tmdb_id, **kwargs):
        with self._db_lock:
            return self.persistent_cache.update_tmdb_person_map(server_key, expired, tmdb_id, **kwargs)

    def query_false_friend_names(self, server_key):
        state = self.bind_server(server_key)
        with self._lock:
            if state["false_friend_names"] is not None:
                return set(state["false_friend_names"])
        with self._db_lock:
            names = self.persistent_cache.query_false_friend_names(server_key)
        with self._lock:
            state["false_friend_names"] = set(names)
        return set(names)

    def add_false_friend_name(self, server_key, name):
        normalized = str(name or "").strip().casefold()
        if not normalized:
            return False
        state = self.bind_server(server_key)
        with self._lock:
            if state["false_friend_names"] is not None and normalized in state["false_friend_names"]:
                return False
        with self._db_lock:
            inserted = self.persistent_cache.add_false_friend_name(server_key, normalized)
        if inserted:
            with self._lock:
                if state["false_friend_names"] is None:
                    state["false_friend_names"] = set()
                state["false_friend_names"].add(normalized)
        return inserted

    def close(self):
        self.persistent_cache.close()


class EmbyCacheDatabase:
    """One SQLite cache file per Emby server under ``emby_cache``."""

    def __init__(self, default_dir, expiration=30):
        self.cache_dir = os.path.join(os.path.abspath(default_dir), "emby_cache")
        self.expiration = expiration
        self._connections = {}
        os.makedirs(self.cache_dir, exist_ok=True)
        if logger:
            logger.info(f"Using dedicated Emby cache directory at {self.cache_dir}")

    def cache_path(self, server_key):
        raw_key = str(server_key or "").strip()
        safe_key = re.sub(r"[^A-Za-z0-9._-]+", "_", raw_key).strip("._")[:96] or "server"
        if safe_key != raw_key:
            digest = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()[:12]
            safe_key = f"{safe_key}-{digest}"
        return os.path.join(self.cache_dir, f"{safe_key}.db")

    def connection(self, server_key):
        server_key = str(server_key)
        if server_key not in self._connections:
            connection = sqlite3.connect(self.cache_path(server_key), check_same_thread=False)
            connection.row_factory = sqlite3.Row
            try:
                connection.execute("PRAGMA journal_mode=WAL")
            except sqlite3.Error:
                pass
            with closing(connection.cursor()) as cursor:
                cursor.execute("""CREATE TABLE IF NOT EXISTS emby_item_cache (
                    item_id TEXT PRIMARY KEY,
                    etag TEXT,
                    fields_json TEXT,
                    item_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL)""")
                cursor.execute("""CREATE TABLE IF NOT EXISTS emby_library_items (
                    library_id TEXT NOT NULL,
                    view_key TEXT NOT NULL,
                    item_id TEXT NOT NULL,
                    etag TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(library_id, view_key, item_id))""")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_emby_library_items_item ON emby_library_items(item_id)")
                cursor.execute("""CREATE TABLE IF NOT EXISTS tmdb_person_map (
                    tmdb_id INTEGER PRIMARY KEY,
                    emby_id TEXT,
                    name TEXT,
                    alias TEXT,
                    meta_json TEXT,
                    expiration_date TEXT)""")
                cursor.execute("CREATE TABLE IF NOT EXISTS false_friend_names (name TEXT PRIMARY KEY)")
            connection.commit()
            self._connections[server_key] = connection
        return self._connections[server_key]

    def close(self):
        for connection in self._connections.values():
            try:
                connection.execute("PRAGMA journal_mode=DELETE")
                connection.close()
            except sqlite3.Error:
                pass
        self._connections.clear()

    def query_emby_items(self, server_key, item_ids):
        normalized_ids = [str(item_id) for item_id in item_ids if item_id not in (None, "")]
        if not normalized_ids:
            return {}
        records = {}
        with self.connection(server_key) as connection:
            with closing(connection.cursor()) as cursor:
                for start in range(0, len(normalized_ids), 500):
                    batch = normalized_ids[start:start + 500]
                    placeholders = ",".join("?" for _ in batch)
                    cursor.execute(
                        f"SELECT item_id, etag, fields_json, item_json FROM emby_item_cache "
                        f"WHERE item_id IN ({placeholders})",
                        batch,
                    )
                    for row in cursor.fetchall():
                        try:
                            item_data = json.loads(row["item_json"]) if row["item_json"] else {}
                        except (TypeError, ValueError):
                            continue
                        try:
                            fields = set(json.loads(row["fields_json"])) if row["fields_json"] else set(item_data)
                        except (TypeError, ValueError):
                            fields = set(item_data)
                        records[str(row["item_id"])] = {
                            "etag": row["etag"],
                            "fields": fields,
                            "data": item_data,
                        }
        return records

    def update_emby_items(self, server_key, records):
        if not records:
            return
        now = datetime.now().isoformat(timespec="seconds")
        with self.connection(server_key) as connection:
            with closing(connection.cursor()) as cursor:
                cursor.executemany(
                    """
                    INSERT INTO emby_item_cache(item_id, etag, fields_json, item_json, updated_at)
                    VALUES(?, ?, ?, ?, ?)
                    ON CONFLICT(item_id) DO UPDATE SET
                        etag = excluded.etag,
                        fields_json = excluded.fields_json,
                        item_json = excluded.item_json,
                        updated_at = excluded.updated_at
                    """,
                    [
                        (
                            str(item_id),
                            record.get("etag"),
                            json.dumps(sorted(set(record.get("fields") or (record.get("data") or {}).keys())), ensure_ascii=False),
                            json.dumps(record.get("data") or {}, ensure_ascii=False),
                            now,
                        )
                        for item_id, record in records.items()
                    ],
                )

    def replace_emby_library_snapshot(self, server_key, library_id, view_key, records):
        now = datetime.now().isoformat(timespec="seconds")
        library_id, view_key = str(library_id), str(view_key)
        with self.connection(server_key) as connection:
            with closing(connection.cursor()) as cursor:
                cursor.executemany(
                    """
                    INSERT INTO emby_item_cache(item_id, etag, fields_json, item_json, updated_at)
                    VALUES(?, ?, ?, ?, ?)
                    ON CONFLICT(item_id) DO UPDATE SET
                        etag = excluded.etag,
                        fields_json = excluded.fields_json,
                        item_json = excluded.item_json,
                        updated_at = excluded.updated_at
                    """,
                    [
                        (
                            str(item_id),
                            record.get("etag"),
                            json.dumps(sorted(set(record.get("fields") or (record.get("data") or {}).keys())), ensure_ascii=False),
                            json.dumps(record.get("data") or {}, ensure_ascii=False),
                            now,
                        )
                        for item_id, record in records.items()
                    ],
                )
                cursor.execute(
                    "DELETE FROM emby_library_items WHERE library_id = ? AND view_key = ?",
                    (library_id, view_key),
                )
                cursor.executemany(
                    "INSERT INTO emby_library_items(library_id, view_key, item_id, etag, updated_at) VALUES(?, ?, ?, ?, ?)",
                    [
                        (library_id, view_key, str(item_id), record.get("etag"), now)
                        for item_id, record in records.items()
                    ],
                )
                cursor.execute(
                    """
                    DELETE FROM emby_item_cache
                    WHERE NOT EXISTS (
                        SELECT 1 FROM emby_library_items
                        WHERE emby_library_items.item_id = emby_item_cache.item_id
                    )
                    """
                )

    def delete_emby_item(self, server_key, item_id):
        with self.connection(server_key) as connection:
            with closing(connection.cursor()) as cursor:
                cursor.execute("DELETE FROM emby_item_cache WHERE item_id = ?", (str(item_id),))

    def query_tmdb_person_map_bulk(self, server_key, tmdb_ids, expiration):
        mapping, missing_ids, expired_ids = {}, set(tmdb_ids or []), set()
        if not tmdb_ids:
            return mapping, missing_ids, expired_ids
        qmarks = ",".join("?" for _ in tmdb_ids)
        with self.connection(server_key) as connection:
            with closing(connection.cursor()) as cursor:
                cursor.execute(f"SELECT * FROM tmdb_person_map WHERE tmdb_id IN ({qmarks})", tuple(tmdb_ids))
                for row in cursor.fetchall():
                    tid = int(row["tmdb_id"])
                    try:
                        meta = json.loads(row["meta_json"]) if row["meta_json"] else {}
                    except (TypeError, ValueError):
                        meta = {}
                    mapping[tid] = {"emby_id": row["emby_id"], "name": row["name"], "alias": row["alias"], "meta": meta}
                    missing_ids.discard(tid)
                    if row["expiration_date"]:
                        cached_at = datetime.strptime(row["expiration_date"], "%Y-%m-%d")
                        if (datetime.now() - cached_at).days > expiration:
                            expired_ids.add(tid)
        return mapping, missing_ids, expired_ids

    def update_tmdb_person_map(self, server_key, expired, tmdb_id, emby_id=None, name=None, alias=None, meta_patch=None, expiration=None):
        expiration = self.expiration if expiration is None else expiration
        expiration_date = datetime.now() if expired else datetime.now() - timedelta(days=random.randint(1, expiration))
        with self.connection(server_key) as connection:
            with closing(connection.cursor()) as cursor:
                cursor.execute("INSERT OR IGNORE INTO tmdb_person_map(tmdb_id) VALUES(?)", (tmdb_id,))
                cursor.execute("SELECT emby_id, name, alias, meta_json FROM tmdb_person_map WHERE tmdb_id = ?", (tmdb_id,))
                row = cursor.fetchone()
                try:
                    current_meta = json.loads(row["meta_json"]) if row and row["meta_json"] else {}
                except (TypeError, ValueError):
                    current_meta = {}
                if meta_patch:
                    current_meta.update(meta_patch)
                cursor.execute(
                    """
                    UPDATE tmdb_person_map
                    SET emby_id = ?, name = ?, alias = ?, meta_json = ?, expiration_date = ?
                    WHERE tmdb_id = ?
                    """,
                    (
                        emby_id if emby_id is not None else (row["emby_id"] if row else None),
                        name if name is not None else (row["name"] if row else None),
                        alias if alias is not None else (row["alias"] if row else None),
                        json.dumps(current_meta, ensure_ascii=False) if current_meta else None,
                        expiration_date.strftime("%Y-%m-%d"),
                        tmdb_id,
                    ),
                )

    def query_false_friend_names(self, server_key):
        with self.connection(server_key) as connection:
            with closing(connection.cursor()) as cursor:
                cursor.execute("SELECT name FROM false_friend_names")
                return {
                    str(row["name"]).strip().casefold()
                    for row in cursor.fetchall()
                    if row["name"]
                }

    def add_false_friend_name(self, server_key, name):
        with self.connection(server_key) as connection:
            with closing(connection.cursor()) as cursor:
                cursor.execute("INSERT OR IGNORE INTO false_friend_names(name) VALUES (?)", (str(name).casefold(),))
                return cursor.rowcount > 0
