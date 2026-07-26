import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from types import SimpleNamespace

from lxml import html
from lxml.etree import ParserError
from requests.exceptions import MissingSchema
from tenacity import retry, retry_if_not_exception_type, stop_after_attempt, wait_fixed

from modules import util
from modules.util import Failed

logger = util.logger


class NotFound(Failed):
    """Raised when TVDb gives a definitive HTTP 4xx for a resource - i.e. TVDb itself confirms there is nothing at this ID."""


class Unavailable(Failed):
    """Raised when TVDb never returned usable content after retries were exhausted (e.g. repeated 202/empty-body); not a confirmed absence like NotFound."""


class TVDbServerError(Exception):
    """Raised for 5xx responses from TVDb; not a Failed subclass so tenacity's retry_if_not_exception_type(Failed) will retry it."""


def _tvdb_retry_exhausted(retry_state):
    """Convert an exhausted TVDb retry loop (5xx, or repeated unparsable 202/empty responses) into Unavailable."""
    raise Unavailable(f"TVDb Error: No usable response from TVDb after {retry_state.attempt_number} attempt(s): {retry_state.outcome.exception()}") from retry_state.outcome.exception()


builders = ["tvdb_list", "tvdb_list_details", "tvdb_movie", "tvdb_movie_details", "tvdb_show", "tvdb_show_details"]
base_url = "https://www.thetvdb.com"
alt_url = "https://thetvdb.com"
api_url = "https://api4.thetvdb.com/v4"
urls = {
    "list": f"{base_url}/lists/",
    "alt_list": f"{alt_url}/lists/",
    "series": f"{base_url}/series/",
    "alt_series": f"{alt_url}/series/",
    "movies": f"{base_url}/movies/",
    "alt_movies": f"{alt_url}/movies/",
    "series_id": f"{base_url}/dereferrer/series/",
    "movie_id": f"{base_url}/dereferrer/movie/",
}
language_translation = {
    "ab": "abk",
    "aa": "aar",
    "af": "afr",
    "ak": "aka",
    "sq": "sqi",
    "am": "amh",
    "ar": "ara",
    "an": "arg",
    "hy": "hye",
    "as": "asm",
    "av": "ava",
    "ae": "ave",
    "ay": "aym",
    "az": "aze",
    "bm": "bam",
    "ba": "bak",
    "eu": "eus",
    "be": "bel",
    "bn": "ben",
    "bi": "bis",
    "bs": "bos",
    "br": "bre",
    "bg": "bul",
    "my": "mya",
    "ca": "cat",
    "ch": "cha",
    "ce": "che",
    "ny": "nya",
    "zh": "zho",
    "cv": "chv",
    "kw": "cor",
    "co": "cos",
    "cr": "cre",
    "hr": "hrv",
    "cs": "ces",
    "da": "dan",
    "dv": "div",
    "nl": "nld",
    "dz": "dzo",
    "en": "eng",
    "eo": "epo",
    "et": "est",
    "ee": "ewe",
    "fo": "fao",
    "fj": "fij",
    "fi": "fin",
    "fr": "fra",
    "ff": "ful",
    "gl": "glg",
    "ka": "kat",
    "de": "deu",
    "el": "ell",
    "gn": "grn",
    "gu": "guj",
    "ht": "hat",
    "ha": "hau",
    "he": "heb",
    "hz": "her",
    "hi": "hin",
    "ho": "hmo",
    "hu": "hun",
    "ia": "ina",
    "id": "ind",
    "ie": "ile",
    "ga": "gle",
    "ig": "ibo",
    "ik": "ipk",
    "io": "ido",
    "is": "isl",
    "it": "ita",
    "iu": "iku",
    "ja": "jpn",
    "jv": "jav",
    "kl": "kal",
    "kn": "kan",
    "kr": "kau",
    "ks": "kas",
    "kk": "kaz",
    "km": "khm",
    "ki": "kik",
    "rw": "kin",
    "ky": "kir",
    "kv": "kom",
    "kg": "kon",
    "ko": "kor",
    "ku": "kur",
    "kj": "kua",
    "la": "lat",
    "lb": "ltz",
    "lg": "lug",
    "li": "lim",
    "ln": "lin",
    "lo": "lao",
    "lt": "lit",
    "lu": "lub",
    "lv": "lav",
    "gv": "glv",
    "mk": "mkd",
    "mg": "mlg",
    "ms": "msa",
    "ml": "mal",
    "mt": "mlt",
    "mi": "mri",
    "mr": "mar",
    "mh": "mah",
    "mn": "mon",
    "na": "nau",
    "nv": "nav",
    "nd": "nde",
    "ne": "nep",
    "ng": "ndo",
    "nb": "nob",
    "nn": "nno",
    "no": "nor",
    "ii": "iii",
    "nr": "nbl",
    "oc": "oci",
    "oj": "oji",
    "cu": "chu",
    "om": "orm",
    "or": "ori",
    "os": "oss",
    "pa": "pan",
    "pi": "pli",
    "fa": "fas",
    "pl": "pol",
    "ps": "pus",
    "pt": "por",
    "qu": "que",
    "rm": "roh",
    "rn": "run",
    "ro": "ron",
    "ru": "rus",
    "sa": "san",
    "sc": "srd",
    "sd": "snd",
    "se": "sme",
    "sm": "smo",
    "sg": "sag",
    "sr": "srp",
    "gd": "gla",
    "sn": "sna",
    "si": "sin",
    "sk": "slk",
    "sl": "slv",
    "so": "som",
    "st": "sot",
    "es": "spa",
    "su": "sun",
    "sw": "swa",
    "ss": "ssw",
    "sv": "swe",
    "ta": "tam",
    "te": "tel",
    "tg": "tgk",
    "th": "tha",
    "ti": "tir",
    "bo": "bod",
    "tk": "tuk",
    "tl": "tgl",
    "tn": "tsn",
    "to": "ton",
    "tr": "tur",
    "ts": "tso",
    "tt": "tat",
    "tw": "twi",
    "ty": "tah",
    "ug": "uig",
    "uk": "ukr",
    "ur": "urd",
    "uz": "uzb",
    "ve": "ven",
    "vi": "vie",
    "vo": "vol",
    "wa": "wln",
    "cy": "cym",
    "wo": "wol",
    "fy": "fry",
    "xh": "xho",
    "yi": "yid",
    "yo": "yor",
    "za": "zha",
    "zu": "zul",
}


