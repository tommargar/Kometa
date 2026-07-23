import hashlib
import json
import os
import re
import sqlite3
import tempfile
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from xml.etree import ElementTree

from modules.util import Failed


VIDEO_EXTENSIONS = {".3g2", ".3gp", ".asf", ".avi", ".divx", ".flv", ".m2ts", ".m4v", ".mkv", ".mov", ".mp4", ".mpeg", ".mpg", ".mts", ".ogm", ".ogv", ".ts", ".vob", ".webm", ".wmv"}
ID_PATTERN = re.compile(r"(?i)(?:tmdb[-_. ]?(\d+)|imdb[-_. ]?(tt\d+))")
YEAR_PATTERN = re.compile(r"(?:^|[ ._\-(])(18\d{2}|19\d{2}|20\d{2})(?=$|[ ._)\-])")


FIELD_TAGS = {
    "title": "title",
    "sort_title": "sorttitle",
    "original_title": "originaltitle",
    "summary": "plot",
    "tagline": "tagline",
    "studio": "studio",
    "content_rating": "mpaa",
    "edition": "edition",
    "originally_available": "premiered",
    "critic_rating": "rating",
    "audience_rating": "userrating",
    "user_rating": "userrating",
}
LIST_TAGS = {
    "genre": "genre",
    "country": "country",
    "director": "director",
    "writer": "credits",
    "producer": "producer",
    "label": "tag",
    "collection": "set",
}


def _text(element, tag):
    child = element.find(tag)
    return child.text.strip() if child is not None and child.text else None


def _normalise_title(value):
    return re.sub(r"[^a-z0-9]+", "", str(value).casefold())


