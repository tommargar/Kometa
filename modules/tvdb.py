import re
from datetime import datetime

from tenacity import retry, retry_if_not_exception_type, stop_after_attempt, wait_fixed

from modules import util
from modules.util import Failed

logger = util.logger

builders = ["tvdb_list", "tvdb_list_details", "tvdb_movie", "tvdb_movie_details", "tvdb_show", "tvdb_show_details"]
base_url = "https://www.thetvdb.com"
alt_url = "https://thetvdb.com"
api_url = "https://api4.thetvdb.com/v4"
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
    def __init__(self, tvdb, tvdb_id, is_movie=False, ignore_cache=False, data=None):
        self._tvdb = tvdb
        self.tvdb_id = tvdb_id
        self.is_movie = is_movie
        self.ignore_cache = ignore_cache
        expired = None
        if not data and self._tvdb.cache and not ignore_cache:
            data, expired = self._tvdb.cache.query_tvdb(tvdb_id, is_movie, self._tvdb.expiration)
        if expired or not data:
            data = self._tvdb.get_item(tvdb_id, is_movie=is_movie)

        self.title = data.get("title")
        self.summary = data.get("summary")
        self.poster_url = data.get("poster_url")
        self.background_url = data.get("background_url")
        self.release_date = data.get("release_date")
        if self.release_date and isinstance(self.release_date, str):
            try:
                self.release_date = datetime.strptime(self.release_date, "%Y-%m-%d")
            except ValueError:
                self.release_date = None
        self.status = data.get("status")
        self.genres = data.get("genres", [])
        if isinstance(self.genres, str):
            self.genres = self.genres.split("|")
        self.networks = data.get("networks", [])
        if isinstance(self.networks, str):
            self.networks = self.networks.split("|")
        self.production = data.get("production", [])
        if isinstance(self.production, str):
            self.production = self.production.split("|")
        self.studio = data.get("studio", [])
        if isinstance(self.studio, str):
            self.studio = self.studio.split("|")

        if self._tvdb.cache and not ignore_cache:
            self._tvdb.cache.update_tvdb(expired, self, self._tvdb.expiration)