class TVDbObj:
    def __init__(self, tvdb, tvdb_id, is_movie=False, ignore_cache=False):
        self._tvdb = tvdb
        self.tvdb_id = tvdb_id
        self.is_movie = is_movie
        self.ignore_cache = ignore_cache
        expired = None
        data = None
        if self._tvdb.cache and not ignore_cache:
            data, expired = self._tvdb.cache.query_tvdb(tvdb_id, is_movie, self._tvdb.expiration)
        if expired or not data:
            item_url = f"{urls['movie_id' if is_movie else 'series_id']}{tvdb_id}"
            try:
                data = self._tvdb.get_movie_request(item_url) if is_movie else self._tvdb.get_request(item_url)
            except NotFound:
                raise NotFound(f"TVDb Error: No {'Movie' if is_movie else 'Series'} found for TVDb ID: {tvdb_id} at {item_url}")
            except Unavailable:
                # Already has its own accurate message - don't relabel it as "No Series/Movie found"
                raise
            except (Failed, TVDbServerError):
                raise Failed(f"TVDb Error: No {'Movie' if is_movie else 'Series'} found for TVDb ID: {tvdb_id} at {item_url}")

        def parse_page(xpath, is_list=False):
            parse_results = data.xpath(xpath)
            if len(parse_results) > 0:
                parse_results = [r.strip() for r in parse_results if len(r) > 0]
            return parse_results if is_list else parse_results[0] if len(parse_results) > 0 else None

        def parse_title_summary(lang=None):
            place = "//div[@class='change_translation_text' and "
            place += f"@data-language='{lang}']" if lang else "not(@style='display:none')]"
            return parse_page(f"{place}/@data-title"), parse_page(f"{place}/p/text()[normalize-space()]")

        if isinstance(data, dict):
            self.title = data["title"]
            self.summary = data["summary"]
            self.poster_url = data["poster_url"]
            self.background_url = data["background_url"]
            self.logo_url = data.get("logo_url", "")
            self.icon_url = data.get("icon_url", "")
            self.release_date = data["release_date"]
            self.status = data["status"]
            self.genres = data["genres"].split("|")
        else:
            self.title, self.summary = parse_title_summary(lang=self._tvdb.language)
            if not self.title and self._tvdb.language in language_translation:
                self.title, self.summary = parse_title_summary(lang=language_translation[self._tvdb.language])
            if not self.title:
                self.title, self.summary = parse_title_summary()
            if not self.title:
                raise Failed(f"TVDb Error: Name not found from TVDb ID: {self.tvdb_id}")

            self.poster_url = parse_page("//div[@id='artwork-posters']//a/@href")
            self.background_url = parse_page("//div[@id='artwork-backgrounds']//a/@href")
            self.logo_url = parse_page("//div[@id='artwork-clearlogo']//a/@href")
            self.icon_url = parse_page("//div[@id='artwork-icons']//a/@href")
            if is_movie:
                released = parse_page("//strong[text()='Released']/parent::li/span/text()[normalize-space()]")
            else:
                released = parse_page("//strong[text()='First Aired']/parent::li/span/text()[normalize-space()]")

            try:
                self.release_date = datetime.strptime(str(released), "%B %d, %Y") if released else released  # noqa
            except ValueError:
                self.release_date = None
            self.status = parse_page("//strong[text()='Status']/parent::li/span/text()[normalize-space()]")

            self.genres = parse_page("//strong[text()='Genres']/parent::li/span/a/text()[normalize-space()]", is_list=True)

        if self._tvdb.cache and not ignore_cache:
            self._tvdb.cache.update_tvdb(expired, self, self._tvdb.expiration)


