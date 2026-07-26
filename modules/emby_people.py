import copy
import hashlib
import json
import re
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from modules import util

logger = util.logger


SYNC_VERSION = 7
MAX_SQLITE_INTEGER = (1 << 63) - 1
NAME_PROPAGATION_WAIT_SECONDS = 2
NAME_INDEX_WAIT_SECONDS = 15


JOB_TO_TYPE_ROLE = {
    "creator": ("Writer", "Creator"),
    "director": ("Director", None),
    "writer": ("Writer", "Autor"),
    "screenplay": ("Writer", None),
    "screenwriter": ("Writer", None),
    "teleplay": ("Writer", None),
    "author": ("Writer", "Autor"),
    "novel": ("Writer", "Romanvorlage"),
    "adaptation": ("Writer", "Adaption"),
    "story": ("Writer", "Story"),
    "comic book": ("Writer", "Comicvorlage"),
    "characters": ("Writer", "Charaktere"),
    "original story": ("Writer", "Story"),
    "theatre play": ("Writer", "Theaterstück"),
    "composer": ("Composer", None),
    "original music composer": ("Composer", "Filmmusik"),
    "lyricist": ("Lyricist", None),
    "songs": ("Lyricist", "Songs"),
    "theme song performance": ("Lyricist", "Theme Song"),
    "producer": ("Producer", None),
    "executive producer": ("Producer", "Executive Producer"),
    "showrunner": ("Producer", "Showrunner"),
    "conductor": ("Conductor", None),
}


CREW_TYPE_ORDER = ("Director", "Writer", "Producer", "Composer", "Conductor", "Lyricist")
CREW_ROLE_PRIORITY = {
    "Director": [None],
    "Writer": ["Autor", "Creator", "Romanvorlage", "Adaption", "Story", "Comicvorlage", "Charaktere", None],
    "Producer": ["Executive Producer", "Showrunner", None],
    "Composer": ["Filmmusik", None],
    "Conductor": [None],
    "Lyricist": ["Songs", "Theme Song", None],
}


@dataclass
class PersonIdentity:
    tmdb_id: int
    base_name: str
    normalized_name: str
    display_name: str
    name_index: int | None = None
    imdb_id: str | None = None
    tvdb_id: str | None = None
    wikidata_id: str | None = None
    emby_id: str | None = None
    emby_etag: str | None = None
    emby_signature: str | None = None
    duplicate_emby_ids: set[str] = field(default_factory=set)
    verified_at: str | None = None
    external_verified_at: str | None = None
    canonical_id: int | None = None

    @property
    def provider(self):
        return "Tmdb" if self.tmdb_id > 0 else "Tvdb"

    @property
    def provider_id(self):
        return str(self.tmdb_id if self.tmdb_id > 0 else -self.tmdb_id)

    @classmethod
    def from_cache(cls, data):
        return cls(
            tmdb_id=int(data["tmdb_id"]),
            base_name=data["base_name"],
            normalized_name=data["normalized_name"],
            display_name=data["display_name"],
            name_index=data.get("name_index"),
            imdb_id=data.get("imdb_id"),
            tvdb_id=data.get("tvdb_id"),
            wikidata_id=data.get("wikidata_id"),
            emby_id=str(data["emby_id"]) if data.get("emby_id") is not None else None,
            emby_etag=data.get("emby_etag"),
            emby_signature=data.get("emby_signature"),
            duplicate_emby_ids={str(value) for value in (data.get("duplicate_emby_ids") or []) if str(value).isdigit()},
            verified_at=data.get("verified_at"),
            external_verified_at=data.get("external_verified_at"),
            canonical_id=int(data["canonical_id"]) if data.get("canonical_id") is not None else None,
        )


@dataclass(frozen=True)
class PersonCredit:
    tmdb_id: int
    name: str
    person_type: str
    role: str | None
    order: int
    imdb_id: str | None = None
    tvdb_id: str | None = None

    def source_tuple(self):
        return self.tmdb_id, self.person_type, self.role or "", self.order, self.imdb_id or "", self.tvdb_id or ""


@dataclass
class ItemPeoplePlan:
    item_id: str
    tmdb_id: int
    credits_source: str
    emby_etag: str | None
    emby_item: dict
    credits: list[PersonCredit]
    credits_hash: str


def normalize_person_name(name):
    value = unicodedata.normalize("NFKC", str(name or ""))
    return re.sub(r"\s+", " ", value).strip().casefold()


def roman_number(number):
    if not isinstance(number, int) or number < 1:
        raise ValueError(f"Roman index must be a positive integer: {number!r}")
    values = (
        (1000, "M"),
        (900, "CM"),
        (500, "D"),
        (400, "CD"),
        (100, "C"),
        (90, "XC"),
        (50, "L"),
        (40, "XL"),
        (10, "X"),
        (9, "IX"),
        (5, "V"),
        (4, "IV"),
        (1, "I"),
    )
    remaining = number
    result = []
    for value, numeral in values:
        count, remaining = divmod(remaining, value)
        result.extend([numeral] * count)
    return "".join(result)


def stable_hash(value):
    payload = json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(payload.encode("ascii")).hexdigest()