class TVDb:
    def __init__(self, requests, cache, params):
        self.requests = requests
        self.cache = cache
        self.apikey = params.get("apikey")
        self.pin = params.get("pin")
        self.language = params.get("language", "en")
        self.expiration = params.get("cache_expiration", 60)
        self.token = None
        if self.apikey:
            self._login()

    def _login(self):
        data = {"apikey": self.apikey}
        if self.pin:
            data["pin"] = self.pin
        try:
            response = self.requests.post(f"{api_url}/login", json=data)
            if response.status_code == 200:
                self.token = response.json()["data"]["token"]
            else:
                raise Failed(f"TVDb Error: Login failed ({response.status_code})")
        except Exception as e:
            raise Failed(f"TVDb Error: Login failed: {e}")

    @retry(stop=stop_after_attempt(6), wait=wait_fixed(10), retry=retry_if_not_exception_type(Failed))
    def _request(self, endpoint, params=None):
        if not self.token:
            if self.apikey:
                self._login()
            else:
                raise Failed("TVDb Error: No API Key provided")

        headers = {"Authorization": f"Bearer {self.token}"}
        url = f"{api_url}{endpoint}"
        response = self.requests.get(url, headers=headers, params=params)

        if response.status_code == 401:
            self._login()
            headers = {"Authorization": f"Bearer {self.token}"}
            response = self.requests.get(url, headers=headers, params=params)

        if response.status_code >= 400:
            raise Failed(f"TVDb Error: Request failed {url} ({response.status_code})")

        return response.json()

    def get_tvdb_obj(self, tvdb_url, is_movie=False):
        tvdb_id, _, _ = self.get_id_from_url(tvdb_url, is_movie=is_movie)
        return TVDbObj(self, tvdb_id, is_movie=is_movie)

    def get_id_from_url(self, tvdb_url, is_movie=False, ignore_cache=False):
        try:
            if not is_movie:
                return int(tvdb_url), None, None
            else:
                return int(tvdb_url), None, None
        except ValueError:
            pass
        tvdb_url = tvdb_url.strip()
        expired = None
        if self.cache and not ignore_cache and not is_movie:
            tvdb_id, expired = self.cache.query_tvdb_map(tvdb_url, self.expiration)
            if tvdb_id and not expired:
                return tvdb_id, None, None
        logger.trace(f"URL: {tvdb_url}")

        # Parse slug from URL
        slug = None
        if "/series/" in tvdb_url:
            slug = tvdb_url.split("/series/")[1].split("/")[0]
        elif "/movies/" in tvdb_url:
            slug = tvdb_url.split("/movies/")[1].split("/")[0]

        if not slug:
            # Try dereferrer ID
            if "/dereferrer/" in tvdb_url:
                try:
                    return int(tvdb_url.split("/")[-1]), None, None
                except ValueError:
                    pass
            raise Failed(f"TVDb Error: Could not parse slug from {tvdb_url}")

        try:
            results = self._request("/search", params={"query": slug, "type": "movie" if is_movie else "series"})
        except Failed:
            raise Failed(f"TVDb Error: Search failed for {slug}")

        if results and "data" in results and results["data"]:
            # Try to match slug exactly or take first result
            match = None
            for item in results["data"]:
                if item.get("slug") == slug:
                    match = item
                    break
            if not match:
                match = results["data"][0]

            tvdb_id = int(match["tvdb_id"])
            tmdb_id = None
            imdb_id = None

            # For movies, we might want to fetch extended info to get external IDs if not in search result
            if is_movie:
                try:
                    extended = self._request(f"/movies/{tvdb_id}/extended")
                    if extended and "data" in extended:
                        remote_ids = extended["data"].get("remoteIds", [])
                        for rid in remote_ids:
                            if rid["sourceName"] == "TheMovieDB.com":
                                tmdb_id = int(rid["id"])
                            elif rid["sourceName"] == "IMDB":
                                imdb_id = rid["id"]
                except Failed:
                    pass

            if self.cache and not ignore_cache and not is_movie:
                self.cache.update_tvdb_map(expired, tvdb_url, tvdb_id, self.expiration)
            return tvdb_id, tmdb_id, imdb_id
        else:
            raise Failed(f"TVDb Error: Could not find ID for {slug}")

    def get_item(self, tvdb_id, is_movie=False):
        endpoint = f"/movies/{tvdb_id}/extended" if is_movie else f"/series/{tvdb_id}/extended"
        try:
            response = self._request(endpoint)
        except Failed as e:
            msg = str(e)
            if "404" in msg:
                raise Failed(f"TVDb Error: Item {tvdb_id} not found")
            raise Failed(f"TVDb Error: Item {tvdb_id} request failed: {msg}")

        if "data" not in response:
            raise Failed(f"TVDb Error: Item {tvdb_id} data not found")

        data = response["data"]
        # print(data)
        companies = data.get("companies") or []
        if isinstance(companies, dict):
            production = [c["name"] for c in (companies.get("production") or [])]
            studios = [c["name"] for c in (companies.get("studio") or [])]
        else:
            production = [c["name"] for c in companies]
            studios = [c["name"] for c in companies]

        item = {
            "title": data.get("name"),
            "summary": data.get("overview"),
            "poster_url": data.get("image"),
            "background_url": data.get("art"),
            "release_date": data.get("firstAired"),
            "status": data.get("status", {}).get("name") if isinstance(data.get("status"), dict) else data.get("status"),
            "genres": [g["name"] for g in (data.get("genres") or [])],
            "networks": [n["name"] for n in (data.get("networks") or [])],
            "production": production,
            "studio": studios,
        }

        # Handle translations if needed
        lang = language_translation.get(self.language, self.language)

        # If language is not eng, try to fetch translation
        if self.language != "eng" and self.language != "en":
            try:
                trans = self._request(f"/{'movies' if is_movie else 'series'}/{tvdb_id}/translations/{lang}")
                if trans and "data" in trans:
                    if trans["data"].get("name"):
                        item["title"] = trans["data"]["name"]
                    if trans["data"].get("overview"):
                        item["summary"] = trans["data"]["overview"]
            except Failed:
                pass

        return item

    def get_list_description(self, tvdb_url):
        if "/lists/" in tvdb_url:
            list_id = tvdb_url.split("/lists/")[1].split("/")[0]
            try:
                data = self._request(f"/lists/{list_id}")
                if "data" in data:
                    return data["data"].get("overview"), data["data"].get("image")
            except Failed:
                pass
        return None, None

    def _ids_from_url(self, tvdb_url):
        ids = []
        tvdb_url = tvdb_url.strip()
        logger.trace(f"URL: {tvdb_url}")
        if "/lists/" in tvdb_url:
            list_id = tvdb_url.split("/lists/")[1].split("/")[0]
            try:
                # Fetch list items (extended to get entities)
                data = self._request(f"/lists/{list_id}/extended")
                if "data" in data and "entities" in data["data"]:
                    for entity in data["data"]["entities"]:
                        if entity.get("seriesId"):
                            ids.append((int(entity["seriesId"]), "tvdb"))
                        elif entity.get("movieId"):
                            ids.append((int(entity["movieId"]), "tvdb"))
            except Failed:
                raise Failed(f"TVDb Error: Failed to fetch list {tvdb_url}")
        return ids

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
