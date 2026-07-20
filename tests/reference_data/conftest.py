from __future__ import annotations

from pathlib import Path

import pytest

from material_studio_mcp_server.reference_data import (
    ReferenceLicense,
    ReferenceSource,
    RetrievalContext,
)


SYNTHETIC_RAW_BYTES = b"data_fixture\r\n# exact bytes; not a structure\r\n"


def make_source(**changes: object) -> ReferenceSource:
    values: dict[str, object] = {
        "source_id": "fixture-reference",
        "provider": "Offline Fixture Provider",
        "provider_record_id": "fixture-record-1",
        "provider_revision": "1",
        "record_url": "https://reference.test/records/fixture-record-1",
        "artifact_url": "https://reference.test/artifacts/fixture-record-1.cif",
        "retrieval": RetrievalContext(
            retrieved_at="2026-07-20T00:00:00Z",
            retrieval_purpose="Isolated offline reference-ingestion test",
        ),
        "media_type": "chemical/x-cif",
        "structure_format": "cif",
        "citation": "Synthetic fixture citation",
        "license": ReferenceLicense(
            name="CC0 1.0 Universal",
            spdx_id="CC0-1.0",
            url="https://creativecommons.org/publicdomain/zero/1.0/",
            redistributable=True,
        ),
    }
    values.update(changes)
    return ReferenceSource(**values)


@pytest.fixture
def reference_source() -> ReferenceSource:
    return make_source()


@pytest.fixture
def reference_store_root(tmp_path: Path) -> Path:
    return tmp_path / "reference-store"