class TVDb:
    def __init__(self, requests, cache, tvdb_language, expiration, apikey=None, pin=None):
        self.requests = requests
        self.cache = cache
        self.language = tvdb_language
        self.expiration = expiration
        self.apikey = apikey
        self.pin = pin
        self._api_token = None

    @staticmethod
    def _response_json(response, action):
        try:
            payload = response.json()
        except (TypeError, ValueError) as error:
            raise Failed(f"TVDb API Error: Invalid JSON while {action}") from error
        if not isinstance(payload, dict):
            raise Failed(f"TVDb API Error: Invalid response while {action}")
        return payload

    def _login_api(self):
        if not self.apikey:
            raise Failed("TVDb API Error: tvdb.apikey is required for TVDb Cast & Crew")
        login_data = {"apikey": self.apikey}
        if self.pin:
            login_data["pin"] = self.pin
        response = self.requests.post(f"{api_url}/login", json=login_data)
        if response.status_code >= 400:
            raise Failed(f"TVDb API Error: Login failed ({response.status_code})")
        payload = self._response_json(response, "logging in")
        token = (payload.get("data") or {}).get("token")
        if not token:
            raise Failed("TVDb API Error: Login returned no bearer token")
        self._api_token = token

    def _api_get(self, path):
        if not self._api_token:
            self._login_api()
        headers = {"Authorization": f"Bearer {self._api_token}"}
        if self.language and self.language != "default":
            headers["Accept-Language"] = self.language
        response = self.requests.get(f"{api_url}{path}", headers=headers)
        if response.status_code == 401:
            self._api_token = None
            self._login_api()
            headers["Authorization"] = f"Bearer {self._api_token}"
            response = self.requests.get(f"{api_url}{path}", headers=headers)
        if response.status_code == 404:
            raise NotFound(f"TVDb API Error: Resource not found at {path}")
        if response.status_code >= 400:
            raise Failed(f"TVDb API Error: Request failed at {path} ({response.status_code})")
        return self._response_json(response, f"requesting {path}").get("data") or {}

    @staticmethod
    def _show_credit_data(tvdb_id, data, item_type=None):
        cast_types = {"actor", "guest star", "host", "musical guest"}
        crew_types = {
            "creator": ("Writer", "Creator", "Creator"),
            "director": ("Director", None, "Director"),
            "executive producer": ("Producer", "Executive Producer", "Executive Producer"),
            "producer": ("Producer", None, "Producer"),
            "showrunner": ("Producer", "Showrunner", "Showrunner"),
            "writer": ("Writer", None, "Writer"),
        }
        cast = []
        crew = []
        characters = data.get("characters") or []
        for position, character in enumerate(characters):
            people_id = character.get("peopleId")
            person_name = str(character.get("personName") or "").strip()
            people_type = str(character.get("peopleType") or "").strip()
            if not str(people_id).isdigit() or not person_name:
                continue
            common = {
                "tvdb_id": int(people_id),
                "name": person_name,
                "order": position,
            }
            if people_type.casefold() in cast_types:
                role = str(character.get("name") or "").strip()
                if not role and people_type.casefold() in {"host", "musical guest"}:
                    role = people_type
                person_type = "GuestStar" if people_type.casefold() == "guest star" else "Actor"
                cast_entry = {**common, "character": role, "person_type": person_type}
                if str(item_type or "").casefold() == "series":
                    sort_value = character.get("sort")
                    if str(sort_value).isdigit() and int(sort_value) > 0:
                        cast_entry["_tvdb_sort"] = int(sort_value)
                cast.append(cast_entry)
            elif people_type.casefold() in crew_types:
                person_type, role, job = crew_types[people_type.casefold()]
                crew.append({**common, "job": job, "department": people_type, "person_type": person_type, "role": role})
        if str(item_type or "").casefold() == "series":
            # TVDb's series endpoint does not return `characters` in the order
            # displayed on the website. The explicit positive `sort` value is
            # the cast order maintained by TVDb's "Edit Cast Order" feature.
            # Unranked entries stay in their source order after ranked Cast.
            cast.sort(key=lambda credit: (0, credit["_tvdb_sort"], credit["order"]) if "_tvdb_sort" in credit else (1, credit["order"], credit["order"]))
        for position, credit in enumerate(cast):
            credit.pop("_tvdb_sort", None)
            credit["order"] = position
        return {
            "tvdb_id": int(tvdb_id),
            "title": str(data.get("name") or ""),
            "cast": cast,
            "crew": crew,
        }

    def get_show_credits(self, tvdb_id, ignore_cache=False):
        try:
            tvdb_id = int(tvdb_id)
        except (TypeError, ValueError) as error:
            raise Failed(f"TVDb API Error: Invalid Series ID: {tvdb_id}") from error
        expired = None
        data = None
        if self.cache and not ignore_cache:
            data, expired = self.cache.query_tvdb_credits(tvdb_id, self.expiration)
        if expired or not data:
            data = self._show_credit_data(tvdb_id, self._api_get(f"/series/{tvdb_id}/extended"), item_type="series")
            if self.cache and not ignore_cache:
                self.cache.update_tvdb_credits(expired, tvdb_id, data, self.expiration)
        return SimpleNamespace(
            tvdb_id=tvdb_id,
            tmdb_id=None,
            title=data.get("title") or "",
            cast=list(data.get("cast") or []),
            crew=list(data.get("crew") or []),
            credits_source="tvdb",
        )

    def _get_item_credits(self, tvdb_id, item_type, ignore_cache=False):
        try:
            tvdb_id = int(tvdb_id)
        except (TypeError, ValueError) as error:
            raise Failed(f"TVDb API Error: Invalid {str(item_type).title()} ID: {tvdb_id}") from error
        expired = None
        data = None
        if self.cache and not ignore_cache:
            data, expired = self.cache.query_tvdb_item_credits(tvdb_id, item_type, self.expiration)
        if expired or not data:
            data = self._show_credit_data(tvdb_id, self._api_get(f"/{item_type}s/{tvdb_id}/extended"), item_type=item_type)
            data["item_type"] = item_type
            if self.cache and not ignore_cache:
                self.cache.update_tvdb_item_credits(expired, tvdb_id, item_type, data, self.expiration)
        return SimpleNamespace(
            tvdb_id=tvdb_id,
            tmdb_id=None,
            title=data.get("title") or "",
            cast=list(data.get("cast") or []),
            crew=list(data.get("crew") or []),
            credits_source="tvdb",
            credits_level=item_type,
        )

    def get_season_credits(self, tvdb_id, ignore_cache=False):
        return self._get_item_credits(tvdb_id, "season", ignore_cache=ignore_cache)

    def get_movie_credits(self, tvdb_id, ignore_cache=False):
        return self._get_item_credits(tvdb_id, "movie", ignore_cache=ignore_cache)

    def get_episode_credits(self, tvdb_id, ignore_cache=False):
        return self._get_item_credits(tvdb_id, "episode", ignore_cache=ignore_cache)

    def get_episode_credits_bulk(self, tvdb_ids, progress_callback=None):
        ordered_ids = []
        cached = {}
        missing = []
        for value in tvdb_ids or []:
            try:
                tvdb_id = int(value)
            except (TypeError, ValueError):
                continue
            if tvdb_id in ordered_ids:
                continue
            ordered_ids.append(tvdb_id)
            data = None
            expired = None
            if self.cache:
                data, expired = self.cache.query_tvdb_item_credits(tvdb_id, "episode", self.expiration)
            if data and not expired:
                cached[tvdb_id] = data
            else:
                missing.append((tvdb_id, expired))

        if missing:
            if not self._api_token:
                self._login_api()

            def fetch(tvdb_id):
                return self._show_credit_data(tvdb_id, self._api_get(f"/episodes/{tvdb_id}/extended"), item_type="episode")

            completed = 0
            with ThreadPoolExecutor(max_workers=min(8, len(missing)), thread_name_prefix="tvdb-credits") as executor:
                futures = {executor.submit(fetch, tvdb_id): (tvdb_id, expired) for tvdb_id, expired in missing}
                for future in as_completed(futures):
                    tvdb_id, expired = futures[future]
                    data = future.result()
                    data["item_type"] = "episode"
                    cached[tvdb_id] = data
                    if self.cache:
                        self.cache.update_tvdb_item_credits(expired, tvdb_id, "episode", data, self.expiration)
                    completed += 1
                    if progress_callback:
                        progress_callback(completed, len(missing))

        return {
            tvdb_id: SimpleNamespace(
                tvdb_id=tvdb_id,
                tmdb_id=None,
                title=cached[tvdb_id].get("title") or "",
                cast=list(cached[tvdb_id].get("cast") or []),
                crew=list(cached[tvdb_id].get("crew") or []),
                credits_source="tvdb",
                credits_level="episode",
            )
            for tvdb_id in ordered_ids
            if tvdb_id in cached
        }

    @staticmethod
    def _people_external_ids(data):
        external_ids = {}
        for remote in (data or {}).get("remoteIds") or []:
            remote_id = str((remote or {}).get("id") or "").strip()
            source = str((remote or {}).get("sourceName") or (remote or {}).get("source") or "").strip().casefold()
            if not remote_id:
                continue
            if "imdb" in source:
                external_ids["imdb_id"] = remote_id
            elif "wikidata" in source:
                external_ids["wikidata_id"] = remote_id
            elif "themoviedb" in source or "tmdb" in source:
                try:
                    external_ids["tmdb_id"] = int(remote_id)
                except ValueError:
                    continue
        return external_ids

    def get_people_external_ids_bulk(self, tvdb_ids, progress_callback=None):
        ordered_ids = []
        for value in tvdb_ids or []:
            try:
                tvdb_id = int(value)
            except (TypeError, ValueError):
                continue
            if tvdb_id not in ordered_ids:
                ordered_ids.append(tvdb_id)
        if not ordered_ids:
            return {}
        results = {}
        missing_ids = list(ordered_ids)
        if self.cache and hasattr(self.cache, "query_tvdb_people_external_ids"):
            results, missing_ids = self.cache.query_tvdb_people_external_ids(ordered_ids, self.expiration)
        if not missing_ids:
            return results
        if not self._api_token:
            self._login_api()

        def fetch(tvdb_id):
            return self._people_external_ids(self._api_get(f"/people/{tvdb_id}/extended"))

        completed = len(ordered_ids) - len(missing_ids)
        batch_size = 500
        for start in range(0, len(missing_ids), batch_size):
            batch = missing_ids[start : start + batch_size]
            fetched_results = {}
            with ThreadPoolExecutor(max_workers=min(8, len(batch)), thread_name_prefix="tvdb-people") as executor:
                futures = {executor.submit(fetch, tvdb_id): tvdb_id for tvdb_id in batch}
                for future in as_completed(futures):
                    tvdb_id = futures[future]
                    try:
                        fetched_results[tvdb_id] = future.result()
                    except Failed as error:
                        logger.warning(f"TVDb Person {tvdb_id} external IDs unavailable: {error}")
                    completed += 1
                    if progress_callback:
                        progress_callback(completed, len(ordered_ids))
            results.update(fetched_results)
            if fetched_results and self.cache and hasattr(self.cache, "update_tvdb_people_external_ids"):
                self.cache.update_tvdb_people_external_ids(fetched_results)
        return results

    def get_tvdb_obj(self, tvdb_url, is_movie=False):
        try:
            # A numeric value is already a TVDb ID. Resolving it through the
            # movie dereferrer first bypasses the object cache and adds an
            # unnecessary HTTP request for every movie.
            tvdb_id = int(tvdb_url)
        except (TypeError, ValueError):
            tvdb_id, _, _ = self.get_id_from_url(tvdb_url, is_movie=is_movie)
        return TVDbObj(self, tvdb_id, is_movie=is_movie)

    def _request(self, tvdb_url):
        response = self.requests.get(tvdb_url, language=self.language)
        if response.status_code >= 400:
            # 4xx = definitive "gone" (NotFound); 5xx = transient (TVDbServerError, retried by tenacity)
            if 400 <= response.status_code < 500:
                raise NotFound(f"({response.status_code}) {response.reason}")
            raise TVDbServerError(f"({response.status_code}) {response.reason}")
        return html.fromstring(response.content)

    @retry(stop=stop_after_attempt(6), wait=wait_fixed(10), retry=retry_if_not_exception_type(Failed), retry_error_callback=_tvdb_retry_exhausted)
    def get_request(self, tvdb_url):
        return self._request(tvdb_url)

    @retry(stop=stop_after_attempt(2), wait=wait_fixed(0.25), retry=retry_if_not_exception_type(Failed), retry_error_callback=_tvdb_retry_exhausted)
    def get_movie_request(self, tvdb_url):
        """Fetch movie metadata without imposing a 50-second per-item retry delay."""
        return self._request(tvdb_url)

    def get_id_from_url(self, tvdb_url, is_movie=False, ignore_cache=False):
        try:
            if not is_movie:
                return int(tvdb_url), None, None
            else:
                tvdb_url = f"{urls['movie_id']}{int(tvdb_url)}"
        except ValueError:
            pass
        tvdb_url = tvdb_url.strip()
        if tvdb_url.startswith((urls["series"], urls["alt_series"], urls["series_id"])):
            media_type = "Series"
        elif tvdb_url.startswith((urls["movies"], urls["alt_movies"], urls["movie_id"])):
            media_type = "Movie"
        else:
            raise Failed(f"TVDb Error: {tvdb_url} must begin with {urls['movies']} or {urls['series']}")
        expired = None
        if self.cache and not ignore_cache and not is_movie:
            tvdb_id, expired = self.cache.query_tvdb_map(tvdb_url, self.expiration)
            if tvdb_id and not expired:
                return tvdb_id, None, None
        logger.trace(f"URL: {tvdb_url}")
        try:
            response = self.get_request(tvdb_url)
        except Unavailable:
            raise
        except (ParserError, Failed, TVDbServerError):
            raise Failed(f"TVDb Error: Failed not parse {tvdb_url}")
        results = response.xpath(f"//*[text()='TheTVDB.com {media_type} ID']/parent::node()/span/text()")
        if len(results) > 0:
            tvdb_id = int(results[0])
            tmdb_id = None
            imdb_id = None
            if media_type == "Movie":
                results = response.xpath("//*[text()='TheMovieDB.com']/@href")
                if len(results) > 0:
                    try:
                        tmdb_id = util.regex_first_int(results[0], "TMDb ID")
                    except Failed:
                        pass
                results = response.xpath("//*[text()='IMDB']/@href")
                if len(results) > 0:
                    try:
                        imdb_id = util.get_id_from_imdb_url(results[0])
                    except Failed:
                        pass
                if tmdb_id is None and imdb_id is None:
                    raise Failed("TVDb Error: No TMDb ID or IMDb ID found")
            if self.cache and not ignore_cache and not is_movie:
                self.cache.update_tvdb_map(expired, tvdb_url, tvdb_id, self.expiration)
            return tvdb_id, tmdb_id, imdb_id
        elif tvdb_url.startswith(urls["movie_id"]):
            err_text = f"using TVDb Movie ID: {tvdb_url[len(urls['movie_id']):]}"
        elif tvdb_url.startswith(urls["series_id"]):
            err_text = f"using TVDb Series ID: {tvdb_url[len(urls['series_id']):]}"
        else:
            err_text = f"ID at the URL {tvdb_url}"
        raise Failed(f"TVDb Error: Could not find a TVDb {media_type} {err_text}")

    def get_list_description(self, tvdb_url):
        response = self.requests.get_html(tvdb_url, language=self.language)
        description = response.xpath("//div[@class='block']/div[not(@style='display:none')]/p/text()")
        description = description[0] if len(description) > 0 and len(description[0]) > 0 else None
        poster = response.xpath("//div[@id='artwork']/div/div/a/@href")
        poster = poster[0] if len(poster) > 0 and len(poster[0]) > 0 else None
        return description, poster

    def _ids_from_url(self, tvdb_url):
        ids = []
        tvdb_url = tvdb_url.strip()
        logger.trace(f"URL: {tvdb_url}")
        if tvdb_url.startswith((urls["list"], urls["alt_list"])):
            try:
                response = self.requests.get_html(tvdb_url, language=self.language)
                items = response.xpath("//div[@id='general']//div/div/h3/a")
                for item in items:
                    title = item.xpath("text()")[0]
                    item_url = item.xpath("@href")[0]
                    if item_url.startswith("/series/"):
                        try:
                            tvdb_id, _, _ = self.get_id_from_url(f"{base_url}{item_url}")
                            if tvdb_id:
                                ids.append((tvdb_id, "tvdb"))
                        except Failed as e:
                            logger.error(f"{e} for series {title}")
                    elif item_url.startswith("/movies/"):
                        try:
                            _, tmdb_id, imdb_id = self.get_id_from_url(f"{base_url}{item_url}", is_movie=True)
                            if tmdb_id:
                                ids.append((tmdb_id, "tmdb"))
                            elif imdb_id:
                                ids.append((imdb_id, "imdb"))
                        except Failed as e:
                            logger.error(f"{e} for movie {title}")
                    else:
                        logger.error(f"TVDb Error: Skipping Movie: {title}")
                    time.sleep(2)
                if len(ids) > 0:
                    return ids
                raise Failed(f"TVDb Error: No TVDb IDs found at {tvdb_url}")
            except MissingSchema:
                logger.stacktrace()
                raise Failed(f"TVDb Error: URL Lookup Failed for {tvdb_url}")
        else:
            raise Failed(f"TVDb Error: {tvdb_url} must begin with {urls['list']}")

    def get_tvdb_ids(self, method, data):
        if method == "tvdb_show":
            logger.info(f"Processing TVDb Show: {data}")
            ids = []
            try:
                tvdb_id, _, _ = self.get_id_from_url(data)
                if tvdb_id:
                    ids.append((tvdb_id, "tvdb"))
            except Failed as e:
                logger.error(e)
            return ids
        elif method == "tvdb_movie":
            logger.info(f"Processing TVDb Movie: {data}")
            ids = []
            try:
                _, tmdb_id, imdb_id = self.get_id_from_url(data)
                if tmdb_id:
                    ids.append((tmdb_id, "tmdb"))
                elif imdb_id:
                    ids.append((imdb_id, "imdb"))
            except Failed as e:
                logger.error(e)
            return ids
        elif method == "tvdb_list":
            logger.info(f"Processing TVDb List: {data}")
            return self._ids_from_url(data)
        else:
            raise Failed(f"TVDb Error: Method {method} not supported")

    def item_filter(self, item, filter_attr, modifier, filter_final, filter_data):
        if filter_attr == "tvdb_title":
            if util.is_string_filter([item.title], modifier, filter_data):
                return False
        elif filter_attr == "tvdb_status":
            if util.is_string_filter([item.status], modifier, filter_data):
                return False
        elif filter_attr == "tvdb_genre":
            attrs = item.genres
            if modifier == ".regex":
                has_match = False
                for reg in filter_data:
                    for name in attrs:
                        if re.compile(reg).search(name):
                            has_match = True
                if has_match is False:
                    return False
            elif modifier in [".count_gt", ".count_gte", ".count_lt", ".count_lte"]:
                test_number = len(attrs) if attrs else 0
                modifier = f".{modifier[7:]}"
                if test_number is None or util.is_number_filter(test_number, modifier, filter_data):
                    return False
            elif (not list(set(filter_data) & set(attrs)) and modifier == "") or (list(set(filter_data) & set(attrs)) and modifier == ".not"):
                return False
        return True
