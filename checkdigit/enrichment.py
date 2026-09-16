"""
enrichment.py
=============
Container enrichment, layered by data-source accessibility (tiers verified against
each provider's live docs; see SOURCES below for URLs).

  OwnerRegistry            -- BIC owner-code register. FREE & OPEN, offline once
                              loaded. Maps the 3-letter owner prefix -> owner, and
                              supplies the set used to CORROBORATE free-text matches.

  Network enrichers        -- queried per container number when enabled (i.e. when
                              you supply credentials). Each is FREE-WITH-AUTH or
                              PAID; none is called unless configured. All endpoints
                              below were taken from the provider's published docs --
                              none are invented. Where a base URL/auth could not be
                              confirmed without an account, the adapter requires YOU
                              to supply it rather than guessing (DcsaEventsEnricher).

Live HTTP uses `httpx` (lazy-imported, so the core stays importable without it; a
clear error is raised if an enabled enricher is used without httpx installed).

WHERE TO PUT YOUR API CREDENTIALS: each enricher class below carries an AUTH block
with the registration URL and the exact env vars to set. api.py reads those env
vars at startup (EnrichmentService.build_from_env) and enables only the sources you
have configured.
"""
from __future__ import annotations

import csv
import json
import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Set


# ───────────────────────────── owner registry ──────────────────────────────
@dataclass
class OwnerInfo:
    code: str
    company: str = ""
    city: str = ""
    country: str = ""
    source: str = "registry"


class OwnerRegistry:
    def __init__(self, owners: Optional[Dict[str, OwnerInfo]] = None):
        self._by_code: Dict[str, OwnerInfo] = dict(owners or {})

    def __len__(self) -> int:
        return len(self._by_code)

    @property
    def prefixes(self) -> Set[str]:
        return set(self._by_code.keys())

    def owner_of(self, eqid_or_code: str) -> Optional[OwnerInfo]:
        return self._by_code.get((eqid_or_code or "")[:3].upper())

    @classmethod
    def from_rows(cls, rows) -> "OwnerRegistry":
        owners: Dict[str, OwnerInfo] = {}
        for row in rows:
            code = (row.get("code") or row.get("owner_code") or "").strip().upper()
            if len(code) != 3 or not code.isalpha():
                continue
            owners[code] = OwnerInfo(
                code=code,
                company=(row.get("company") or row.get("name") or "").strip(),
                city=(row.get("city") or "").strip(),
                country=(row.get("country") or "").strip(),
                source="bic-register")
        return cls(owners)

    @classmethod
    def from_csv(cls, path: str) -> "OwnerRegistry":
        with open(path, newline="", encoding="utf-8") as fh:
            return cls.from_rows(csv.DictReader(fh))

    @classmethod
    def from_json(cls, path: str) -> "OwnerRegistry":
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return cls.from_rows(data if isinstance(data, list) else data.get("owners", []))


def load_owner_registry(path: str) -> OwnerRegistry:
    return OwnerRegistry.from_json(path) if path.lower().endswith(".json") else OwnerRegistry.from_csv(path)


# ───────────────────────── source registry (reference) ─────────────────────
@dataclass
class SourceInfo:
    name: str
    url: str
    tier: str            # FREE-OPEN | FREE-WITH-AUTH | PAID | GATED
    auth: str
    wired: bool          # True = this module can call it from a bare container number
    note: str = ""


