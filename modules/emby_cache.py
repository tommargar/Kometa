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
        self.expiration = persistent_cache.expiration
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
            referenced_ids = {str(view_item_id) for (bound_server, _), bound_library in self._libraries.items() if bound_server == str(server_key) for view_ids in bound_library["views"].values() for view_item_id in view_ids}
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
            return {str(item_id) for view_ids in library_state["views"].values() for item_id in view_ids}

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

    def query_emby_person_identities(self, server_key, tmdb_ids=None):
        with self._db_lock:
            return self.persistent_cache.query_emby_person_identities(server_key, tmdb_ids)

    def update_emby_person_identity(self, server_key, *args, **kwargs):
        with self._db_lock:
            return self.persistent_cache.update_emby_person_identity(server_key, *args, **kwargs)

    def update_emby_person_identities(self, server_key, identities):
        with self._db_lock:
            return self.persistent_cache.update_emby_person_identities(server_key, identities)

    def update_emby_person_verifications(self, server_key, identities):
        with self._db_lock:
            return self.persistent_cache.update_emby_person_verifications(server_key, identities)

    def clear_emby_person_identity_mapping(self, server_key, tmdb_id):
        with self._db_lock:
            return self.persistent_cache.clear_emby_person_identity_mapping(server_key, tmdb_id)

    def query_emby_people_item_states(self, server_key, item_ids):
        with self._db_lock:
            return self.persistent_cache.query_emby_people_item_states(server_key, item_ids)

    def update_emby_people_item_state(self, server_key, *args):
        with self._db_lock:
            return self.persistent_cache.update_emby_people_item_state(server_key, *args)

    def replace_emby_item_people_links(self, server_key, item_id, tmdb_ids):
        with self._db_lock:
            return self.persistent_cache.replace_emby_item_people_links(server_key, item_id, tmdb_ids)

    def query_emby_items_for_people(self, server_key, tmdb_ids):
        with self._db_lock:
            return self.persistent_cache.query_emby_items_for_people(server_key, tmdb_ids)

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

    def query_tvdb_people_external_ids(self, tvdb_ids, expiration):
        with self._db_lock:
            return self.persistent_cache.query_tvdb_people_external_ids(
                "external-providers",
                tvdb_ids,
                expiration,
            )

    def update_tvdb_people_external_ids(self, external_ids):
        with self._db_lock:
            return self.persistent_cache.update_tvdb_people_external_ids(
                "external-providers",
                external_ids,
            )

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
                cursor.execute("""CREATE TABLE IF NOT EXISTS tvdb_people_external_ids (
                    tvdb_id INTEGER PRIMARY KEY,
                    tmdb_id INTEGER,
                    imdb_id TEXT,
                    wikidata_id TEXT,
                    expiration_date TEXT)""")
                cursor.execute("PRAGMA table_info(tvdb_people_external_ids)")
                if "wikidata_id" not in {row[1] for row in cursor.fetchall()}:
                    cursor.execute("ALTER TABLE tvdb_people_external_ids ADD COLUMN wikidata_id TEXT")
                cursor.execute("""CREATE TABLE IF NOT EXISTS emby_person_identity (
                    tmdb_id INTEGER PRIMARY KEY,
                    imdb_id TEXT,
                    tvdb_id TEXT,
                    wikidata_id TEXT,
                    base_name TEXT NOT NULL,
                    normalized_name TEXT NOT NULL,
                    name_index INTEGER,
                    display_name TEXT NOT NULL,
                    emby_id TEXT,
                    emby_etag TEXT,
                    emby_signature TEXT,
                    duplicate_emby_ids TEXT,
                    verified_at TEXT,
                    external_verified_at TEXT,
                    canonical_id INTEGER)""")
                cursor.execute("PRAGMA table_info(emby_person_identity)")
                identity_columns = [row[1] for row in cursor.fetchall()]
                if "emby_etag" not in identity_columns:
                    cursor.execute("ALTER TABLE emby_person_identity ADD COLUMN emby_etag TEXT")
                if "emby_signature" not in identity_columns:
                    cursor.execute("ALTER TABLE emby_person_identity ADD COLUMN emby_signature TEXT")
                if "duplicate_emby_ids" not in identity_columns:
                    cursor.execute("ALTER TABLE emby_person_identity ADD COLUMN duplicate_emby_ids TEXT")
                if "canonical_id" not in identity_columns:
                    cursor.execute("ALTER TABLE emby_person_identity ADD COLUMN canonical_id INTEGER")
                if "external_verified_at" not in identity_columns:
                    cursor.execute("ALTER TABLE emby_person_identity ADD COLUMN external_verified_at TEXT")
                if "wikidata_id" not in identity_columns:
                    cursor.execute("ALTER TABLE emby_person_identity ADD COLUMN wikidata_id TEXT")
                cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_emby_person_identity_name_index " "ON emby_person_identity(normalized_name, name_index) " "WHERE name_index IS NOT NULL")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_emby_person_identity_emby ON emby_person_identity(emby_id)")
                cursor.execute("""CREATE TABLE IF NOT EXISTS emby_people_item_state (
                    library_id TEXT NOT NULL,
                    item_id TEXT PRIMARY KEY,
                    tmdb_id INTEGER NOT NULL,
                    emby_etag TEXT,
                    credits_source TEXT NOT NULL DEFAULT 'tmdb',
                    source_credits_hash TEXT,
                    applied_hash TEXT,
                    sync_version INTEGER NOT NULL,
                    last_success TEXT)""")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_emby_people_item_state_library ON emby_people_item_state(library_id)")
                cursor.execute("""CREATE TABLE IF NOT EXISTS emby_item_person (
                    item_id TEXT NOT NULL,
                    tmdb_id INTEGER NOT NULL,
                    PRIMARY KEY(item_id, tmdb_id))""")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_emby_item_person_tmdb ON emby_item_person(tmdb_id)")
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
                    batch = normalized_ids[start : start + 500]
                    placeholders = ",".join("?" for _ in batch)
                    cursor.execute(
                        f"SELECT item_id, etag, fields_json, item_json FROM emby_item_cache " f"WHERE item_id IN ({placeholders})",
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
                    [(library_id, view_key, str(item_id), record.get("etag"), now) for item_id, record in records.items()],
                )
                cursor.execute("""
                    DELETE FROM emby_item_cache
                    WHERE NOT EXISTS (
                        SELECT 1 FROM emby_library_items
                        WHERE emby_library_items.item_id = emby_item_cache.item_id
                    )
                    """)

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

    def query_emby_person_identities(self, server_key, tmdb_ids=None):
        identities = {}
        with self.connection(server_key) as connection:
            with closing(connection.cursor()) as cursor:
                params = []
                sql = "SELECT * FROM emby_person_identity"
                if tmdb_ids is not None:
                    normalized_ids = sorted({int(tmdb_id) for tmdb_id in tmdb_ids if str(tmdb_id).lstrip("-").isdigit()})
                    if not normalized_ids:
                        return identities
                    sql += f" WHERE tmdb_id IN ({','.join('?' for _ in normalized_ids)})"
                    params.extend(normalized_ids)
                cursor.execute(sql, tuple(params))
                for row in cursor.fetchall():
                    identities[int(row["tmdb_id"])] = {
                        "tmdb_id": int(row["tmdb_id"]),
                        "imdb_id": row["imdb_id"],
                        "tvdb_id": row["tvdb_id"],
                        "wikidata_id": row["wikidata_id"],
                        "base_name": row["base_name"],
                        "normalized_name": row["normalized_name"],
                        "name_index": row["name_index"],
                        "display_name": row["display_name"],
                        "emby_id": row["emby_id"],
                        "emby_etag": row["emby_etag"],
                        "emby_signature": row["emby_signature"],
                        "duplicate_emby_ids": json.loads(row["duplicate_emby_ids"]) if row["duplicate_emby_ids"] else [],
                        "verified_at": row["verified_at"],
                        "external_verified_at": row["external_verified_at"],
                        "canonical_id": row["canonical_id"],
                    }
        return identities

    def update_emby_person_identity(
        self,
        server_key,
        tmdb_id,
        base_name,
        normalized_name,
        display_name,
        name_index=None,
        emby_id=None,
        imdb_id=None,
        tvdb_id=None,
        wikidata_id=None,
        emby_etag=None,
        emby_signature=None,
        duplicate_emby_ids=None,
        verified_at=None,
        external_verified_at=None,
        canonical_id=None,
    ):
        with self.connection(server_key) as connection:
            with closing(connection.cursor()) as cursor:
                cursor.execute(
                    """
                    INSERT INTO emby_person_identity(
                        tmdb_id, imdb_id, tvdb_id, wikidata_id, base_name, normalized_name,
                        name_index, display_name, emby_id, emby_etag, emby_signature, duplicate_emby_ids, verified_at, external_verified_at, canonical_id
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(tmdb_id) DO UPDATE SET
                        imdb_id = excluded.imdb_id,
                        tvdb_id = excluded.tvdb_id,
                        wikidata_id = excluded.wikidata_id,
                        base_name = excluded.base_name,
                        normalized_name = excluded.normalized_name,
                        name_index = excluded.name_index,
                        display_name = excluded.display_name,
                        emby_id = excluded.emby_id,
                        emby_etag = excluded.emby_etag,
                        emby_signature = excluded.emby_signature,
                        duplicate_emby_ids = excluded.duplicate_emby_ids,
                        verified_at = excluded.verified_at,
                        external_verified_at = excluded.external_verified_at,
                        canonical_id = excluded.canonical_id
                    """,
                    (
                        int(tmdb_id),
                        imdb_id,
                        tvdb_id,
                        wikidata_id,
                        base_name,
                        normalized_name,
                        name_index,
                        display_name,
                        str(emby_id) if emby_id is not None else None,
                        emby_etag,
                        emby_signature,
                        json.dumps(sorted({str(value) for value in (duplicate_emby_ids or []) if str(value).isdigit()}, key=int)),
                        verified_at,
                        external_verified_at,
                        int(canonical_id) if canonical_id is not None else None,
                    ),
                )

    def update_emby_person_identities(self, server_key, identities):
        rows = list(identities or [])
        if not rows:
            return
        with self.connection(server_key) as connection:
            with closing(connection.cursor()) as cursor:
                cursor.execute("UPDATE emby_person_identity SET name_index = NULL")
                cursor.executemany(
                    """
                    INSERT INTO emby_person_identity(
                        tmdb_id, imdb_id, tvdb_id, wikidata_id, base_name, normalized_name,
                        name_index, display_name, emby_id, emby_etag, emby_signature, duplicate_emby_ids, verified_at, external_verified_at, canonical_id
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(tmdb_id) DO UPDATE SET
                        imdb_id = excluded.imdb_id,
                        tvdb_id = excluded.tvdb_id,
                        wikidata_id = excluded.wikidata_id,
                        base_name = excluded.base_name,
                        normalized_name = excluded.normalized_name,
                        name_index = excluded.name_index,
                        display_name = excluded.display_name,
                        emby_id = excluded.emby_id,
                        emby_etag = excluded.emby_etag,
                        emby_signature = excluded.emby_signature,
                        duplicate_emby_ids = excluded.duplicate_emby_ids,
                        verified_at = excluded.verified_at,
                        external_verified_at = excluded.external_verified_at,
                        canonical_id = excluded.canonical_id
                    """,
                    [
                        (
                            int(identity["tmdb_id"]),
                            identity.get("imdb_id"),
                            identity.get("tvdb_id"),
                            identity.get("wikidata_id"),
                            identity["base_name"],
                            identity["normalized_name"],
                            identity.get("name_index"),
                            identity["display_name"],
                            str(identity["emby_id"]) if identity.get("emby_id") is not None else None,
                            identity.get("emby_etag"),
                            identity.get("emby_signature"),
                            json.dumps(sorted({str(value) for value in (identity.get("duplicate_emby_ids") or []) if str(value).isdigit()}, key=int)),
                            identity.get("verified_at"),
                            identity.get("external_verified_at"),
                            int(identity["canonical_id"]) if identity.get("canonical_id") is not None else None,
                        )
                        for identity in rows
                    ],
                )

    def update_emby_person_verifications(self, server_key, identities):
        rows = list(identities or [])
        if not rows:
            return
        with self.connection(server_key) as connection:
            with closing(connection.cursor()) as cursor:
                cursor.executemany(
                    """
                    UPDATE emby_person_identity
                    SET emby_etag = ?, emby_signature = ?, verified_at = COALESCE(?, verified_at)
                    WHERE tmdb_id = ?
                    """,
                    [
                        (
                            identity.get("emby_etag"),
                            identity.get("emby_signature"),
                            identity.get("verified_at"),
                            int(identity["tmdb_id"]),
                        )
                        for identity in rows
                    ],
                )

    def clear_emby_person_identity_mapping(self, server_key, tmdb_id):
        with self.connection(server_key) as connection:
            with closing(connection.cursor()) as cursor:
                cursor.execute(
                    "UPDATE emby_person_identity SET emby_id = NULL, emby_etag = NULL, emby_signature = NULL, duplicate_emby_ids = NULL, verified_at = NULL WHERE tmdb_id = ?",
                    (int(tmdb_id),),
                )

    def query_tvdb_people_external_ids(self, server_key, tvdb_ids, expiration):
        ordered_ids = sorted({int(tvdb_id) for tvdb_id in tvdb_ids or []})
        if not ordered_ids:
            return {}, []
        cached = {}
        refresh_ids = []
        with self.connection(server_key) as connection:
            with closing(connection.cursor()) as cursor:
                for start in range(0, len(ordered_ids), 900):
                    batch = ordered_ids[start : start + 900]
                    placeholders = ",".join("?" for _ in batch)
                    cursor.execute(
                        f"SELECT * FROM tvdb_people_external_ids WHERE tvdb_id IN ({placeholders})",
                        batch,
                    )
                    for row in cursor.fetchall():
                        inserted = datetime.strptime(row["expiration_date"], "%Y-%m-%d")
                        if (datetime.now() - inserted).days > expiration:
                            refresh_ids.append(int(row["tvdb_id"]))
                        else:
                            cached[int(row["tvdb_id"])] = {
                                "tmdb_id": int(row["tmdb_id"]) if row["tmdb_id"] is not None else None,
                                "imdb_id": row["imdb_id"],
                                "wikidata_id": row["wikidata_id"],
                            }
        refresh_ids.extend(tvdb_id for tvdb_id in ordered_ids if tvdb_id not in cached and tvdb_id not in refresh_ids)
        return cached, refresh_ids

    def update_tvdb_people_external_ids(self, server_key, external_ids):
        rows = list((external_ids or {}).items())
        if not rows:
            return
        expiration_date = datetime.now().strftime("%Y-%m-%d")
        with self.connection(server_key) as connection:
            with closing(connection.cursor()) as cursor:
                cursor.executemany(
                    """
                    INSERT INTO tvdb_people_external_ids(tvdb_id, tmdb_id, imdb_id, wikidata_id, expiration_date)
                    VALUES(?, ?, ?, ?, ?)
                    ON CONFLICT(tvdb_id) DO UPDATE SET
                        tmdb_id = excluded.tmdb_id,
                        imdb_id = excluded.imdb_id,
                        wikidata_id = excluded.wikidata_id,
                        expiration_date = excluded.expiration_date
                    """,
                    [
                        (
                            int(tvdb_id),
                            int(data["tmdb_id"]) if data.get("tmdb_id") is not None else None,
                            data.get("imdb_id"),
                            data.get("wikidata_id"),
                            expiration_date,
                        )
                        for tvdb_id, data in rows
                    ],
                )

    def query_emby_people_item_states(self, server_key, item_ids):
        normalized_ids = sorted({str(item_id) for item_id in item_ids if item_id is not None})
        if not normalized_ids:
            return {}
        with self.connection(server_key) as connection:
            with closing(connection.cursor()) as cursor:
                cursor.execute(
                    f"SELECT * FROM emby_people_item_state " f"WHERE item_id IN ({','.join('?' for _ in normalized_ids)})",
                    normalized_ids,
                )
                return {str(row["item_id"]): dict(row) for row in cursor.fetchall()}

    def update_emby_people_item_state(
        self,
        server_key,
        library_id,
        item_id,
        tmdb_id,
        emby_etag,
        credits_source,
        source_credits_hash,
        applied_hash,
        sync_version,
    ):
        with self.connection(server_key) as connection:
            with closing(connection.cursor()) as cursor:
                cursor.execute(
                    """
                    INSERT INTO emby_people_item_state(
                        library_id, item_id, tmdb_id, emby_etag, credits_source,
                        source_credits_hash, applied_hash, sync_version, last_success
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(item_id) DO UPDATE SET
                        library_id = excluded.library_id,
                        tmdb_id = excluded.tmdb_id,
                        emby_etag = excluded.emby_etag,
                        credits_source = excluded.credits_source,
                        source_credits_hash = excluded.source_credits_hash,
                        applied_hash = excluded.applied_hash,
                        sync_version = excluded.sync_version,
                        last_success = excluded.last_success
                    """,
                    (
                        str(library_id),
                        str(item_id),
                        int(tmdb_id),
                        emby_etag,
                        str(credits_source),
                        source_credits_hash,
                        applied_hash,
                        int(sync_version),
                        datetime.now().isoformat(timespec="seconds"),
                    ),
                )

    def replace_emby_item_people_links(self, server_key, item_id, tmdb_ids):
        normalized_ids = sorted({int(tmdb_id) for tmdb_id in tmdb_ids if str(tmdb_id).lstrip("-").isdigit()})
        with self.connection(server_key) as connection:
            with closing(connection.cursor()) as cursor:
                cursor.execute("DELETE FROM emby_item_person WHERE item_id = ?", (str(item_id),))
                cursor.executemany(
                    "INSERT INTO emby_item_person(item_id, tmdb_id) VALUES(?, ?)",
                    [(str(item_id), tmdb_id) for tmdb_id in normalized_ids],
                )

    def query_emby_items_for_people(self, server_key, tmdb_ids):
        normalized_ids = sorted({int(tmdb_id) for tmdb_id in tmdb_ids if str(tmdb_id).lstrip("-").isdigit()})
        if not normalized_ids:
            return set()
        with self.connection(server_key) as connection:
            with closing(connection.cursor()) as cursor:
                cursor.execute(
                    f"SELECT DISTINCT item_id FROM emby_item_person " f"WHERE tmdb_id IN ({','.join('?' for _ in normalized_ids)})",
                    normalized_ids,
                )
                return {str(row[0]) for row in cursor.fetchall()}

    def query_false_friend_names(self, server_key):
        with self.connection(server_key) as connection:
            with closing(connection.cursor()) as cursor:
                cursor.execute("SELECT name FROM false_friend_names")
                return {str(row["name"]).strip().casefold() for row in cursor.fetchall() if row["name"]}

    def add_false_friend_name(self, server_key, name):
        with self.connection(server_key) as connection:
            with closing(connection.cursor()) as cursor:
                cursor.execute("INSERT OR IGNORE INTO false_friend_names(name) VALUES (?)", (str(name).casefold(),))
                return cursor.rowcount > 0
