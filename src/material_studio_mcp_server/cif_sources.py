"""Secure, reproducible CIF and Crystallography Open Database ingestion.

This module is deliberately independent from the MCP server and project state
store.  Callers can preview a request without network or filesystem effects,
then explicitly execute it and decide separately whether an imported artifact
should become part of a model revision.
"""

from __future__ import annotations

import hashlib
import http.client
import ipaddress
import json
import os
import re
import secrets
import shutil
import socket
import ssl
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence
from urllib.parse import urlencode, urljoin, urlsplit, urlunsplit


DEFAULT_ALLOWED_CIF_HOSTS = (
    "www.crystallography.net",
    "crystallography.net",
)
DEFAULT_MAX_CIF_BYTES = 8 * 1024 * 1024
DEFAULT_MAX_SEARCH_BYTES = 2 * 1024 * 1024
DEFAULT_REDIRECT_LIMIT = 4
DEFAULT_TIMEOUT_SECONDS = 20.0

_COD_ID_RE = re.compile(r"^[0-9]{6,9}$")
_FORMULA_TOKEN_RE = re.compile(r"^([A-Z][a-z]?)([1-9][0-9]*)?$")
_CIF_DATA_BLOCK_RE = re.compile(r"(?mi)^[ \t]*data_([^\s#]+)")
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_DISALLOWED_CONTENT_TYPES = (
    "text/html",
    "application/xhtml+xml",
    "application/json",
    "application/xml",
    "text/xml",
)
_CHEMICAL_ELEMENT_SYMBOLS = frozenset(
    {
        "Ac",
        "Ag",
        "Al",
        "Am",
        "Ar",
        "As",
        "At",
        "Au",
        "B",
        "Ba",
        "Be",
        "Bh",
        "Bi",
        "Bk",
        "Br",
        "C",
        "Ca",
        "Cd",
        "Ce",
        "Cf",
        "Cl",
        "Cm",
        "Cn",
        "Co",
        "Cr",
        "Cs",
        "Cu",
        "Db",
        "Ds",
        "Dy",
        "Er",
        "Es",
        "Eu",
        "F",
        "Fe",
        "Fl",
        "Fm",
        "Fr",
        "Ga",
        "Gd",
        "Ge",
        "H",
        "He",
        "Hf",
        "Hg",
        "Ho",
        "Hs",
        "I",
        "In",
        "Ir",
        "K",
        "Kr",
        "La",
        "Li",
        "Lr",
        "Lu",
        "Lv",
        "Mc",
        "Md",
        "Mg",
        "Mn",
        "Mo",
        "Mt",
        "N",
        "Na",
        "Nb",
        "Nd",
        "Ne",
        "Nh",
        "Ni",
        "No",
        "Np",
        "O",
        "Og",
        "Os",
        "P",
        "Pa",
        "Pb",
        "Pd",
        "Pm",
        "Po",
        "Pr",
        "Pt",
        "Pu",
        "Ra",
        "Rb",
        "Re",
        "Rf",
        "Rg",
        "Rh",
        "Rn",
        "Ru",
        "S",
        "Sb",
        "Sc",
        "Se",
        "Sg",
        "Si",
        "Sm",
        "Sn",
        "Sr",
        "Ta",
        "Tb",
        "Tc",
        "Te",
        "Th",
        "Ti",
        "Tl",
        "Tm",
        "Ts",
        "U",
        "V",
        "W",
        "Xe",
        "Y",
        "Yb",
        "Zn",
        "Zr",
    }
)


class CifSourceError(ValueError):
    """Raised when a CIF source violates an ingestion safety contract."""


Resolver = Callable[..., Sequence[tuple[Any, ...]]]
ResponseOpener = Callable[[str, str, float, Mapping[str, str]], Any]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _normalize_hostname(hostname: str) -> str:
    candidate = hostname.strip().rstrip(".")
    if not candidate:
        raise CifSourceError("HTTPS source hostname is empty.")
    if any(ord(character) < 33 or ord(character) == 127 for character in candidate):
        raise CifSourceError("HTTPS source hostname contains control or whitespace characters.")
    try:
        return candidate.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise CifSourceError("HTTPS source hostname is not valid IDNA.") from exc


def _normalize_allowlist(allowed_hosts: Iterable[str]) -> tuple[str, ...]:
    normalized = tuple(sorted({_normalize_hostname(host) for host in allowed_hosts}))
    if not normalized:
        raise CifSourceError("At least one explicit HTTPS hostname must be allowlisted.")
    return normalized