class EmbyPeopleSync:
    """Library-scoped, provider-backed Cast & Crew synchronization for Emby."""

    def __init__(self, emby_server, tmdb, cache, library_id=None, tvdb=None):
        self.emby = emby_server
        self.tmdb = tmdb
        self.tvdb = tvdb
        self.cache = cache
        self.library_id = str(library_id or getattr(emby_server, "library_id", "") or "")
        system_info = getattr(emby_server, "system_info", {}) or {}
        self.server_id = str(getattr(emby_server, "cache_server_key", None) or system_info.get("Id") or system_info.get("ServerId") or getattr(emby_server, "friendlyName", "") or getattr(emby_server, "emby_server_url", ""))
        self.plans: list[ItemPeoplePlan] = []
        self.identities: dict[int, PersonIdentity] = {}
        self._identity_records: dict[int, PersonIdentity] = {}
        self._unique_identity_cache: list[PersonIdentity] | None = None
        self._identities_by_name: dict[str, list[PersonIdentity]] = {}
        self._identities_by_tvdb: dict[str, list[PersonIdentity]] = {}
        self._identities_by_imdb: dict[str, list[PersonIdentity]] = {}
        self._identities_by_wikidata: dict[str, list[PersonIdentity]] = {}
        self._discovered_names: dict[int, str] = {}
        self._discovered_external_ids: dict[int, dict[str, str]] = {}
        self._finalized = False
        self._resolved = False
        self._changed_identity_ids: set[int] = set()
        self._identity_cache_dirty = False
        self._detached_identity_ids: set[int] = set()
        self._temporary_materialization_identity_ids: set[int] = set()
        self._new_person_ids: set[int] = set()
        self._duplicate_identity_ids: set[int] = set()
        self._same_identity_duplicate_ids: set[int] = set()
        self._noncanonical_person_ids: set[str] = set()
        self._duplicate_person_owners: dict[str, PersonIdentity] = {}
        self._canonical_person_owners: dict[str, PersonIdentity] = {}
        self._ensured_person_ids: set[int] = set()
        self._post_apply_verify_ids: set[int] = set()
        self._identity_errors: dict[int, Exception] = {}
        self._resolved_person_items: dict[int, dict] = {}
        self._states: dict[str, dict] = {}
        self._tmdb_identity_evidence: dict[int, dict] = {}
        self._wikidata_identity_evidence: dict[str, dict] = {}
        self._duplicate_routing_counts: dict[tuple[int, str, str], int] = {}
        self._staged_identity_links: dict[int, dict[int, list[dict]]] = {}
        self._staged_show_identity_contexts: dict[int, dict[int, list[dict]]] = {}

    def stage_item(self, emby_item, credits_item, source=None):
        if not emby_item or not credits_item:
            return False
        item_id = str(emby_item.get("Id") or "")
        tmdb_id = getattr(credits_item, "tmdb_id", None) or self._provider_value(emby_item.get("ProviderIds"), "Tmdb")
        tvdb_id = getattr(credits_item, "tvdb_id", None) or self._provider_value(emby_item.get("ProviderIds"), "Tvdb")
        source_item_id = int(tmdb_id) if tmdb_id is not None and str(tmdb_id).isdigit() else -int(tvdb_id) if tvdb_id is not None and str(tvdb_id).isdigit() else None
        if not item_id or source_item_id is None:
            return False

        credits_source = str(source or getattr(credits_item, "credits_source", None) or getattr(credits_item, "credit_source", None) or "tmdb").strip().casefold()
        cast_source = str(source or getattr(credits_item, "cast_source", None) or credits_source).strip().casefold()
        crew_source = str(source or getattr(credits_item, "crew_source", None) or credits_source).strip().casefold()
        credits = self._credits_from_source(
            getattr(credits_item, "cast", None),
            getattr(credits_item, "crew", None),
            cast_source,
            crew_source=crew_source,
            item_type=emby_item.get("Type"),
        )
        if not credits:
            return False
        for credit in credits:
            self._discovered_names.setdefault(credit.tmdb_id, credit.name)
            external_ids = self._discovered_external_ids.setdefault(credit.tmdb_id, {})
            if credit.imdb_id:
                external_ids["imdb_id"] = credit.imdb_id
            if credit.tvdb_id:
                external_ids["tvdb_id"] = credit.tvdb_id
        tmdb_show_id = getattr(credits_item, "tmdb_show_id", None)
        if str(tmdb_show_id or "").isdigit():
            tmdb_show_id = int(tmdb_show_id)
            for credit in credits:
                if credit.tmdb_id >= 0:
                    continue
                self._staged_show_identity_contexts.setdefault(credit.tmdb_id, {}).setdefault(tmdb_show_id, []).append(
                    {
                        "item_id": item_id,
                        "item_name": str(emby_item.get("Name") or item_id),
                        "name": credit.name,
                        "role": credit.role or "",
                        "person_type": credit.person_type,
                        "tmdb_show_id": tmdb_show_id,
                    }
                )
        credit_ids = {credit.tmdb_id for credit in credits}
        for link in getattr(credits_item, "identity_links", None) or []:
            tmdb_person_id = link.get("tmdb_id")
            tvdb_person_id = link.get("tvdb_id")
            name = str(link.get("name") or "").strip()
            role = str(link.get("role") or "").strip()
            if not (str(tmdb_person_id or "").isdigit() and str(tvdb_person_id or "").isdigit() and name and role):
                continue
            tmdb_person_id = int(tmdb_person_id)
            alias_id = -int(tvdb_person_id)
            matching_credit = next(
                (credit for credit in credits if credit.tmdb_id == alias_id and normalize_person_name(credit.name) == normalize_person_name(name) and normalize_person_name(credit.role) == normalize_person_name(role)),
                None,
            )
            if alias_id not in credit_ids or matching_credit is None:
                continue
            self._discovered_names.setdefault(tmdb_person_id, name)
            self._staged_identity_links.setdefault(alias_id, {}).setdefault(tmdb_person_id, []).append(
                {
                    "item_id": item_id,
                    "item_name": str(emby_item.get("Name") or item_id),
                    "name": name,
                    "role": role,
                    "person_type": str(link.get("person_type") or matching_credit.person_type),
                }
            )

        self.plans.append(
            ItemPeoplePlan(
                item_id=item_id,
                tmdb_id=source_item_id,
                credits_source=credits_source,
                emby_etag=emby_item.get("Etag"),
                emby_item=copy.deepcopy(emby_item),
                credits=credits,
                credits_hash=stable_hash(
                    {
                        "source": credits_source,
                        "cast_source": cast_source,
                        "crew_source": crew_source,
                        "credits": [credit.source_tuple() for credit in credits],
                    }
                ),
            )
        )
        return True

    def finalize_discovery(self):
        if self._finalized:
            return
        discovery_started = time.monotonic()
        logger.info(f"Emby Cast & Crew discovery | loading People database | {len(self._discovered_names)} source identities")
        cached = self.cache.query_emby_person_identities(self.server_id) if self.cache else {}
        records = {tmdb_id: PersonIdentity.from_cache(data) for tmdb_id, data in cached.items()}
        logger.info(f"Emby Cast & Crew discovery | loaded {len(records)} cached People identities | {time.monotonic() - discovery_started:.1f}s")

        discovered_total = len(self._discovered_names)
        for discovered_index, (tmdb_id, base_name) in enumerate(self._discovered_names.items(), 1):
            normalized_name = normalize_person_name(base_name)
            external_ids = self._discovered_external_ids.get(tmdb_id, {})
            existing = records.get(tmdb_id)
            if existing:
                if existing.base_name != base_name or existing.normalized_name != normalized_name:
                    existing.base_name = base_name
                    existing.normalized_name = normalized_name
                    existing.display_name = base_name if existing.name_index is None else f"{base_name} ({roman_number(existing.name_index)})"
                    self._changed_identity_ids.add(tmdb_id)
                for field in ("imdb_id", "tvdb_id", "wikidata_id"):
                    source_id = external_ids.get(field)
                    cached_id = getattr(existing, field)
                    if source_id and not cached_id:
                        setattr(existing, field, source_id)
                        self._changed_identity_ids.add(tmdb_id)
                    elif source_id and cached_id != source_id:
                        logger.warning(f"Credit source changed {field.replace('_id', '').upper()} ID for " f"person identity {tmdb_id} from {cached_id} to {source_id}; keeping cached ID")
            else:
                records[tmdb_id] = PersonIdentity(
                    tmdb_id=tmdb_id,
                    base_name=base_name,
                    normalized_name=normalized_name,
                    display_name=base_name,
                    imdb_id=external_ids.get("imdb_id"),
                    tvdb_id=external_ids.get("tvdb_id"),
                    wikidata_id=external_ids.get("wikidata_id"),
                )
                self._changed_identity_ids.add(tmdb_id)
            if discovered_index % 10000 == 0 or discovered_index == discovered_total:
                logger.ghost(f"Emby Cast & Crew identity merge | {discovered_index}/{discovered_total}")

        logger.info(f"Emby Cast & Crew discovery | merged {len(self._discovered_names)} source identities | " f"{time.monotonic() - discovery_started:.1f}s")
        self._resolve_external_cross_source_identities(records)
        self._resolve_contextual_show_identity_links(records)
        self._apply_staged_credit_identity_links(records)
        self._enforce_unique_external_ids(records)
        working = dict(records)
        proven_singletons = set()
        for source_id, record in records.items():
            if record.canonical_id is None:
                continue
            canonical = records.get(record.canonical_id)
            externally_linked = canonical and canonical.canonical_id is None and record.tmdb_id < 0 and canonical.tmdb_id > 0 and canonical.tvdb_id == record.provider_id
            if externally_linked:
                working[source_id] = canonical
                proven_singletons.add(canonical.normalized_name)
                # A provider alias is no longer an independently addressable
                # namesake once its external identity has been proven. Clear a
                # stale Roman index before persisting it; otherwise the alias
                # can collide with the canonical identity's deterministic
                # (normalized_name, name_index) slot.
                if record.name_index is not None or record.display_name != record.base_name:
                    record.name_index = None
                    record.display_name = record.base_name
                    self._changed_identity_ids.add(source_id)
            else:
                record.canonical_id = None
                self._changed_identity_ids.add(source_id)

        self._identity_records = records
        self.identities = working
        self._invalidate_identity_indexes()
        groups = {}
        unique_identities = self._unique_identities()
        for identity in unique_identities:
            identity.duplicate_emby_ids.discard(str(identity.emby_id or ""))
            if identity.duplicate_emby_ids:
                self._same_identity_duplicate_ids.add(identity.tmdb_id)
                self._noncanonical_person_ids.update(identity.duplicate_emby_ids)
            groups.setdefault(identity.normalized_name, []).append(identity)
        logger.ghost(f"Emby Cast & Crew discovery | grouping {len(records)} People identities | {len(groups)} names")
        for normalized_name, identities in groups.items():
            self._reindex_identity_group(identities, clear_singleton=normalized_name in proven_singletons)
        self._refresh_duplicate_identity_ids(
            groups=groups,
            unique_identities=unique_identities,
        )
        if self.cache and (self._changed_identity_ids or self._identity_cache_dirty):
            logger.ghost(f"Emby Cast & Crew discovery | saving People database | " f"{len(self._changed_identity_ids)} changed identities")
            self._persist_all_identities()
        self._finalized = True
        elapsed = max(time.monotonic() - discovery_started, 0.001)
        logger.info(f"Emby Cast & Crew Discovery Complete | {len(records)} People identities | " f"{len(self._discovered_names)} used by this library | {elapsed:.1f}s | {len(records) / elapsed:.0f} Identities/s")

    def _apply_staged_credit_identity_links(self, records):
        """Merge provider identities proven by one unique credit in one item."""
        if not self._staged_identity_links:
            return
        verified_at = datetime.now().isoformat(timespec="seconds")
        for alias_id, candidates in sorted(self._staged_identity_links.items()):
            if len(candidates) != 1:
                logger.warning(f"External People item-credit bridge unresolved | TVDb {-alias_id} | " f"multiple TMDb candidates {', '.join(str(value) for value in sorted(candidates))}")
                continue
            tmdb_id, proofs = next(iter(candidates.items()))
            canonical = records.get(tmdb_id)
            alias = records.get(alias_id)
            if not canonical or not alias:
                continue
            cached_bridge = (
                alias.canonical_id == canonical.tmdb_id
                and canonical.tvdb_id == alias.provider_id
                and canonical.external_verified_at
                and alias.external_verified_at
                and not self._external_identity_audit_due(canonical)
                and not self._external_identity_audit_due(alias)
            )
            if cached_bridge:
                logger.ghost(f"External People item-credit bridge cache hit | TVDb {alias.provider_id} = " f"TMDb {canonical.tmdb_id}")
                continue
            evidence = self._get_tmdb_identity_evidence(tmdb_id)
            authoritative_name = str(evidence.get("name") or canonical.base_name).strip()
            if not evidence.get("available") or normalize_person_name(authoritative_name) != canonical.normalized_name or canonical.normalized_name != alias.normalized_name:
                logger.warning(f"External People item-credit bridge rejected | TVDb {alias.provider_id} -> " f"TMDb {tmdb_id} | TMDb identity name did not verify")
                continue
            authoritative_imdb_id = evidence.get("imdb_id")
            wikidata_id = evidence.get("wikidata_id")
            if alias.imdb_id and authoritative_imdb_id and str(alias.imdb_id) != str(authoritative_imdb_id):
                context_cache = getattr(self.tmdb, "cache", None)
                if context_cache and hasattr(context_cache, "update_tvdb_show_people_map"):
                    for tmdb_show_id in {
                        proof.get("tmdb_show_id")
                        for proof in proofs
                        if str(proof.get("tmdb_show_id") or "").isdigit()
                    }:
                        context_cache.update_tvdb_show_people_map(
                            int(tmdb_show_id),
                            {int(alias.provider_id): None},
                        )
                logger.warning(
                    f"External People item-credit bridge rejected | "
                    f"TVDb {alias.provider_id} -> TMDb {tmdb_id} | "
                    f"verified IMDb conflict: TVDb {alias.imdb_id}, "
                    f"TMDb {authoritative_imdb_id}"
                )
                continue
            wikidata_proof = None
            if wikidata_id:
                wikidata_evidence = self._get_wikidata_identity_evidence(wikidata_id)
                wikidata_tmdb_id = wikidata_evidence.get("tmdb_id")
                wikidata_tvdb_id = wikidata_evidence.get("tvdb_id")
                wikidata_imdb_id = wikidata_evidence.get("imdb_id")
                conflicts = []
                if wikidata_tmdb_id and int(wikidata_tmdb_id) != canonical.tmdb_id:
                    conflicts.append(f"TMDb {wikidata_tmdb_id}")
                if wikidata_tvdb_id and str(wikidata_tvdb_id) != alias.provider_id:
                    conflicts.append(f"TVDb {wikidata_tvdb_id}")
                if authoritative_imdb_id and wikidata_imdb_id and str(wikidata_imdb_id) != str(authoritative_imdb_id):
                    conflicts.append(f"IMDb {wikidata_imdb_id}")
                if conflicts:
                    logger.warning(f"External People item-credit bridge rejected | Wikidata {wikidata_id} conflicts with " f"TVDb {alias.provider_id} -> TMDb {tmdb_id}: {', '.join(conflicts)}")
                    continue
                if wikidata_evidence.get("available"):
                    authoritative_imdb_id = authoritative_imdb_id or wikidata_imdb_id
                    canonical.wikidata_id = wikidata_id
                    alias.wikidata_id = wikidata_id
                    if wikidata_tmdb_id == canonical.tmdb_id and str(wikidata_tvdb_id or "") == alias.provider_id:
                        wikidata_proof = f"Wikidata {wikidata_id}"
            if authoritative_imdb_id:
                canonical.imdb_id = str(authoritative_imdb_id)
                alias.imdb_id = str(authoritative_imdb_id)
            canonical.external_verified_at = verified_at
            alias.external_verified_at = verified_at
            self._merge_external_identity_records(
                canonical,
                alias,
                authoritative=True,
            )
            self._changed_identity_ids.update((canonical.tmdb_id, alias.tmdb_id))
            proof = proofs[0]
            logger.info(
                f"External People identity verified | TVDb {alias.provider_id} = TMDb {canonical.tmdb_id} = "
                f"IMDb {canonical.imdb_id or 'unavailable'}" + (f" = {wikidata_proof}" if wikidata_proof else "") + f" | exact item credit | {proof['item_name']} | {proof['person_type']} as {proof['role']}"
            )

    @staticmethod
    def _context_person_type(person_type):
        value = str(person_type or "").strip()
        return "Actor" if value in {"Actor", "GuestStar"} else value

    def _resolve_contextual_show_identity_links(self, records):
        """Bridge TVDb-only credits through one externally verified TMDb show.

        TVDb often omits remote IDs for episode writers and guest stars. An
        exact, unique name/type match in the aggregate credits of the same
        TMDb/TVDb-linked show is safe contextual evidence. Both positive and
        negative results are cached so unchanged shows do not hit TMDb again.
        """
        if not self._staged_show_identity_contexts or not self.tmdb or not hasattr(self.tmdb, "get_show_aggregate_people"):
            return
        context_cache = getattr(self.tmdb, "cache", None)
        if not context_cache or not hasattr(context_cache, "query_tvdb_show_people_map") or not hasattr(context_cache, "update_tvdb_show_people_map"):
            return

        aliases_by_show = {}
        for alias_id, show_contexts in self._staged_show_identity_contexts.items():
            alias = records.get(alias_id)
            if not alias or alias.tmdb_id >= 0 or alias.canonical_id is not None or not alias.external_verified_at:
                continue
            for tmdb_show_id, proofs in show_contexts.items():
                aliases_by_show.setdefault(int(tmdb_show_id), {})[alias_id] = proofs

        for tmdb_show_id, aliases in sorted(aliases_by_show.items()):
            tvdb_ids = [-alias_id for alias_id in aliases]
            cached, missing_ids = context_cache.query_tvdb_show_people_map(
                tmdb_show_id,
                tvdb_ids,
                getattr(self.tmdb, "expiration", getattr(context_cache, "expiration", 30)),
            )
            if missing_ids:
                try:
                    aggregate_people = self.tmdb.get_show_aggregate_people(tmdb_show_id)
                except Exception as error:
                    logger.warning(f"TMDb show {tmdb_show_id} aggregate People lookup unavailable: {error}")
                    continue
                candidates_by_key = {}
                for person in aggregate_people or []:
                    person_id = person.get("id")
                    name = str(person.get("name") or "").strip()
                    person_type = self._context_person_type(person.get("person_type"))
                    if not str(person_id or "").isdigit() or not name or not person_type:
                        continue
                    candidates_by_key.setdefault((normalize_person_name(name), person_type), set()).add(int(person_id))

                fetched = {}
                for tvdb_id in missing_ids:
                    alias_id = -int(tvdb_id)
                    alias = records.get(alias_id)
                    candidate_ids = set()
                    for proof in aliases.get(alias_id, []):
                        candidate_ids.update(
                            candidates_by_key.get(
                                (alias.normalized_name, self._context_person_type(proof.get("person_type"))),
                                set(),
                            )
                        )
                    fetched[int(tvdb_id)] = next(iter(candidate_ids)) if len(candidate_ids) == 1 else None
                context_cache.update_tvdb_show_people_map(tmdb_show_id, fetched)
                cached.update(fetched)

            for alias_id, proofs in aliases.items():
                tmdb_id = cached.get(-alias_id)
                if not str(tmdb_id or "").isdigit():
                    continue
                tmdb_id = int(tmdb_id)
                canonical = records.get(tmdb_id)
                alias = records.get(alias_id)
                if canonical is None:
                    canonical = PersonIdentity(
                        tmdb_id=tmdb_id,
                        base_name=alias.base_name,
                        normalized_name=alias.normalized_name,
                        display_name=alias.base_name,
                    )
                    records[tmdb_id] = canonical
                    self._changed_identity_ids.add(tmdb_id)
                self._staged_identity_links.setdefault(alias_id, {}).setdefault(tmdb_id, []).append(proofs[0])
                logger.ghost(f"External People show-credit bridge | TVDb {-alias_id} -> TMDb {tmdb_id} | show {tmdb_show_id}")

    def _resolve_external_cross_source_identities(self, records):
        """Rebuild TVDb aliases exclusively from authoritative TVDb crosswalks."""
        resolve_started = time.monotonic()
        canonical_emby_ids = {str(identity.emby_id) for identity in records.values() if identity.tmdb_id > 0 and identity.emby_id}
        for canonical in list(records.values()):
            if canonical.tmdb_id <= 0 or not str(canonical.tvdb_id or "").isdigit():
                continue
            alias_id = -int(canonical.tvdb_id)
            if alias_id not in records:
                records[alias_id] = PersonIdentity(
                    tmdb_id=alias_id,
                    base_name=canonical.base_name,
                    normalized_name=canonical.normalized_name,
                    display_name=canonical.base_name,
                    tvdb_id=str(canonical.tvdb_id),
                    wikidata_id=canonical.wikidata_id,
                )
                self._changed_identity_ids.add(alias_id)

        aliases = [identity for identity in records.values() if identity.tmdb_id < 0]
        # A previously audited TVDb alias may already contain the decisive
        # IMDb ID even when that alias is not used by the current library.
        # Consolidate that cached external proof immediately instead of
        # waiting for another TVDb request or for the alias to appear as a
        # source credit. This also lets materialization recognize an Emby
        # false friend whose Person carries both provider IDs.
        canonical_by_imdb_name = {}
        for candidate in records.values():
            if candidate.tmdb_id > 0 and candidate.canonical_id is None and candidate.imdb_id:
                canonical_by_imdb_name.setdefault(
                    (candidate.imdb_id, candidate.normalized_name),
                    [],
                ).append(candidate)
        for alias in aliases:
            if alias.canonical_id is not None or not alias.imdb_id or not alias.external_verified_at:
                continue
            imdb_candidates = [
                candidate
                for candidate in canonical_by_imdb_name.get(
                    (alias.imdb_id, alias.normalized_name),
                    [],
                )
                if not candidate.tvdb_id or candidate.tvdb_id == alias.provider_id
            ]
            if len(imdb_candidates) == 1:
                canonical = imdb_candidates[0]
                self._merge_external_identity_records(
                    canonical,
                    alias,
                    authoritative=True,
                )
                logger.info(
                    f"External People cached identity consolidated | "
                    f"TVDb {alias.provider_id} = TMDb {canonical.provider_id} = "
                    f"IMDb {alias.imdb_id}"
                )
        forced_alias_ids = {
            -int(canonical.tvdb_id)
            for canonical in records.values()
            if (canonical.tmdb_id > 0 and canonical.tmdb_id in self._changed_identity_ids and str(canonical.tvdb_id or "").isdigit() and (-int(canonical.tvdb_id) not in records or records[-int(canonical.tvdb_id)].canonical_id != canonical.tmdb_id))
        }
        cached_change_alias_ids = set()
        tvdb_cache = getattr(self.tvdb, "cache", None)
        active_aliases = [alias for alias in aliases if alias.tmdb_id in self._discovered_names]
        if active_aliases and tvdb_cache and hasattr(tvdb_cache, "query_tvdb_people_external_ids"):
            cached_external, _ = tvdb_cache.query_tvdb_people_external_ids(
                [alias.provider_id for alias in active_aliases],
                getattr(self.tvdb, "expiration", getattr(tvdb_cache, "expiration", 30)),
            )
            for alias in active_aliases:
                external = cached_external.get(int(alias.provider_id)) or cached_external.get(alias.provider_id) or {}
                remote_tmdb_id = external.get("tmdb_id")
                remote_imdb_id = str(external.get("imdb_id") or "").strip() or None
                remote_wikidata_id = str(external.get("wikidata_id") or "").strip() or None
                if (
                    (str(remote_tmdb_id or "").isdigit() and (alias.canonical_id != int(remote_tmdb_id) or int(remote_tmdb_id) not in records))
                    or (remote_imdb_id and alias.imdb_id != remote_imdb_id)
                    or (remote_wikidata_id and alias.wikidata_id != remote_wikidata_id)
                ):
                    cached_change_alias_ids.add(alias.tmdb_id)
        due_aliases = [alias for alias in aliases if alias.tmdb_id in forced_alias_ids or alias.tmdb_id in cached_change_alias_ids or self._external_identity_audit_due(alias)]
        logger.info(f"Emby Cast & Crew discovery | TVDb crosswalk cache | " f"{len(aliases) - len(due_aliases)} hits | {len(due_aliases)} due | " f"{time.monotonic() - resolve_started:.1f}s")
        if due_aliases and len(due_aliases) <= 10:
            logger.debug("TVDb crosswalk due identities | " + ", ".join(f"{alias.provider_id} ({'changed-link' if alias.tmdb_id in forced_alias_ids else 'expired'})" for alias in due_aliases))
        if not aliases or not self.tvdb or not hasattr(self.tvdb, "get_people_external_ids_bulk"):
            return

        aliases_by_canonical = {}
        due_alias_ids = {alias.tmdb_id for alias in due_aliases}
        for alias in aliases:
            if alias.tmdb_id not in due_alias_ids and alias.canonical_id and alias.canonical_id in records:
                aliases_by_canonical.setdefault(alias.canonical_id, []).append(alias)

        external_by_tvdb = {}
        if due_aliases:
            try:
                external_by_tvdb = self.tvdb.get_people_external_ids_bulk(
                    [alias.provider_id for alias in due_aliases],
                    progress_callback=lambda completed, total: logger.ghost(f"TVDb People Crosswalk Verification | {completed}/{total}"),
                )
            except Exception as error:
                logger.warning(f"TVDb People external-ID lookup unavailable: {error}")
                return

        external_verified_at = datetime.now().isoformat(timespec="seconds")
        for alias in due_aliases:
            numeric_provider_id = int(alias.provider_id)
            if numeric_provider_id in external_by_tvdb:
                external = external_by_tvdb[numeric_provider_id]
            elif alias.provider_id in external_by_tvdb:
                external = external_by_tvdb[alias.provider_id]
            else:
                # A missing key means that the provider request failed. Preserve
                # the last known relationship; only a successful empty response
                # is authoritative evidence that this TVDb record has no links.
                if alias.canonical_id and alias.canonical_id in records:
                    aliases_by_canonical.setdefault(alias.canonical_id, []).append(alias)
                continue
            if alias.external_verified_at != external_verified_at:
                alias.external_verified_at = external_verified_at
                self._identity_cache_dirty = True
            previous_canonical_id = alias.canonical_id
            before = (
                alias.imdb_id,
                alias.wikidata_id,
                alias.canonical_id,
                alias.name_index,
                alias.display_name,
                alias.emby_id,
                alias.emby_etag,
                alias.emby_signature,
                tuple(sorted(alias.duplicate_emby_ids)),
            )
            external = external or {}
            remote_tmdb_id = external.get("tmdb_id")
            remote_imdb_id = external.get("imdb_id")
            remote_wikidata_id = external.get("wikidata_id")
            previous_alias_imdb_id = alias.imdb_id
            previous_alias_verified_at = alias.external_verified_at
            alias.imdb_id = str(remote_imdb_id) if remote_imdb_id else None
            alias.wikidata_id = str(remote_wikidata_id) if remote_wikidata_id else None
            alias.canonical_id = None
            if remote_wikidata_id:
                wikidata_evidence = self._get_wikidata_identity_evidence(remote_wikidata_id)
                wikidata_tvdb_id = wikidata_evidence.get("tvdb_id")
                if wikidata_evidence.get("available") and (not wikidata_tvdb_id or wikidata_tvdb_id == alias.provider_id):
                    remote_tmdb_id = remote_tmdb_id or wikidata_evidence.get("tmdb_id")
                    remote_imdb_id = remote_imdb_id or wikidata_evidence.get("imdb_id")
                elif wikidata_tvdb_id and wikidata_tvdb_id != alias.provider_id:
                    logger.warning(f"Wikidata People identity conflict | {remote_wikidata_id} reports TVDb " f"{wikidata_tvdb_id}, not TVDb {alias.provider_id}; ignoring Wikidata crosswalk")
            if str(remote_tmdb_id or "").isdigit():
                remote_tmdb_id = int(remote_tmdb_id)
                canonical = records.get(remote_tmdb_id)
                if canonical is None:
                    evidence = self._get_tmdb_identity_evidence(remote_tmdb_id)
                    canonical_name = str(evidence.get("name") or alias.base_name).strip()
                    canonical_imdb_id = evidence.get("imdb_id") or remote_imdb_id
                    canonical = PersonIdentity(
                        tmdb_id=remote_tmdb_id,
                        base_name=canonical_name,
                        normalized_name=normalize_person_name(canonical_name),
                        display_name=canonical_name,
                        imdb_id=str(canonical_imdb_id) if canonical_imdb_id else None,
                        tvdb_id=alias.provider_id,
                        wikidata_id=str(remote_wikidata_id) if remote_wikidata_id else evidence.get("wikidata_id"),
                        external_verified_at=external_verified_at if evidence.get("available") else None,
                    )
                    records[remote_tmdb_id] = canonical
                    self._changed_identity_ids.add(remote_tmdb_id)
                    logger.info(f"External People identity created | TVDb {alias.provider_id} -> " f"TMDb {remote_tmdb_id} | IMDb {canonical.imdb_id or 'unavailable'}")
                if canonical:
                    canonical_before = (
                        canonical.imdb_id,
                        canonical.tvdb_id,
                        canonical.emby_id,
                        canonical.emby_etag,
                        canonical.emby_signature,
                    )
                    remote_imdb_id = str(remote_imdb_id) if remote_imdb_id else None
                    canonical_wikidata_id = str(remote_wikidata_id or canonical.wikidata_id or "").strip() or None
                    if remote_imdb_id and canonical.imdb_id and canonical.imdb_id != remote_imdb_id:
                        cached_canonical_imdb = str(canonical.imdb_id)
                        evidence = self._get_tmdb_identity_evidence(canonical.tmdb_id)
                        authoritative_imdb = evidence.get("imdb_id")
                        selected_imdb_id = str(authoritative_imdb or canonical.imdb_id or remote_imdb_id)
                        if selected_imdb_id == remote_imdb_id:
                            logger.warning(f"External People identity conflict resolved | TVDb {alias.provider_id} -> TMDb {canonical.tmdb_id} | " f"verified IMDb {selected_imdb_id}; replacing stale cached IMDb {cached_canonical_imdb}")
                        else:
                            logger.warning(f"External People identity conflict resolved | TVDb {alias.provider_id} -> TMDb {canonical.tmdb_id} | " f"ignoring TVDb IMDb {remote_imdb_id}; using canonical IMDb {selected_imdb_id}")
                        canonical.imdb_id = selected_imdb_id
                        alias.imdb_id = selected_imdb_id
                    elif canonical.imdb_id:
                        alias.imdb_id = str(canonical.imdb_id)
                    elif remote_imdb_id:
                        canonical.imdb_id = remote_imdb_id
                        alias.imdb_id = remote_imdb_id
                    canonical.tvdb_id = alias.provider_id
                    canonical.wikidata_id = canonical_wikidata_id
                    alias.wikidata_id = canonical_wikidata_id
                    if not canonical.emby_id and alias.emby_id:
                        canonical.emby_id = alias.emby_id
                        canonical.emby_etag = alias.emby_etag
                        canonical.emby_signature = alias.emby_signature
                    alias.canonical_id = canonical.tmdb_id
                    aliases_by_canonical.setdefault(canonical.tmdb_id, []).append(alias)
                    canonical_after = (
                        canonical.imdb_id,
                        canonical.tvdb_id,
                        canonical.emby_id,
                        canonical.emby_etag,
                        canonical.emby_signature,
                    )
                    if canonical_before != canonical_after:
                        self._changed_identity_ids.add(canonical.tmdb_id)
            elif remote_imdb_id:
                imdb_candidates = [candidate for candidate in records.values() if (candidate.tmdb_id > 0 and candidate.canonical_id is None and candidate.imdb_id == str(remote_imdb_id) and candidate.normalized_name == alias.normalized_name)]
                if len(imdb_candidates) == 1:
                    canonical = imdb_candidates[0]
                    canonical.tvdb_id = alias.provider_id
                    alias.canonical_id = canonical.tmdb_id
                    aliases_by_canonical.setdefault(canonical.tmdb_id, []).append(alias)
                    self._changed_identity_ids.add(canonical.tmdb_id)
            elif previous_canonical_id is not None:
                previous_canonical = records.get(previous_canonical_id)
                matching_imdb_evidence = bool(previous_canonical and previous_canonical.imdb_id and previous_alias_imdb_id == previous_canonical.imdb_id)
                matching_item_credit_evidence = bool(previous_canonical and previous_alias_verified_at and previous_canonical.external_verified_at == previous_alias_verified_at)
                contextual_mapping_verified = (
                    previous_canonical
                    and previous_canonical.tmdb_id > 0
                    and previous_canonical.tvdb_id == alias.provider_id
                    and previous_canonical.normalized_name == alias.normalized_name
                    and previous_canonical.external_verified_at
                    and (matching_imdb_evidence or matching_item_credit_evidence)
                )
                if contextual_mapping_verified:
                    alias.imdb_id = previous_canonical.imdb_id
                    alias.canonical_id = previous_canonical.tmdb_id
                    previous_canonical.external_verified_at = external_verified_at
                    alias.external_verified_at = external_verified_at
                    aliases_by_canonical.setdefault(previous_canonical.tmdb_id, []).append(alias)
            inherited_mapping_disproved = previous_canonical_id is not None and alias.canonical_id != previous_canonical_id
            unlinked_alias_collides = alias.canonical_id is None and alias.emby_id is not None and str(alias.emby_id) in canonical_emby_ids
            if inherited_mapping_disproved or unlinked_alias_collides:
                # This Emby mapping was inherited through the now-disproved
                # cross-source relationship. It must not be allowed to rename
                # or overwrite the previously linked canonical Person.
                alias.emby_id = None
                alias.emby_etag = None
                alias.emby_signature = None
                alias.duplicate_emby_ids.clear()
            after = (
                alias.imdb_id,
                alias.wikidata_id,
                alias.canonical_id,
                alias.name_index,
                alias.display_name,
                alias.emby_id,
                alias.emby_etag,
                alias.emby_signature,
                tuple(sorted(alias.duplicate_emby_ids)),
            )
            if before != after:
                self._changed_identity_ids.add(alias.tmdb_id)

        for canonical in (identity for identity in records.values() if identity.tmdb_id > 0):
            proven_aliases = aliases_by_canonical.get(canonical.tmdb_id, [])
            proven_ids = {alias.provider_id for alias in proven_aliases}
            previous_tvdb_id = canonical.tvdb_id
            if str(previous_tvdb_id or "") in proven_ids:
                selected_tvdb_id = str(previous_tvdb_id)
            elif len(proven_aliases) == 1:
                selected_tvdb_id = proven_aliases[0].provider_id
            else:
                imdb_matches = [alias.provider_id for alias in proven_aliases if canonical.imdb_id and alias.imdb_id == canonical.imdb_id]
                selected_tvdb_id = imdb_matches[0] if len(imdb_matches) == 1 else None
            canonical.tvdb_id = selected_tvdb_id
            if previous_tvdb_id != canonical.tvdb_id:
                canonical.emby_etag = None
                canonical.emby_signature = None
                self._changed_identity_ids.add(canonical.tmdb_id)
            if len(proven_aliases) > 1 and selected_tvdb_id is None:
                logger.warning(f"External People identity conflict | TMDb {canonical.tmdb_id} has multiple authoritative TVDb IDs " f"{', '.join(sorted(proven_ids, key=int))}; no primary TVDb ID selected")

    def _merge_external_identity_records(self, canonical, alias, authoritative=False):
        if canonical.tvdb_id and canonical.tvdb_id != alias.provider_id:
            if not authoritative:
                logger.warning(f"External People identity conflict | TMDb {canonical.provider_id} already maps to " f"TVDb {canonical.tvdb_id}, not TVDb {alias.provider_id}; keeping separate identities")
                return False
            logger.info(f"External People identity resolved | TMDb {canonical.provider_id} primary TVDb " f"{canonical.tvdb_id} -> {alias.provider_id} | authoritative TVDb crosswalk")
        before = (
            canonical.tvdb_id,
            canonical.imdb_id,
            canonical.wikidata_id,
            canonical.emby_id,
            canonical.emby_etag,
            canonical.emby_signature,
            tuple(sorted(canonical.duplicate_emby_ids)),
            alias.tvdb_id,
            alias.wikidata_id,
            alias.canonical_id,
            alias.name_index,
            alias.display_name,
        )
        canonical.tvdb_id = alias.provider_id
        if alias.imdb_id:
            if canonical.imdb_id and canonical.imdb_id != alias.imdb_id:
                logger.warning(f"External People identity conflict | TMDb {canonical.provider_id} has IMDb " f"{canonical.imdb_id}, TVDb {alias.provider_id} has IMDb {alias.imdb_id}; keeping cached IMDb ID")
            elif not canonical.imdb_id:
                canonical.imdb_id = alias.imdb_id
        if alias.wikidata_id:
            if canonical.wikidata_id and canonical.wikidata_id != alias.wikidata_id:
                logger.warning(f"External People identity conflict | TMDb {canonical.provider_id} has Wikidata " f"{canonical.wikidata_id}, TVDb {alias.provider_id} has Wikidata {alias.wikidata_id}; " f"keeping TMDb Wikidata ID")
            elif not canonical.wikidata_id:
                canonical.wikidata_id = alias.wikidata_id
        if not canonical.emby_id and alias.emby_id:
            canonical.emby_id = alias.emby_id
            canonical.emby_etag = alias.emby_etag
            canonical.emby_signature = alias.emby_signature
        canonical.duplicate_emby_ids.update(alias.duplicate_emby_ids)
        alias.tvdb_id = alias.provider_id
        alias.wikidata_id = canonical.wikidata_id or alias.wikidata_id
        alias.canonical_id = canonical.tmdb_id
        alias.name_index = None
        alias.display_name = alias.base_name
        after = (
            canonical.tvdb_id,
            canonical.imdb_id,
            canonical.wikidata_id,
            canonical.emby_id,
            canonical.emby_etag,
            canonical.emby_signature,
            tuple(sorted(canonical.duplicate_emby_ids)),
            alias.tvdb_id,
            alias.wikidata_id,
            alias.canonical_id,
            alias.name_index,
            alias.display_name,
        )
        if before != after:
            self._changed_identity_ids.update((canonical.tmdb_id, alias.tmdb_id))
        return True

    def _enforce_unique_external_ids(self, records):
        """Ensure each TVDb/IMDb/Wikidata ID belongs to one proven identity."""
        for field_name, provider_name in (
            ("tvdb_id", "TVDb"),
            ("imdb_id", "IMDb"),
            ("wikidata_id", "Wikidata"),
        ):
            owners = {}
            for identity in records.values():
                if identity.tmdb_id > 0 and identity.canonical_id is None:
                    external_id = getattr(identity, field_name)
                    if external_id:
                        owners.setdefault(str(external_id), []).append(identity)
            for external_id, identities in owners.items():
                if len(identities) < 2:
                    continue
                ordered = sorted(identities, key=lambda value: value.tmdb_id)
                proven_ids = set()
                if field_name == "tvdb_id" and external_id.isdigit():
                    alias = records.get(-int(external_id))
                    if alias and alias.canonical_id in {identity.tmdb_id for identity in ordered}:
                        proven_ids.add(alias.canonical_id)
                elif field_name in {"imdb_id", "wikidata_id"}:
                    for identity in ordered:
                        if not identity.tvdb_id or not str(identity.tvdb_id).isdigit():
                            continue
                        alias = records.get(-int(identity.tvdb_id))
                        if alias and alias.canonical_id == identity.tmdb_id and getattr(alias, field_name) == external_id:
                            proven_ids.add(identity.tmdb_id)
                winner = next((identity for identity in ordered if identity.tmdb_id in proven_ids), None) if len(proven_ids) == 1 else None
                losing_identities = [identity for identity in ordered if identity is not winner]
                for losing_identity in losing_identities:
                    setattr(losing_identity, field_name, None)
                    losing_identity.emby_etag = None
                    losing_identity.emby_signature = None
                    self._changed_identity_ids.add(losing_identity.tmdb_id)
                if winner:
                    logger.warning(f"External People identity conflict | {provider_name} {external_id} is assigned to TMDb " f"{', '.join(str(identity.tmdb_id) for identity in ordered)}; authoritative crosswalk keeps TMDb {winner.tmdb_id}")
                else:
                    logger.warning(
                        f"External People identity conflict | {provider_name} {external_id} is assigned to TMDb " f"{', '.join(str(identity.tmdb_id) for identity in ordered)}; removed from all canonical identities pending authoritative resolution"
                    )

    def apply(self):
        self.finalize_discovery()
        summary = {"staged": len(self.plans), "updated": 0, "skipped": 0, "failed": 0, "created": 0}
        if not self.plans:
            return summary

        logger.info("")
        logger.separator(f"Emby Cast & Crew Apply: {len(self.plans)} Items", space=False, border=False)
        logger.info("Emby provider search disabled; linked People are verified by external IDs and names")
        if hasattr(self.emby, "get_stable_item_etags"):
            stable_etags = self.emby.get_stable_item_etags([plan.item_id for plan in self.plans])
            for plan in self.plans:
                plan.emby_etag = stable_etags.get(plan.item_id)
        self._states = self.cache.query_emby_people_item_states(self.server_id, [plan.item_id for plan in self.plans]) if self.cache else {}
        verification_plans = []
        etag_only_updates = 0
        for plan in self.plans:
            state = self._states.get(plan.item_id)
            if self._fast_state_match(plan, state)[0]:
                continue
            applied_hash = self._cached_people_match_ignoring_etag(plan, state)
            if applied_hash:
                self._store_item_state(plan, applied_hash)
                updated_state = dict(state)
                updated_state["emby_etag"] = plan.emby_etag
                self._states[plan.item_id] = updated_state
                etag_only_updates += 1
            else:
                verification_plans.append(plan)
        logger.info(
            f"Emby Cast & Crew verification scope | {len(verification_plans)} changed or unverified Items | " f"{len(self.plans) - len(verification_plans)} cached Items" + (f" | {etag_only_updates} ETag-only cache updates" if etag_only_updates else "")
        )
        self._detect_false_friends(verification_plans)
        audit_ids = set()
        for plan in self.plans:
            fast_skip, _ = self._fast_state_match(plan, self._states.get(plan.item_id))
            plan_ids = {credit.tmdb_id for credit in plan.credits}
            if fast_skip:
                audit_ids.update(tmdb_id for tmdb_id in plan_ids if self._external_identity_audit_due(self.identities[tmdb_id]) or (self._requires_name_lock(self.identities[tmdb_id]) and self._identity_audit_due(self.identities[tmdb_id])))

        self._ensure_audit_people(audit_ids)

        self._ensure_changed_people()
        item_apply_started = time.monotonic()
        for index, plan in enumerate(self.plans, 1):
            try:
                elapsed = max(time.monotonic() - item_apply_started, 0.001)
                completed = index - 1
                speed = completed / elapsed
                eta_seconds = int((len(self.plans) - completed) / speed) if speed > 0 else 0
                eta = f"{eta_seconds // 60}m {eta_seconds % 60:02d}s" if speed > 0 else "calculating"
                source_name = plan.credits_source.upper()
                logger.ghost(f"({index}/{len(self.plans)}) {source_name} Cast & Crew | {plan.emby_item.get('Name') or plan.item_id} | " f"{len(plan.credits)} Credits | {speed:.2f} Items/s | ETA {eta}")
                if index == 1 or index % 100 == 0:
                    logger.info(f"Emby Cast & Crew Progress | {index}/{len(self.plans)} | " f"updated={summary['updated']} skipped={summary['skipped']} failed={summary['failed']} | " f"{speed:.2f} Items/s | ETA {eta}")
                fast_skip, applied_hash = self._fast_state_match(plan, self._states.get(plan.item_id))
                if fast_skip:
                    summary["skipped"] += 1
                    continue

                created, fresh, item_updated, reindexed_after_creation = self._materialize_plan_people(plan)
                summary["created"] += created
                self._ensure_plan_people(plan)
                if reindexed_after_creation:
                    final_people = self._desired_people(plan, allow_placeholders=True, name_as_id=True)
                    fresh, final_changed = self._replace_people_safe(
                        plan.item_id,
                        final_people,
                        materializing=True,
                        force=True,
                    )
                    item_updated = item_updated or final_changed
                desired = self._desired_people(plan, allow_placeholders=True, name_as_id=True)
                applied_hash = stable_hash(self._people_materialization_signature(desired))
                if self._people_materialization_signature(fresh.get("People") or []) != self._people_materialization_signature(desired):
                    raise RuntimeError(f"Emby item {plan.item_id} returned unexpected Cast & Crew relationships")
                if item_updated:
                    summary["updated"] += 1
                else:
                    summary["skipped"] += 1
                self._store_item_state(plan, applied_hash, refresh_etag=item_updated)
                if item_updated:
                    logger.info(f"({index}/{len(self.plans)}) Updated Cast & Crew | {fresh.get('Name') or plan.item_id}")
            except Exception as error:
                summary["failed"] += 1
                logger.error(f"({index}/{len(self.plans)}) Cast & Crew failed for Emby item {plan.item_id}: {error}")

        post_apply_identities = {id(identity): identity for identity in (self.identities.get(tmdb_id) for tmdb_id in self._post_apply_verify_ids) if identity and identity.emby_id}
        if post_apply_identities:
            logger.info(f"Emby Person post-apply lock verification | 0/{len(post_apply_identities)}")
        for verify_index, identity in enumerate(sorted(post_apply_identities.values(), key=lambda value: value.tmdb_id), 1):
            try:
                self._set_person_name_without_refresh(identity, identity.display_name)
            except Exception as error:
                summary["failed"] += 1
                logger.error(f"Emby person post-apply verification failed for {identity.provider} person {identity.provider_id}: {error}")
            if verify_index == len(post_apply_identities) or verify_index % 25 == 0:
                logger.info(f"Emby Person post-apply lock verification | {verify_index}/{len(post_apply_identities)}")

        for (identity_id, duplicate_person_id, canonical_person_id), count in sorted(self._duplicate_routing_counts.items()):
            if count <= 1:
                continue
            identity = self._identity_records.get(identity_id)
            provider = identity.provider if identity else ("Tmdb" if identity_id > 0 else "Tvdb")
            provider_id = identity.provider_id if identity else str(abs(identity_id))
            logger.info(f"Emby duplicate Person routing summary | {provider} {provider_id} | " f"Emby Person {duplicate_person_id} -> canonical Emby Person {canonical_person_id} | " f"{count} item relationships")

        item_elapsed = max(time.monotonic() - item_apply_started, 0.001)
        item_speed = len(self.plans) / item_elapsed
        logger.info(f"Emby Cast & Crew | staged={summary['staged']} updated={summary['updated']} skipped={summary['skipped']} " f"created={summary['created']} failed={summary['failed']} duration={item_elapsed:.1f}s speed={item_speed:.2f} Items/s")
        return summary

    def _fast_state_match(self, plan, state):
        credit_ids = {credit.tmdb_id for credit in plan.credits}
        if (
            not state
            or plan.emby_etag is None
            or int(state.get("sync_version") or 0) != SYNC_VERSION
            or state.get("emby_etag") != plan.emby_etag
            or state.get("credits_source") != plan.credits_source
            or state.get("source_credits_hash") != plan.credits_hash
            or credit_ids & (self._changed_identity_ids | set(self._identity_errors))
            or any(not self.identities.get(tmdb_id) for tmdb_id in credit_ids)
        ):
            return False, None
        desired = self._desired_people(plan, allow_placeholders=True, name_as_id=True)
        applied_hash = stable_hash(self._people_materialization_signature(desired))
        return state.get("applied_hash") == applied_hash, applied_hash

    def _cached_people_match_ignoring_etag(self, plan, state):
        """Accept an unrelated item ETag change without querying Person items."""
        credit_ids = {credit.tmdb_id for credit in plan.credits}
        state_version = int((state or {}).get("sync_version") or 0)
        if (
            not state
            or plan.emby_etag is None
            or state_version <= 0
            or state_version > SYNC_VERSION
            or state.get("credits_source") != plan.credits_source
            or state.get("source_credits_hash") != plan.credits_hash
            or credit_ids & set(self._identity_errors)
            or any(not self.identities.get(tmdb_id) for tmdb_id in credit_ids)
        ):
            return None
        desired = self._desired_people(plan, allow_placeholders=True, name_as_id=True)
        desired_signature = self._people_materialization_signature(desired)
        applied_hash = stable_hash(desired_signature)
        if state_version == SYNC_VERSION and state.get("applied_hash") != applied_hash:
            return None
        actual_signature = self._people_materialization_signature(plan.emby_item.get("People") or [])
        return applied_hash if actual_signature == desired_signature else None

    def _detect_false_friends(self, plans=None):
        plans = self.plans if plans is None else list(plans)
        # Emby does not reliably change an item's stable bulk ETag when only a
        # Person relationship changes. Refresh People once for plans that
        # contain indexed namesakes so false-friend discovery cannot depend on
        # a stale central item payload or on item processing order.
        namesake_plans = [
            plan
            for plan in plans
            if (
                any(
                    self.identities.get(credit.tmdb_id)
                    and self.identities[credit.tmdb_id].name_index is not None
                    for credit in plan.credits
                )
                and self._people_materialization_signature(
                    plan.emby_item.get("People") or []
                )
                != self._people_materialization_signature(
                    self._desired_people(
                        plan,
                        allow_placeholders=True,
                        name_as_id=True,
                    )
                )
            )
        ]
        if namesake_plans:
            fresh_namesake_items = {}
            direct_batch_size = 200
            started = time.monotonic()
            for start in range(0, len(namesake_plans), direct_batch_size):
                batch = namesake_plans[start : start + direct_batch_size]
                fresh_namesake_items.update(
                    self.emby.get_items_direct_bulk(
                        [plan.item_id for plan in batch],
                        fields=["People", "ProviderIds", "Type", "Etag"],
                    )
                    or {}
                )
                completed = min(start + len(batch), len(namesake_plans))
                elapsed = max(time.monotonic() - started, 0.001)
                logger.ghost(
                    f"Emby namesake relationship verification | "
                    f"{completed}/{len(namesake_plans)} | "
                    f"{completed / elapsed:.1f} Items/s"
                )
            refreshed = 0
            for plan in namesake_plans:
                fresh_item = (fresh_namesake_items or {}).get(plan.item_id)
                if fresh_item and "People" in fresh_item:
                    plan.emby_item["People"] = copy.deepcopy(fresh_item.get("People") or [])
                    refreshed += 1
            logger.info(
                f"Emby namesake relationship refresh | "
                f"{refreshed}/{len(namesake_plans)} Items"
            )
        linked_person_ids = set()
        for plan in plans:
            linked_person_ids.update(str(person.get("Id")) for person in (plan.emby_item.get("People") or []) if str(person.get("Id") or "").isdigit())

        # False-friend matching only needs People linked to the staged items.
        # Global maintenance is limited to known name-conflict groups and
        # identities whose canonical assignment changed during discovery.
        # Fetching every unique Person in a large server database adds no
        # identity evidence and makes each library run needlessly expensive.
        used_identity_ids = {self.identities[credit.tmdb_id].tmdb_id for plan in plans for credit in plan.credits if credit.tmdb_id in self.identities}
        maintenance_identity_ids = used_identity_ids
        maintenance_person_ids = {str(identity.emby_id) for identity in self._identity_records.values() if (identity.tmdb_id in maintenance_identity_ids or identity.canonical_id in maintenance_identity_ids) and str(identity.emby_id or "").isdigit()}
        maintenance_person_ids.update(person_id for identity in self._unique_identities() if identity.tmdb_id in used_identity_ids for person_id in identity.duplicate_emby_ids if str(person_id).isdigit())
        person_ids = sorted(linked_person_ids | maintenance_person_ids, key=int)
        if not person_ids:
            return
        people_by_id = {}
        started = time.monotonic()
        batch_size = 1000
        total_batches = (len(person_ids) + batch_size - 1) // batch_size
        for batch_index in range(total_batches):
            batch = person_ids[batch_index * batch_size : (batch_index + 1) * batch_size]
            people_by_id.update(self.emby.get_items_bulk(batch, fields=["ProviderIds", "Name", "SortName", "Type", "Etag"], force_refresh=False) or {})
            elapsed = max(time.monotonic() - started, 0.001)
            completed = min((batch_index + 1) * batch_size, len(person_ids))
            logger.ghost(f"Emby People identity verification | {completed}/{len(person_ids)} | {completed / elapsed:.2f} People/s")

        self._maintain_duplicate_person_quarantines(people_by_id)
        self._reconcile_verified_emby_identity_bridges(people_by_id)

        detached = False
        for identity in self._identity_records.values():
            person_id = str(identity.emby_id or "")
            person = people_by_id.get(person_id)
            if not person or person.get("Type") != "Person":
                continue
            expected_ids = {"tvdb": identity.provider_id} if identity.tmdb_id < 0 else {"tmdb": identity.provider_id}
            if identity.imdb_id:
                expected_ids["imdb"] = identity.imdb_id
            if identity.tvdb_id:
                expected_ids["tvdb"] = identity.tvdb_id
            if identity.wikidata_id:
                expected_ids["wikidata"] = identity.wikidata_id
            actual_ids = {provider: self._provider_value(person.get("ProviderIds") or {}, provider) for provider in expected_ids}
            conflicts = []
            for provider, expected in expected_ids.items():
                actual = actual_ids[provider]
                if actual and str(actual) != str(expected):
                    conflicts.append(f"{provider.upper()} expected {expected}, found {actual}")
            if not conflicts:
                continue
            primary_provider = "tvdb" if identity.tmdb_id < 0 else "tmdb"
            primary_actual = actual_ids.get(primary_provider)
            primary_expected = expected_ids[primary_provider]
            primary_conflict = bool(primary_actual and str(primary_actual) != str(primary_expected))
            any_authoritative_match = any(actual and str(actual) == str(expected_ids[provider]) for provider, actual in actual_ids.items())
            if not primary_conflict and (primary_actual or any_authoritative_match):
                identity.emby_etag = None
                identity.emby_signature = None
                self._changed_identity_ids.add(identity.tmdb_id)
                detached = True
                logger.info(f"Emby Person external-ID correction queued | {identity.provider} {identity.provider_id} | " f"Emby Person {person_id} | {', '.join(conflicts)}")
                continue
            identity.emby_id = None
            identity.emby_etag = None
            identity.emby_signature = None
            identity.duplicate_emby_ids.discard(person_id)
            self._changed_identity_ids.add(identity.tmdb_id)
            self._detached_identity_ids.add(identity.tmdb_id)
            detached = True
            logger.warning(f"Emby Person identity detached | {identity.provider} {identity.provider_id} | " f"Emby Person {person_id} | {', '.join(conflicts)}")
        if detached:
            self._persist_all_identities()

        references = []
        for plan in plans:
            references.extend(self._match_plan_person_references(plan, people_by_id))

        source_occurrences = {}
        for plan in plans:
            for identity_id in {credit.tmdb_id for credit in plan.credits}:
                source_occurrences[identity_id] = source_occurrences.get(identity_id, 0) + 1
        self._reconcile_verified_emby_identity_bridges(
            people_by_id,
            references,
            source_occurrences=source_occurrences,
        )
        self._reconcile_cross_source_identities(people_by_id, references)
        checked_pairs = set()
        detected = {}
        for person_id, identity_id in references:
            key = person_id, identity_id
            if key in checked_pairs:
                continue
            checked_pairs.add(key)
            identity = self.identities.get(identity_id)
            person = people_by_id.get(person_id)
            if not identity or not person or person.get("Type") != "Person":
                continue
            false_friend, reason = self._is_false_friend(identity, person)
            if false_friend and reason.startswith("unresolved cross-provider identity"):
                error = RuntimeError(f"Cannot verify {identity.provider} person {identity.provider_id} against " f"Emby Person {person_id}: {reason}")
                self._identity_errors[identity_id] = error
                logger.warning(str(error))
                continue
            if false_friend and identity_id not in detected:
                detected[identity_id] = person, reason

        identities_changed = False
        for identity_id, (person, reason) in detected.items():
            identity = self.identities[identity_id]
            person_id = str(person.get("Id") or "")
            duplicate_owner = self._duplicate_person_owners.get(person_id)
            canonical_owner = self._canonical_person_owners.get(person_id)
            neutral_quarantine = person_id.isdigit() and person.get("Name") == f"Emby Duplicate Person {person_id}" and not any(str(key).casefold() in {"tmdb", "tvdb", "imdb"} for key in (person.get("ProviderIds") or {}))
            if neutral_quarantine and canonical_owner is not None and canonical_owner is not identity:
                # This Person was quarantined incorrectly in an earlier run:
                # its numeric Emby ID is the central canonical assignment of
                # another external identity. Restore that identity once and
                # never reclaim the ID as a duplicate of the expected credit.
                if person_id in identity.duplicate_emby_ids:
                    identity.duplicate_emby_ids.discard(person_id)
                    identities_changed = True
                if self._duplicate_person_owners.get(person_id) is identity:
                    self._duplicate_person_owners.pop(person_id, None)
                canonical_owner.emby_etag = None
                canonical_owner.emby_signature = None
                self._changed_identity_ids.add(canonical_owner.tmdb_id)
                identities_changed = True
                logger.info(f"Emby canonical Person restoration queued | {canonical_owner.provider} " f"{canonical_owner.provider_id} | Emby Person {person_id} was linked while " f"{identity.provider} {identity.provider_id} was expected")
                continue
            if neutral_quarantine and duplicate_owner is None and str(identity.emby_id or "").isdigit() and str(identity.emby_id) != person_id:
                identity.duplicate_emby_ids.add(person_id)
                self._duplicate_person_owners[person_id] = identity
                duplicate_owner = identity
                identities_changed = True
            if duplicate_owner and neutral_quarantine:
                # A quarantined same-identity Person is a stale relationship,
                # not a new namesake. Let item routing replace it with the
                # canonical Person without consuming another Roman suffix.
                self._noncanonical_person_ids.add(person_id)
                if duplicate_owner is identity:
                    self._same_identity_duplicate_ids.add(identity.tmdb_id)
                    previous_index_state = (identity.name_index, identity.display_name)
                    self._reindex_identity_name(
                        identity.normalized_name,
                        clear_singleton=True,
                    )
                    if previous_index_state != (identity.name_index, identity.display_name):
                        identities_changed = True
                continue
            if person_id in identity.duplicate_emby_ids or person_id in self._noncanonical_person_ids:
                # Cross-source reconciliation may already have quarantined
                # this linked noncanonical record and removed its external
                # IDs. It is still a known duplicate of the expected identity,
                # not a new namesake that should consume another Roman index.
                _, quarantine_changed = self._quarantine_duplicate_person(identity, person_id)
                if quarantine_changed:
                    logger.info(f"Emby linked duplicate Person quarantined | {identity.provider} {identity.provider_id} | " f"Emby Person {person_id} | canonical Emby Person {identity.emby_id}")
                continue
            actual_identity = self._known_identity_for_person(person, exclude=identity)
            if actual_identity and actual_identity.normalized_name == identity.normalized_name:
                # The linked false friend is already known by its external IDs.
                # Correct that linked Person globally instead of inventing an
                # ever-higher suffix for the expected identity on every run.
                mapping_changed = False
                if str(identity.emby_id or "") == person_id:
                    identity.emby_id = None
                    identity.emby_etag = None
                    identity.emby_signature = None
                    identity.duplicate_emby_ids.discard(person_id)
                    self._detached_identity_ids.add(identity.tmdb_id)
                    mapping_changed = True
                duplicate_link = False
                if person_id.isdigit() and str(actual_identity.emby_id or "") != person_id:
                    if actual_identity.emby_id:
                        if person_id not in actual_identity.duplicate_emby_ids:
                            actual_identity.duplicate_emby_ids.add(person_id)
                            mapping_changed = True
                        self._same_identity_duplicate_ids.add(actual_identity.tmdb_id)
                        self._noncanonical_person_ids.add(person_id)
                        duplicate_link = True
                    else:
                        actual_identity.emby_id = person_id
                        actual_identity.emby_etag = None
                        actual_identity.emby_signature = None
                        mapping_changed = True
                if str(actual_identity.emby_id or "") == person_id:
                    self._resolved_person_items[actual_identity.tmdb_id] = person
                name_group = [candidate for candidate in self._unique_identities() if candidate.normalized_name == identity.normalized_name]
                index_state_before = {candidate.tmdb_id: (candidate.name_index, candidate.display_name) for candidate in name_group}
                self._reindex_identity_group(name_group)
                if duplicate_link:
                    _, quarantine_changed = self._quarantine_duplicate_person(
                        actual_identity,
                        person_id,
                    )
                    mapping_changed = mapping_changed or quarantine_changed
                reindexed = any(index_state_before[candidate.tmdb_id] != (candidate.name_index, candidate.display_name) for candidate in name_group)
                if mapping_changed:
                    self._changed_identity_ids.update((identity.tmdb_id, actual_identity.tmdb_id))
                if mapping_changed or reindexed:
                    identities_changed = True
                    logger.info(
                        f"Emby known false friend linked | {identity.provider} {identity.provider_id} expected | " f"{actual_identity.provider} {actual_identity.provider_id} found | " f"{actual_identity.base_name} -> {actual_identity.display_name}"
                    )
                continue
            old_display_name = identity.display_name
            identity_changed = self._assign_false_friend_index(identity)
            if identity_changed:
                logger.info(f"Emby false friend detected | {identity.provider} {identity.provider_id} | " f"{identity.base_name} -> {person.get('Name') or person.get('Id')} | {reason} | using {identity.display_name}")
            if identity_changed or identity.display_name != old_display_name:
                identities_changed = True
        if identities_changed:
            self._persist_all_identities()

    def _match_plan_person_references(self, plan, people_by_id):
        """Match Emby relationships to source credits without relying on list order."""
        current_people = [person for person in (plan.emby_item.get("People") or []) if str(person.get("Id") or "").isdigit()]
        unmatched_credits = set(range(len(plan.credits)))
        unmatched_people = set(range(len(current_people)))
        references = []

        def relationship_matches(credit_index, person_index):
            credit = plan.credits[credit_index]
            person = current_people[person_index]
            return (person.get("Type") or "") == credit.person_type and (person.get("Role") or "").strip() == (credit.role or "")

        def commit_mutual_unique(candidate):
            credit_candidates = {credit_index: [person_index for person_index in unmatched_people if relationship_matches(credit_index, person_index) and candidate(credit_index, person_index)] for credit_index in unmatched_credits}
            person_candidate_counts = {}
            for candidates in credit_candidates.values():
                for person_index in candidates:
                    person_candidate_counts[person_index] = person_candidate_counts.get(person_index, 0) + 1
            matches = [(credit_index, candidates[0]) for credit_index, candidates in credit_candidates.items() if len(candidates) == 1 and person_candidate_counts.get(candidates[0]) == 1]
            for credit_index, person_index in matches:
                person_id = str(current_people[person_index].get("Id"))
                references.append((person_id, plan.credits[credit_index].tmdb_id))
                unmatched_credits.discard(credit_index)
                unmatched_people.discard(person_index)

        # Reuse an already verified numeric Emby mapping first.
        commit_mutual_unique(lambda credit_index, person_index: (self.identities[plan.credits[credit_index].tmdb_id].emby_id is not None and str(self.identities[plan.credits[credit_index].tmdb_id].emby_id) == str(current_people[person_index].get("Id"))))

        # Otherwise let exact external IDs prove the identity.
        commit_mutual_unique(
            lambda credit_index, person_index: self._person_has_matching_external_id(
                self.identities[plan.credits[credit_index].tmdb_id],
                people_by_id.get(str(current_people[person_index].get("Id"))) or {},
            )
        )

        # Indexed and stale embedded names are safe after removing Kometa's
        # managed Roman suffix, provided the relationship is mutually unique.
        commit_mutual_unique(
            lambda credit_index, person_index: self._comparable_unindexed_person_name(current_people[person_index].get("Name") or (people_by_id.get(str(current_people[person_index].get("Id"))) or {}).get("Name"))
            == self._comparable_unindexed_person_name(self.identities[plan.credits[credit_index].tmdb_id].base_name)
        )

        # A unique Type/Role relationship can reveal a real false friend even
        # when both its name and external IDs are wrong. Ambiguous groups are
        # intentionally left unmatched instead of guessing by position.
        commit_mutual_unique(lambda credit_index, person_index: True)
        return references

    @staticmethod
    def _person_has_matching_external_id(identity, person):
        actual_ids = {str(key).casefold(): str(value) for key, value in (person.get("ProviderIds") or {}).items() if value not in (None, "")}
        expected_ids = {}
        if identity.tmdb_id > 0:
            expected_ids["tmdb"] = str(identity.tmdb_id)
        else:
            expected_ids["tvdb"] = str(-identity.tmdb_id)
        if identity.imdb_id:
            expected_ids["imdb"] = str(identity.imdb_id)
        if identity.tvdb_id:
            expected_ids["tvdb"] = str(identity.tvdb_id)
        if identity.wikidata_id:
            expected_ids["wikidata"] = str(identity.wikidata_id)
        return any(actual_ids.get(provider) == expected for provider, expected in expected_ids.items())

    def _is_false_friend(self, identity, person):
        expected_ids = {}
        if identity.tmdb_id > 0:
            expected_ids["tmdb"] = str(identity.tmdb_id)
        if identity.tvdb_id:
            expected_ids["tvdb"] = str(identity.tvdb_id)
        elif identity.tmdb_id < 0:
            expected_ids["tvdb"] = str(-identity.tmdb_id)
        if identity.imdb_id:
            expected_ids["imdb"] = str(identity.imdb_id)
        if identity.wikidata_id:
            expected_ids["wikidata"] = str(identity.wikidata_id)

        actual_ids = {str(key).casefold(): str(value) for key, value in (person.get("ProviderIds") or {}).items() if value not in (None, "")}
        conflicts = [provider for provider, expected in expected_ids.items() if provider in actual_ids and actual_ids[provider] != expected]
        matches = [provider for provider, expected in expected_ids.items() if actual_ids.get(provider) == expected]
        actual_name = person.get("Name") or ""
        names_match = self._comparable_unindexed_person_name(actual_name) == self._comparable_unindexed_person_name(identity.base_name)
        primary_provider = "tmdb" if identity.tmdb_id > 0 else "tvdb"
        if primary_provider in conflicts:
            details = ", ".join(f"{provider.upper()} expected {expected_ids[provider]}, got {actual_ids[provider]}" for provider in conflicts)
            return True, details
        if matches:
            correction = f"; correcting conflicting {', '.join(provider.upper() for provider in conflicts)}" if conflicts else ""
            return False, f"matching external ID ({', '.join(provider.upper() for provider in matches)}){correction}"
        if conflicts:
            details = ", ".join(f"{provider.upper()} expected {expected_ids[provider]}, got {actual_ids[provider]}" for provider in conflicts)
            return True, details
        identity_providers = {"tmdb", "tvdb", "imdb", "wikidata"}
        expected_identity_providers = identity_providers & set(expected_ids)
        actual_identity_providers = identity_providers & set(actual_ids)
        if expected_identity_providers and actual_identity_providers:
            return True, ("unresolved cross-provider identity " f"(expected {', '.join(sorted(expected_identity_providers)).upper()}, " f"found {', '.join(sorted(actual_identity_providers)).upper()}; no externally verified bridge)")
        if not names_match:
            return True, f"name mismatch ({identity.base_name!r} != {actual_name!r}) and no matching external ID"
        return False, "matching name; external ID unavailable"

    def _assign_false_friend_index(self, identity):
        self._unique_identities()
        group = self._identities_by_name.get(identity.normalized_name, [])
        if len(group) > 1:
            before = {candidate.tmdb_id: (candidate.name_index, candidate.display_name) for candidate in group}
            self._reindex_identity_group(group)
            return any(before[candidate.tmdb_id] != (candidate.name_index, candidate.display_name) for candidate in group)
        # An existing conflict index is authoritative People-DB state. Seeing
        # the same unresolved Emby relationship on a later run must never
        # consume the next Roman number.
        name_index = max(identity.name_index or 1, 1)
        display_name = f"{identity.base_name} ({roman_number(name_index)})"
        changed = identity.name_index != name_index or identity.display_name != display_name or identity.emby_id is not None or identity.emby_etag is not None or identity.emby_signature is not None
        if not changed:
            return False
        identity.name_index = name_index
        identity.display_name = display_name
        identity.emby_id = None
        identity.emby_etag = None
        identity.emby_signature = None
        identity.verified_at = None
        self._duplicate_identity_ids.add(identity.tmdb_id)
        self._changed_identity_ids.add(identity.tmdb_id)
        return True

    def _reconcile_verified_emby_identity_bridges(
        self,
        people_by_id,
        references=None,
        source_occurrences=None,
    ):
        """Promote a TVDb-only identity from an IMDb-verified TMDb candidate.

        A Kometa-managed Person carrying the exact TVDb ID is sufficient.
        Without that primary ID, require the same linked Person to match at
        least two staged source relationships so one ambiguous role cannot
        merge unrelated namesakes.
        """
        changed_names = set()
        verified_at = datetime.now().isoformat(timespec="seconds")
        referenced_people = {}
        for person_id, source_id in references or []:
            key = int(source_id), str(person_id)
            referenced_people[key] = referenced_people.get(key, 0) + 1
        occurrence_counts = dict(source_occurrences or {})
        if not occurrence_counts:
            for (source_id, _), count in referenced_people.items():
                occurrence_counts[source_id] = occurrence_counts.get(source_id, 0) + count
        for alias in list(self._identity_records.values()):
            if alias.tmdb_id >= 0 or alias.canonical_id is not None:
                continue
            candidate_person_ids = {person_id for (source_id, person_id), count in referenced_people.items() if (source_id == alias.tmdb_id and count >= 1 and occurrence_counts.get(alias.tmdb_id, 0) >= 2)}
            managed_person_id = str(alias.emby_id or "")
            if managed_person_id.isdigit():
                candidate_person_ids.add(managed_person_id)

            verified_candidates = []
            for person_id in sorted(candidate_person_ids, key=int):
                person = people_by_id.get(person_id) or {}
                if person.get("Type") != "Person":
                    continue
                provider_ids = {str(key).casefold(): str(value) for key, value in (person.get("ProviderIds") or {}).items() if value not in (None, "")}
                tmdb_id = provider_ids.get("tmdb")
                person_imdb_id = provider_ids.get("imdb")
                person_tvdb_id = provider_ids.get("tvdb")
                reference_count = referenced_people.get((alias.tmdb_id, person_id), 0)
                source_occurrence_count = occurrence_counts.get(alias.tmdb_id, 0)
                exact_tvdb_id = person_tvdb_id == alias.provider_id
                if (
                    not str(tmdb_id or "").isdigit()
                    or not person_imdb_id
                    or (person_tvdb_id and not exact_tvdb_id)
                    or (not exact_tvdb_id and source_occurrence_count < 2)
                    or self._comparable_unindexed_person_name(person.get("Name") or "") != self._comparable_unindexed_person_name(alias.base_name)
                ):
                    continue

                tmdb_id = int(tmdb_id)
                evidence = self._get_tmdb_identity_evidence(tmdb_id)
                authoritative_imdb_id = evidence.get("imdb_id")
                authoritative_name = str(evidence.get("name") or "").strip()
                if not evidence.get("available") or not authoritative_imdb_id or authoritative_imdb_id != person_imdb_id:
                    logger.warning(f"External People identity bridge rejected | TVDb {alias.provider_id} | " f"Emby Person {person_id} proposes TMDb {tmdb_id}, IMDb {person_imdb_id}; " f"TMDb verified IMDb {authoritative_imdb_id or 'unavailable'}")
                    continue
                verified_candidates.append(
                    (
                        tmdb_id,
                        authoritative_imdb_id,
                        authoritative_name,
                        person_id,
                        person,
                        source_occurrence_count,
                        exact_tvdb_id,
                    )
                )

            candidate_tmdb_ids = {candidate[0] for candidate in verified_candidates}
            if len(candidate_tmdb_ids) != 1:
                if len(candidate_tmdb_ids) > 1:
                    logger.warning(f"External People identity bridge unresolved | TVDb {alias.provider_id} | " f"multiple verified TMDb candidates {', '.join(str(value) for value in sorted(candidate_tmdb_ids))}")
                continue
            verified_candidates.sort(key=lambda candidate: (not candidate[6], -candidate[5], int(candidate[3])))
            tmdb_id, authoritative_imdb_id, authoritative_name, person_id, person, reference_count, exact_tvdb_id = verified_candidates[0]

            canonical = self._identity_records.get(tmdb_id)
            canonical_name = authoritative_name or alias.base_name
            if canonical and canonical.normalized_name != normalize_person_name(canonical_name):
                logger.warning(f"External People identity bridge rejected | TVDb {alias.provider_id} -> TMDb {tmdb_id} | " f"name mismatch {alias.base_name!r} != {canonical.base_name!r}")
                continue
            if canonical and canonical.tvdb_id and canonical.tvdb_id != alias.provider_id:
                logger.warning(f"External People identity bridge rejected | TVDb {alias.provider_id} -> TMDb {tmdb_id} | " f"TMDb identity already maps to TVDb {canonical.tvdb_id}")
                continue
            if canonical is None:
                canonical = PersonIdentity(
                    tmdb_id=tmdb_id,
                    base_name=canonical_name,
                    normalized_name=normalize_person_name(canonical_name),
                    display_name=canonical_name,
                )
                self._identity_records[tmdb_id] = canonical

            canonical.imdb_id = authoritative_imdb_id
            canonical.tvdb_id = alias.provider_id
            canonical.external_verified_at = verified_at
            if not canonical.emby_id:
                canonical.emby_id = person_id
                canonical.emby_etag = person.get("Etag")
                canonical.emby_signature = self._person_manifest_signature(person)
            alias.imdb_id = authoritative_imdb_id
            alias.tvdb_id = alias.provider_id
            alias.canonical_id = canonical.tmdb_id
            alias.external_verified_at = verified_at
            alias.name_index = None
            alias.display_name = alias.base_name
            self.identities[alias.tmdb_id] = canonical
            self.identities[canonical.tmdb_id] = canonical
            self._changed_identity_ids.update((canonical.tmdb_id, alias.tmdb_id))
            changed_names.add(canonical.normalized_name)
            proof = f"exact TVDb ID" if exact_tvdb_id else f"{reference_count} matching item relationships"
            logger.info(f"External People identity verified | TVDb {alias.provider_id} = TMDb {canonical.tmdb_id} = " f"IMDb {authoritative_imdb_id} | Emby Person {person_id} | {proof}")

        if changed_names:
            self._enforce_unique_external_ids(self._identity_records)
            self._invalidate_identity_indexes()
            for normalized_name in changed_names:
                self._reindex_identity_name(normalized_name, clear_singleton=True)
            self._persist_all_identities()

    def _reconcile_cross_source_identities(self, people_by_id, references):
        source_live_ids = {source_id: set() for source_id in self._identity_records}
        reference_counts = {}
        for person_id, source_id in references:
            if person_id in people_by_id and source_id in source_live_ids:
                source_live_ids[source_id].add(person_id)
                reference_counts[(person_id, source_id)] = reference_counts.get((person_id, source_id), 0) + 1
        for source_id, identity in self._identity_records.items():
            person_id = str(identity.emby_id or "")
            if person_id in people_by_id:
                source_live_ids[source_id].add(person_id)

        candidates = {}
        for person_id, person in people_by_id.items():
            provider_ids = {str(key).casefold(): str(value) for key, value in (person.get("ProviderIds") or {}).items() if value not in (None, "")}
            tmdb_id = provider_ids.get("tmdb")
            tvdb_id = provider_ids.get("tvdb")
            if not (str(tmdb_id or "").isdigit() and str(tvdb_id or "").isdigit()):
                continue
            pair = int(tmdb_id), -int(tvdb_id)
            tmdb_identity = self._identity_records.get(pair[0])
            tvdb_identity = self._identity_records.get(pair[1])
            if not tmdb_identity or not tvdb_identity:
                continue
            person_imdb_id = provider_ids.get("imdb")
            if tmdb_identity.normalized_name != tvdb_identity.normalized_name:
                # Some providers expose reversed or localized Person names.
                # Permit that mismatch only after TMDb independently confirms
                # the same IMDb ID carried by the exact TVDb Person.
                evidence = self._get_tmdb_identity_evidence(tmdb_identity.tmdb_id)
                verified_imdb_id = evidence.get("imdb_id")
                if (
                    not evidence.get("available")
                    or not person_imdb_id
                    or tvdb_identity.imdb_id != person_imdb_id
                    or verified_imdb_id != person_imdb_id
                ):
                    continue
                tmdb_identity.imdb_id = person_imdb_id
                tmdb_identity.external_verified_at = datetime.now().isoformat(
                    timespec="seconds"
                )
                self._changed_identity_ids.add(tmdb_identity.tmdb_id)
            managed_bridge = (
                tvdb_identity.canonical_id is None
                and person_id
                in {
                    str(tmdb_identity.emby_id or ""),
                    str(tvdb_identity.emby_id or ""),
                }
                and (not tmdb_identity.imdb_id or not person_imdb_id or tmdb_identity.imdb_id == person_imdb_id)
                and (not tvdb_identity.imdb_id or not person_imdb_id or tvdb_identity.imdb_id == person_imdb_id)
            )
            # A previously Kometa-managed canonical Person carrying both
            # exact primary IDs can bridge providers whose public crosswalk is
            # empty. Conflicting IMDb evidence still rejects the merge.
            if tvdb_identity.canonical_id != pair[0] and not managed_bridge:
                continue
            actual_name = self._comparable_unindexed_person_name(person.get("Name") or "")
            known_names = {
                self._comparable_unindexed_person_name(tmdb_identity.base_name),
                self._comparable_unindexed_person_name(tmdb_identity.display_name),
                self._comparable_unindexed_person_name(tvdb_identity.display_name),
            }
            if actual_name not in known_names:
                continue
            candidates.setdefault(pair, set()).add(person_id)
            if managed_bridge:
                logger.info(f"Emby managed external-ID bridge accepted | TMDb {tmdb_identity.provider_id} = " f"TVDb {tvdb_identity.provider_id} | Emby Person {person_id}")

        for source_id, record in self._identity_records.items():
            if record.canonical_id is not None:
                candidates.setdefault((record.canonical_id, source_id), set())

        candidates, database_changed = self._resolve_competing_alias_claims(candidates)
        consolidated = 0
        reused = 0
        reindex_names = set()
        reconcile_started = time.monotonic()
        sorted_candidates = sorted(candidates.items())
        for candidate_index, ((canonical_id, alias_id), confirming_ids) in enumerate(sorted_candidates, 1):
            canonical = self._identity_records.get(canonical_id)
            alias = self._identity_records.get(alias_id)
            if not canonical or not alias:
                continue
            live_ids = source_live_ids.get(canonical_id, set()) | source_live_ids.get(alias_id, set()) | set(confirming_ids)
            canonical_live_ids = {person_id for person_id in live_ids if person_id in people_by_id and not self._is_false_friend(canonical, people_by_id[person_id])[0]}
            alias_live_ids = {person_id for person_id in live_ids if person_id in people_by_id and not self._is_false_friend(alias, people_by_id[person_id])[0]}
            bridge_ids = canonical_live_ids & alias_live_ids
            if not bridge_ids and not (canonical_live_ids and alias_live_ids):
                continue
            live_ids = canonical_live_ids | alias_live_ids
            confirming_ids = set(confirming_ids) & bridge_ids
            alias_active = alias.canonical_id == canonical_id
            externally_confirmed = alias_active and canonical.tvdb_id == alias.provider_id
            if not live_ids or (not confirming_ids and not externally_confirmed):
                continue

            canonical_before = (
                canonical.imdb_id,
                canonical.tvdb_id,
                canonical.emby_id,
                canonical.name_index,
                canonical.display_name,
                tuple(sorted(canonical.duplicate_emby_ids)),
            )
            alias_before = (
                alias.imdb_id,
                alias.tvdb_id,
                alias.emby_id,
                alias.canonical_id,
                alias.name_index,
                alias.display_name,
            )
            confirming_person = people_by_id[next(iter(confirming_ids))] if confirming_ids else {}
            provider_ids = {str(key).casefold(): str(value) for key, value in (confirming_person.get("ProviderIds") or {}).items() if value not in (None, "")}
            canonical.tvdb_id = provider_ids.get("tvdb") or canonical.tvdb_id
            canonical.imdb_id = provider_ids.get("imdb") or canonical.imdb_id
            previous_emby_id = canonical.emby_id
            canonical.emby_id = self._select_canonical_emby_person(
                live_ids,
                people_by_id,
                reference_counts,
                canonical,
                alias,
            )
            if str(canonical.emby_id or "") != str(previous_emby_id or ""):
                canonical.emby_etag = None
                canonical.emby_signature = None
            alias.tvdb_id = provider_ids.get("tvdb") or alias.tvdb_id
            alias.imdb_id = provider_ids.get("imdb") or alias.imdb_id
            if str(alias.emby_id or "") != str(canonical.emby_id or ""):
                alias.emby_id = canonical.emby_id
                alias.emby_etag = None
                alias.emby_signature = None
            alias.canonical_id = canonical_id
            alias.name_index = None
            alias.display_name = alias.base_name
            if self.identities.get(alias_id) is not canonical:
                self.identities[alias_id] = canonical
            # Mapping many aliases one by one must not rebuild the complete
            # 200k+ identity index for every candidate. Reindex each affected
            # name once after all mappings have been applied.
            reindex_names.add(canonical.normalized_name)
            noncanonical_ids = {str(person_id) for person_id in live_ids if str(person_id) != str(canonical.emby_id)}
            if noncanonical_ids:
                self._same_identity_duplicate_ids.add(canonical.tmdb_id)
                self._noncanonical_person_ids.update(noncanonical_ids)
                previous_duplicate_ids = set(canonical.duplicate_emby_ids)
                canonical.duplicate_emby_ids.update(noncanonical_ids)
                canonical.duplicate_emby_ids.discard(str(canonical.emby_id or ""))
                if canonical.duplicate_emby_ids != previous_duplicate_ids:
                    database_changed = True
                for duplicate_emby_id in sorted(noncanonical_ids, key=int):
                    duplicate_person = people_by_id.get(duplicate_emby_id) or {}
                    duplicate_name = self._duplicate_quarantine_name(canonical, duplicate_emby_id)
                    duplicate_provider_ids = {key: value for key, value in (duplicate_person.get("ProviderIds") or {}).items() if str(key).casefold() not in {"tmdb", "tvdb", "imdb"}}
                    duplicate_needs_update = (
                        duplicate_person.get("Name") != duplicate_name
                        or duplicate_person.get("SortName") != duplicate_name
                        or dict(duplicate_person.get("ProviderIds") or {}) != duplicate_provider_ids
                        or not {"Name", "SortName"}.issubset(set(duplicate_person.get("LockedFields") or []))
                    )
                    if not duplicate_needs_update:
                        continue
                    try:
                        _, duplicate_changed = self._quarantine_duplicate_person(
                            canonical,
                            duplicate_emby_id,
                        )
                        if duplicate_changed:
                            logger.info(f"Emby duplicate Person quarantined | {canonical.provider} {canonical.provider_id} | " f"Emby Person {duplicate_emby_id} -> {duplicate_name} | external IDs removed | " f"canonical Emby Person {canonical.emby_id}")
                    except Exception as error:
                        self._identity_errors[canonical.tmdb_id] = error
                        logger.error(f"Emby duplicate Person quarantine failed for {canonical.provider} person " f"{canonical.provider_id}, Emby Person {duplicate_emby_id}: {error}")
            canonical_after = (
                canonical.imdb_id,
                canonical.tvdb_id,
                canonical.emby_id,
                canonical.name_index,
                canonical.display_name,
                tuple(sorted(canonical.duplicate_emby_ids)),
            )
            alias_after = (
                alias.imdb_id,
                alias.tvdb_id,
                alias.emby_id,
                alias.canonical_id,
                alias.name_index,
                alias.display_name,
            )
            identity_changed = canonical_before != canonical_after or alias_before != alias_after
            selected_person = people_by_id.get(str(canonical.emby_id)) or {}
            selected_provider_ids = {str(key).casefold(): str(value) for key, value in (selected_person.get("ProviderIds") or {}).items() if value not in (None, "")}
            person_needs_update = (
                selected_person.get("Name") != canonical.display_name
                or not self._sort_name_matches(canonical.display_name, selected_person.get("SortName"))
                or selected_provider_ids.get("tmdb") != canonical.provider_id
                or selected_provider_ids.get("tvdb") != alias.provider_id
                or (canonical.imdb_id and selected_provider_ids.get("imdb") != canonical.imdb_id)
            )
            if identity_changed or person_needs_update:
                self._changed_identity_ids.update((canonical_id, alias_id))
            if identity_changed:
                database_changed = True
            if not alias_active:
                if len(live_ids) > 1:
                    consolidated += 1
                    logger.info(f"Emby Person duplicate consolidated | TMDb {canonical.provider_id} = TVDb {alias.provider_id} | " f"{canonical.base_name} | {len(live_ids)} Emby People | canonical Emby Person {canonical.emby_id}")
                else:
                    reused += 1
            elapsed = max(time.monotonic() - reconcile_started, 0.001)
            logger.ghost(f"Emby cross-source People reconciliation | {candidate_index}/{len(sorted_candidates)} | " f"{candidate_index / elapsed:.1f} Identities/s")

        changed_before_reindex = set(self._changed_identity_ids)
        self._invalidate_identity_indexes()
        for normalized_name in sorted(reindex_names):
            self._reindex_identity_name(normalized_name, clear_singleton=True)
        if self._changed_identity_ids != changed_before_reindex:
            database_changed = True
        if database_changed:
            self._persist_all_identities()
        self._refresh_duplicate_identity_ids()
        self._mark_person_lock_mismatches(people_by_id)
        if sorted_candidates:
            logger.info(f"Emby Cross-Source People Reconciliation Complete | {len(sorted_candidates)} checked | " f"{consolidated} duplicate sets consolidated | {reused} existing People reused")

    def _resolve_competing_alias_claims(self, candidates):
        """Resolve competing Emby claims only through the TVDb crosswalk."""
        claims = {}
        for canonical_id, alias_id in candidates:
            if canonical_id > 0 and alias_id < 0:
                claims.setdefault(alias_id, set()).add(canonical_id)

        conflict_alias_ids = [
            alias_id
            for alias_id, canonical_ids in claims.items()
            if (len(canonical_ids) > 1 and (alias_id not in self._identity_records or self._identity_records[alias_id].canonical_id not in canonical_ids or self._external_identity_audit_due(self._identity_records[alias_id])))
        ]
        audited_alias_ids = set(conflict_alias_ids)
        external_by_tvdb = {}
        if conflict_alias_ids and self.tvdb and hasattr(self.tvdb, "get_people_external_ids_bulk"):
            try:
                external_by_tvdb = self.tvdb.get_people_external_ids_bulk([-alias_id for alias_id in conflict_alias_ids])
            except Exception as error:
                logger.warning(f"TVDb People conflict verification unavailable: {error}")

        changed = False
        for alias_id, canonical_ids in claims.items():
            if len(canonical_ids) < 2:
                continue
            alias = self._identity_records.get(alias_id)
            if not alias:
                continue
            external = external_by_tvdb.get(-alias_id) or external_by_tvdb.get(str(-alias_id)) or {}
            remote_tmdb_id = external.get("tmdb_id")
            winner_id = int(remote_tmdb_id) if str(remote_tmdb_id or "").isdigit() and int(remote_tmdb_id) in canonical_ids else None
            if winner_id is None and alias.canonical_id in canonical_ids:
                winner_id = alias.canonical_id
            for canonical_id in canonical_ids:
                if canonical_id != winner_id:
                    candidates.pop((canonical_id, alias_id), None)
            if winner_id is None:
                logger.warning(f"External People identity conflict | TVDb {alias.provider_id} is claimed by TMDb " f"{', '.join(str(value) for value in sorted(canonical_ids))}; no authoritative crosswalk, keeping unresolved")
                continue
            winner = self._identity_records[winner_id]
            mapping_changed = False
            if alias.canonical_id != winner_id:
                alias.canonical_id = winner_id
                alias.emby_id = winner.emby_id or alias.emby_id
                alias.emby_etag = None
                alias.emby_signature = None
                self.identities[alias_id] = winner
                self._invalidate_identity_indexes()
                self._changed_identity_ids.update((winner_id, alias_id))
                changed = True
                mapping_changed = True
            if alias_id in audited_alias_ids or mapping_changed:
                logger.warning(f"External People identity conflict | TVDb {alias.provider_id} is claimed by TMDb " f"{', '.join(str(value) for value in sorted(canonical_ids))}; authoritative crosswalk keeps TMDb {winner_id}")
        return candidates, changed

    @staticmethod
    def _select_canonical_emby_person(live_ids, people_by_id, reference_counts, canonical, alias):
        relevant_sources = {canonical.tmdb_id, alias.tmdb_id}

        def score(person_id):
            person = people_by_id.get(person_id) or {}
            provider_ids = {str(key).casefold(): str(value) for key, value in (person.get("ProviderIds") or {}).items() if value not in (None, "")}
            matching_ids = int(provider_ids.get("tmdb") == canonical.provider_id) + int(provider_ids.get("tvdb") == alias.provider_id)
            if canonical.imdb_id or alias.imdb_id:
                matching_ids += int(provider_ids.get("imdb") in {canonical.imdb_id, alias.imdb_id})
            base_name_match = int(EmbyPeopleSync._comparable_person_name(person.get("Name") or "") == EmbyPeopleSync._comparable_person_name(canonical.base_name))
            stored_canonical_match = int(str(canonical.emby_id or "") == str(person_id))
            numeric_preference = -int(person_id) if str(person_id).isdigit() else 0
            current_references = sum(reference_counts.get((person_id, source_id), 0) for source_id in relevant_sources)
            # When two records carry the same proven external identity, keep
            # the one actively referenced by staged items. A stale cached
            # canonical assignment must not outrank the usable linked Person.
            return matching_ids, base_name_match, current_references, stored_canonical_match, numeric_preference

        return str(max(live_ids, key=score))

    def _unique_identities(self):
        if self._unique_identity_cache is None:
            unique = {}
            for identity in self.identities.values():
                unique[id(identity)] = identity
            self._unique_identity_cache = list(unique.values())
            self._identities_by_name = {}
            self._identities_by_tvdb = {}
            self._identities_by_imdb = {}
            self._identities_by_wikidata = {}
            for identity in self._unique_identity_cache:
                self._identities_by_name.setdefault(identity.normalized_name, []).append(identity)
                if identity.tvdb_id:
                    self._identities_by_tvdb.setdefault(str(identity.tvdb_id), []).append(identity)
                if identity.imdb_id:
                    self._identities_by_imdb.setdefault(str(identity.imdb_id), []).append(identity)
                if identity.wikidata_id:
                    self._identities_by_wikidata.setdefault(str(identity.wikidata_id), []).append(identity)
        return self._unique_identity_cache

    def _invalidate_identity_indexes(self):
        self._unique_identity_cache = None
        self._identities_by_name = {}
        self._identities_by_tvdb = {}
        self._identities_by_imdb = {}
        self._identities_by_wikidata = {}

    def _refresh_duplicate_identity_ids(self, groups=None, unique_identities=None):
        groups_supplied = groups is not None
        groups = groups if groups_supplied else {}
        self._duplicate_person_owners = {}
        self._canonical_person_owners = {}
        unique_identities = unique_identities if unique_identities is not None else self._unique_identities()
        canonical_claims = {}
        for identity in unique_identities:
            if str(identity.emby_id or "").isdigit():
                canonical_claims.setdefault(str(identity.emby_id), []).append(identity)
        self._canonical_person_owners = {person_id: owners[0] for person_id, owners in canonical_claims.items() if len({id(owner) for owner in owners}) == 1}

        for identity in unique_identities:
            if not groups_supplied:
                groups.setdefault(identity.normalized_name, []).append(identity)
            for duplicate_emby_id in set(identity.duplicate_emby_ids):
                duplicate_emby_id = str(duplicate_emby_id)
                canonical_owner = self._canonical_person_owners.get(duplicate_emby_id)
                if canonical_owner is not None and canonical_owner is not identity:
                    identity.duplicate_emby_ids.discard(duplicate_emby_id)
                    self._changed_identity_ids.add(identity.tmdb_id)
                    logger.info(f"Emby misclassified duplicate link removed | {identity.provider} {identity.provider_id} | " f"Emby Person {duplicate_emby_id} belongs to " f"{canonical_owner.provider} {canonical_owner.provider_id}")
                    continue
                self._duplicate_person_owners[duplicate_emby_id] = identity
        self._duplicate_identity_ids = {identity.tmdb_id for identities in groups.values() for identity in identities if len(identities) > 1 or identity.name_index is not None}

    def _requires_name_lock(self, identity):
        return identity.tmdb_id in self._duplicate_identity_ids or identity.tmdb_id in self._same_identity_duplicate_ids

    def _mark_person_lock_mismatches(self, people_by_id):
        candidates = [identity for identity in self._unique_identities() if identity.emby_id and (self._requires_name_lock(identity) or identity.tmdb_id in self._changed_identity_ids)]
        pending_direct = []
        authoritative = []
        for identity in candidates:
            person = people_by_id.get(str(identity.emby_id))
            if not person or person.get("Type") != "Person":
                continue
            requires_name_lock = self._requires_name_lock(identity)
            # A unique, unchanged identity has no lock work to perform. If a
            # former duplicate becomes unique, reindexing has already marked it
            # changed and it is verified below with a direct Person response.
            if not requires_name_lock and identity.tmdb_id not in self._changed_identity_ids:
                continue

            current_etag = person.get("Etag")
            current_signature = self._person_manifest_signature(person)
            # Emby's /Items bulk endpoint omits LockedFields even when that
            # field is explicitly requested. Person ETags may also change when
            # only derived relationship counters change. A stable signature of
            # the identity fields therefore proves that the last complete
            # direct lock verification is still current.
            if identity.emby_signature and identity.emby_signature == current_signature and identity.tmdb_id not in self._changed_identity_ids:
                if identity.emby_etag != current_etag:
                    identity.emby_etag = current_etag
                    authoritative.append((identity, None, current_etag, current_signature))
                continue

            if "LockedFields" not in person:
                pending_direct.append((identity, current_etag, current_signature))
            else:
                authoritative.append((identity, person, current_etag, current_signature))

        direct_people = {}
        started = time.monotonic()
        batch_size = 200
        for start in range(0, len(pending_direct), batch_size):
            batch = pending_direct[start : start + batch_size]
            batch_ids = [identity.emby_id for identity, _, _ in batch]
            if hasattr(self.emby, "get_items_direct_bulk"):
                fetched = self.emby.get_items_direct_bulk(
                    batch_ids,
                    fields=["ProviderIds", "Name", "SortName", "LockedFields", "Type", "Etag"],
                )
            else:
                fetched = {str(identity.emby_id): self.emby.get_item(identity.emby_id, force_refresh=True) for identity, _, _ in batch}
            direct_people.update(fetched or {})
            completed = min(start + len(batch), len(pending_direct))
            elapsed = max(time.monotonic() - started, 0.001)
            logger.ghost(f"Emby Person direct lock verification | {completed}/{len(pending_direct)} | " f"{completed / elapsed:.1f} People/s")

        authoritative.extend((identity, direct_people.get(str(identity.emby_id)), manifest_etag, manifest_signature) for identity, manifest_etag, manifest_signature in pending_direct)
        verified_identities = []
        for identity, person, manifest_etag, manifest_signature in authoritative:
            if person is None and identity.emby_signature == manifest_signature:
                verified_identities.append(identity)
                continue
            if not person or person.get("Type") != "Person" or "LockedFields" not in person:
                continue
            self._resolved_person_items[identity.tmdb_id] = person
            locked_fields = set(person.get("LockedFields") or [])
            name_locked = bool({"Name", "SortName"} & locked_fields)
            requires_name_lock = self._requires_name_lock(identity)
            provider_ids = {str(key).casefold(): str(value) for key, value in (person.get("ProviderIds") or {}).items() if value not in (None, "")}
            identity_matches = (
                person.get("Name") == identity.display_name
                and self._sort_name_matches(identity.display_name, person.get("SortName"))
                and provider_ids.get(identity.provider.casefold()) == identity.provider_id
                and (not identity.imdb_id or provider_ids.get("imdb") == identity.imdb_id)
                and (not identity.tvdb_id or provider_ids.get("tvdb") == identity.tvdb_id)
                and (not identity.wikidata_id or provider_ids.get("wikidata") == identity.wikidata_id)
            )
            locks_match = (requires_name_lock and {"Name", "SortName"}.issubset(locked_fields)) or (not requires_name_lock and not name_locked)
            if not identity_matches or not locks_match:
                self._changed_identity_ids.add(identity.tmdb_id)
            elif identity.tmdb_id not in self._changed_identity_ids:
                # Direct user-item ETags are unstable on Emby and can change
                # on every identical read. Persist the stable bulk-manifest
                # ETag whose state was proven by this direct response.
                identity.emby_etag = manifest_etag
                identity.emby_signature = manifest_signature
                verified_identities.append(identity)
        if verified_identities and self.cache:
            if hasattr(self.cache, "update_emby_person_verifications"):
                self.cache.update_emby_person_verifications(
                    self.server_id,
                    [
                        {
                            "tmdb_id": identity.tmdb_id,
                            "emby_etag": identity.emby_etag,
                            "emby_signature": identity.emby_signature,
                            "verified_at": identity.verified_at,
                        }
                        for identity in verified_identities
                    ],
                )
            else:
                for identity in verified_identities:
                    self._store_identity(identity)

    @staticmethod
    def _person_manifest_signature(person):
        provider_ids = sorted(
            (
                str(key).casefold(),
                str(value),
            )
            for key, value in (person.get("ProviderIds") or {}).items()
            if value not in (None, "")
        )
        return stable_hash(
            {
                "version": 2,
                "name": person.get("Name") or "",
                "sort_name": person.get("SortName") or "",
                "type": person.get("Type") or "",
                "provider_ids": provider_ids,
            }
        )

    def _reindex_identity_name(self, normalized_name, clear_singleton=False):
        self._unique_identities()
        identities = self._identities_by_name.get(normalized_name, [])
        self._reindex_identity_group(identities, clear_singleton=clear_singleton)

    def _reindex_identity_group(self, identities, clear_singleton=False):
        if len(identities) == 1:
            identity = identities[0]
            if clear_singleton and (identity.name_index is not None or identity.display_name != identity.base_name):
                identity.name_index = None
                identity.display_name = identity.base_name
                self._changed_identity_ids.add(identity.tmdb_id)
            return
        # Every member of a proven namesake group receives a stable suffix.
        # This makes Emby's name-only item updates unambiguous without
        # temporarily renaming the canonical Person.
        # TMDb identities sort before TVDb-only identities, then by provider ID.
        sorted_identities = sorted(identities, key=lambda value: (value.provider != "Tmdb", int(value.provider_id)))
        for name_index, identity in enumerate(sorted_identities, 1):
            display_name = f"{identity.base_name} ({roman_number(name_index)})"
            if identity.name_index != name_index:
                identity.name_index = name_index
                self._changed_identity_ids.add(identity.tmdb_id)
            if identity.display_name != display_name:
                identity.display_name = display_name
                self._changed_identity_ids.add(identity.tmdb_id)

    @staticmethod
    def _identity_row(identity):
        return {
            "tmdb_id": identity.tmdb_id,
            "imdb_id": identity.imdb_id,
            "tvdb_id": identity.tvdb_id,
            "wikidata_id": identity.wikidata_id,
            "base_name": identity.base_name,
            "normalized_name": identity.normalized_name,
            "name_index": identity.name_index,
            "display_name": identity.display_name,
            "emby_id": identity.emby_id,
            "emby_etag": identity.emby_etag,
            "emby_signature": identity.emby_signature,
            "duplicate_emby_ids": sorted(identity.duplicate_emby_ids, key=int),
            "verified_at": identity.verified_at,
            "external_verified_at": identity.external_verified_at,
            "canonical_id": identity.canonical_id,
        }

    def _persist_all_identities(self):
        if self.cache:
            # Canonical aliases are lookup records, not separate namesakes.
            # Keep this invariant at the persistence boundary as well, because
            # identities can also be merged after discovery while applying
            # item relationships.
            for identity in self._identity_records.values():
                if identity.canonical_id is not None and (identity.name_index is not None or identity.display_name != identity.base_name):
                    identity.name_index = None
                    identity.display_name = identity.base_name
                    self._changed_identity_ids.add(identity.tmdb_id)
            self.cache.update_emby_person_identities(
                self.server_id,
                [self._identity_row(identity) for identity in self._identity_records.values()],
            )

    def _identity_audit_due(self, identity):
        if not identity.verified_at:
            return True
        try:
            audit_days = max(int(getattr(self.cache, "expiration", 30) or 30), 1)
            return datetime.fromisoformat(identity.verified_at) < datetime.now() - timedelta(days=audit_days)
        except (TypeError, ValueError):
            return True

    def _external_identity_audit_due(self, identity):
        if not identity.external_verified_at:
            return True
        try:
            audit_days = max(int(getattr(self.cache, "expiration", 30) or 30), 1)
            return datetime.fromisoformat(identity.external_verified_at) < datetime.now() - timedelta(days=audit_days)
        except (TypeError, ValueError):
            return True

    def _ensure_audit_people(self, audit_ids):
        sorted_ids = sorted(audit_ids)
        total = len(sorted_ids)
        if total:
            logger.info(f"Emby Person Audit | 0/{total}")
            emby_ids = sorted(
                {str(self.identities[tmdb_id].emby_id) for tmdb_id in sorted_ids if str(self.identities[tmdb_id].emby_id or "").isdigit()},
                key=int,
            )
            batch_size = 1000
            for batch_start in range(0, len(emby_ids), batch_size):
                batch = emby_ids[batch_start : batch_start + batch_size]
                bulk_people = self.emby.get_items_bulk(
                    batch,
                    fields=["Etag", "ImageTags", "LockedFields", "Name", "ProviderIds", "SortName", "Type"],
                    force_refresh=False,
                )
                for tmdb_id in sorted_ids:
                    identity = self.identities[tmdb_id]
                    person = bulk_people.get(str(identity.emby_id or ""))
                    if person:
                        self._resolved_person_items[identity.tmdb_id] = person
        for index, tmdb_id in enumerate(sorted_ids, 1):
            identity = self.identities[tmdb_id]
            if not identity.emby_id or tmdb_id in self._ensured_person_ids:
                if index == total or index % 25 == 0:
                    logger.info(f"Emby Person Audit | {index}/{total}")
                continue
            try:
                self._ensure_external_ids(identity)
                self._ensure_person_metadata(identity)
                self._ensured_person_ids.add(tmdb_id)
            except Exception as error:
                self._identity_errors[tmdb_id] = error
                logger.error(f"Emby person audit failed for {identity.provider} person {identity.provider_id}: {error}")
            if index == total or index % 25 == 0:
                logger.info(f"Emby Person Audit | {index}/{total}")

    def _credits_from_source(self, cast, crew, source, crew_source=None, item_type=None):
        crew_source = crew_source or source
        item_type = str(item_type or "").strip().casefold()
        cast_credits = []
        seen = set()
        for position, actor in enumerate(cast or []):
            role = (actor.get("character") or "").strip() or None
            if role and re.search(r"\buncredited\b", role, flags=re.IGNORECASE):
                continue
            tmdb_id = self._valid_tmdb_person_id(self._credit_tmdb_id(actor, source))
            name = (actor.get("name") or "").strip()
            person_type = str(actor.get("person_type") or "Actor").strip() or "Actor"
            if tmdb_id is None or not name:
                continue
            key = tmdb_id, person_type, role
            if key in seen:
                continue
            seen.add(key)
            order = actor.get("order")
            cast_credits.append(
                PersonCredit(
                    tmdb_id,
                    name,
                    person_type,
                    role,
                    int(order) if str(order).isdigit() else position,
                    imdb_id=self._credit_provider_id(actor, "Imdb"),
                    tvdb_id=self._credit_provider_id(actor, "Tvdb"),
                )
            )

        if source != "tvdb":
            cast_credits.sort(key=lambda credit: (credit.order, credit.name.casefold(), credit.tmdb_id))
        credits = list(cast_credits)

        crew_buckets = {person_type: [] for person_type in CREW_TYPE_ORDER}
        for position, member in enumerate(crew or []):
            job = (member.get("job") or "").strip().lower()
            mapped = JOB_TO_TYPE_ROLE.get(job)
            explicit_person_type = str(member.get("person_type") or "").strip()
            if explicit_person_type:
                mapped = (explicit_person_type, (member.get("role") or "").strip() or None)
            tmdb_id = self._valid_tmdb_person_id(self._credit_tmdb_id(member, crew_source))
            name = (member.get("name") or "").strip()
            if not mapped or tmdb_id is None or not name:
                continue
            person_type, role = mapped
            if item_type == "movie" and person_type == "Producer" and role == "Executive Producer":
                continue
            if item_type in {"series", "season", "episode"} and person_type == "Producer" and role is None:
                continue
            key = tmdb_id, person_type, role
            if key in seen:
                continue
            seen.add(key)
            crew_buckets[person_type].append(
                PersonCredit(
                    tmdb_id,
                    name,
                    person_type,
                    role,
                    position,
                    imdb_id=self._credit_provider_id(member, "Imdb"),
                    tvdb_id=self._credit_provider_id(member, "Tvdb"),
                )
            )

        for person_type in CREW_TYPE_ORDER:
            priorities = {role: index for index, role in enumerate(CREW_ROLE_PRIORITY[person_type])}
            bucket = crew_buckets[person_type]
            bucket.sort(key=lambda credit: (priorities.get(credit.role, len(priorities)), credit.role or "", credit.name.casefold(), credit.tmdb_id))
            credits.extend(bucket)
        return credits

    def _credit_tmdb_id(self, credit, source):
        explicit_id = credit.get("tmdb_id") or self._credit_provider_id(credit, "Tmdb")
        if explicit_id is not None:
            return explicit_id
        if source == "tmdb":
            return credit.get("id")
        if source == "tvdb":
            tvdb_id = self._credit_provider_id(credit, "Tvdb")
            if tvdb_id is not None and str(tvdb_id).isdigit():
                return -int(tvdb_id)
        return None

    def _valid_tmdb_person_id(self, value):
        if value is None or not str(value).lstrip("-").isdigit():
            return None
        person_id = int(value)
        if person_id == 0 or abs(person_id) > MAX_SQLITE_INTEGER:
            logger.warning(f"Ignoring invalid provider person ID in Cast & Crew: {value}")
            return None
        return person_id

    def _credit_provider_id(self, credit, provider):
        direct_keys = {
            "Tmdb": ("tmdb_id",),
            "Imdb": ("imdb_id",),
            "Tvdb": ("tvdb_id", "thetvdb_id"),
        }
        for key in direct_keys[provider]:
            if credit.get(key) not in (None, ""):
                return str(credit[key])
        return self._provider_value(credit.get("ProviderIds") or credit.get("provider_ids"), provider)

    def _known_identity_for_person(self, person, exclude=None):
        provider_ids = {str(key).casefold(): str(value) for key, value in (person.get("ProviderIds") or {}).items() if value not in (None, "")}
        matches = {}
        self._unique_identities()
        tmdb_id = provider_ids.get("tmdb")
        if str(tmdb_id or "").isdigit():
            identity = self.identities.get(int(tmdb_id))
            if identity is not None and identity is not exclude:
                matches[id(identity)] = identity
        tvdb_id = provider_ids.get("tvdb")
        if str(tvdb_id or "").isdigit():
            identity = self.identities.get(-int(tvdb_id))
            if identity is not None and identity is not exclude:
                matches[id(identity)] = identity
            for candidate in self._identities_by_tvdb.get(str(tvdb_id), []):
                if candidate is not exclude:
                    matches[id(candidate)] = candidate
        imdb_id = provider_ids.get("imdb")
        if imdb_id:
            for candidate in self._identities_by_imdb.get(imdb_id, []):
                if candidate is not exclude:
                    matches[id(candidate)] = candidate
        wikidata_id = provider_ids.get("wikidata")
        if wikidata_id:
            for candidate in self._identities_by_wikidata.get(wikidata_id, []):
                if candidate is not exclude:
                    matches[id(candidate)] = candidate
        return next(iter(matches.values())) if len(matches) == 1 else None

    def _materialize_plan_people(self, plan):
        placeholders = self._desired_people(plan, allow_placeholders=True, name_as_id=True)
        fresh, item_updated = self._replace_people_safe(plan.item_id, placeholders, materializing=True)
        created = 0
        reindexed_after_creation = False
        # Recovery handles all mismatched People in one pass. More than four
        # complete clear/materialize cycles indicates an Emby name-index
        # collision that will not improve by retrying dozens of credits.
        max_attempts = min(len({credit.tmdb_id for credit in plan.credits}) + 2, 4)
        for _ in range(max_attempts):
            people = fresh.get("People") or []
            if len(people) != len(plan.credits):
                raise RuntimeError(f"Emby item {plan.item_id} returned an unexpected People count")

            unresolved = {}
            for credit, person in zip(plan.credits, people):
                identity = self.identities[credit.tmdb_id]
                person_id = str(person.get("Id") or "")
                if not person_id.isdigit():
                    continue
                if identity.emby_id and str(identity.emby_id) == person_id and identity.tmdb_id not in self._detached_identity_ids:
                    continue
                existing = unresolved.get(id(identity))
                if existing and existing[1] != person_id:
                    raise RuntimeError(f"Emby materialized multiple Person IDs for {identity.display_name}")
                unresolved[id(identity)] = (identity, person_id)

            if not unresolved:
                break
            people_by_id = (
                self.emby.get_items_bulk(
                    sorted({person_id for _, person_id in unresolved.values()}, key=int),
                    fields=["ProviderIds", "Name", "SortName", "Type", "Etag"],
                    force_refresh=True,
                )
                or {}
            )
            retry_materialization = False
            for identity, person_id in unresolved.values():
                person = people_by_id.get(person_id)
                if not person or person.get("Type") != "Person":
                    if identity.name_index is not None:
                        raise RuntimeError(f"Emby did not return materialized Person {person_id} for {identity.display_name}")
                    continue
                false_friend, reason = self._is_false_friend(identity, person)
                if false_friend:
                    if person_id in identity.duplicate_emby_ids or person_id in self._noncanonical_person_ids:
                        self._quarantine_duplicate_person(identity, person_id)
                        self._changed_identity_ids.add(identity.tmdb_id)
                        retry_materialization = True
                        logger.info(f"Emby linked duplicate Person rerouted | {identity.provider} {identity.provider_id} | " f"Emby Person {person_id} | canonical Emby Person {identity.emby_id}")
                        continue
                    if str(identity.emby_id or "") == person_id:
                        identity.emby_id = None
                        identity.emby_etag = None
                        identity.emby_signature = None
                        identity.duplicate_emby_ids.discard(person_id)
                        self._changed_identity_ids.add(identity.tmdb_id)
                        self._detached_identity_ids.add(identity.tmdb_id)
                        self._store_identity(identity)
                    known_identity = self._known_identity_for_person(person, exclude=identity)
                    if known_identity:
                        duplicate_link = False
                        if str(known_identity.emby_id or "") != person_id:
                            if known_identity.emby_id:
                                known_identity.duplicate_emby_ids.add(person_id)
                                self._same_identity_duplicate_ids.add(known_identity.tmdb_id)
                                self._noncanonical_person_ids.add(person_id)
                                duplicate_link = True
                            else:
                                known_identity.emby_id = person_id
                                known_identity.emby_etag = None
                                known_identity.emby_signature = None
                        if str(known_identity.emby_id or "") == person_id:
                            self._resolved_person_items[known_identity.tmdb_id] = person
                        self._store_identity(known_identity)
                        self._ensure_external_ids(known_identity)
                        self._ensure_person_metadata(known_identity)
                        self._ensured_person_ids.add(known_identity.tmdb_id)
                        if duplicate_link:
                            self._quarantine_duplicate_person(known_identity, person_id)
                        logger.info(f"Emby existing namesake identified | {known_identity.provider} {known_identity.provider_id} | " f"{known_identity.base_name} -> {known_identity.display_name}")
                        if not identity.emby_id:
                            self._assign_false_friend_index(identity)
                            self._store_identity(identity)
                            self._temporary_materialization_identity_ids.add(identity.tmdb_id)
                            logger.info(f"Emby collision-free Person materialization | {identity.provider} {identity.provider_id} | " f"temporarily using {identity.display_name}")
                    else:
                        self._assign_false_friend_index(identity)
                        self._store_identity(identity)
                        logger.info(f"Emby false friend resolved after materialization | {identity.provider} {identity.provider_id} | " f"{identity.base_name} -> {person.get('Name') or person_id} | {reason} | using {identity.display_name}")
                    retry_materialization = True
                    continue

                was_missing = not identity.emby_id
                if was_missing:
                    identity.emby_id = person_id
                    identity.emby_etag = None
                    identity.emby_signature = None
                elif str(identity.emby_id) != person_id:
                    identity.duplicate_emby_ids.add(person_id)
                    self._same_identity_duplicate_ids.add(identity.tmdb_id)
                    self._noncanonical_person_ids.add(person_id)
                if str(identity.emby_id or "") == person_id:
                    self._resolved_person_items[identity.tmdb_id] = person
                if was_missing:
                    self._new_person_ids.add(identity.tmdb_id)
                    if identity.name_index is not None:
                        created += 1
                    if identity.tmdb_id in self._temporary_materialization_identity_ids:
                        self._reindex_identity_name(identity.normalized_name)
                        reindexed_after_creation = True
                        self._temporary_materialization_identity_ids.discard(identity.tmdb_id)
                        for grouped_identity in self._unique_identities():
                            if grouped_identity.normalized_name == identity.normalized_name:
                                self._store_identity(grouped_identity)
                self._store_identity(identity)

            if not retry_materialization:
                break
            placeholders = self._desired_people(plan, allow_placeholders=True, name_as_id=True)
            fresh, changed = self._replace_people_safe(plan.item_id, placeholders, materializing=True, force=True)
            item_updated = item_updated or changed
        else:
            raise RuntimeError(f"Unable to materialize collision-free People for Emby item {plan.item_id}")
        return created, fresh, item_updated, reindexed_after_creation

    def _ensure_plan_people(self, plan):
        for tmdb_id in sorted({credit.tmdb_id for credit in plan.credits}):
            identity = self.identities[tmdb_id]
            if tmdb_id in self._ensured_person_ids:
                continue
            if tmdb_id in self._identity_errors:
                raise RuntimeError(f"Person update failed for {identity.provider} person {identity.provider_id}: {self._identity_errors[tmdb_id]}")
            missing_external_ids_due = identity.tmdb_id > 0 and (not identity.imdb_id or not identity.tvdb_id) and self._external_identity_audit_due(identity)
            if identity.tmdb_id not in self._changed_identity_ids and tmdb_id not in self._changed_identity_ids and identity.tmdb_id not in self._new_person_ids and not missing_external_ids_due:
                continue
            if not identity.emby_id:
                if identity.name_index is None:
                    continue
                raise RuntimeError(f"No numeric Emby ID for {identity.provider} person {identity.provider_id}")
            if identity.tmdb_id > 0 or identity.name_index is not None or identity.tvdb_id or identity.imdb_id:
                self._ensure_external_ids(identity)
            self._ensure_person_metadata(identity)
            self._ensured_person_ids.add(tmdb_id)

    def _ensure_changed_people(self):
        """Apply canonical reindexing to already known Person items."""
        changed = {}
        for tmdb_id in self._changed_identity_ids:
            identity = self.identities[tmdb_id]
            if identity.emby_id:
                changed[id(identity)] = identity
        changed_identities = sorted(changed.values(), key=lambda identity: identity.tmdb_id)
        total = len(changed_identities)
        if total:
            logger.info(f"Emby Person Metadata Reconciliation | 0/{total}")
        if total >= 25:
            emby_ids = sorted({str(identity.emby_id) for identity in changed_identities if identity.emby_id}, key=int)
            bulk_people = self.emby.get_items_bulk(
                emby_ids,
                fields=["Etag", "ImageTags", "LockedFields", "Name", "ProviderIds", "SortName"],
                force_refresh=True,
            )
            for identity in changed_identities:
                person = bulk_people.get(str(identity.emby_id))
                if person:
                    self._resolved_person_items[identity.tmdb_id] = person
            logger.info(f"Emby Person Metadata Reconciliation | bulk-loaded {len(bulk_people)}/{len(emby_ids)} People")
        for index, identity in enumerate(changed_identities, 1):
            tmdb_id = identity.tmdb_id
            if tmdb_id in self._ensured_person_ids:
                if index == total or index % 25 == 0:
                    logger.info(f"Emby Person Metadata Reconciliation | {index}/{total}")
                continue
            try:
                self._ensure_external_ids(identity)
                self._ensure_person_metadata(identity)
                self._ensured_person_ids.add(tmdb_id)
            except Exception as error:
                self._identity_errors[tmdb_id] = error
                logger.error(f"Emby person update failed for {identity.provider} person {identity.provider_id}: {error}")
            if index == total or index % 25 == 0:
                logger.info(f"Emby Person Metadata Reconciliation | {index}/{total}")

    def _ensure_external_ids(self, identity):
        if identity.tmdb_id < 0:
            self._store_identity(identity)
            return
        if not self._external_identity_audit_due(identity):
            return
        evidence = self._get_tmdb_identity_evidence(identity.tmdb_id, refresh=True)
        if not evidence.get("available"):
            return

        imdb_id = evidence.get("imdb_id")
        tvdb_id = evidence.get("tvdb_id")
        wikidata_id = evidence.get("wikidata_id")
        if identity.imdb_id and imdb_id and identity.imdb_id != str(imdb_id):
            logger.warning(f"TMDb person {identity.tmdb_id} changed IMDb ID from {identity.imdb_id} to {imdb_id}; " f"using the currently verified TMDb value")
        if imdb_id:
            identity.imdb_id = str(imdb_id)
        if identity.tvdb_id and tvdb_id and identity.tvdb_id != str(tvdb_id):
            logger.warning(f"TMDb person {identity.tmdb_id} changed TVDb ID from {identity.tvdb_id} to {tvdb_id}; " f"using the currently verified TMDb value")
        if tvdb_id:
            identity.tvdb_id = str(tvdb_id)
        if identity.wikidata_id and wikidata_id and identity.wikidata_id != str(wikidata_id):
            logger.warning(f"TMDb person {identity.tmdb_id} changed Wikidata ID from {identity.wikidata_id} " f"to {wikidata_id}; using the currently verified TMDb value")
        if wikidata_id:
            identity.wikidata_id = str(wikidata_id)
        identity.external_verified_at = datetime.now().isoformat(timespec="seconds")
        self._store_identity(identity)

    def _ensure_person_metadata(self, identity):
        person = self._resolved_person_items.get(identity.tmdb_id)
        if person is None or "LockedFields" not in person:
            person = self.emby.get_item(identity.emby_id, force_refresh=True)
        if not person or person.get("Type") != "Person":
            raise RuntimeError(f"Emby person {identity.emby_id} is missing")

        current_provider_ids = dict(person.get("ProviderIds") or {})
        managed_provider_keys = {identity.provider.casefold()}
        if identity.tmdb_id > 0:
            managed_provider_keys.add("tmdb")
        if identity.imdb_id:
            managed_provider_keys.add("imdb")
        if identity.tvdb_id:
            managed_provider_keys.add("tvdb")
        if identity.wikidata_id:
            managed_provider_keys.add("wikidata")
        desired_provider_ids = {key: value for key, value in current_provider_ids.items() if str(key).casefold() not in managed_provider_keys}
        if identity.tmdb_id > 0:
            desired_provider_ids["Tmdb"] = str(identity.tmdb_id)
        if identity.imdb_id:
            desired_provider_ids["Imdb"] = identity.imdb_id
        if identity.tvdb_id:
            desired_provider_ids["Tvdb"] = identity.tvdb_id
        if identity.wikidata_id:
            desired_provider_ids["Wikidata"] = identity.wikidata_id
        requires_name_lock = self._requires_name_lock(identity)
        locked_fields = [field for field in (person.get("LockedFields") or []) if field not in ("Name", "SortName")]
        if requires_name_lock:
            locked_fields.extend(("Name", "SortName"))

        def person_state_ok(candidate):
            if not candidate or candidate.get("Name") != identity.display_name or not self._sort_name_matches(identity.display_name, candidate.get("SortName")):
                return False
            candidate_provider_ids = dict(candidate.get("ProviderIds") or {})
            providers_ok = self._provider_value(candidate_provider_ids, identity.provider) == identity.provider_id
            if identity.imdb_id:
                providers_ok = providers_ok and self._provider_value(candidate_provider_ids, "Imdb") == identity.imdb_id
            if identity.tvdb_id:
                providers_ok = providers_ok and self._provider_value(candidate_provider_ids, "Tvdb") == identity.tvdb_id
            if identity.wikidata_id:
                providers_ok = providers_ok and self._provider_value(candidate_provider_ids, "Wikidata") == identity.wikidata_id
            candidate_locks = set(candidate.get("LockedFields") or [])
            if requires_name_lock:
                locks_ok = {"Name", "SortName"}.issubset(candidate_locks)
            else:
                locks_ok = not ({"Name", "SortName"} & candidate_locks)
            return providers_ok and locks_ok

        def check_write_response(response, action):
            if response is not None:
                self._check_response(response, action)
                return
            # EmbyServer.update_item returns None both for a failed request and
            # for a payload that becomes a no-op after provider-ID merging.
            # A fresh complete response distinguishes those cases safely.
            candidate = self.emby.get_item(identity.emby_id, force_refresh=True)
            if not person_state_ok(candidate):
                self._check_response(response, action)

        def check_unlock_response(response):
            if response is not None:
                self._check_response(
                    response,
                    f"unlocking Emby person name {identity.emby_id}",
                )
                return
            candidate = self.emby.get_item(identity.emby_id, force_refresh=True)
            candidate_locks = set((candidate or {}).get("LockedFields") or [])
            if {"Name", "SortName"} & candidate_locks:
                self._check_response(
                    response,
                    f"unlocking Emby person name {identity.emby_id}",
                )

        def write_person_identity(current_person, action):
            current_locks = list((current_person or {}).get("LockedFields") or [])
            unlocked_fields = [field for field in current_locks if field not in ("Name", "SortName")]
            if unlocked_fields != current_locks:
                response = self.emby.update_item(
                    identity.emby_id,
                    {
                        "Id": identity.emby_id,
                        "LockedFields": unlocked_fields,
                    },
                )
                check_unlock_response(response)
            response = self.emby.update_item(
                identity.emby_id,
                {
                    "Id": identity.emby_id,
                    "Name": identity.display_name,
                    "SortName": identity.display_name,
                    "ForcedSortName": identity.display_name,
                    "ProviderIds": desired_provider_ids,
                    "LockedFields": locked_fields,
                },
            )
            check_write_response(response, action)

        name_changed = person.get("Name") != identity.display_name or not self._sort_name_matches(identity.display_name, person.get("SortName"))
        normalized_current_provider_ids = {str(key).casefold(): str(value) for key, value in current_provider_ids.items() if value not in (None, "")}
        normalized_desired_provider_ids = {str(key).casefold(): str(value) for key, value in desired_provider_ids.items() if value not in (None, "")}
        provider_changed = normalized_current_provider_ids != normalized_desired_provider_ids
        expected_provider_ids = {str(key).casefold(): str(value) for key, value in desired_provider_ids.items() if str(key).casefold() in managed_provider_keys and value not in (None, "")}
        actual_provider_ids = {str(key).casefold(): str(value) for key, value in current_provider_ids.items() if value not in (None, "")}
        provider_conflict = any(provider in actual_provider_ids and actual_provider_ids[provider] != expected for provider, expected in expected_provider_ids.items())
        needs_update = name_changed or provider_changed or list(person.get("LockedFields") or []) != locked_fields
        if needs_update:
            write_person_identity(person, f"updating Emby person {identity.emby_id}")

        # A newly materialized Person only needs its identity fields written and
        # verified. Full metadata and image replacement is reserved for an
        # existing Person whose name or populated external IDs contradict the
        # expected identity. Missing provider IDs are filled without a refresh.
        full_refresh_needed = identity.tmdb_id not in self._new_person_ids and (name_changed or provider_conflict)
        image_tags_before = dict(person.get("ImageTags") or {})
        if full_refresh_needed:
            if not self.emby.refresh_item(identity.emby_id, replace_all_metadata=True, replace_all_images=True):
                raise RuntimeError(f"Metadata and image refresh failed for Emby person {identity.emby_id}")

        if not needs_update and not full_refresh_needed:
            identity.verified_at = datetime.now().isoformat(timespec="seconds")
            self._store_identity(identity)
            return

        fresh = self.emby.get_item(identity.emby_id, force_refresh=True)
        if not person_state_ok(fresh):
            write_person_identity(fresh, f"restoring Emby person {identity.emby_id} after refresh")
            fresh = self.emby.get_item(identity.emby_id, force_refresh=True)
            if not person_state_ok(fresh):
                raise RuntimeError(
                    f"Person verification failed for Emby person {identity.emby_id}: expected Name/SortName={identity.display_name!r}, "
                    f"actual Name={(fresh or {}).get('Name')!r}, SortName={(fresh or {}).get('SortName')!r}, "
                    f"LockedFields={(fresh or {}).get('LockedFields')!r}"
                )

        if full_refresh_needed:
            image_tags_after = dict((fresh or {}).get("ImageTags") or {})
            if image_tags_after != image_tags_before:
                image_status = "replaced"
            elif image_tags_after:
                image_status = "refreshed (provider returned the same image)"
            else:
                image_status = "refreshed (provider returned no image)"
            logger.info(f"Emby Person Metadata Refreshed | {identity.display_name} | Image: {image_status}")
        elif needs_update:
            logger.info(f"Emby Person Identity Updated | {identity.display_name} | Name and external IDs verified")

        # A Person write invalidates the previously verified bulk-manifest
        # ETag. It is re-established by the next bulk/direct lock check.
        identity.emby_etag = None
        identity.emby_signature = None
        identity.verified_at = datetime.now().isoformat(timespec="seconds")
        self._store_identity(identity)

    def _desired_people(self, plan, allow_placeholders, name_as_id=False):
        desired = []
        for credit in plan.credits:
            identity = self.identities[credit.tmdb_id]
            if name_as_id:
                person_id = identity.display_name
            elif identity.emby_id:
                person_id = identity.emby_id
            elif allow_placeholders:
                person_id = identity.display_name
            else:
                raise RuntimeError(f"Unresolved {identity.provider} person {identity.provider_id}")
            entry = {
                "Id": str(person_id),
                "Name": identity.display_name,
                "Type": credit.person_type,
            }
            if credit.role:
                entry["Role"] = credit.role
            desired.append(entry)
        return desired

    def _replace_people_safe(self, item_id, desired_people, materializing=False, force=False):
        before = self.emby.get_item(item_id, force_refresh=True)
        if not before:
            raise RuntimeError(f"Unable to load Emby item {item_id}")
        before_signature = self._people_materialization_signature(before.get("People") or []) if materializing else self._people_signature(before.get("People") or [])
        desired_signature = self._people_materialization_signature(desired_people) if materializing else self._people_signature(desired_people)
        if not force and before_signature == desired_signature:
            return before, False
        snapshot = copy.deepcopy(before)
        try:
            # Proven namesakes use permanent (I)/(II)/... names, so the
            # complete authoritative list can be written without an empty
            # intermediate People payload.
            response = self.emby.update_item(item_id, {"People": desired_people})
            self._check_response(response, f"updating People on Emby item {item_id}")
            after = self.emby.get_item(item_id, force_refresh=True)
            if not after:
                raise RuntimeError(f"Unable to verify Emby item {item_id}")
            actual_signature = self._people_materialization_signature(after.get("People") or []) if materializing else self._people_signature(after.get("People") or [])
            expected_signature = self._people_materialization_signature(desired_people) if materializing else self._people_signature(desired_people)
            if materializing and actual_signature != expected_signature:
                apply_deadline = time.monotonic() + 2
                while actual_signature != expected_signature and time.monotonic() < apply_deadline:
                    time.sleep(0.05)
                    after = self.emby.get_item(item_id, force_refresh=True) or after
                    actual_signature = self._people_materialization_signature(after.get("People") or [])
            if materializing and actual_signature != expected_signature:
                after = self._recover_false_friend_people(item_id, desired_people, after)
                actual_signature = self._people_materialization_signature(after.get("People") or [])
                expected_signature = self._people_materialization_signature(desired_people)
            if actual_signature != expected_signature:
                raise RuntimeError(self._people_verification_error(item_id, expected_signature, actual_signature))

            ignored = {"People", "Etag", "DateModified", "DateLastSaved", "DateLastRefreshed"}
            changed_fields = [key for key in sorted(set(snapshot) | set(after)) if key not in ignored and snapshot.get(key) != after.get(key)]
            if changed_fields:
                raise RuntimeError(f"Emby item {item_id} changed unrelated fields during People update: {', '.join(changed_fields)}")
            return after, True
        except Exception:
            if materializing:
                self._restore_people_snapshot(item_id, snapshot.get("People") or [])
            raise

    def _recover_false_friend_people(self, item_id, desired_people, actual_item):
        actual_people = actual_item.get("People") or []
        expected_signature = self._people_materialization_signature(desired_people)
        actual_signature = self._people_materialization_signature(actual_people)
        if len(expected_signature) != len(actual_signature):
            return actual_item

        mismatch_indices = sorted(index for index, (expected, actual) in enumerate(zip(expected_signature, actual_signature)) if expected != actual)
        if not mismatch_indices:
            return actual_item
        # Only names may be repaired here. A Type/Role discrepancy is a real
        # relationship error and must not be hidden by person materialization.
        if any(expected_signature[index][1:] != actual_signature[index][1:] for index in mismatch_indices):
            return actual_item

        indexed_payload = copy.deepcopy(desired_people)
        temporary_name_targets = {}
        routing_identities = {}
        displaced_identities = {}
        routing_targets = {}
        for index in mismatch_indices:
            entry = indexed_payload[index]
            identity = self._identity_for_people_entry(entry)
            if not identity:
                return actual_item
            actual_person = actual_people[index]
            actual_person_id = str(actual_person.get("Id") or "")
            actual_person_item = self.emby.get_item(actual_person_id, force_refresh=True) if actual_person_id.isdigit() else None
            reason = "name mismatch"
            false_friend = True
            if actual_person_item:
                false_friend, reason = self._is_false_friend(identity, actual_person_item)
            if false_friend and reason.startswith("unresolved cross-provider identity"):
                error = RuntimeError(f"Cannot recover {identity.provider} person {identity.provider_id} from " f"Emby Person {actual_person_id}: {reason}")
                self._identity_errors[identity.tmdb_id] = error
                logger.warning(str(error))
                return actual_item
            actual_identity = self._known_identity_for_person(actual_person_item, exclude=identity) if actual_person_item else None
            if actual_person_id in identity.duplicate_emby_ids or actual_person_id in self._noncanonical_person_ids:
                canonical_person_id = str(identity.emby_id or "")
                if not canonical_person_id.isdigit() or canonical_person_id == actual_person_id:
                    return actual_item
                if not self._person_name_is_published(identity):
                    old_canonical_person_id = canonical_person_id
                    identity.duplicate_emby_ids.discard(actual_person_id)
                    identity.duplicate_emby_ids.add(old_canonical_person_id)
                    identity.emby_id = actual_person_id
                    identity.emby_etag = None
                    identity.emby_signature = None
                    self._noncanonical_person_ids.discard(actual_person_id)
                    self._noncanonical_person_ids.add(old_canonical_person_id)
                    self._duplicate_person_owners.pop(actual_person_id, None)
                    self._duplicate_person_owners[old_canonical_person_id] = identity
                    self._canonical_person_owners.pop(old_canonical_person_id, None)
                    self._canonical_person_owners[actual_person_id] = identity
                    self._quarantine_duplicate_person(identity, old_canonical_person_id)
                    self._resolved_person_items[identity.tmdb_id] = actual_person_item
                    self._ensured_person_ids.discard(identity.tmdb_id)
                    self._changed_identity_ids.add(identity.tmdb_id)
                    self._ensure_external_ids(identity)
                    self._ensure_person_metadata(identity)
                    self._ensured_person_ids.add(identity.tmdb_id)
                    self._store_identity(identity)
                    routing_targets[index] = actual_person_id
                    temporary_name_targets[identity.display_name] = actual_person_id
                    self._post_apply_verify_ids.add(identity.tmdb_id)
                    entry["Id"] = identity.display_name
                    entry["Name"] = identity.display_name
                    desired_people[index]["Id"] = identity.display_name
                    desired_people[index]["Name"] = identity.display_name
                    logger.info(f"Emby linked duplicate Person promoted | {identity.provider} " f"{identity.provider_id} | linked Emby Person {actual_person_id} is canonical | " f"unpublished Emby Person {old_canonical_person_id} quarantined")
                    continue
                self._quarantine_duplicate_person(identity, actual_person_id)
                # Emby's item editor can retain a stale historical lookup key
                # even after the duplicate was permanently neutralized. Use a
                # short-lived unique name only on the verified canonical
                # Person while the empty item is refilled, then restore its
                # authoritative Kometa name in the routing finally block.
                routing_name = self._canonical_routing_name(identity)
                routing_identities[id(identity)] = (identity, routing_name)
                routing_targets[index] = canonical_person_id
                temporary_name_targets[routing_name] = canonical_person_id
                self._post_apply_verify_ids.add(identity.tmdb_id)
                entry["Id"] = routing_name
                entry["Name"] = routing_name
                routing_key = identity.tmdb_id, actual_person_id, canonical_person_id
                routing_count = self._duplicate_routing_counts.get(routing_key, 0) + 1
                self._duplicate_routing_counts[routing_key] = routing_count
                if routing_count == 1:
                    logger.info(f"Emby linked duplicate Person routing queued | {identity.provider} {identity.provider_id} | " f"Emby Person {actual_person_id} | canonical Emby Person {canonical_person_id}")
                else:
                    logger.ghost(f"Emby linked duplicate Person routing | {identity.provider} {identity.provider_id} | " f"{routing_count} item relationships")
                continue
            if false_friend and str(identity.emby_id or "") == actual_person_id:
                identity.emby_id = None
                identity.emby_etag = None
                identity.emby_signature = None
                identity.duplicate_emby_ids.discard(actual_person_id)
                self._changed_identity_ids.add(identity.tmdb_id)
                self._detached_identity_ids.add(identity.tmdb_id)
                self._store_identity(identity)
            if not false_friend:
                # The external identity is correct. If the People database
                # already selected a different canonical Emby Person, the
                # returned item is a duplicate version of that same real
                # person. Permanently quarantine the noncanonical record so
                # Emby's name-only relationship lookup can select the
                # canonical Person without a temporary rename.
                canonical_person_id = str(identity.emby_id or "")
                same_identity_duplicate_routing = False
                if actual_person_id.isdigit() and canonical_person_id.isdigit() and canonical_person_id != actual_person_id:
                    self._same_identity_duplicate_ids.add(identity.tmdb_id)
                    self._noncanonical_person_ids.add(actual_person_id)
                    identity.duplicate_emby_ids.add(actual_person_id)
                    self._quarantine_duplicate_person(identity, actual_person_id)
                    routing_name = self._canonical_routing_name(identity)
                    routing_identities[id(identity)] = (identity, routing_name)
                    routing_targets[index] = canonical_person_id
                    temporary_name_targets[routing_name] = canonical_person_id
                    self._post_apply_verify_ids.add(identity.tmdb_id)
                    same_identity_duplicate_routing = True
                elif actual_person_id.isdigit() and not canonical_person_id.isdigit():
                    identity.emby_id = actual_person_id
                    identity.emby_etag = None
                    identity.emby_signature = None
                if str(identity.emby_id or "") == actual_person_id:
                    self._resolved_person_items[identity.tmdb_id] = actual_person_item
                self._changed_identity_ids.add(identity.tmdb_id)
                self._store_identity(identity)
                if str(identity.emby_id or "").isdigit():
                    # A matching external identity with a stale base/indexed
                    # name is the correct Person, not a false friend. Apply
                    # Kometa's authoritative name before the relationship
                    # update, even if this Person was verified earlier in the
                    # same run under its previous name.
                    self._ensure_external_ids(identity)
                    self._ensure_person_metadata(identity)
                    self._ensured_person_ids.add(identity.tmdb_id)
                    # A same-identity duplicate route changes this canonical
                    # Person to its unique routing name immediately below.
                    # Waiting for both that name and the just-restored display
                    # name is contradictory because one Person can publish
                    # only one current name at a time.
                    if not same_identity_duplicate_routing:
                        temporary_name_targets[identity.display_name] = str(identity.emby_id)
                if same_identity_duplicate_routing:
                    entry["Id"] = routing_name
                    entry["Name"] = routing_name
                else:
                    entry["Id"] = identity.display_name
                    entry["Name"] = identity.display_name
                    desired_people[index]["Id"] = identity.display_name
                    desired_people[index]["Name"] = identity.display_name
            elif actual_identity and actual_identity.normalized_name == identity.normalized_name:
                # This is a known namesake, not a new collision. Repair the
                # linked namesake and retain the expected identity's stable
                # base/(II) assignment. Move the false friend temporarily out
                # of Emby's normalized name index while routing the expected
                # relationship. Renaming the expected Person instead leaves
                # the false friend occupying the ambiguous lookup key.
                if actual_person_id.isdigit() and str(actual_identity.emby_id or "") != actual_person_id:
                    if actual_identity.emby_id:
                        actual_identity.duplicate_emby_ids.add(actual_person_id)
                        self._same_identity_duplicate_ids.add(actual_identity.tmdb_id)
                        self._noncanonical_person_ids.add(actual_person_id)
                    else:
                        actual_identity.emby_id = actual_person_id
                        actual_identity.emby_etag = None
                        actual_identity.emby_signature = None
                if str(actual_identity.emby_id or "") == actual_person_id:
                    self._resolved_person_items[actual_identity.tmdb_id] = actual_person_item
                self._reindex_identity_name(identity.normalized_name)
                self._changed_identity_ids.update((identity.tmdb_id, actual_identity.tmdb_id))
                self._persist_all_identities()
                # Re-indexing is a material metadata change. The identity may
                # already have been verified earlier in this run while it still
                # used the unsuffixed name, so do not let the per-run ensured
                # cache suppress this targeted update.
                self._ensure_external_ids(actual_identity)
                self._ensure_person_metadata(actual_identity)
                self._ensured_person_ids.add(actual_identity.tmdb_id)
                if str(identity.emby_id or "").isdigit():
                    self._ensure_external_ids(identity)
                    self._ensure_person_metadata(identity)
                    self._ensured_person_ids.add(identity.tmdb_id)
                self._post_apply_verify_ids.update(candidate.tmdb_id for candidate in self._unique_identities() if candidate.normalized_name == identity.normalized_name)
                if str(identity.emby_id or "").isdigit() and str(identity.emby_id) != actual_person_id:
                    # Emby can fuzzy-match "Name (II)" to "Name (I)" even
                    # though both permanent names are correct. Keep the
                    # expected canonical Person untouched and move only the
                    # currently mis-selected namesake to a unique temporary
                    # name while the empty item is refilled.
                    displacement_name = self._namesake_displacement_name(
                        actual_identity,
                        actual_person_id,
                    )
                    displaced_identities[id(actual_identity)] = (
                        actual_identity,
                        displacement_name,
                    )
                    routing_targets[index] = str(identity.emby_id)
                    temporary_name_targets[displacement_name] = actual_person_id
                    temporary_name_targets[identity.display_name] = str(identity.emby_id)
                    entry["Id"] = identity.display_name
                    entry["Name"] = identity.display_name
                else:
                    temporary_name_targets[actual_identity.display_name] = actual_person_id
                    entry["Id"] = identity.display_name
                    entry["Name"] = identity.display_name
                desired_people[index]["Id"] = entry["Id"]
                desired_people[index]["Name"] = entry["Name"]
                if not str(identity.emby_id or "").isdigit():
                    self._temporary_materialization_identity_ids.add(identity.tmdb_id)
                    self._store_identity(identity)
            else:
                self._assign_false_friend_index(identity)
                self._temporary_materialization_identity_ids.add(identity.tmdb_id)
                self._store_identity(identity)
                entry["Id"] = identity.display_name
                entry["Name"] = identity.display_name
                desired_people[index]["Id"] = identity.display_name
                desired_people[index]["Name"] = identity.display_name
            if "Id" not in entry or entry.get("Id") == desired_people[index].get("Id"):
                entry["Id"] = identity.display_name
                entry["Name"] = identity.display_name
            resolution_label = "Emby false friend resolved" if false_friend else "Emby Person relationship normalized"
            logger.info(f"{resolution_label} | {identity.provider} {identity.provider_id} | " f"{identity.base_name} -> {actual_person.get('Name') or actual_person_id} | {reason} | " f"using {identity.display_name}")

        recovered_item = actual_item
        if routing_identities or displaced_identities:
            routed = False
            try:
                for identity, routing_name in routing_identities.values():
                    self._set_person_name_without_refresh(identity, routing_name)
                for identity, displacement_name in displaced_identities.values():
                    self._set_person_name_without_refresh(
                        identity,
                        displacement_name,
                    )
                self._wait_for_person_name_index(temporary_name_targets)
                self._clear_item_people_and_wait(item_id)
                indexed_response = self.emby.update_item(item_id, {"People": indexed_payload})
                self._check_response(indexed_response, f"refilling routed People on Emby item {item_id}")
                recovered_item = self.emby.get_item(item_id, force_refresh=True) or actual_item
                route_deadline = time.monotonic() + 15
                while time.monotonic() < route_deadline:
                    routed_people = recovered_item.get("People") or []
                    if len(routed_people) == len(indexed_payload) and all(index < len(routed_people) and str(routed_people[index].get("Id") or "") == canonical_person_id for index, canonical_person_id in routing_targets.items()):
                        routed = True
                        break
                    time.sleep(0.05)
                    recovered_item = self.emby.get_item(item_id, force_refresh=True) or recovered_item
                if not routed:
                    actual_routes = {index: str((recovered_item.get("People") or [{}])[index].get("Id") or "") for index in routing_targets if index < len(recovered_item.get("People") or [])}
                    raise RuntimeError(f"Canonical Person routing failed for Emby item {item_id}: " f"expected={routing_targets}, actual={actual_routes}")
            finally:
                for identity, _ in routing_identities.values():
                    self._set_person_name_without_refresh(identity, identity.display_name)
                for identity, _ in displaced_identities.values():
                    self._set_person_name_without_refresh(identity, identity.display_name)

            desired_signature = self._people_materialization_signature(desired_people)
            final_deadline = time.monotonic() + getattr(
                self,
                "_name_propagation_wait_seconds",
                NAME_PROPAGATION_WAIT_SECONDS,
            )
            while time.monotonic() < final_deadline:
                recovered_item = self.emby.get_item(item_id, force_refresh=True) or recovered_item
                if self._people_materialization_signature(recovered_item.get("People") or []) == desired_signature:
                    return recovered_item
                time.sleep(0.05)
            # Never send another name-based People payload here. Emby may
            # still have a stale embedded name while the relationship already
            # points at the verified canonical numeric Person. Rewriting by
            # name at that moment can select the quarantined duplicate again.
            routed_people = recovered_item.get("People") or []
            if len(routed_people) == len(desired_people) and all(index < len(routed_people) and str(routed_people[index].get("Id") or "") == canonical_person_id for index, canonical_person_id in routing_targets.items()):
                logger.info(f"Emby canonical Person routing verified | item {item_id} | " f"{len(routing_targets)} relationship names pending Emby propagation")
                verified_item = copy.deepcopy(recovered_item)
                for index, (actual_person, desired_person) in enumerate(zip(verified_item["People"], desired_people)):
                    identity = self._identity_for_people_entry(desired_person)
                    if identity and str(identity.emby_id or "").isdigit() and str(actual_person.get("Id") or "") == str(identity.emby_id):
                        actual_person["Name"] = desired_person["Name"]
                return verified_item
            raise RuntimeError(f"Canonical Person routing was lost for Emby item {item_id} after restoring Person names")

        self._wait_for_person_name_index(temporary_name_targets)
        indexed_response = self.emby.update_item(item_id, {"People": indexed_payload})
        self._check_response(indexed_response, f"materializing indexed false-friend People on Emby item {item_id}")
        recovered_item = self.emby.get_item(item_id, force_refresh=True) or actual_item
        indexed_signature = self._people_materialization_signature(indexed_payload)
        recovered_signature = self._people_materialization_signature(recovered_item.get("People") or [])
        apply_deadline = time.monotonic() + 3
        while recovered_signature != indexed_signature and time.monotonic() < apply_deadline:
            time.sleep(0.05)
            recovered_item = self.emby.get_item(item_id, force_refresh=True) or recovered_item
            recovered_signature = self._people_materialization_signature(recovered_item.get("People") or [])
        return recovered_item

    @staticmethod
    def _canonical_routing_name(identity):
        return f"Kometa Canonical Person {identity.provider} " f"{identity.provider_id} {identity.emby_id}"

    @staticmethod
    def _namesake_displacement_name(identity, emby_id):
        return f"Kometa Namesake Person {identity.provider} " f"{identity.provider_id} {emby_id}"

    def _person_name_is_published(self, identity):
        exact = getattr(self.emby, "get_person_by_exact_name", None)
        search = getattr(self.emby, "get_person_by_name", None)
        if not callable(exact) and not callable(search):
            return True
        return any(str(person.get("Id") or "") == str(identity.emby_id or "") and person.get("Name") == identity.display_name for person in self._published_people_by_name(identity.display_name))

    def _published_people_by_name(self, person_name):
        exact = getattr(self.emby, "get_person_by_exact_name", None)
        if callable(exact):
            person = exact(person_name)
            return [person] if person else []
        search = getattr(self.emby, "get_person_by_name", None)
        return (search(person_name) or []) if callable(search) else []

    def _clear_item_people_and_wait(self, item_id, timeout=15):
        response = self.emby.update_item(item_id, {"People": []})
        self._check_response(response, f"clearing People on Emby item {item_id}")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            item = self.emby.get_item(item_id, force_refresh=True)
            if item and not (item.get("People") or []):
                return item
            time.sleep(0.05)
        raise RuntimeError(f"Emby item {item_id} did not publish an empty People list")

    def _wait_for_person_name_index(self, expected_names, timeout=NAME_INDEX_WAIT_SECONDS):
        pending = {str(name): str(person_id) for name, person_id in (expected_names or {}).items() if name and str(person_id or "").isdigit()}
        exact = getattr(self.emby, "get_person_by_exact_name", None)
        search = getattr(self.emby, "get_person_by_name", None)
        if not pending or (not callable(exact) and not callable(search)):
            return
        started = time.monotonic()
        logger.ghost(f"Emby Person name index wait | 0/{len(pending)} names | timeout={timeout}s")
        total = len(pending)
        deadline = time.monotonic() + timeout
        while pending:
            for name, person_id in list(pending.items()):
                hits = self._published_people_by_name(name)
                if any(str(hit.get("Id") or "") == person_id and hit.get("Name") == name for hit in hits):
                    pending.pop(name, None)
            if not pending:
                elapsed = max(time.monotonic() - started, 0.001)
                if elapsed >= 1:
                    logger.info(f"Emby Person name index wait | {total}/{total} names | {elapsed:.1f}s")
                else:
                    logger.ghost(f"Emby Person name index wait | {total}/{total} names | {elapsed:.1f}s")
                return
            completed = total - len(pending)
            elapsed = max(time.monotonic() - started, 0.001)
            logger.ghost(f"Emby Person name index wait | {completed}/{total} names | {elapsed:.1f}s")
            if time.monotonic() >= deadline:
                unresolved = ", ".join(f"{name!r} -> {person_id}" for name, person_id in pending.items())
                raise RuntimeError(f"Emby Person name index did not publish {unresolved}")
            time.sleep(0.05)

    def _set_person_name_without_refresh(self, identity, display_name, emby_id=None):
        """Write a temporary/canonical Person name without provider refresh."""
        target_emby_id = str(emby_id or identity.emby_id or "")
        person = self.emby.get_item(target_emby_id, force_refresh=True)
        if not person or person.get("Type") != "Person":
            raise RuntimeError(f"Emby person {target_emby_id} is missing")
        temporary_name = display_name != identity.display_name
        requires_name_lock = self._requires_name_lock(identity)

        def locks_ok(candidate):
            candidate_locks = set((candidate or {}).get("LockedFields") or [])
            if requires_name_lock:
                return {"Name", "SortName"}.issubset(candidate_locks)
            return not ({"Name", "SortName"} & candidate_locks)

        def name_state_ok(candidate):
            return bool(candidate and candidate.get("Name") == display_name and (temporary_name or self._sort_name_matches(display_name, candidate.get("SortName"))) and locks_ok(candidate))

        # Emby may return no response for a no-op update. Avoid sending one
        # when its current state already matches Kometa's authoritative state.
        if name_state_ok(person):
            if target_emby_id == str(identity.emby_id or ""):
                self._resolved_person_items[identity.tmdb_id] = person
            return person, False

        current_locks = list(person.get("LockedFields") or [])
        unlocked_locks = [field for field in current_locks if field not in ("Name", "SortName")]
        if unlocked_locks != current_locks:
            response = self.emby.update_item(
                target_emby_id,
                {"Id": target_emby_id, "LockedFields": unlocked_locks},
            )
            self._check_response(response, f"unlocking temporary Emby person name {target_emby_id}")
        desired_locks = list(unlocked_locks)
        if self._requires_name_lock(identity):
            desired_locks.extend(("Name", "SortName"))
        response = self.emby.update_item(
            target_emby_id,
            {
                "Id": target_emby_id,
                "Name": display_name,
                "SortName": display_name,
                "ForcedSortName": display_name,
                "ProviderIds": dict(person.get("ProviderIds") or {}),
                "LockedFields": desired_locks,
            },
        )
        self._check_response(response, f"setting Emby person name {target_emby_id}")
        fresh = self.emby.get_item(target_emby_id, force_refresh=True)
        state_ok = name_state_ok(fresh)
        if not state_ok:
            # Emby can apply Name and SortName a fraction apart after an
            # unlock/write sequence. Repeat the small identity write once
            # before treating it as a real failure.
            retry_locks = [field for field in ((fresh or {}).get("LockedFields") or []) if field not in ("Name", "SortName")]
            if retry_locks != list((fresh or {}).get("LockedFields") or []):
                response = self.emby.update_item(
                    target_emby_id,
                    {"Id": target_emby_id, "LockedFields": retry_locks},
                )
                self._check_response(response, f"retrying Emby person name unlock {target_emby_id}")
            response = self.emby.update_item(
                target_emby_id,
                {
                    "Id": target_emby_id,
                    "Name": display_name,
                    "SortName": display_name,
                    "ForcedSortName": display_name,
                    "ProviderIds": dict((fresh or person).get("ProviderIds") or {}),
                    "LockedFields": desired_locks,
                },
            )
            self._check_response(response, f"retrying Emby person name {target_emby_id}")
            fresh = self.emby.get_item(target_emby_id, force_refresh=True)
            state_ok = name_state_ok(fresh)
        if not state_ok:
            raise RuntimeError(
                f"Person name verification failed for Emby person {target_emby_id}: "
                f"expected {display_name!r}, actual Name={(fresh or {}).get('Name')!r}, "
                f"SortName={(fresh or {}).get('SortName')!r}, "
                f"LockedFields={(fresh or {}).get('LockedFields')!r}"
            )
        if target_emby_id == str(identity.emby_id or ""):
            identity.emby_etag = None
            identity.emby_signature = None
            self._resolved_person_items[identity.tmdb_id] = fresh
            self._store_identity(identity)
        return fresh, True

    @staticmethod
    def _duplicate_quarantine_name(identity, emby_id):
        # The real name must not occur anywhere in the quarantine value.
        # Emby's People assignment uses fuzzy/contained-name matching, so both
        # "John Forsythe [Duplicate ...]" and "[Duplicate ...] John Forsythe"
        # can steal updates intended for the canonical Person.
        return f"Emby Duplicate Person {emby_id}"

    def _maintain_duplicate_person_quarantines(self, people_by_id):
        """Migrate and preserve every cached noncanonical Person name."""
        for identity in self._unique_identities():
            for duplicate_emby_id in sorted(identity.duplicate_emby_ids, key=int):
                duplicate_emby_id = str(duplicate_emby_id)
                if duplicate_emby_id not in people_by_id:
                    continue
                if self._duplicate_quarantine_state_ok(
                    identity,
                    duplicate_emby_id,
                    people_by_id[duplicate_emby_id],
                ):
                    continue
                try:
                    fresh, changed = self._quarantine_duplicate_person(identity, duplicate_emby_id)
                    people_by_id[duplicate_emby_id] = fresh
                    if changed:
                        logger.info(f"Emby duplicate Person quarantine migrated | {identity.provider} {identity.provider_id} | " f"Emby Person {duplicate_emby_id} -> {fresh.get('Name')}")
                except Exception as error:
                    self._identity_errors[identity.tmdb_id] = error
                    logger.error(f"Emby duplicate Person quarantine maintenance failed for {identity.provider} person " f"{identity.provider_id}, Emby Person {duplicate_emby_id}: {error}")

    def _quarantine_duplicate_person(self, identity, emby_id):
        """Make a noncanonical Emby Person permanently unambiguous."""
        target_emby_id = str(emby_id or "")
        person = self.emby.get_item(target_emby_id, force_refresh=True)
        if not person or person.get("Type") != "Person":
            raise RuntimeError(f"Emby person {target_emby_id} is missing")

        quarantine_name = self._duplicate_quarantine_name(identity, target_emby_id)
        desired_provider_ids = {key: value for key, value in (person.get("ProviderIds") or {}).items() if str(key).casefold() not in {"tmdb", "tvdb", "imdb"}}
        desired_locks = [field for field in (person.get("LockedFields") or []) if field not in ("Name", "SortName")]
        desired_locks.extend(("Name", "SortName"))

        def state_ok(candidate):
            return self._duplicate_quarantine_state_ok(
                identity,
                target_emby_id,
                candidate,
            )

        if state_ok(person):
            return person, False

        current_locks = list(person.get("LockedFields") or [])
        unlocked_locks = [field for field in current_locks if field not in ("Name", "SortName")]
        if unlocked_locks != current_locks:
            response = self.emby.update_item(
                target_emby_id,
                {"Id": target_emby_id, "LockedFields": unlocked_locks},
            )
            self._check_response(response, f"unlocking duplicate Emby person {target_emby_id}")

        payload = {
            "Id": target_emby_id,
            "Name": quarantine_name,
            "SortName": quarantine_name,
            "ForcedSortName": quarantine_name,
            "ProviderIds": desired_provider_ids,
            "_ReplaceProviderIds": True,
            "LockedFields": desired_locks,
        }
        response = self.emby.update_item(target_emby_id, payload)
        if response is None:
            fresh = self.emby.get_item(target_emby_id, force_refresh=True)
            if not state_ok(fresh):
                self._check_response(response, f"quarantining duplicate Emby person {target_emby_id}")
        else:
            self._check_response(response, f"quarantining duplicate Emby person {target_emby_id}")

        fresh = self.emby.get_item(target_emby_id, force_refresh=True)
        if not state_ok(fresh):
            raise RuntimeError(
                f"Duplicate Person quarantine verification failed for Emby person {target_emby_id}: "
                f"expected Name/SortName={quarantine_name!r} with no TMDb/TVDb/IMDb IDs, "
                f"actual Name={(fresh or {}).get('Name')!r}, SortName={(fresh or {}).get('SortName')!r}, "
                f"ProviderIds={(fresh or {}).get('ProviderIds')!r}, "
                f"LockedFields={(fresh or {}).get('LockedFields')!r}"
            )
        return fresh, True

    def _duplicate_quarantine_state_ok(self, identity, emby_id, candidate):
        quarantine_name = self._duplicate_quarantine_name(identity, emby_id)
        provider_ids = {key: value for key, value in ((candidate or {}).get("ProviderIds") or {}).items() if str(key).casefold() not in {"tmdb", "tvdb", "imdb"}}
        return bool(
            candidate
            and candidate.get("Name") == quarantine_name
            and self._sort_name_matches(quarantine_name, candidate.get("SortName"))
            and dict(candidate.get("ProviderIds") or {}) == provider_ids
            and {"Name", "SortName"}.issubset(set(candidate.get("LockedFields") or []))
        )

    def _restore_people_snapshot(self, item_id, snapshot_people):
        try:
            current = self.emby.get_item(item_id, force_refresh=True)
            if current and self._people_signature(current.get("People") or []) == self._people_signature(snapshot_people):
                return
            response = self.emby.update_item(item_id, {"People": snapshot_people})
            if response is None:
                current = self.emby.get_item(item_id, force_refresh=True)
                if current and self._people_signature(current.get("People") or []) == self._people_signature(snapshot_people):
                    return
            self._check_response(response, f"restoring People on Emby item {item_id}")
        except Exception as rollback_error:
            logger.error(f"Unable to restore People on Emby item {item_id}: {rollback_error}")

    def _identity_for_people_entry(self, entry):
        name = str((entry or {}).get("Name") or "")
        matches = [identity for identity in self._unique_identities() if identity.display_name == name]
        return matches[0] if len(matches) == 1 else None

    def _get_tmdb_identity_evidence(self, tmdb_id, refresh=False):
        tmdb_id = int(tmdb_id)
        if not refresh and tmdb_id in self._tmdb_identity_evidence:
            return self._tmdb_identity_evidence[tmdb_id]
        try:
            person = self.tmdb.get_person(tmdb_id, partial="external_ids")
        except Exception as error:
            logger.warning(f"TMDb Person {tmdb_id} external-ID verification unavailable: {error}")
            evidence = {
                "available": False,
                "name": None,
                "imdb_id": None,
                "tvdb_id": None,
                "wikidata_id": None,
            }
        else:
            external = self._object_value(person, "external_ids")
            evidence = {
                "available": True,
                "name": self._object_value(person, "name"),
                "imdb_id": self._object_value(person, "imdb_id") or self._object_value(external, "imdb_id"),
                "tvdb_id": (self._object_value(person, "tvdb_id") or self._object_value(external, "tvdb_id") or self._object_value(external, "thetvdb_id")),
                "wikidata_id": self._object_value(person, "wikidata_id") or self._object_value(external, "wikidata_id"),
            }
            for key in ("name", "imdb_id", "tvdb_id", "wikidata_id"):
                if evidence[key] not in (None, ""):
                    evidence[key] = str(evidence[key])
        self._tmdb_identity_evidence[tmdb_id] = evidence
        return evidence

    def _get_wikidata_identity_evidence(self, wikidata_id):
        wikidata_id = str(wikidata_id or "").strip().upper()
        if wikidata_id in self._wikidata_identity_evidence:
            return self._wikidata_identity_evidence[wikidata_id]
        if not hasattr(self.tmdb, "get_wikidata_person_ids"):
            evidence = {
                "available": False,
                "wikidata_id": wikidata_id,
                "tmdb_id": None,
                "tvdb_id": None,
                "imdb_id": None,
            }
        else:
            try:
                evidence = dict(self.tmdb.get_wikidata_person_ids(wikidata_id) or {})
                evidence["available"] = True
            except Exception as error:
                logger.warning(f"Wikidata Person {wikidata_id} verification unavailable: {error}")
                evidence = {
                    "available": False,
                    "wikidata_id": wikidata_id,
                    "tmdb_id": None,
                    "tvdb_id": None,
                    "imdb_id": None,
                }
        self._wikidata_identity_evidence[wikidata_id] = evidence
        return evidence

    def _store_identity(self, identity):
        if not self.cache:
            return
        self.cache.update_emby_person_identity(
            self.server_id,
            identity.tmdb_id,
            identity.base_name,
            identity.normalized_name,
            identity.display_name,
            name_index=identity.name_index,
            emby_id=identity.emby_id,
            imdb_id=identity.imdb_id,
            tvdb_id=identity.tvdb_id,
            wikidata_id=identity.wikidata_id,
            emby_etag=identity.emby_etag,
            emby_signature=identity.emby_signature,
            duplicate_emby_ids=sorted(identity.duplicate_emby_ids, key=int),
            verified_at=identity.verified_at,
            external_verified_at=identity.external_verified_at,
            canonical_id=identity.canonical_id,
        )

    def _store_item_state(self, plan, applied_hash, refresh_etag=False):
        if not self.cache:
            return
        stable_etag = plan.emby_etag
        if refresh_etag and hasattr(self.emby, "get_stable_item_etags"):
            stable_etag = self.emby.get_stable_item_etags([plan.item_id]).get(plan.item_id)
        plan.emby_etag = stable_etag
        self.cache.update_emby_people_item_state(
            self.server_id,
            self.library_id,
            plan.item_id,
            plan.tmdb_id,
            stable_etag,
            plan.credits_source,
            plan.credits_hash,
            applied_hash,
            SYNC_VERSION,
        )
        self.cache.replace_emby_item_people_links(
            self.server_id,
            plan.item_id,
            [credit.tmdb_id for credit in plan.credits],
        )

    @staticmethod
    def _people_signature(people):
        return [
            (
                str(person.get("Id") or ""),
                (person.get("Name") or "").strip(),
                person.get("Type") or "",
                (person.get("Role") or "").strip(),
            )
            for person in people or []
        ]

    @staticmethod
    def _people_relationship_signature(people):
        return [
            (
                (person.get("Name") or "").strip(),
                person.get("Type") or "",
                (person.get("Role") or "").strip(),
            )
            for person in people or []
        ]

    @staticmethod
    def _comparable_person_name(value):
        normalized = unicodedata.normalize("NFKD", str(value or ""))
        without_accents = "".join(character for character in normalized if not unicodedata.combining(character))
        return re.sub(r"\s+", " ", without_accents).strip().casefold()

    @classmethod
    def _sort_name_matches(cls, expected, actual):
        return bool(str(actual or "").strip()) and cls._comparable_person_name(expected) == cls._comparable_person_name(actual)

    @classmethod
    def _comparable_unindexed_person_name(cls, value):
        without_managed_index = re.sub(r"\s+\([IVXLCDM]+\)$", "", str(value or "").strip(), flags=re.IGNORECASE)
        return cls._comparable_person_name(without_managed_index)

    @classmethod
    def _people_materialization_signature(cls, people):
        return [
            (
                cls._comparable_person_name(person.get("Name")),
                person.get("Type") or "",
                (person.get("Role") or "").strip(),
            )
            for person in people or []
        ]

    @staticmethod
    def _people_verification_error(item_id, expected, actual):
        difference = next(
            (f"index {index}: expected={expected_value!r}, actual={actual_value!r}" for index, (expected_value, actual_value) in enumerate(zip(expected, actual)) if expected_value != actual_value),
            None,
        )
        if difference is None and len(expected) != len(actual):
            difference = f"expected {len(expected)} relationships, received {len(actual)}"
        return f"People verification failed for Emby item {item_id}" + (f" ({difference})" if difference else "")

    @staticmethod
    def _provider_value(provider_ids, key):
        key = key.casefold()
        for provider_key, value in (provider_ids or {}).items():
            if str(provider_key).casefold() == key and value not in (None, ""):
                return str(value)
        return None

    @staticmethod
    def _object_value(obj, key):
        if obj is None:
            return None
        if isinstance(obj, dict):
            return obj.get(key)
        return getattr(obj, key, None)

    @staticmethod
    def _check_response(response, operation):
        if response is None:
            raise RuntimeError(f"Emby returned no response while {operation}")
        status_code = getattr(response, "status_code", 204)
        if status_code not in (200, 204):
            raise RuntimeError(f"Emby returned HTTP {status_code} while {operation}")
