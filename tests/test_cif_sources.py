from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

from material_studio_mcp_server import cif_sources as cif_sources_module
from material_studio_mcp_server.cif_sources import (
    CifSourceError,
    build_cod_search_url,
    cod_cif_url,
    fetch_cif_source,
    fetch_https_bytes,
    plan_cif_fetch,
    search_cod,
    validate_hill_formula,
)


VALID_CIF = b"""data_test
_cell_length_a 5.43
_cell_length_b 5.43
_cell_length_c 5.43
_cell_angle_alpha 90
_cell_angle_beta 90
_cell_angle_gamma 90
loop_
_atom_site_label
_atom_site_type_symbol
_atom_site_fract_x
_atom_site_fract_y
_atom_site_fract_z
Si1 Si 0 0 0
"""


class FakeResponse:
    def __init__(
        self,
        body: bytes = b"",
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status = status
        self.headers = headers or {}
        self._body = body
        self._offset = 0
        self.read_sizes: list[int] = []
        self.closed = False

    def read(self, amount: int = -1) -> bytes:
        self.read_sizes.append(amount)
        if amount < 0:
            amount = len(self._body) - self._offset
        chunk = self._body[self._offset : self._offset + amount]
        self._offset += len(chunk)
        return chunk

    def close(self) -> None:
        self.closed = True


class FakeOpener:
    def __init__(self, routes: dict[str, Any]) -> None:
        self.routes = routes
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self,
        url: str,
        approved_ip: str,
        timeout_seconds: float,
        headers: dict[str, str],
    ) -> FakeResponse:
        self.calls.append(
            {
                "url": url,
                "approved_ip": approved_ip,
                "timeout_seconds": timeout_seconds,
                "headers": headers,
            }
        )
        route = self.routes[url]
        return route() if callable(route) else route


def public_resolver(hostname: str, port: int, **_: Any) -> list[tuple[Any, ...]]:
    assert port == 443
    return [
        (
            socket.AF_INET,
            socket.SOCK_STREAM,
            socket.IPPROTO_TCP,
            "",
            ("93.184.216.34", port),
        )
    ]


def resolver_for(address: str):
    family = socket.AF_INET6 if ":" in address else socket.AF_INET

    def resolve(hostname: str, port: int, **_: Any) -> list[tuple[Any, ...]]:
        return [
            (
                family,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                (address, port),
            )
        ]

    return resolve


def test_preview_performs_no_dns_network_or_filesystem_write(tmp_path: Path) -> None:
    artifact_root = tmp_path / "must-not-exist"

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("preview attempted an external side effect")

    result = fetch_cif_source(
        artifact_root=artifact_root,
        cod_id="1000000",
        execution_mode="preview",
        resolver=forbidden,
        opener=forbidden,
    )

    assert result["status"] == "ready"
    assert result["network_performed"] is False
    assert result["dns_resolution_performed"] is False
    assert result["writes_performed"] is False
    assert not artifact_root.exists()


@pytest.mark.parametrize(
    "url",
    [
        "http://www.crystallography.net/cod/1000000.cif",
        "https://user:password@www.crystallography.net/cod/1000000.cif",
        "https://www.crystallography.net:444/cod/1000000.cif",
        "https://example.com/1000000.cif",
        "https://www.crystallography.net/cod/1000000.cif#fragment",
    ],
)
def test_preview_rejects_unsafe_urls(tmp_path: Path, url: str) -> None:
    with pytest.raises(CifSourceError):
        plan_cif_fetch(artifact_root=tmp_path / "artifacts", source_url=url)


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.5",
        "169.254.10.2",
        "224.0.0.1",
        "::1",
        "fe80::1",
        "ff02::1",
        "0.0.0.0",
    ],
)
def test_fetch_rejects_non_public_dns_results(address: str) -> None:
    def forbidden_opener(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("request opened after forbidden DNS result")

    with pytest.raises(CifSourceError, match="forbidden non-public"):
        fetch_https_bytes(
            cod_cif_url("1000000"),
            resolver=resolver_for(address),
            opener=forbidden_opener,
        )


def test_fetch_fails_closed_for_mixed_public_private_dns_results() -> None:
    def mixed_resolver(hostname: str, port: int, **_: Any) -> list[tuple[Any, ...]]:
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("93.184.216.34", port),
            ),
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("192.168.1.2", port),
            ),
        ]

    with pytest.raises(CifSourceError, match="forbidden non-public"):
        fetch_https_bytes(
            cod_cif_url("1000000"),
            resolver=mixed_resolver,
            opener=lambda *args, **kwargs: pytest.fail("opener must not run"),
        )