# This registry is the in-app record of what each enrichment source actually is:
# its address, access tier, auth, whether THIS app can call it from a bare
# container number ("wired"), and the operative caveat. Tiers and access facts
# are grounded in published carrier/agency documentation (2026). Exposed
# read-only at GET /sources.
#
# A hard distinction this list encodes: a carrier's free, no-login PUBLIC WEB
# TRACKING PAGE is NOT an integration target. Those pages are governed by Terms
# of Use that prohibit automated access; some carriers (COSCO) actively block
# it. Where a carrier offers an official self-service API, that is the
# compliant path and is what this app wires. Where it does not, the source is
# documented as GATED, not scraped.
SOURCES: List[SourceInfo] = [
    # ---- container-number-queryable: wired or one-config-away ----
    SourceInfo("bic-owner-register", "https://www.bic-code.org/bic-codes/", "FREE-OPEN",
               "none for the downloadable register; the letter-search web page returns 403 to "
               "scrapers, so use the register file or the BIC API",
               wired=True, note="Loaded offline from a CSV/JSON you download; maps owner prefix "
                                "-> company and powers free-text corroboration. No no-auth JSON API."),
    SourceInfo("bic-boxtech", "https://app.bic-boxtech.org/api/v2.0", "FREE-WITH-AUTH",
               "free account (email verification); OAuth2 POST /oauth/token Basic -> Bearer",
               wired=True, note="Self-service. GET /container/{n}: size/type, tare, owner "
                                "(code_holder), operator. Fair-use. Sandbox host uat.bic-boxtech.org."),
    SourceInfo("maersk", "https://api.maersk.com", "FREE-WITH-AUTH",
               "free Consumer-Key (self-service; 'no sales rep required'). Optional Bearer.",
               wired=True, note="Self-service Track & Trace Plus, DCSA v2.2. Covers Maersk + "
                                "Hamburg Sud/Sealand via MEC. PII masked unless you are the "
                                "bill-to/shipper. Free-tier call quota not publicly documented."),
    SourceInfo("cma-cgm", "https://apis.cma-cgm.net/operation/trackandtrace/v1", "FREE-WITH-AUTH",
               "Public tier: API key in KeyId header (self-service, free trial; key issued in "
               "~2-3 days, ~1-month trial). Private tier (rail/ramp/inland) needs OAuth2 + booking-party id.",
               wired=True, note="Self-service. GET /events?equipmentReference={n}, DCSA v2.2.0. "
                                "~6-12h data lag; B/L returns more events than a bare container number."),
    SourceInfo("hapag-lloyd", "https://api-portal.hlag.com/", "FREE-WITH-AUTH",
               "free account + verification + app approval (up to ~1 week). Labeled BETA.",
               wired=False, note="Self-service but BETA (Hapag advises trusting the website over the "
                                 "API until the next release). DCSA v2.2.4 events by equipmentReference. "
                                 "Wire via DcsaEventsEnricher with your gateway base + auth."),
    SourceInfo("terminal49", "https://api.terminal49.com/v2", "FREE-WITH-AUTH",
               "free Developer Key (email only; no credit card, no sales call). Token auth.",
               wired=False, note="Free tier tracks up to ~10-50 active containers; full API is paid. "
                                 "Async create-then-poll AND requires a SCAC, so it cannot run from a "
                                 "bare container number -- see Terminal49Enricher."),
    # ---- carriers WITHOUT self-service APIs: gated, never scraped ----
    SourceInfo("msc", "https://www.msc.com/track-a-shipment", "GATED",
               "no public self-service tracking API (myMSC portal / EDI onboarding)",
               wired=False, note="Public web box is one-at-a-time and ToS-restricted; not an "
                                 "integration target. Use an aggregator for MSC if you must automate."),
    SourceInfo("cosco", "https://elines.coscoshipping.com/", "GATED",
               "no self-service public API; portal documented to BLOCK automated access",
               wired=False, note="Do not scrape. Public box allows <=6 containers manually. Reach "
                                 "COSCO events through a commercial aggregator instead."),
    SourceInfo("one-line", "https://ecomm.one-line.com/", "GATED",
               "no self-service public API (eCommerce / EDI)", wired=False,
               note="Public box accepts multiple identifiers manually; automation needs an aggregator."),
    SourceInfo("zim", "https://api.zim.com/", "FREE-WITH-AUTH",
               "API key (registration)", wired=False,
               note="No self-service public tracking API documented; if your account exposes a DCSA "
                    "gateway, wire it via DcsaEventsEnricher. Otherwise use an aggregator."),
    SourceInfo("evergreen-oocl-yangming-hmm", "https://www.shipmentlink.com/", "GATED",
               "no self-service public APIs (customer/EDI onboarding)", wired=False,
               note="Evergreen/OOCL/Yang Ming/HMM gate APIs behind customer relationships; "
                    "public boxes are one-identifier and ToS-restricted. Aggregator for automation."),
    # ---- aggregators (paid or freemium-with-account) ----
    SourceInfo("vizion", "https://www.vizionapi.com/", "PAID",
               "API key (no free tier)", wired=False,
               note="Normalized multi-carrier events; the value is covering carriers that block "
                    "direct automation. Confirm endpoint in account, then DcsaEventsEnricher or custom."),
    SourceInfo("shipsgo", "https://shipsgo.com/", "FREE-WITH-AUTH",
               "API key (freemium; registration required, no anonymous lookup)", wired=False,
               note="Confirm endpoint in account."),
    SourceInfo("searates", "https://www.searates.com/", "FREE-WITH-AUTH",
               "API key (freemium)", wired=False, note="Confirm endpoint in account."),
    SourceInfo("jsoncargo", "https://jsoncargo.com/", "FREE-WITH-AUTH",
               "API key (freemium); per-carrier tracking APIs incl. CMA CGM", wired=False,
               note="Confirm endpoint in account."),
    SourceInfo("terminal49-aggregator-note", "https://www.terminal49.com/", "FREE-WITH-AUTH",
               "see terminal49 above", wired=False,
               note="Most generous free aggregator tier (~50 containers/mo) but needs an email account "
                    "and the SCAC+async flow; not auto-wired."),
    SourceInfo("apm-terminals", "https://www.apmterminals.com/", "FREE-WITH-AUTH",
               "free account; OAuth2", wired=False,
               note="Import Availability API keys on a FACILITY code (e.g. USLAX), not a bare "
                    "container number -- terminal-availability, not container enrichment."),
    # ---- no-account REFERENCE / context datasets: NOT container-keyed ----
    SourceInfo("imf-portwatch", "https://services9.arcgis.com/weJ1QsnbMYJlCHdG/ArcGIS/rest/"
               "services/Daily_Ports_Data/FeatureServer/0", "FREE-OPEN",
               "NONE -- open ArcGIS GeoServices API, no account",
               wired=False, note="Port-level only (portcalls/import by port & ISO3 country), updated "
                                 "weekly Tue 9AM ET, 2065 ports. Cannot enrich a container number; "
                                 "exposed as a standalone port-context lookup (see /portwatch helper). "
                                 "The cleanest truly-anonymous maritime API."),
    SourceInfo("eurostat-comext", "https://ec.europa.eu/eurostat/api/dissemination", "FREE-OPEN",
               "NONE -- SDMX REST API, no account", wired=False,
               note="EU trade-in-goods statistics (HS-code/country level), not container-keyed. "
                    "Macro trade context only."),
    SourceInfo("un-comtrade", "https://comtradeapi.un.org/", "FREE-WITH-AUTH",
               "anonymous: 500 records/call; free key: 500 calls/day, 100k records/call", wired=False,
               note="HS-code/country trade flows, not container-keyed. Macro context only."),
    SourceInfo("noaa-marinecadastre", "https://marinecadastre.gov/ais/", "FREE-OPEN",
               "NONE -- CC0 public-domain bulk download", wired=False,
               note="Historic AIS, US waters only, keyed by MMSI/vessel -- not by container number."),
    SourceInfo("aisstream", "wss://stream.aisstream.io/v0/stream", "FREE-WITH-AUTH",
               "free API key (do not expose client-side)", wired=False,
               note="Live AIS by MMSI/vessel, WebSocket; max 1 sub update/sec, sub message within 3s. "
                    "Vessel positions, not container enrichment."),
]