def _validate_https_url(
    url: str,
    *,
    allowed_hosts: Iterable[str],
) -> dict[str, Any]:
    if not isinstance(url, str) or not url:
        raise CifSourceError("HTTPS source URL must be a non-empty string.")
    if len(url) > 4096:
        raise CifSourceError("HTTPS source URL exceeds 4096 characters.")
    if "\\" in url or any(ord(character) < 32 or ord(character) == 127 for character in url):
        raise CifSourceError("HTTPS source URL contains an unsafe character.")

    try:
        split = urlsplit(url)
        port = split.port
    except ValueError as exc:
        raise CifSourceError("HTTPS source URL is malformed.") from exc

    if split.scheme.lower() != "https":
        raise CifSourceError("Only HTTPS CIF sources are allowed.")
    if not split.netloc or split.hostname is None:
        raise CifSourceError("HTTPS source URL must contain a hostname.")
    if split.username is not None or split.password is not None:
        raise CifSourceError("Credentials in HTTPS source URLs are forbidden.")
    if port not in (None, 443):
        raise CifSourceError("Custom HTTPS ports are forbidden.")
    if split.fragment:
        raise CifSourceError("URL fragments are forbidden for CIF sources.")

    hostname = _normalize_hostname(split.hostname)
    normalized_allowlist = _normalize_allowlist(allowed_hosts)
    if hostname not in normalized_allowlist:
        raise CifSourceError(
            f"HTTPS source hostname {hostname!r} is not in the explicit allowlist."
        )

    path = split.path or "/"
    normalized_netloc = f"[{hostname}]" if ":" in hostname else hostname
    normalized_url = urlunsplit(("https", normalized_netloc, path, split.query, ""))
    return {
        "url": normalized_url,
        "hostname": hostname,
        "port": 443,
        "path_and_query": path + (f"?{split.query}" if split.query else ""),
        "allowed_hosts": list(normalized_allowlist),
    }


