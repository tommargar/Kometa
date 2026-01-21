import re, time
from modules import util
from modules.util import Failed

logger = util.logger

sort_options = {
    "name": "by/name/",
    "popularity": "by/popular/",
    "newest": "by/newest/",
    "oldest": "by/oldest/",
    "updated": ""
}
builders = ["letterboxd_list", "letterboxd_list_details"]
base_url = "https://letterboxd.com"

class Letterboxd:
    def __init__(self, requests, cache=None):
        self.requests = requests
        self.cache = cache

    def _request(self, url, language, xpath=None):
        logger.trace(f"URL: {url}")
        response = self.requests.get_html(url, language=language)
        return response.xpath(xpath) if xpath else response

    def _parse_page(self, list_url, language):
        if "ajax" not in list_url:
            list_url = list_url.replace("https://letterboxd.com/films", "https://letterboxd.com/films/ajax")

        def get_elements(resp):
            els = resp.xpath("//li[contains(@class, 'posteritem')]")
            if not els:
                els = resp.xpath("//div[@data-film-id]")
            if not els:
                els = resp.xpath("//*[@data-film-id]")
            # logger.debug(f"Found {len(els)} elements")
            # for i, el in enumerate(els[:5]):
            #     logger.debug(f"Element {i}: {el.tag} {el.attrib}")
            return els

        # response = self._request(list_url, language)
        # letterboxd_elements = get_elements(response)
        letterboxd_elements = None

        if not letterboxd_elements:
            # logger.debug(f"No items found, trying Cloudscraper for {list_url}")
            try:
                response = self.requests.get_scrape_html(list_url)
                letterboxd_elements = get_elements(response)
            except Exception as e:
                logger.debug(f"Cloudscraper failed: {e}")

        items = []
        if letterboxd_elements:
            for element in letterboxd_elements:
                try:
                    data_el = element
                    if element.tag == 'li':
                        children = element.xpath(".//*[@data-film-id]")
                        if children:
                            data_el = children[0]
                        else:
                            children = element.xpath(".//*[@data-target-link]")
                            if children:
                                data_el = children[0]
                            else:
                                logger.debug(f"Found li element but no film data inside.")
                                continue

                    slugs = data_el.xpath("@data-target-link")
                    if slugs:
                        slug = slugs[0]
                    else:
                        slugs = data_el.xpath("@data-item-slug")
                        if slugs:
                            slug = f"/film/{slugs[0]}/"
                        else:
                            logger.debug("No slug found for item")
                            continue

                    ids = data_el.xpath("@data-film-id")
                    if ids:
                        letterboxd_id = ids[0]
                    else:
                        logger.debug(f"No film ID found for {slug}")
                        continue

                    year = None
                    item_names = data_el.xpath("@data-item-name")
                    if item_names:
                        match = re.search(r"\((\d{4})\)$", item_names[0])
                        if match:
                            year = int(match.group(1))

                    if not year:
                        years = element.xpath(f".//div[@class='body']/div/header/span/span/a/text()")
                        if not years and element.tag != 'li':
                             years = element.xpath(f"parent::article/div[@class='body']/div/header/span/span/a/text()")
                        if years:
                            year = int(years[0])

                    comments = element.xpath(f".//div[@class='body']/div/p/text()")
                    if not comments and element.tag != 'li':
                        comments = element.xpath(f"parent::article/div[@class='body']/div/p/text()")

                    rating = None
                    ratings = element.xpath(".//span[contains(@class, 'rating')]/@class")
                    if not ratings and element.tag != 'li':
                         ratings = element.xpath("parent::article/div[@class='body']/div/span/@class")
                    if not ratings and element.tag != 'li':
                         ratings = element.xpath("parent::li/p/span[contains(@class, 'rating')]/@class")

                    if ratings:
                        match = re.search("rated-(\\d+)", ratings[0])
                        if match:
                            rating = int(match.group(1))

                    if rating is None:
                        if element.tag == 'li':
                            owner_ratings = element.xpath("@data-owner-rating")
                        else:
                            owner_ratings = element.xpath("parent::li/@data-owner-rating")

                        if owner_ratings and str(owner_ratings[0]).isdigit():
                            rating = int(owner_ratings[0])

                    items.append((letterboxd_id, slug, year, comments[0] if comments else None, rating))
                except Exception as e:
                    logger.error(f"Letterboxd Error: Failed to parse item: {e}")

        next_url = response.xpath("//a[@class='next']/@href")
        if not items and next_url:
             logger.warning(f"No items found on page but next page exists: {list_url}")
        return items, next_url

    def _parse_list(self, list_url, limit, language):
        items, next_url = self._parse_page(list_url, language)
        while len(next_url) > 0:
            time.sleep(2)
            new_items, next_url = self._parse_page(f"{base_url}{next_url[0]}", language)
            items.extend(new_items)
            if limit and len(items) >= limit:
                return items[:limit]
        return items

    def _tmdb(self, letterboxd_url, language):
        def get_id(resp):
            ids = resp.xpath("//*[@data-tmdb-id]/@data-tmdb-id")
            if ids and ids[0]:
                return int(ids[0])
            ids = resp.xpath("//a[contains(@href, 'themoviedb.org')]/@href")
            if len(ids) > 0 and ids[0]:
                return util.regex_first_int(ids[0], "TMDb Movie ID")
            # print(resp.text)
            return None
        # try:
        #     tmdb_id = get_id(self._request(letterboxd_url, language))
        #     if tmdb_id:
        #         return tmdb_id
        # except Exception:
        #     pass
        try:
            tmdb_id = get_id(self.requests.get_scrape_html(letterboxd_url))
            if tmdb_id:
                return tmdb_id
        except Exception as e:
            logger.debug(f"Cloudscraper failed: {e}")
        raise Failed(f"Letterboxd Error: TMDb Movie ID not found at {letterboxd_url}")

    def get_user_lists(self, username, sort, language):
        next_page = [f"/{username}/lists/{sort_options[sort]}"]
        lists = []
        while next_page:
            response = self._request(f"{base_url}{next_page[0]}", language)
            sections = response.xpath("//article[@data-film-list-id]/div/div/div/h2/a")
            lists.extend([(f"{base_url}{s.xpath('@href')[0]}", s.xpath("text()")[0]) for s in sections])
            next_page = response.xpath("//div[@class='pagination']/div/a[@class='next']/@href")
        return lists

    def get_list_description(self, list_url, language):
        descriptions = self._request(f"{list_url}", language, xpath="//meta[@name='description']/@content")
        if len(descriptions) > 0 and len(descriptions[0]) > 0 and "About this list: " in descriptions[0]:
            return str(descriptions[0]).split("About this list: ")[1]
        return None

    def validate_letterboxd_lists(self, err_type, letterboxd_lists, language):
        valid_lists = []
        for letterboxd_dict in util.get_list(letterboxd_lists, split=False):
            if not isinstance(letterboxd_dict, dict):
                letterboxd_dict = {"url": letterboxd_dict}
            dict_methods = {dm.lower(): dm for dm in letterboxd_dict}
            final = {
                "url": util.parse(err_type, "url", letterboxd_dict, methods=dict_methods, parent="letterboxd_list").strip(),
                "limit": util.parse(err_type, "limit", letterboxd_dict, methods=dict_methods, datatype="int", parent="letterboxd_list", default=0) if "limit" in dict_methods else 0,
                "note": util.parse(err_type, "note", letterboxd_dict, methods=dict_methods, parent="letterboxd_list") if "note" in dict_methods else None,
                "rating": util.parse(err_type, "rating", letterboxd_dict, methods=dict_methods, datatype="int", parent="letterboxd_list", maximum=10, range_split="-") if "rating" in dict_methods else None,
                "year": util.parse(err_type, "year", letterboxd_dict, methods=dict_methods, datatype="int", parent="letterboxd_list", minimum=1000, maximum=3000, range_split="-") if "year" in dict_methods else None
            }
            if not final["url"].startswith(base_url):
                raise Failed(f"{err_type} Error: {final['url']} must begin with: {base_url}")
            valid_lists.append(final)
        return valid_lists

    def get_tmdb_ids(self, method, data, language):
        if method == "letterboxd_list":
            logger.info(f"Processing Letterboxd List: {data}")
            items = self._parse_list(data["url"], data["limit"], language)
            total_items = len(items)
            if total_items > 0:
                ids = []
                filtered_ids = []
                for i, item in enumerate(items, 1):
                    letterboxd_id, slug, year, note, rating = item
                    filtered = False
                    if data["year"]:
                        start_year, end_year = data["year"].split("-")
                        if not year or int(end_year) < year or year < int(start_year):
                            filtered = True
                    if data["rating"]:
                        start_rating, end_rating = data["rating"].split("-")
                        if not rating or int(end_rating) < rating or rating < int(start_rating):
                            filtered = True
                    if data["note"]:
                        if not note or data["note"] not in note:
                            filtered = True
                    if filtered:
                        filtered_ids.append(slug)
                        continue
                    logger.ghost(f"Finding TMDb ID {i}/{total_items}")
                    tmdb_id = None
                    expired = None
                    if self.cache:
                        tmdb_id, expired = self.cache.query_letterboxd_map(letterboxd_id)
                    if not tmdb_id or expired is not False:
                        try:
                            tmdb_id = self._tmdb(f"{base_url}{slug}", language)
                        except Failed as e:
                            logger.error(e)
                            continue
                        if self.cache:
                            self.cache.update_letterboxd_map(expired, letterboxd_id, tmdb_id)
                    ids.append((tmdb_id, "tmdb"))
                logger.info(f"Processed {total_items} TMDb IDs")
                if filtered_ids:
                    logger.info(f"Filtered: {filtered_ids}")
                return ids
            else:
                raise Failed(f"Letterboxd Error: No List Items found in {data}")
        else:
            raise Failed(f"Letterboxd Error: Method {method} not supported")