def test_redirect_revalidates_dns_before_opening_next_hop() -> None:
    first_url = cod_cif_url("1000000")
    second_url = cod_cif_url("1000001")
    opener = FakeOpener(
        {
            first_url: FakeResponse(
                status=302,
                headers={"Location": second_url},
            )
        }
    )

    def rebinding_resolver(hostname: str, port: int, **_: Any) -> list[tuple[Any, ...]]:
        address = "93.184.216.34" if len(opener.calls) == 0 else "127.0.0.1"
        return resolver_for(address)(hostname, port)

    with pytest.raises(CifSourceError, match="forbidden non-public"):
        fetch_https_bytes(
            first_url,
            resolver=rebinding_resolver,
            opener=opener,
        )

    assert [call["url"] for call in opener.calls] == [first_url]


def test_redirect_revalidates_explicit_host_allowlist() -> None:
    first_url = cod_cif_url("1000000")
    opener = FakeOpener(
        {
            first_url: FakeResponse(
                status=302,
                headers={"Location": "https://attacker.example/payload.cif"},
            )
        }
    )

    with pytest.raises(CifSourceError, match="explicit allowlist"):
        fetch_https_bytes(
            first_url,
            resolver=public_resolver,
            opener=opener,
        )

    assert len(opener.calls) == 1


def test_streaming_byte_limit_is_enforced_without_content_length() -> None:
    url = cod_cif_url("1000000")
    response = FakeResponse(b"01234567890")
    opener = FakeOpener({url: response})

    with pytest.raises(CifSourceError, match="byte limit"):
        fetch_https_bytes(
            url,
            max_bytes=10,
            resolver=public_resolver,
            opener=opener,
        )

    assert response.closed is True
    assert response.read_sizes
    assert max(response.read_sizes) <= 11


def test_declared_oversize_is_rejected_before_body_read() -> None:
    url = cod_cif_url("1000000")
    response = FakeResponse(
        b"not-read",
        headers={"Content-Length": "999"},
    )
    opener = FakeOpener({url: response})

    with pytest.raises(CifSourceError, match="byte limit"):
        fetch_https_bytes(
            url,
            max_bytes=100,
            resolver=public_resolver,
            opener=opener,
        )

    assert response.read_sizes == []
    assert response.closed is True


@pytest.mark.parametrize(
    ("payload", "content_type"),
    [
        (b"\xff\xfe\x00\x00", "chemical/x-cif"),
        (b"<html><body>not cif</body></html>", "text/html"),
        (b"data_weak\nloop_\n_atom_site_label\nSi1\n", "text/plain"),
    ],
)
def test_execute_conservatively_rejects_non_cif_payloads(
    tmp_path: Path,
    payload: bytes,
    content_type: str,
) -> None:
    url = cod_cif_url("1000000")
    opener = FakeOpener(
        {
            url: FakeResponse(
                payload,
                headers={"Content-Type": content_type},
            )
        }
    )

    with pytest.raises(CifSourceError):
        fetch_cif_source(
            artifact_root=tmp_path / "artifacts",
            cod_id="1000000",
            execution_mode="execute",
            resolver=public_resolver,
            opener=opener,
        )

    assert not (tmp_path / "artifacts").exists()


def test_execute_persists_content_and_provenance_immutably(tmp_path: Path) -> None:
    url = cod_cif_url("1000000")
    artifact_root = tmp_path / "artifacts"
    opener = FakeOpener(
        {
            url: lambda: FakeResponse(
                VALID_CIF,
                headers={"Content-Type": "chemical/x-cif; charset=utf-8"},
            )
        }
    )

    first = fetch_cif_source(
        artifact_root=artifact_root,
        cod_id="1000000",
        execution_mode="execute",
        resolver=public_resolver,
        opener=opener,
    )
    source_path = Path(first["record"]["source_path"])
    provenance_path = Path(first["record"]["provenance_path"])
    source_mtime = source_path.stat().st_mtime_ns
    provenance_mtime = provenance_path.stat().st_mtime_ns

    assert first["record"]["existing"] is False
    assert first["writes_performed"] is True
    assert source_path.read_bytes() == VALID_CIF
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    assert provenance["record_id"] == first["record"]["record_id"]
    assert provenance["stable_identity"]["content_sha256"] == first["content_sha256"]
    assert provenance["dns_receipts"][0]["all_addresses_public"] is True
    assert provenance["cif_validation"]["coordinate_system"] == "fractional"
    claim_path = artifact_root / "records" / f"{first['record']['record_id']}.claim"
    assert claim_path.is_file()

    second = fetch_cif_source(
        artifact_root=artifact_root,
        cod_id="1000000",
        execution_mode="execute",
        resolver=public_resolver,
        opener=opener,
    )

    assert second["record"]["existing"] is True
    assert second["writes_performed"] is False
    assert second["record"]["record_id"] == first["record"]["record_id"]
    assert source_path.stat().st_mtime_ns == source_mtime
    assert provenance_path.stat().st_mtime_ns == provenance_mtime