def _address_is_forbidden(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = [address]
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        addresses.append(address.ipv4_mapped)
    return any(
        item.is_private
        or item.is_loopback
        or item.is_link_local
        or item.is_multicast
        or item.is_reserved
        or item.is_unspecified
        or not item.is_global
        for item in addresses
    )


def _resolve_public_addresses(
    hostname: str,
    *,
    resolver: Resolver,
) -> tuple[list[str], dict[str, Any]]:
    try:
        answers = resolver(
            hostname,
            443,
            family=socket.AF_UNSPEC,
            type=socket.SOCK_STREAM,
            proto=socket.IPPROTO_TCP,
        )
    except OSError as exc:
        raise CifSourceError(f"DNS resolution failed for {hostname!r}.") from exc

    addresses: list[str] = []
    for answer in answers:
        if len(answer) < 5 or not answer[4]:
            continue
        raw_address = str(answer[4][0]).split("%", 1)[0]
        try:
            parsed_address = ipaddress.ip_address(raw_address)
        except ValueError as exc:
            raise CifSourceError(
                f"DNS returned an invalid IP address for {hostname!r}."
            ) from exc
        canonical = str(parsed_address)
        if canonical not in addresses:
            addresses.append(canonical)

    if not addresses:
        raise CifSourceError(f"DNS returned no usable addresses for {hostname!r}.")

    forbidden = [
        address
        for address in addresses
        if _address_is_forbidden(ipaddress.ip_address(address))
    ]
    if forbidden:
        raise CifSourceError(
            f"DNS for {hostname!r} returned a forbidden non-public address."
        )

    return addresses, {
        "hostname": hostname,
        "port": 443,
        "addresses": addresses,
        "all_addresses_public": True,
    }


class _PinnedResponse:
    """Close the HTTP response and its one-use pinned connection together."""

    def __init__(self, response: http.client.HTTPResponse, connection: http.client.HTTPSConnection):
        self._response = response
        self._connection = connection
        self.status = response.status
        self.headers = response.headers

    def read(self, amount: int = -1) -> bytes:
        return self._response.read(amount)

    def close(self) -> None:
        try:
            self._response.close()
        finally:
            self._connection.close()


def _open_pinned_https(
    url: str,
    approved_ip: str,
    timeout_seconds: float,
    headers: Mapping[str, str],
) -> _PinnedResponse:
    validated = urlsplit(url)
    hostname = validated.hostname
    if hostname is None:
        raise CifSourceError("Pinned HTTPS request has no hostname.")

    raw_socket = socket.create_connection((approved_ip, 443), timeout=timeout_seconds)
    connection: http.client.HTTPSConnection | None = None
    try:
        context = ssl.create_default_context()
        tls_socket = context.wrap_socket(raw_socket, server_hostname=hostname)
        connection = http.client.HTTPSConnection(
            hostname,
            port=443,
            timeout=timeout_seconds,
            context=context,
        )
        connection.sock = tls_socket
        target = (validated.path or "/") + (
            f"?{validated.query}" if validated.query else ""
        )
        connection.request("GET", target, headers=dict(headers))
        response = connection.getresponse()
        return _PinnedResponse(response, connection)
    except Exception:
        if connection is not None:
            connection.close()
        else:
            raw_socket.close()
        raise


def _response_header(response: Any, name: str) -> str | None:
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    value = headers.get(name)
    return None if value is None else str(value).strip()


def fetch_https_bytes(
    url: str,
    *,
    allowed_hosts: Iterable[str] = DEFAULT_ALLOWED_CIF_HOSTS,
    max_bytes: int = DEFAULT_MAX_CIF_BYTES,
    max_redirects: int = DEFAULT_REDIRECT_LIMIT,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    accept: str = "chemical/x-cif, text/plain;q=0.9, application/octet-stream;q=0.5",
    resolver: Resolver = socket.getaddrinfo,
    opener: ResponseOpener = _open_pinned_https,
) -> dict[str, Any]:
    """Fetch bounded bytes after validating every URL and DNS hop.

    The default transport connects to one already-reviewed IP while retaining
    the original hostname for TLS certificate verification and SNI.
    """

    if not isinstance(max_bytes, int) or max_bytes < 1:
        raise CifSourceError("max_bytes must be a positive integer.")
    if not isinstance(max_redirects, int) or not 0 <= max_redirects <= 10:
        raise CifSourceError("max_redirects must be between 0 and 10.")
    if not isinstance(timeout_seconds, (int, float)) or not 0 < timeout_seconds <= 120:
        raise CifSourceError("timeout_seconds must be in the range (0, 120].")

    requested_url = url
    current_url = url
    redirect_chain: list[dict[str, Any]] = []
    dns_receipts: list[dict[str, Any]] = []
    visited: set[str] = set()

    for hop_index in range(max_redirects + 1):
        validated = _validate_https_url(current_url, allowed_hosts=allowed_hosts)
        normalized_url = validated["url"]
        if normalized_url in visited:
            raise CifSourceError("HTTPS redirect loop detected.")
        visited.add(normalized_url)

        addresses, dns_receipt = _resolve_public_addresses(
            validated["hostname"],
            resolver=resolver,
        )
        dns_receipt["url"] = normalized_url
        dns_receipts.append(dns_receipt)

        response = opener(
            normalized_url,
            addresses[0],
            float(timeout_seconds),
            {
                "Accept": accept,
                "Accept-Encoding": "identity",
                "Connection": "close",
                "User-Agent": "materials-studio-mcp-cif/1",
            },
        )
        try:
            status = int(getattr(response, "status"))
            if status in _REDIRECT_STATUSES:
                if hop_index >= max_redirects:
                    raise CifSourceError("HTTPS redirect limit exceeded.")
                location = _response_header(response, "Location")
                if not location or len(location) > 4096:
                    raise CifSourceError("HTTPS redirect has no safe Location header.")
                next_url = urljoin(normalized_url, location)
                # Validate immediately; DNS is deliberately refreshed on the next hop.
                next_validated = _validate_https_url(
                    next_url,
                    allowed_hosts=allowed_hosts,
                )
                redirect_chain.append(
                    {
                        "status": status,
                        "from_url": normalized_url,
                        "to_url": next_validated["url"],
                    }
                )
                current_url = next_validated["url"]
                continue

            if status != 200:
                raise CifSourceError(f"HTTPS source returned HTTP status {status}.")

            content_encoding = (_response_header(response, "Content-Encoding") or "identity").lower()
            if content_encoding not in ("", "identity"):
                raise CifSourceError("Compressed HTTP responses are not accepted.")

            content_length_header = _response_header(response, "Content-Length")
            if content_length_header is not None:
                try:
                    content_length = int(content_length_header, 10)
                except ValueError as exc:
                    raise CifSourceError("HTTP Content-Length is invalid.") from exc
                if content_length < 0 or content_length > max_bytes:
                    raise CifSourceError("HTTPS source exceeds the configured byte limit.")

            payload = bytearray()
            while True:
                remaining_probe = max_bytes + 1 - len(payload)
                if remaining_probe <= 0:
                    raise CifSourceError("HTTPS source exceeds the configured byte limit.")
                chunk = response.read(min(64 * 1024, remaining_probe))
                if not isinstance(chunk, (bytes, bytearray)):
                    raise CifSourceError("HTTPS transport returned a non-byte response.")
                if not chunk:
                    break
                payload.extend(chunk)
                if len(payload) > max_bytes:
                    raise CifSourceError("HTTPS source exceeds the configured byte limit.")

            return {
                "requested_url": requested_url,
                "final_url": normalized_url,
                "redirect_chain": redirect_chain,
                "dns_receipts": dns_receipts,
                "content_type": _response_header(response, "Content-Type"),
                "content_length": len(payload),
                "payload": bytes(payload),
            }
        finally:
            response.close()

    raise CifSourceError("HTTPS redirect limit exceeded.")


def cod_cif_url(cod_id: str | int) -> str:
    """Return the canonical HTTPS CIF URL for a validated numeric COD ID."""

    normalized = str(cod_id)
    if not _COD_ID_RE.fullmatch(normalized):
        raise CifSourceError("COD IDs must contain exactly 6 to 9 decimal digits.")
    return f"https://www.crystallography.net/cod/{normalized}.cif"


def validate_hill_formula(formula: str) -> str:
    """Validate COD's space-separated empirical Hill-notation formula."""

    if not isinstance(formula, str) or not formula:
        raise CifSourceError("COD formula must be a non-empty string.")
    if len(formula) > 200:
        raise CifSourceError("COD formula may not exceed 200 characters.")
    if formula != formula.strip():
        raise CifSourceError("COD formula may not have leading or trailing whitespace.")
    if any(ord(character) < 32 or ord(character) > 126 for character in formula):
        raise CifSourceError("COD formula must contain printable ASCII characters only.")

    raw_tokens = formula.split(" ")
    if any(not token for token in raw_tokens):
        raise CifSourceError(
            "COD formula element tokens must be separated by one ASCII space."
        )

    symbols: list[str] = []
    token_by_symbol: dict[str, str] = {}
    for token in raw_tokens:
        match = _FORMULA_TOKEN_RE.fullmatch(token)
        if match is None:
            raise CifSourceError(
                "COD formula tokens must be an element symbol with an optional "
                "positive integer count."
            )
        symbol, count = match.groups()
        if symbol not in _CHEMICAL_ELEMENT_SYMBOLS:
            raise CifSourceError(f"Unknown chemical element symbol {symbol!r}.")
        if count == "1":
            raise CifSourceError("Hill notation omits an explicit element count of 1.")
        if symbol in token_by_symbol:
            raise CifSourceError(f"Duplicate chemical element symbol {symbol!r}.")
        symbols.append(symbol)
        token_by_symbol[symbol] = token

    if "C" in token_by_symbol:
        expected_symbols = ["C"]
        if "H" in token_by_symbol:
            expected_symbols.append("H")
        expected_symbols.extend(
            sorted(symbol for symbol in symbols if symbol not in {"C", "H"})
        )
    else:
        expected_symbols = sorted(symbols)
    if symbols != expected_symbols:
        expected_formula = " ".join(
            token_by_symbol[symbol] for symbol in expected_symbols
        )
        raise CifSourceError(
            "COD formula element symbols are not in Hill order; "
            f"expected {expected_formula!r}."
        )
    return formula


def _normalize_cod_text(*, text: str | None, query: str | None) -> str:
    if text is not None and query is not None:
        raise CifSourceError("Supply only text; query is a compatibility alias.")
    selected = text if text is not None else query
    if selected is None:
        return ""
    if not isinstance(selected, str):
        raise CifSourceError("COD text search must be a string.")
    normalized = selected.strip()
    if not normalized:
        raise CifSourceError("COD text search must not be empty.")
    if len(normalized) > 200:
        raise CifSourceError("COD text search may not exceed 200 characters.")
    if any(ord(character) < 32 or ord(character) == 127 for character in normalized):
        raise CifSourceError("COD text search contains control characters.")
    return normalized


def build_cod_search_url(
    *,
    text: str | None = None,
    query: str | None = None,
    formula: str | None = None,
    max_results: int = 10,
) -> str:
    """Build a bounded COD JSON-search URL."""

    normalized_text = _normalize_cod_text(text=text, query=query)
    normalized_formula = (
        validate_hill_formula(formula) if formula is not None else ""
    )
    if not normalized_text and not normalized_formula:
        raise CifSourceError("COD search requires text or formula.")
    if not isinstance(max_results, int) or isinstance(max_results, bool):
        raise CifSourceError("max_results must be an integer.")
    if not 1 <= max_results <= 100:
        raise CifSourceError("max_results must be between 1 and 100.")

    parameters: dict[str, str | int] = {
        "format": "json",
    }
    if normalized_text:
        parameters["text"] = normalized_text
    if normalized_formula:
        parameters["formula"] = normalized_formula
    return "https://www.crystallography.net/cod/result?" + urlencode(parameters)


def _validate_cif_payload(payload: bytes, *, content_type: str | None) -> dict[str, Any]:
    if not payload:
        raise CifSourceError("CIF response is empty.")
    normalized_content_type = (content_type or "").split(";", 1)[0].strip().lower()
    if normalized_content_type in _DISALLOWED_CONTENT_TYPES:
        raise CifSourceError("HTTP Content-Type is not compatible with a CIF artifact.")
    try:
        text = payload.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError as exc:
        raise CifSourceError("CIF artifact is not valid UTF-8.") from exc

    if any(
        (ord(character) < 32 and character not in "\t\r\n") or ord(character) == 127
        for character in text
    ):
        raise CifSourceError("CIF artifact contains forbidden control characters.")

    leading = text.lstrip().lower()
    if leading.startswith(("<!doctype", "<html", "<?xml", "{", "[")):
        raise CifSourceError("Response body does not conservatively resemble CIF text.")

    data_block_match = _CIF_DATA_BLOCK_RE.search(text)
    if data_block_match is None:
        raise CifSourceError("CIF artifact has no data_ block.")
    if re.search(r"(?mi)^[ \t]*loop_[ \t]*(?:#.*)?$", text) is None:
        raise CifSourceError("CIF artifact has no loop_ declaration.")

    required_cell_tags = (
        "_cell_length_a",
        "_cell_length_b",
        "_cell_length_c",
        "_cell_angle_alpha",
        "_cell_angle_beta",
        "_cell_angle_gamma",
    )
    missing_cell_tags = [
        tag
        for tag in required_cell_tags
        if re.search(rf"(?mi)^[ \t]*{re.escape(tag)}(?:[ \t]|$)", text) is None
    ]
    if missing_cell_tags:
        raise CifSourceError("CIF artifact is missing required unit-cell tags.")

    identity_tags = ("_atom_site_label", "_atom_site_type_symbol")
    if not any(
        re.search(rf"(?mi)^[ \t]*{re.escape(tag)}(?:[ \t]|$)", text)
        for tag in identity_tags
    ):
        raise CifSourceError("CIF artifact has no atom-site identity tag.")

    fractional_tags = (
        "_atom_site_fract_x",
        "_atom_site_fract_y",
        "_atom_site_fract_z",
    )
    cartesian_tags = (
        "_atom_site_Cartn_x",
        "_atom_site_Cartn_y",
        "_atom_site_Cartn_z",
    )

    def has_all(tags: Sequence[str]) -> bool:
        return all(
            re.search(rf"(?mi)^[ \t]*{re.escape(tag)}(?:[ \t]|$)", text)
            for tag in tags
        )

    if has_all(fractional_tags):
        coordinate_system = "fractional"
    elif has_all(cartesian_tags):
        coordinate_system = "cartesian"
    else:
        raise CifSourceError("CIF artifact has no complete atom coordinate triplet.")

    return {
        "valid": True,
        "encoding": "utf-8",
        "data_block": data_block_match.group(1),
        "coordinate_system": coordinate_system,
        "required_cell_tags_present": True,
        "atom_site_identity_present": True,
    }


def plan_cif_fetch(
    *,
    artifact_root: str | os.PathLike[str],
    source_url: str | None = None,
    cod_id: str | int | None = None,
    allowed_hosts: Iterable[str] = DEFAULT_ALLOWED_CIF_HOSTS,
    max_bytes: int = DEFAULT_MAX_CIF_BYTES,
) -> dict[str, Any]:
    """Preview a fetch with no DNS, network, or filesystem access."""

    if (source_url is None) == (cod_id is None):
        raise CifSourceError("Supply exactly one of source_url or cod_id.")
    if not isinstance(max_bytes, int) or max_bytes < 1:
        raise CifSourceError("max_bytes must be a positive integer.")

    source: dict[str, Any]
    if cod_id is not None:
        normalized_cod_id = str(cod_id)
        requested_url = cod_cif_url(normalized_cod_id)
        source = {"kind": "cod_id", "cod_id": normalized_cod_id}
    else:
        requested_url = str(source_url)
        source = {"kind": "https_url"}

    validated = _validate_https_url(requested_url, allowed_hosts=allowed_hosts)
    root = os.path.abspath(os.fspath(artifact_root))
    stable_plan = {
        "source": source,
        "requested_url": validated["url"],
        "allowed_hosts": validated["allowed_hosts"],
        "max_bytes": max_bytes,
        "artifact_root": root,
    }
    return {
        "schema_version": "materials-studio-cif-fetch-plan/v1",
        "execution_mode": "preview",
        "status": "ready",
        "plan_id": _sha256_bytes(_canonical_json_bytes(stable_plan)),
        **stable_plan,
        "network_performed": False,
        "dns_resolution_performed": False,
        "writes_performed": False,
        "record_id": None,
    }


def _is_link_like(path: Path) -> bool:
    try:
        details = path.lstat()
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(details.st_mode):
        return True
    attributes = getattr(details, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse_flag)


def _assert_no_existing_link_components(path: Path) -> None:
    absolute = Path(os.path.abspath(os.fspath(path)))
    candidates = list(reversed(absolute.parents)) + [absolute]
    for candidate in candidates:
        if os.path.lexists(candidate) and _is_link_like(candidate):
            raise CifSourceError(
                f"Artifact path contains a symlink or reparse point: {candidate}"
            )


def _write_exclusive(path: Path, payload: bytes) -> None:
    binary_flag = getattr(os, "O_BINARY", 0)
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | binary_flag,
        0o600,
    )
    try:
        offset = 0
        while offset < len(payload):
            offset += os.write(descriptor, payload[offset:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _read_bounded_file(path: Path, *, max_bytes: int) -> bytes:
    if _is_link_like(path):
        raise CifSourceError("Immutable CIF record contains a link-like file.")
    try:
        details = path.stat()
    except FileNotFoundError as exc:
        raise CifSourceError("Immutable CIF record file is missing.") from exc
    if not stat.S_ISREG(details.st_mode) or details.st_size > max_bytes:
        raise CifSourceError("Immutable CIF record file is missing or oversized.")
    with path.open("rb") as handle:
        payload = handle.read(max_bytes + 1)
    if len(payload) > max_bytes:
        raise CifSourceError("Immutable CIF record file exceeds its byte limit.")
    return payload


def _verify_existing_record(
    record_directory: Path,
    *,
    record_id: str,
    stable_identity: Mapping[str, Any],
    expected_payload_sha256: str,
    max_bytes: int,
) -> dict[str, Any]:
    if _is_link_like(record_directory) or not record_directory.is_dir():
        raise CifSourceError("Immutable CIF record path is not a safe directory.")
    source_path = record_directory / "source.cif"
    provenance_path = record_directory / "provenance.json"
    claim_path = record_directory.parent / f"{record_id}.claim"
    source_payload = _read_bounded_file(source_path, max_bytes=max_bytes)
    provenance_payload = _read_bounded_file(provenance_path, max_bytes=512 * 1024)
    try:
        provenance = json.loads(provenance_payload.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CifSourceError("Immutable CIF provenance is invalid.") from exc
    if not isinstance(provenance, dict):
        raise CifSourceError("Immutable CIF provenance must be a JSON object.")
    if provenance.get("record_id") != record_id:
        raise CifSourceError("Immutable CIF provenance record ID does not match.")
    if provenance.get("stable_identity") != dict(stable_identity):
        raise CifSourceError("Immutable CIF provenance identity does not match.")
    if _sha256_bytes(source_payload) != expected_payload_sha256:
        raise CifSourceError("Immutable CIF source hash does not match provenance.")
    expected_claim = {
        "schema_version": "materials-studio-cif-record-claim/v1",
        "record_id": record_id,
        "stable_identity_sha256": _sha256_bytes(
            _canonical_json_bytes(stable_identity)
        ),
        "source_sha256": expected_payload_sha256,
        "provenance_sha256": _sha256_bytes(provenance_payload),
    }
    expected_claim_bytes = _canonical_json_bytes(expected_claim)
    claim_recovered = False
    if not os.path.lexists(claim_path):
        try:
            _write_exclusive(claim_path, expected_claim_bytes)
            claim_recovered = True
        except FileExistsError:
            pass
    claim_payload = _read_bounded_file(claim_path, max_bytes=64 * 1024)
    try:
        claim = json.loads(claim_payload.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CifSourceError("Immutable CIF publication claim is invalid.") from exc
    if not isinstance(claim, dict):
        raise CifSourceError(
            "Immutable CIF publication claim must be a JSON object."
        )
    if claim != expected_claim:
        raise CifSourceError("Immutable CIF provenance hash does not match its claim.")
    return {
        "record_id": record_id,
        "record_directory": str(record_directory),
        "source_path": str(source_path),
        "provenance_path": str(provenance_path),
        "content_sha256": expected_payload_sha256,
        "existing": True,
        "claim_recovered": claim_recovered,
        "provenance": provenance,
    }


def _persist_immutable_cif(
    *,
    artifact_root: Path,
    payload: bytes,
    stable_identity: Mapping[str, Any],
    provenance_details: Mapping[str, Any],
    max_bytes: int,
) -> dict[str, Any]:
    _assert_no_existing_link_components(artifact_root)
    artifact_root.mkdir(parents=True, exist_ok=True)
    records_directory = artifact_root / "records"
    records_directory.mkdir(exist_ok=True)
    _assert_no_existing_link_components(records_directory)

    identity_bytes = _canonical_json_bytes(stable_identity)
    record_id = _sha256_bytes(identity_bytes)
    payload_sha256 = _sha256_bytes(payload)
    record_directory = records_directory / record_id

    if os.path.lexists(record_directory):
        return _verify_existing_record(
            record_directory,
            record_id=record_id,
            stable_identity=stable_identity,
            expected_payload_sha256=payload_sha256,
            max_bytes=max_bytes,
        )

    temporary_directory = records_directory / (
        f".{record_id}.tmp-{os.getpid()}-{secrets.token_hex(8)}"
    )
    os.mkdir(temporary_directory)
    try:
        provenance = {
            "schema_version": "materials-studio-cif-source-provenance/v1",
            "record_id": record_id,
            "stable_identity": dict(stable_identity),
            "retrieved_at": _utc_now(),
            **dict(provenance_details),
        }
        provenance_bytes = _canonical_json_bytes(provenance)
        _write_exclusive(temporary_directory / "source.cif", payload)
        _write_exclusive(
            temporary_directory / "provenance.json",
            provenance_bytes,
        )
        _fsync_directory(temporary_directory)

        try:
            os.rename(temporary_directory, record_directory)
        except OSError:
            if not record_directory.is_dir():
                raise
            return _verify_existing_record(
                record_directory,
                record_id=record_id,
                stable_identity=stable_identity,
                expected_payload_sha256=payload_sha256,
                max_bytes=max_bytes,
            )
        _fsync_directory(records_directory)
        published = _verify_existing_record(
            record_directory,
            record_id=record_id,
            stable_identity=stable_identity,
            expected_payload_sha256=payload_sha256,
            max_bytes=max_bytes,
        )
        published["existing"] = False
        return published
    finally:
        if (
            temporary_directory.exists()
            and not _is_link_like(temporary_directory)
            and temporary_directory.parent.resolve()
            == records_directory.resolve()
            and temporary_directory.name.startswith(f".{record_id}.tmp-")
        ):
            shutil.rmtree(temporary_directory)


def fetch_cif_source(
    *,
    artifact_root: str | os.PathLike[str],
    source_url: str | None = None,
    cod_id: str | int | None = None,
    execution_mode: str = "preview",
    allowed_hosts: Iterable[str] = DEFAULT_ALLOWED_CIF_HOSTS,
    max_bytes: int = DEFAULT_MAX_CIF_BYTES,
    max_redirects: int = DEFAULT_REDIRECT_LIMIT,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    resolver: Resolver = socket.getaddrinfo,
    opener: ResponseOpener = _open_pinned_https,
) -> dict[str, Any]:
    """Preview or execute a secure CIF fetch and immutable publication."""

    plan = plan_cif_fetch(
        artifact_root=artifact_root,
        source_url=source_url,
        cod_id=cod_id,
        allowed_hosts=allowed_hosts,
        max_bytes=max_bytes,
    )
    if execution_mode == "preview":
        return plan
    if execution_mode != "execute":
        raise CifSourceError("execution_mode must be 'preview' or 'execute'.")

    fetched = fetch_https_bytes(
        plan["requested_url"],
        allowed_hosts=plan["allowed_hosts"],
        max_bytes=max_bytes,
        max_redirects=max_redirects,
        timeout_seconds=timeout_seconds,
        resolver=resolver,
        opener=opener,
    )
    validation = _validate_cif_payload(
        fetched["payload"],
        content_type=fetched["content_type"],
    )
    content_sha256 = _sha256_bytes(fetched["payload"])
    stable_identity = {
        "schema_version": "materials-studio-cif-source-identity/v1",
        "source": plan["source"],
        "requested_url": fetched["requested_url"],
        "final_url": fetched["final_url"],
        "redirect_chain": fetched["redirect_chain"],
        "content_sha256": content_sha256,
        "content_length": fetched["content_length"],
    }
    record = _persist_immutable_cif(
        artifact_root=Path(plan["artifact_root"]),
        payload=fetched["payload"],
        stable_identity=stable_identity,
        provenance_details={
            "plan_id": plan["plan_id"],
            "content_type": fetched["content_type"],
            "dns_receipts": fetched["dns_receipts"],
            "cif_validation": validation,
        },
        max_bytes=max_bytes,
    )
    return {
        "schema_version": "materials-studio-cif-fetch-result/v1",
        "execution_mode": "execute",
        "status": "completed",
        "plan_id": plan["plan_id"],
        "network_performed": True,
        "dns_resolution_performed": True,
        "writes_performed": bool(
            not record["existing"] or record.get("claim_recovered")
        ),
        "source": plan["source"],
        "requested_url": fetched["requested_url"],
        "final_url": fetched["final_url"],
        "redirect_chain": fetched["redirect_chain"],
        "dns_receipts": fetched["dns_receipts"],
        "content_length": fetched["content_length"],
        "content_sha256": content_sha256,
        "cif_validation": validation,
        "record": record,
    }


def search_cod(
    *,
    text: str | None = None,
    query: str | None = None,
    formula: str | None = None,
    max_results: int = 10,
    execution_mode: str = "preview",
    allowed_hosts: Iterable[str] = DEFAULT_ALLOWED_CIF_HOSTS,
    max_response_bytes: int = DEFAULT_MAX_SEARCH_BYTES,
    max_redirects: int = DEFAULT_REDIRECT_LIMIT,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    resolver: Resolver = socket.getaddrinfo,
    opener: ResponseOpener = _open_pinned_https,
) -> dict[str, Any]:
    """Preview or execute a bounded, read-only COD metadata search."""

    search_url = build_cod_search_url(
        text=text,
        query=query,
        formula=formula,
        max_results=max_results,
    )
    validated = _validate_https_url(search_url, allowed_hosts=allowed_hosts)
    normalized_text = _normalize_cod_text(text=text, query=query)
    normalized_formula = (
        validate_hill_formula(formula) if formula is not None else None
    )
    plan_basis = {
        "text": normalized_text or None,
        "formula": normalized_formula,
        "max_results": max_results,
        "search_url": validated["url"],
        "allowed_hosts": validated["allowed_hosts"],
        "max_response_bytes": max_response_bytes,
    }
    plan_id = _sha256_bytes(_canonical_json_bytes(plan_basis))
    if execution_mode == "preview":
        return {
            "schema_version": "materials-studio-cod-search-plan/v1",
            "execution_mode": "preview",
            "status": "ready",
            "plan_id": plan_id,
            **plan_basis,
            "network_performed": False,
            "dns_resolution_performed": False,
            "writes_performed": False,
        }
    if execution_mode != "execute":
        raise CifSourceError("execution_mode must be 'preview' or 'execute'.")

    fetched = fetch_https_bytes(
        validated["url"],
        allowed_hosts=validated["allowed_hosts"],
        max_bytes=max_response_bytes,
        max_redirects=max_redirects,
        timeout_seconds=timeout_seconds,
        accept="application/json",
        resolver=resolver,
        opener=opener,
    )
    try:
        decoded = fetched["payload"].decode("utf-8-sig", errors="strict")
        raw_results = json.loads(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CifSourceError("COD search response is not valid UTF-8 JSON.") from exc
    if not isinstance(raw_results, list):
        raise CifSourceError("COD search response must be a JSON array.")

    candidates: list[dict[str, Any]] = []
    for row in raw_results:
        if len(candidates) >= max_results:
            break
        if not isinstance(row, dict):
            continue
        raw_cod_id = row.get("file", row.get("id"))
        if raw_cod_id is None:
            continue
        normalized_cod_id = str(raw_cod_id)
        if not _COD_ID_RE.fullmatch(normalized_cod_id):
            continue
        candidate: dict[str, Any] = {
            "cod_id": normalized_cod_id,
            "cif_url": cod_cif_url(normalized_cod_id),
        }
        for output_name, source_names in (
            ("formula", ("formula", "chemform")),
            ("mineral", ("mineral", "mineral_name")),
            ("common_name", ("commonname", "common_name")),
            ("space_group", ("sg", "spacegroup", "space_group")),
        ):
            value = next(
                (row.get(source_name) for source_name in source_names if row.get(source_name)),
                None,
            )
            if value is not None:
                rendered = str(value)
                if len(rendered) <= 512 and not any(ord(character) < 32 for character in rendered):
                    candidate[output_name] = rendered
        candidates.append(candidate)

    return {
        "schema_version": "materials-studio-cod-search-result/v1",
        "execution_mode": "execute",
        "status": "completed",
        "plan_id": plan_id,
        **plan_basis,
        "network_performed": True,
        "dns_resolution_performed": True,
        "writes_performed": False,
        "final_url": fetched["final_url"],
        "redirect_chain": fetched["redirect_chain"],
        "dns_receipts": fetched["dns_receipts"],
        "candidate_count": len(candidates),
        "candidates": candidates,
    }


__all__ = [
    "CifSourceError",
    "DEFAULT_ALLOWED_CIF_HOSTS",
    "build_cod_search_url",
    "cod_cif_url",
    "fetch_cif_source",
    "fetch_https_bytes",
    "plan_cif_fetch",
    "search_cod",
    "validate_hill_formula",
]