# ──────────────────────────── HTTP enricher base ───────────────────────────
class EnricherError(RuntimeError):
    """Configuration/usage error for an enricher (loud; not a transient network fault)."""


def _dcsa_latest_event(data, source: str) -> Dict[str, str]:
    """Map a DCSA Track & Trace events response to a flat enrichment dict (latest event)."""
    events = data.get("events", data) if isinstance(data, dict) else data
    if not isinstance(events, list) or not events:
        return {}
    latest = max(events, key=lambda ev: ev.get("eventDateTime") or ev.get("eventCreatedDateTime") or "")
    code = (latest.get("equipmentEventTypeCode") or latest.get("transportEventTypeCode")
            or latest.get("shipmentEventTypeCode"))
    loc = latest.get("eventLocation") or {}
    vessel = (latest.get("transportCall") or {}).get("vessel") or {}
    out = {
        "last_event_type": latest.get("eventType"),
        "last_event_code": code,
        "last_event_time": latest.get("eventDateTime"),
        "last_location": loc.get("UNLocationCode") or loc.get("locationName"),
        "iso_equipment_code": latest.get("ISOEquipmentCode"),
        "vessel_name": vessel.get("vesselName"),
        "vessel_imo": vessel.get("vesselIMONumber"),
        "source": source,
    }
    return {k: v for k, v in out.items() if v}