def test_atomic_record_recovers_a_missing_post_publish_claim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = cod_cif_url("1000000")
    artifact_root = tmp_path / "artifacts"
    opener = FakeOpener(
        {
            url: lambda: FakeResponse(
                VALID_CIF,
                headers={"Content-Type": "chemical/x-cif"},
            )
        }
    )
    original_write_exclusive = cif_sources_module._write_exclusive

    def interrupt_claim_publication(path: Path, payload: bytes) -> None:
        if path.suffix == ".claim":
            raise OSError("simulated interruption after atomic directory publish")
        original_write_exclusive(path, payload)

    monkeypatch.setattr(
        cif_sources_module,
        "_write_exclusive",
        interrupt_claim_publication,
    )
    with pytest.raises(OSError, match="simulated interruption"):
        fetch_cif_source(
            artifact_root=artifact_root,
            cod_id="1000000",
            execution_mode="execute",
            resolver=public_resolver,
            opener=opener,
        )

    records_directory = artifact_root / "records"
    published_directories = [
        path
        for path in records_directory.iterdir()
        if path.is_dir() and not path.name.startswith(".")
    ]
    assert len(published_directories) == 1
    record_directory = published_directories[0]
    assert (record_directory / "source.cif").read_bytes() == VALID_CIF
    assert (record_directory / "provenance.json").is_file()
    claim_path = records_directory / f"{record_directory.name}.claim"
    assert not claim_path.exists()

    monkeypatch.setattr(
        cif_sources_module,
        "_write_exclusive",
        original_write_exclusive,
    )
    recovered = fetch_cif_source(
        artifact_root=artifact_root,
        cod_id="1000000",
        execution_mode="execute",
        resolver=public_resolver,
        opener=opener,
    )

    assert recovered["record"]["existing"] is True
    assert recovered["record"]["claim_recovered"] is True
    assert recovered["writes_performed"] is True
    assert claim_path.is_file()


def test_existing_record_tampering_is_detected(tmp_path: Path) -> None:
    url = cod_cif_url("1000000")
    opener = FakeOpener(
        {
            url: lambda: FakeResponse(
                VALID_CIF,
                headers={"Content-Type": "chemical/x-cif"},
            )
        }
    )
    first = fetch_cif_source(
        artifact_root=tmp_path / "artifacts",
        cod_id="1000000",
        execution_mode="execute",
        resolver=public_resolver,
        opener=opener,
    )
    Path(first["record"]["source_path"]).write_bytes(VALID_CIF + b"# tampered\n")

    with pytest.raises(CifSourceError, match="hash"):
        fetch_cif_source(
            artifact_root=tmp_path / "artifacts",
            cod_id="1000000",
            execution_mode="execute",
            resolver=public_resolver,
            opener=opener,
        )


def test_existing_provenance_tampering_is_detected(tmp_path: Path) -> None:
    url = cod_cif_url("1000000")
    opener = FakeOpener(
        {
            url: lambda: FakeResponse(
                VALID_CIF,
                headers={"Content-Type": "chemical/x-cif"},
            )
        }
    )
    first = fetch_cif_source(
        artifact_root=tmp_path / "artifacts",
        cod_id="1000000",
        execution_mode="execute",
        resolver=public_resolver,
        opener=opener,
    )
    provenance_path = Path(first["record"]["provenance_path"])
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    provenance["retrieved_at"] = "tampered"
    provenance_path.write_text(json.dumps(provenance), encoding="utf-8")

    with pytest.raises(CifSourceError, match="provenance hash"):
        fetch_cif_source(
            artifact_root=tmp_path / "artifacts",
            cod_id="1000000",
            execution_mode="execute",
            resolver=public_resolver,
            opener=opener,
        )