def _json_default(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


@dataclass
class NfoMovie:
    path: Path
    nfo_path: Path
    title: str
    year: int | None = None
    tmdb_id: int | None = None
    imdb_id: str | None = None

    @property
    def key(self):
        return str(self.path.resolve())


class NfoIndex:
    """Incremental movie-file index and Kodi/Jellyfin compatible NFO writer."""

    def __init__(self, database, roots, video_extensions=None, nfo_name="movie.nfo"):
        self.database = Path(database)
        self.roots = [Path(root).expanduser().resolve() for root in roots]
        self.video_extensions = {f".{str(ext).lower().lstrip('.')}" for ext in (video_extensions or VIDEO_EXTENSIONS)}
        self.nfo_name = nfo_name
        if not self.roots:
            raise Failed("NFO Error: At least one library folder is required")
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.database)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS nfo_movies (
                path TEXT PRIMARY KEY, size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL,
                nfo_path TEXT NOT NULL, nfo_mtime_ns INTEGER, title TEXT NOT NULL,
                year INTEGER, tmdb_id INTEGER, imdb_id TEXT, metadata_hash TEXT,
                last_seen_ns INTEGER NOT NULL
            )"""
        )
        self.connection.commit()

    def close(self):
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _nfo_path(self, video):
        if self.nfo_name == "movie.nfo":
            return video.with_name("movie.nfo")
        if self.nfo_name in {"file", "<file>.nfo"}:
            return video.with_suffix(".nfo")
        return video.with_name(self.nfo_name)

    def _read_identity(self, video, nfo_path):
        title = video.stem
        year = None
        tmdb_id = None
        imdb_id = None
        match = ID_PATTERN.search(str(video.parent.name) + " " + video.stem)
        if match:
            tmdb_id = int(match.group(1)) if match.group(1) else None
            imdb_id = match.group(2).lower() if match.group(2) else None
        title_year = YEAR_PATTERN.search(title)
        year_match = title_year or YEAR_PATTERN.search(video.parent.name)
        if year_match:
            year = int(year_match.group(1))
        if title_year:
            title = title[: title_year.start(1)].rstrip(" ._-(")
        title = re.sub(r"[._]+", " ", title).strip()
        if nfo_path.exists():
            try:
                root = ElementTree.parse(nfo_path).getroot()
                title = _text(root, "title") or title
                year_text = _text(root, "year")
                year = int(year_text) if year_text and year_text.isdigit() else year
                for unique_id in root.findall("uniqueid"):
                    value = (unique_id.text or "").strip()
                    id_type = (unique_id.get("type") or "").lower()
                    if id_type == "tmdb" and value.isdigit():
                        tmdb_id = int(value)
                    elif id_type == "imdb" and value.startswith("tt"):
                        imdb_id = value.lower()
                tmdb_text = _text(root, "tmdbid")
                imdb_text = _text(root, "imdbid")
                tmdb_id = int(tmdb_text) if tmdb_text and tmdb_text.isdigit() else tmdb_id
                imdb_id = imdb_text.lower() if imdb_text and imdb_text.startswith("tt") else imdb_id
            except (ElementTree.ParseError, OSError):
                pass
        return NfoMovie(video, nfo_path, title, year, tmdb_id, imdb_id)

    def scan(self):
        """Scan roots and return only new or filesystem-changed movies."""
        changed = []
        seen_at = time.time_ns()
        for root in self.roots:
            if not root.is_dir():
                raise Failed(f"NFO Error: Library folder not found: {root}")
            for video in root.rglob("*"):
                if not video.is_file() or video.suffix.lower() not in self.video_extensions:
                    continue
                stat = video.stat()
                nfo_path = self._nfo_path(video)
                nfo_mtime = nfo_path.stat().st_mtime_ns if nfo_path.exists() else None
                old = self.connection.execute("SELECT size, mtime_ns, nfo_mtime_ns FROM nfo_movies WHERE path = ?", (str(video.resolve()),)).fetchone()
                movie = self._read_identity(video, nfo_path)
                is_changed = old is None or old["size"] != stat.st_size or old["mtime_ns"] != stat.st_mtime_ns or old["nfo_mtime_ns"] != nfo_mtime
                self.connection.execute(
                    """INSERT INTO nfo_movies(path, size, mtime_ns, nfo_path, nfo_mtime_ns, title, year, tmdb_id, imdb_id, last_seen_ns)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(path) DO UPDATE SET size=excluded.size, mtime_ns=excluded.mtime_ns,
                       nfo_path=excluded.nfo_path, nfo_mtime_ns=excluded.nfo_mtime_ns, title=excluded.title,
                       year=excluded.year, tmdb_id=excluded.tmdb_id, imdb_id=excluded.imdb_id,
                       last_seen_ns=excluded.last_seen_ns""",
                    (movie.key, stat.st_size, stat.st_mtime_ns, str(nfo_path), nfo_mtime, movie.title, movie.year, movie.tmdb_id, movie.imdb_id, seen_at),
                )
                if is_changed:
                    changed.append(movie)
        self.connection.execute("DELETE FROM nfo_movies WHERE last_seen_ns != ?", (seen_at,))
        self.connection.commit()
        return changed

    def movies(self):
        rows = self.connection.execute("SELECT * FROM nfo_movies ORDER BY path").fetchall()
        return [NfoMovie(Path(row["path"]), Path(row["nfo_path"]), row["title"], row["year"], row["tmdb_id"], row["imdb_id"]) for row in rows]

    def match(self, mapping_name, metadata):
        match_data = metadata.get("match") if isinstance(metadata.get("match"), dict) else {}
        mapping_id = match_data.get("mapping_id", metadata.get("mapping_id"))
        if mapping_id is None and (isinstance(mapping_name, int) or str(mapping_name).lower().startswith("tt")):
            mapping_id = mapping_name
        title = str(match_data.get("title", metadata.get("alt_title", mapping_name)))
        year = match_data.get("year", metadata.get("year"))
        output = []
        for movie in self.movies():
            if mapping_id is not None:
                value = str(mapping_id).lower()
                if (value.startswith("tt") and movie.imdb_id == value) or (value.isdigit() and movie.tmdb_id == int(value)):
                    output.append(movie)
            elif _normalise_title(movie.title) == _normalise_title(title) and (year is None or movie.year == int(year)):
                output.append(movie)
        return output

    def _set_text(self, root, tag, value):
        nodes = root.findall(tag)
        node = nodes[0] if nodes else ElementTree.SubElement(root, tag)
        node.text = str(value)
        for extra in nodes[1:]:
            root.remove(extra)

    def _set_list(self, root, tag, values):
        for node in root.findall(tag):
            root.remove(node)
        if isinstance(values, str):
            values = [part.strip() for part in values.split(",")]
        for value in values or []:
            if value is not None and str(value).strip():
                ElementTree.SubElement(root, tag).text = str(value).strip()

    def write(self, movie, metadata, force=False):
        """Merge one Kometa metadata mapping into an NFO. Returns True when written."""
        payload_hash = hashlib.sha256(json.dumps(metadata, sort_keys=True, default=_json_default).encode("utf-8")).hexdigest()
        old = self.connection.execute("SELECT metadata_hash FROM nfo_movies WHERE path = ?", (movie.key,)).fetchone()
        if not force and old and old["metadata_hash"] == payload_hash and movie.nfo_path.exists():
            return False
        if movie.nfo_path.exists():
            try:
                root = ElementTree.parse(movie.nfo_path).getroot()
                if root.tag != "movie":
                    raise Failed(f"NFO Error: Expected <movie> root in {movie.nfo_path}")
            except ElementTree.ParseError as error:
                raise Failed(f"NFO Error: Invalid XML in {movie.nfo_path}: {error}") from error
        else:
            root = ElementTree.Element("movie")
        for field, tag in FIELD_TAGS.items():
            if field in metadata and metadata[field] is not None:
                self._set_text(root, tag, metadata[field])
        if "year" in metadata and metadata["year"] is not None:
            self._set_text(root, "year", metadata["year"])
        for field, tag in LIST_TAGS.items():
            if field in metadata:
                self._set_list(root, tag, metadata[field])
        tmdb_id = metadata.get("tmdb_id", metadata.get("tmdb_movie")) or movie.tmdb_id
        imdb_id = metadata.get("imdb_id") or movie.imdb_id
        for node in list(root.findall("uniqueid")):
            if (node.get("type") or "").lower() in {"tmdb", "imdb"}:
                root.remove(node)
        if tmdb_id:
            node = ElementTree.SubElement(root, "uniqueid", {"type": "tmdb", "default": "true"})
            node.text = str(tmdb_id)
            self._set_text(root, "tmdbid", tmdb_id)
        if imdb_id:
            node = ElementTree.SubElement(root, "uniqueid", {"type": "imdb", "default": "false" if tmdb_id else "true"})
            node.text = str(imdb_id)
            self._set_text(root, "imdbid", imdb_id)
        ElementTree.indent(root, space="  ")
        movie.nfo_path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=f".{movie.nfo_path.name}.", suffix=".tmp", dir=movie.nfo_path.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                ElementTree.ElementTree(root).write(stream, encoding="utf-8", xml_declaration=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, movie.nfo_path)
        finally:
            if os.path.exists(temporary):
                os.remove(temporary)
        nfo_mtime = movie.nfo_path.stat().st_mtime_ns
        self.connection.execute("UPDATE nfo_movies SET nfo_mtime_ns = ?, metadata_hash = ? WHERE path = ?", (nfo_mtime, payload_hash, movie.key))
        self.connection.commit()
        return True

    def apply(self, metadata_mappings, changed_only=True):
        self.scan()
        result = {"written": 0, "unchanged": 0, "unmatched": []}
        for mapping_name, metadata in metadata_mappings.items():
            matches = self.match(mapping_name, metadata)
            if not matches:
                result["unmatched"].append(str(mapping_name))
            for movie in matches:
                if self.write(movie, metadata):
                    result["written"] += 1
                else:
                    result["unchanged"] += 1
        return result