class _HttpEnricher:
    name = "http"

    #: last successful JSON body fetched by THIS adapter instance. Captured so
    #: EnrichmentService.enrich_detailed can warehouse the FULL return without
    #: changing any adapter's enrich() contract. Adapter instances are used
    #: serially (the background task loops containers one at a time); this is
    #: not a thread-safe mechanism and is documented as such.
    _last_payload = None

    def __init__(self, *, timeout: float = 8.0):
        self.timeout = timeout

    @property
    def enabled(self) -> bool:
        return False

    def enrich(self, eqid: str) -> Dict[str, str]:
        return {}

    # -- HTTP helpers (httpx lazy-imported; unit tests monkeypatch these) ---- #
    def _httpx(self):
        try:
            import httpx
        except ModuleNotFoundError as exc:                  # fail loud, don't silently no-op
            raise EnricherError(
                f"{self.name}: live enrichment needs httpx (pip install 'httpx>=0.27')") from exc
        return httpx

    def _get_json(self, url, *, headers=None, params=None, auth=None):
        httpx = self._httpx()
        r = httpx.get(url, headers=headers, params=params, auth=auth, timeout=self.timeout)
        r.raise_for_status()
        self._last_payload = r.json()
        return r.json()

    def _post_json(self, url, *, headers=None, json_body=None, auth=None):
        httpx = self._httpx()
        r = httpx.post(url, headers=headers, json=json_body, auth=auth, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    @staticmethod
    def _status_of(exc):
        return getattr(getattr(exc, "response", None), "status_code", None)

    def _is_unauthorized(self, exc):
        return self._status_of(exc) in (401, 403)

    def _is_not_found(self, exc):
        return self._status_of(exc) == 404


# ─────────────────────────────── BoxTech ───────────────────────────────────
class BoxTechEnricher(_HttpEnricher):
    """
    BIC BoxTech global container database -> technical details by container number.

    +-- AUTH: BIC BoxTech ---------------------------------------------------+
    | Register: https://app.bic-boxtech.org/sign-up  (live)                  |
    |           https://uat.bic-boxtech.org/sign-up  (sandbox)               |
    | OAuth2: POST {base}/oauth/token with HTTP Basic (your BoxTech          |
    |         username:password) -> {"accessToken": "..."} used as Bearer.   |
    | >>> SET ENV:  BOXTECH_USERNAME   BOXTECH_PASSWORD                       |
    |     (optional BOXTECH_ENV=uat to use the sandbox host)                  |
    +------------------------------------------------------------------------+
    Endpoint verified from the published OpenAPI spec (boxtech-api-v2.json):
        GET /container/{containerNumber}  ->  code_holder, current_operator,
                                              detail_st, group_st, manufacture_date, ...
    """
    name = "bic-boxtech"
    LIVE = "https://app.bic-boxtech.org/api/v2.0"
    UAT = "https://uat.bic-boxtech.org/api/v2.0"

    def __init__(self, username=None, password=None, *, sandbox=False, **kw):
        super().__init__(**kw)
        self.username = username
        self.password = password
        self.base = self.UAT if sandbox else self.LIVE
        self._token: Optional[str] = None

    @property
    def enabled(self) -> bool:
        return bool(self.username and self.password)

    def _bearer(self) -> str:
        if self._token:
            return self._token
        data = self._post_json(f"{self.base}/oauth/token", auth=(self.username, self.password))
        token = data.get("accessToken") or data.get("access_token")
        if not token:
            raise EnricherError("boxtech: /oauth/token response did not contain an access token")
        self._token = token
        return token

    def enrich(self, eqid: str) -> Dict[str, str]:
        last = None
        for attempt in (1, 2):
            token = self._bearer()
            try:
                data = self._get_json(f"{self.base}/container/{eqid}",
                                      headers={"Authorization": f"Bearer {token}"})
            except Exception as exc:
                if self._is_not_found(exc):
                    return {}                              # container simply not in BoxTech
                if attempt == 1 and self._is_unauthorized(exc):
                    self._token = None                      # token expired -> refresh once
                    last = exc
                    continue
                raise
            out = {
                "owner_name": data.get("code_holder") or data.get("codeholder"),
                "operator": data.get("current_operator"),
                "size_type": data.get("detail_st"),
                "group_type": data.get("group_st"),
                "manufacture_date": data.get("manufacture_date"),
                "source": self.name,
            }
            return {k: v for k, v in out.items() if v}
        raise last  # pragma: no cover


# ─────────────────────────────── Maersk ────────────────────────────────────
class MaerskEnricher(_HttpEnricher):
    """
    Maersk public ocean Track & Trace (DCSA v2.2) -> latest event by container.

    +-- AUTH: Maersk --------------------------------------------------------+
    | Register an app: https://developer.maersk.com  (free self-service).    |
    | You receive a Consumer-Key, sent as the `Consumer-Key` header.         |
    | >>> SET ENV:  MAERSK_CONSUMER_KEY                                       |
    |     (optional MAERSK_BEARER if your product also needs an OAuth token,  |
    |      optional MAERSK_TT_PATH to override the events path,               |
    |      optional MAERSK_PREFIXES="MAE,MSK,MRK,SEU" to only query Maersk    |
    |      boxes and skip others)                                             |
    +------------------------------------------------------------------------+
    Host verified: https://api.maersk.com . The SDK documents
        GET /track-and-trace-private/events?equipmentReference={n}
    A public variant may apply to your key -- confirm the exact path in your portal.
    """
    name = "maersk"
    BASE = "https://api.maersk.com"
    DEFAULT_PATH = "/track-and-trace-private/events"

    def __init__(self, consumer_key=None, *, bearer=None, path=None, base=None, prefixes=None, **kw):
        super().__init__(**kw)
        self.consumer_key = consumer_key
        self.bearer = bearer
        self.base = base or self.BASE
        self.path = path or self.DEFAULT_PATH
        self.prefixes = {p.upper() for p in prefixes} if prefixes else None

    @property
    def enabled(self) -> bool:
        return bool(self.consumer_key)

    def enrich(self, eqid: str) -> Dict[str, str]:
        if self.prefixes and eqid[:3].upper() not in self.prefixes:
            return {}
        headers = {"Consumer-Key": self.consumer_key}
        if self.bearer:
            headers["Authorization"] = f"Bearer {self.bearer}"
        try:
            data = self._get_json(f"{self.base}{self.path}", headers=headers,
                                  params={"equipmentReference": eqid})
        except Exception as exc:
            if self._is_not_found(exc):
                return {}
            raise
        return _dcsa_latest_event(data, self.name)


# ───────────────────── generic DCSA carrier (you supply base+auth) ─────────
class CmaCgmEnricher(_HttpEnricher):
    """
    CMA CGM Public-tier Track & Trace (DCSA v2.2.0) -> latest event by container.
    Self-service: register at the developer portal, get an API key, start calling
    (a free trial applies; scaling to production volume needs a commercial contact).
    The Public tier returns equipment moves, transshipment events, and planned
    vessel dates -- the same data as the portal, in structured form.

    +-- AUTH: CMA CGM --------------------------------------------------------+
    | Register: https://api-portal.cma-cgm.com/  (Public tier, free trial).   |
    | Auth: API key sent in the `KeyId` header by default. Some portal apps   |
    | instead expect the key as a Bearer token -- if so, set                  |
    | CMACGM_KEY_HEADER=Authorization and CMACGM_KEY_PREFIX="Bearer ".         |
    | >>> SET ENV:  CMACGM_API_KEY                                            |
    |     (optional CMACGM_KEY_HEADER, CMACGM_KEY_PREFIX, CMACGM_BASE_URL,     |
    |      CMACGM_PREFIXES="CMAU,CGMU,APLU,ECMU" to scope to CMA-group boxes)  |
    +------------------------------------------------------------------------+
    Endpoint verified from CMA CGM's published DCSA v2.2.0 client (api-portal.cma-cgm.com):
        GET {base}/events?equipmentReference={n}
        base = https://apis.cma-cgm.net/operation/trackandtrace/v1
    NOTE: CMA CGM returns more events for a B/L than for a bare container number,
    and the feed lags the vessel by several hours -- expect sparse/None at times.
    """
    name = "cma-cgm"
    BASE = "https://apis.cma-cgm.net/operation/trackandtrace/v1"

    def __init__(self, api_key=None, *, base=None, key_header="KeyId",
                 key_prefix="", prefixes=None, **kw):
        super().__init__(**kw)
        self.api_key = api_key
        self.base = base or self.BASE
        self.key_header = key_header or "KeyId"
        self.key_prefix = key_prefix or ""
        self.prefixes = {p.upper() for p in prefixes} if prefixes else None

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def enrich(self, eqid: str) -> Dict[str, str]:
        if self.prefixes and eqid[:3].upper() not in self.prefixes:
            return {}
        headers = {self.key_header: f"{self.key_prefix}{self.api_key}"}
        try:
            data = self._get_json(f"{self.base}/events", headers=headers,
                                  params={"equipmentReference": eqid})
        except Exception as exc:
            if self._is_not_found(exc):
                return {}
            raise
        return _dcsa_latest_event(data, self.name)


class DcsaEventsEnricher(_HttpEnricher):
    """
    Generic DCSA Track & Trace events enricher for any carrier whose gateway base
    URL + auth you supply (Hapag-Lloyd, CMA CGM public tier, ZIM, ...). No carrier
    endpoint is baked in -- you point it at the verified base from YOUR portal app.

    +-- AUTH: per-carrier --------------------------------------------------+
    | Register at the carrier's developer portal (e.g. Hapag-Lloyd:          |
    |   https://api-portal.hlag.com/ , DCSA v2.2.4, self-service beta) and    |
    | note (a) the API gateway base URL and (b) how it authenticates.        |
    | >>> SET ENV (example for Hapag-Lloyd):                                  |
    |     HAPAG_BASE_URL=https://<your-gateway-base>                          |
    |     HAPAG_EVENTS_PATH=/events            (default; adjust per docs)      |
    |     HAPAG_AUTH_HEADER=Authorization  and  HAPAG_AUTH_VALUE=Bearer ...    |
    |     (or HAPAG_API_KEY_HEADER + HAPAG_API_KEY for a key-header carrier)   |
    +------------------------------------------------------------------------+
    Query shape (DCSA): GET {base}{events_path}?equipmentReference={container}.
    """
    def __init__(self, name, base_url, *, events_path="/events",
                 auth_header=None, auth_value=None, prefixes=None, **kw):
        super().__init__(**kw)
        self.name = name
        self.base_url = (base_url or "").rstrip("/")
        self.events_path = events_path or "/events"
        self.auth_header = auth_header
        self.auth_value = auth_value
        self.prefixes = {p.upper() for p in prefixes} if prefixes else None

    @property
    def enabled(self) -> bool:
        return bool(self.base_url)

    def enrich(self, eqid: str) -> Dict[str, str]:
        if self.prefixes and eqid[:3].upper() not in self.prefixes:
            return {}
        headers = {}
        if self.auth_header and self.auth_value:
            headers[self.auth_header] = self.auth_value
        try:
            data = self._get_json(f"{self.base_url}{self.events_path}", headers=headers,
                                  params={"equipmentReference": eqid})
        except Exception as exc:
            if self._is_not_found(exc):
                return {}
            raise
        return _dcsa_latest_event(data, self.name)


# ─────────────────────────────── Terminal49 ────────────────────────────────
class Terminal49Enricher(_HttpEnricher):
    """
    Terminal49 multi-carrier tracking. NOTE: this is an ASYNC create-then-poll API
    AND it requires a SCAC alongside the number, so it cannot run from a bare
    container number. It is NOT auto-wired into on-ingest enrichment for that
    reason; it is provided here with the verified endpoint for you to use directly.

    +-- AUTH: Terminal49 ---------------------------------------------------+
    | Get a free Developer Key in your account API settings.                 |
    | Auth header: `Authorization: Token <key>`  (note the "Token " prefix).  |
    | >>> SET ENV:  TERMINAL49_API_KEY                                        |
    +------------------------------------------------------------------------+
    Verified flow (https://api.terminal49.com/v2):
      POST /tracking_requests  (JSON:API body with request_type/request_number/scac)
      then poll GET /tracking_requests/{id}, then GET /shipments. Data arrives via
      webhook in production.
    """
    name = "terminal49"
    BASE = "https://api.terminal49.com/v2"

    def __init__(self, api_key=None, *, scac_resolver=None, **kw):
        super().__init__(**kw)
        self.api_key = api_key
        self.scac_resolver = scac_resolver   # callable(eqid)->SCAC, or None

    @property
    def enabled(self) -> bool:
        return bool(self.api_key and self.scac_resolver)

    def create_tracking_request(self, container: str, scac: str) -> Dict[str, str]:
        headers = {"Authorization": f"Token {self.api_key}",
                   "Content-Type": "application/vnd.api+json"}
        body = {"data": {"type": "tracking_request",
                         "attributes": {"request_type": "container",
                                        "request_number": container, "scac": scac}}}
        return self._post_json(f"{self.BASE}/tracking_requests", headers=headers, json_body=body)

    def enrich(self, eqid: str) -> Dict[str, str]:
        if not self.scac_resolver:
            return {}
        scac = self.scac_resolver(eqid)
        if not scac:
            return {}                          # cannot track a container without its carrier
        resp = self.create_tracking_request(eqid, scac)
        rid = (resp.get("data") or {}).get("id")
        return {"terminal49_request_id": rid, "terminal49_status": "submitted",
                "source": self.name} if rid else {}


# ───────────────────────────── service facade ──────────────────────────────
class EnrichmentService:
    """Combines the offline owner registry with any configured network enrichers."""

    def __init__(self, registry: Optional[OwnerRegistry] = None,
                 extra: Optional[List[_HttpEnricher]] = None):
        self.registry = registry
        self.extra = list(extra or [])

    @property
    def owner_prefixes(self) -> Set[str]:
        return self.registry.prefixes if self.registry else set()

    @property
    def has_external(self) -> bool:
        return any(e.enabled for e in self.extra)

    def enrich_owner(self, eqid: str) -> Dict[str, str]:
        """Offline, instant: owner identity from the registry only (no network)."""
        if not self.registry:
            return {}
        info = self.registry.owner_of(eqid)
        if not info:
            return {}
        out = {"owner_name": info.company, "owner_city": info.city,
               "owner_country": info.country, "source": info.source}
        return {k: v for k, v in out.items() if v}

    def enrich_detailed(self, eqid: str):
        """Query every enabled network source, returning BOTH views of the data:
        (triples, merged_flat) where triples = [(source_name, flat_dict, raw_payload)]
        -- one per SUCCESSFUL source, raw_payload being the complete JSON the
        source returned (None only if an adapter bypassed _get_json). Transient
        faults skip that source (and its payload is never collected, so e.g. a
        BoxTech token response from a failed call cannot leak into storage);
        configuration errors (EnricherError) propagate loudly."""
        triples = []
        out: Dict[str, str] = {}
        sources: List[str] = []
        for e in self.extra:
            if not e.enabled:
                continue
            e._last_payload = None                          # clear before the call
            try:
                d = e.enrich(eqid)
            except EnricherError:
                raise                                       # loud: misconfiguration
            except Exception:                               # network/HTTP/parse -> skip source
                continue
            if d:
                src_name = d.pop("source", e.name)
                sources.append(src_name)
                triples.append((src_name, dict(d), getattr(e, "_last_payload", None)))
                out.update({k: v for k, v in d.items() if v})
        if sources:
            out["source"] = ", ".join(dict.fromkeys(sources))
        return triples, out

    def enrich_external(self, eqid: str) -> Dict[str, str]:
        """Merged flat view across enabled network sources (see enrich_detailed)."""
        _, merged = self.enrich_detailed(eqid)
        return merged

    def enrich(self, eqid: str) -> Dict[str, str]:
        merged = self.enrich_owner(eqid)
        ext = self.enrich_external(eqid)
        src_parts: List[str] = []
        for s in (merged.get("source"), ext.get("source")):
            if s:
                src_parts.extend(s.split(", "))
        merged.update({k: v for k, v in ext.items() if k != "source" and v})
        if src_parts:
            merged["source"] = ", ".join(dict.fromkeys(src_parts))
        return merged

    # -- build the enabled set from environment variables -------------------- #
    @classmethod
    def build_from_env(cls, registry: Optional[OwnerRegistry] = None, env=None) -> "EnrichmentService":
        env = env or os.environ
        extra: List[_HttpEnricher] = []

        # BIC BoxTech ------------------------------------------------------- #
        if env.get("BOXTECH_USERNAME") and env.get("BOXTECH_PASSWORD"):
            extra.append(BoxTechEnricher(
                env["BOXTECH_USERNAME"], env["BOXTECH_PASSWORD"],
                sandbox=(env.get("BOXTECH_ENV", "live").lower() == "uat")))

        # Maersk ------------------------------------------------------------ #
        if env.get("MAERSK_CONSUMER_KEY"):
            extra.append(MaerskEnricher(
                env["MAERSK_CONSUMER_KEY"], bearer=env.get("MAERSK_BEARER"),
                path=env.get("MAERSK_TT_PATH"),
                prefixes=_split(env.get("MAERSK_PREFIXES"))))

        # CMA CGM (dedicated Public-tier adapter with verified endpoint) ----- #
        if env.get("CMACGM_API_KEY"):
            extra.append(CmaCgmEnricher(
                env["CMACGM_API_KEY"],
                base=env.get("CMACGM_BASE_URL"),
                key_header=env.get("CMACGM_KEY_HEADER", "KeyId"),
                key_prefix=env.get("CMACGM_KEY_PREFIX", ""),
                prefixes=_split(env.get("CMACGM_PREFIXES"))))

        # Generic DCSA carriers you point at your portal's gateway. CMACGM stays
        # here as a fallback ONLY if no dedicated CMACGM_API_KEY was given (so a
        # user who set up the generic path earlier keeps working).
        dcsa = [("HAPAG", "hapag-lloyd"), ("ZIM", "zim")]
        if not env.get("CMACGM_API_KEY"):
            dcsa.append(("CMACGM", "cma-cgm"))
        for prefix, name in dcsa:
            base = env.get(f"{prefix}_BASE_URL")
            if not base:
                continue
            header, value = _auth_header(env, prefix)
            extra.append(DcsaEventsEnricher(
                name, base, events_path=env.get(f"{prefix}_EVENTS_PATH", "/events"),
                auth_header=header, auth_value=value,
                prefixes=_split(env.get(f"{prefix}_PREFIXES"))))

        return cls(registry=registry, extra=extra)


def _split(value: Optional[str]) -> Optional[List[str]]:
    return [p.strip() for p in value.split(",") if p.strip()] if value else None


def _auth_header(env, prefix: str):
    """Resolve (header_name, header_value) for a DCSA carrier from env."""
    if env.get(f"{prefix}_AUTH_HEADER") and env.get(f"{prefix}_AUTH_VALUE"):
        return env[f"{prefix}_AUTH_HEADER"], env[f"{prefix}_AUTH_VALUE"]
    if env.get(f"{prefix}_API_KEY_HEADER") and env.get(f"{prefix}_API_KEY"):
        return env[f"{prefix}_API_KEY_HEADER"], env[f"{prefix}_API_KEY"]
    if env.get(f"{prefix}_BEARER"):
        return "Authorization", f"Bearer {env[f'{prefix}_BEARER']}"
    return None, None


def from_app_env(env=None) -> "EnrichmentService":
    """
    Application bootstrap shared by api.py and watch_folder.py (single source so
    headless drops get the exact same corroboration/enrichment as web uploads):
    load the owner registry named by CHECKDIGIT_OWNER_REGISTRY -- failing LOUDLY
    if it is set but missing -- then enable external enrichers from credentials
    in the environment (see each enricher's AUTH block).
    """
    env = env or os.environ
    registry = None
    path = env.get("CHECKDIGIT_OWNER_REGISTRY")
    if path:
        if not os.path.exists(path):
            raise RuntimeError(
                f"CHECKDIGIT_OWNER_REGISTRY is set to {path!r} but no such file "
                f"exists. Provide the register there, or unset the variable to run "
                f"without enrichment.")
        registry = load_owner_registry(path)
    return EnrichmentService.build_from_env(registry=registry, env=env)


# ───────────────────── PortWatch (no-account port context) ──────────────────
class PortWatchClient(_HttpEnricher):
    """
    IMF PortWatch port-activity lookup. Genuinely NO ACCOUNT: an open ArcGIS
    GeoServices FeatureServer. This is NOT container enrichment -- PortWatch is
    keyed by port and country (ISO3), not by container number -- so it is
    deliberately kept OUT of the per-container enrichment loop and exposed only
    as an explicit, on-demand port-context utility (api.py: GET /portwatch).

    Endpoint verified from IMF's published ArcGIS service + R tutorial:
        {BASE}/query?where=...&outFields=...&f=json
        BASE = .../Daily_Ports_Data/FeatureServer/0
    Fields include: date, portid, portname, country, ISO3, portcalls_container,
    import_container, export_container, portcalls. Updated weekly (Tue 9AM ET).
    No auth, fair public use; data is port-level activity, not shipment-level.
    """
    name = "imf-portwatch"
    BASE = ("https://services9.arcgis.com/weJ1QsnbMYJlCHdG/ArcGIS/rest/"
            "services/Daily_Ports_Data/FeatureServer/0")

    def latest_for_country(self, iso3: str, *, limit: int = 25) -> List[dict]:
        """Most-recent port-activity rows for an ISO3 country code (e.g. 'USA')."""
        iso3 = (iso3 or "").upper()
        if not (len(iso3) == 3 and iso3.isalpha()):
            raise EnricherError("portwatch: country must be a 3-letter ISO3 code")
        params = {
            "where": f"ISO3='{iso3}'",
            "outFields": "date,portid,portname,country,ISO3,portcalls_container,"
                         "import_container,export_container,portcalls",
            "orderByFields": "date DESC", "resultRecordCount": limit,
            "returnGeometry": "false", "f": "json"}
        data = self._get_json(f"{self.BASE}/query", params=params)
        return [f.get("attributes", {}) for f in data.get("features", [])]