def test_allowed_redirect_is_recorded_in_provenance(tmp_path: Path) -> None:
    first_url = cod_cif_url("1000000")
    second_url = cod_cif_url("1000001")
    opener = FakeOpener(
        {
            first_url: FakeResponse(status=302, headers={"Location": second_url}),
            second_url: FakeResponse(
                VALID_CIF,
                headers={"Content-Type": "text/plain"},
            ),
        }
    )
    result = fetch_cif_source(
        artifact_root=tmp_path / "artifacts",
        cod_id="1000000",
        execution_mode="execute",
        resolver=public_resolver,
        opener=opener,
    )

    assert result["final_url"] == second_url
    assert result["redirect_chain"] == [
        {"status": 302, "from_url": first_url, "to_url": second_url}
    ]
    assert len(result["dns_receipts"]) == 2


@pytest.mark.parametrize("cod_id", ["abc", "12345", "1234567890", "../123456"])
def test_cod_id_must_be_numeric_and_bounded(cod_id: str) -> None:
    with pytest.raises(CifSourceError):
        cod_cif_url(cod_id)


def test_cod_url_and_search_url_are_canonical() -> None:
    assert cod_cif_url(1000000) == "https://www.crystallography.net/cod/1000000.cif"
    search_url = build_cod_search_url(
        text="silicon dioxide",
        formula="O2 Si",
        max_results=5,
    )
    parsed = urlsplit(search_url)
    assert parsed.scheme == "https"
    assert parsed.netloc == "www.crystallography.net"
    assert parsed.path == "/cod/result"
    assert parse_qs(parsed.query) == {
        "format": ["json"],
        "text": ["silicon dioxide"],
        "formula": ["O2 Si"],
    }
    assert "count" not in parse_qs(parsed.query)


def test_cod_query_is_a_compatibility_alias_for_official_text_parameter() -> None:
    alias_url = build_cod_search_url(query="quartz", max_results=1)
    canonical_url = build_cod_search_url(text="quartz", max_results=100)

    assert alias_url == canonical_url
    assert parse_qs(urlsplit(alias_url).query)["text"] == ["quartz"]
    with pytest.raises(CifSourceError, match="compatibility alias"):
        build_cod_search_url(text="quartz", query="silica")


@pytest.mark.parametrize(
    "formula",
    [
        "O2 Si",
        "C H4",
        "C6 H12 O6",
        "C Ca O3",
        "Cl Na",
        "Db",
        "Og",
    ],
)
def test_hill_formula_validation_accepts_ordered_unique_elements(
    formula: str,
) -> None:
    assert validate_hill_formula(formula) == formula


@pytest.mark.parametrize(
    "formula",
    [
        "Si O2",
        "H4 C",
        "O O2",
        "Xx2",
        "SiO2",
        "O2  Si",
        " O2 Si",
        "O2 Si ",
        "O1 Si",
        "O0 Si",
        "O2.5 Si",
        "D2 O",
        "O2\tSi",
    ],
)
def test_hill_formula_validation_rejects_unsafe_or_noncanonical_input(
    formula: str,
) -> None:
    with pytest.raises(CifSourceError):
        validate_hill_formula(formula)


def test_cod_search_preview_has_no_side_effects() -> None:
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("preview attempted network access")

    result = search_cod(
        text="quartz",
        execution_mode="preview",
        resolver=forbidden,
        opener=forbidden,
    )

    assert result["status"] == "ready"
    assert result["text"] == "quartz"
    assert result["network_performed"] is False
    assert result["writes_performed"] is False


def test_cod_search_returns_only_valid_bounded_candidates() -> None:
    search_url = build_cod_search_url(text="quartz", max_results=2)
    response_body = json.dumps(
        [
            {
                "file": "1000000",
                "formula": "Si O2",
                "mineral": "Quartz",
                "sg": "P 31 2 1",
            },
            {"file": "../bad", "formula": "bad"},
            {"file": 1000001, "commonname": "Silica"},
            {"file": "1000002", "commonname": "must be locally truncated"},
        ]
    ).encode("utf-8")
    opener = FakeOpener(
        {
            search_url: FakeResponse(
                response_body,
                headers={"Content-Type": "application/json"},
            )
        }
    )

    result = search_cod(
        text="quartz",
        max_results=2,
        execution_mode="execute",
        resolver=public_resolver,
        opener=opener,
    )

    assert result["writes_performed"] is False
    assert result["candidate_count"] == 2
    assert "count" not in parse_qs(urlsplit(result["search_url"]).query)
    assert result["candidates"][0] == {
        "cod_id": "1000000",
        "cif_url": cod_cif_url("1000000"),
        "formula": "Si O2",
        "mineral": "Quartz",
        "space_group": "P 31 2 1",
    }
    assert result["candidates"][1]["cod_id"] == "1000001"
