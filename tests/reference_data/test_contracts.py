from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from material_studio_mcp_server.reference_data import (
    ReferenceLicense,
    ReferenceSource,
    RetrievalContext,
    ReviewedRequestHeader,
)
from material_studio_mcp_server.reference_data.contracts import (
    IngestionReceipt,
    IngestionVerification,
    RawArtifactFingerprint,
    RawArtifactRecord,
    RawDeduplicationBoundary,
    RawDeduplicationResult,
    ReferenceContractModel,
    ReferenceManifest,
    ReferenceMetadataProjection,
)

from conftest import make_source


NEW_MODELS = (
    ReferenceContractModel,
    ReviewedRequestHeader,
    RetrievalContext,
    ReferenceLicense,
    ReferenceSource,
    RawArtifactFingerprint,
    RawArtifactRecord,
    RawDeduplicationBoundary,
    ReferenceManifest,
    IngestionVerification,
    IngestionReceipt,
    ReferenceMetadataProjection,
    RawDeduplicationResult,
)


def test_all_reference_models_are_strict_frozen_and_closed() -> None:
    for model in NEW_MODELS:
        assert model.model_config["strict"] is True
        assert model.model_config["frozen"] is True
        assert model.model_config["extra"] == "forbid"

    source = make_source()
    with pytest.raises(ValidationError, match="frozen"):
        source.provider = "changed"  # type: ignore[misc]
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ReferenceLicense(
            name="CC0 1.0 Universal",
            spdx_id="CC0-1.0",
            url="https://creativecommons.org/publicdomain/zero/1.0/",
            redistributable=True,
            token="forbidden",
        )


@pytest.mark.parametrize("value", [0, 1, "true", "false"])
def test_evidence_boolean_rejects_coercion(value: object) -> None:
    with pytest.raises(ValidationError):
        ReferenceLicense(
            name="CC0 1.0 Universal",
            spdx_id="CC0-1.0",
            url="https://creativecommons.org/publicdomain/zero/1.0/",
            redistributable=value,
        )


def test_retrieval_timestamp_has_no_default_and_requires_rfc3339_timezone() -> None:
    with pytest.raises(ValidationError):
        RetrievalContext(retrieval_purpose="Missing timestamp")

    for malformed in (
        "2026-07-20T00:00:00",
        "2026-13-20T00:00:00Z",
        "2026-07-20 00:00:00Z",
        "2026-07-20T00:00:00.1234567Z",
        "2026-07-20T00:00:00Z\n",
    ):
        with pytest.raises(ValidationError):
            RetrievalContext(
                retrieved_at=malformed,
                retrieval_purpose="Malformed timestamp test",
            )

    context = RetrievalContext(
        retrieved_at="2026-07-20T08:30:00+08:00",
        retrieval_purpose="Timezone-aware test",
    )
    assert context.retrieved_at == "2026-07-20T08:30:00+08:00"

    with pytest.raises(ValidationError):
        RetrievalContext(
            retrieved_at=datetime(2026, 7, 20, tzinfo=timezone.utc),
            retrieval_purpose="Strict string contract",
        )


@pytest.mark.parametrize(
    "unsafe_url",
    (
        "http://reference.test/record/1",
        "https://user:password@reference.test/record/1",
        "https://reference.test/record/1#fragment",
        "https://reference.test/record/1?api_key=value",
        "https://reference.test/record/1?x-amz-credential=value",
        "https://reference.test/record/1?oauth_token=value",
        "https://reference.test/record/1?%61pi_key=value",
        "https://reference.test/record/%0A1",
        "https://reference.test/record/1\n",
        "https://reference.test\\record\\1",
    ),
)
def test_source_rejects_malformed_or_secret_bearing_urls(unsafe_url: str) -> None:
    with pytest.raises(ValidationError):
        make_source(record_url=unsafe_url)


def test_pinned_cod_revision_marker_is_not_misclassified_as_userinfo() -> None:
    artifact_url = "https://www.crystallography.net/cod/1010995.cif@278158"
    source = make_source(artifact_url=artifact_url)
    assert source.artifact_url == artifact_url


def test_source_requires_complete_identity_format_license_and_retrieval() -> None:
    source = make_source()
    payload = source.model_dump()
    for required_field in (
        "source_id",
        "provider",
        "provider_record_id",
        "record_url",
        "artifact_url",
        "retrieval",
        "media_type",
        "structure_format",
        "license",
    ):
        incomplete = dict(payload)
        incomplete.pop(required_field)
        with pytest.raises(ValidationError):
            ReferenceSource(**incomplete)

    assert source.citation == "Synthetic fixture citation"
    assert source.license.redistributable is True


def test_source_rejects_control_characters_and_unsafe_context_text() -> None:
    with pytest.raises(ValidationError):
        make_source(provider="Fixture\x00Provider")
    with pytest.raises(ValidationError):
        RetrievalContext(
            retrieved_at="2026-07-20T00:00:00Z",
            retrieval_purpose="authorization=Bearer secret",
        )
    with pytest.raises(ValidationError):
        RetrievalContext(
            retrieved_at="2026-07-20T00:00:00Z",
            retrieval_purpose="Offline fixture",
            query="api_key=secret",
        )


@pytest.mark.parametrize(
    "query",
    (
        "token=DO_NOT_LEAK_QUERY_SECRET",
        "auth=DO_NOT_LEAK_QUERY_SECRET",
        "key=DO_NOT_LEAK_QUERY_SECRET",
        "sig=DO_NOT_LEAK_QUERY_SECRET",
        "%74oken=DO_NOT_LEAK_QUERY_SECRET",
        "%2574oken=DO_NOT_LEAK_QUERY_SECRET",
    ),
)
def test_bare_credential_query_keys_are_rejected_without_error_leak(
    query: str,
) -> None:
    sentinel = "DO_NOT_LEAK_QUERY_SECRET"
    with pytest.raises(ValidationError) as captured:
        RetrievalContext(
            retrieved_at="2026-07-20T00:00:00Z",
            retrieval_purpose="Offline fixture",
            query=query,
        )
    assert sentinel not in repr(captured.value.errors())
    assert sentinel not in str(captured.value)
    assert sentinel not in captured.value.json()


def test_headers_are_bounded_allowlisted_unique_and_sorted() -> None:
    accept = ReviewedRequestHeader(name="Accept", value="chemical/x-cif")
    agent = ReviewedRequestHeader(name="User-Agent", value="reviewed-fixture/1")
    context = RetrievalContext(
        retrieved_at="2026-07-20T00:00:00Z",
        retrieval_purpose="Offline fixture",
        request_headers=(accept, agent),
    )
    assert context.request_headers == (accept, agent)

    for name in ("Authorization", "Cookie", "Proxy-Authorization", "X-Custom"):
        with pytest.raises(ValidationError):
            ReviewedRequestHeader(name=name, value="redacted")
    with pytest.raises(ValidationError):
        RetrievalContext(
            retrieved_at="2026-07-20T00:00:00Z",
            retrieval_purpose="Offline fixture",
            request_headers=(agent, accept),
        )
    with pytest.raises(ValidationError):
        RetrievalContext(
            retrieved_at="2026-07-20T00:00:00Z",
            retrieval_purpose="Offline fixture",
            request_headers=(accept, accept),
        )
    with pytest.raises(ValidationError):
        RetrievalContext(
            retrieved_at="2026-07-20T00:00:00Z",
            retrieval_purpose="Offline fixture",
            request_headers=[accept],
        )


def test_source_rejects_ambiguous_urls_and_malformed_media_type() -> None:
    same = "https://reference.test/records/fixture-record-1"
    with pytest.raises(ValidationError):
        make_source(record_url=same, artifact_url=same)
    with pytest.raises(ValidationError):
        make_source(media_type="not a media type")


def test_secret_sentinel_is_absent_from_default_validation_error_inputs() -> None:
    sentinel = "DO_NOT_LEAK_SECRET_SENTINEL_9f6d"
    factories = (
        lambda: make_source(
            record_url=f"https://user:{sentinel}@reference.test/record/1"
        ),
        lambda: make_source(
            record_url=f"https://user:{sentinel}@[::1/record/1"
        ),
        lambda: make_source(
            artifact_url=f"https://reference.test/file.cif?api_key={sentinel}"
        ),
        lambda: RetrievalContext(
            retrieved_at="2026-07-20T00:00:00Z",
            retrieval_purpose="Offline fixture",
            query=f"access_token={sentinel}",
        ),
        lambda: ReviewedRequestHeader(name="Authorization", value=sentinel),
        lambda: ReferenceLicense(
            name="CC0 1.0 Universal",
            spdx_id="CC0-1.0",
            url=f"https://license.test/terms?token={sentinel}",
            redistributable=True,
        ),
        lambda: ReferenceLicense(
            name="CC0 1.0 Universal",
            spdx_id="CC0-1.0",
            url="https://license.test/terms",
            redistributable=True,
            authorization=sentinel,
        ),
    )
    for factory in factories:
        with pytest.raises(ValidationError) as captured:
            factory()
        assert sentinel not in repr(captured.value.errors())


@pytest.mark.parametrize(
    "malformed_url",
    (
        r"https:\\user:z9Q4vL2p7@reference.test/record/1",
        "https:/user:z9Q4vL2p7@reference.test/record/1",
        "https:user:z9Q4vL2p7@reference.test/record/1",
    ),
)
def test_malformed_userinfo_is_redacted_with_an_opaque_sentinel(
    malformed_url: str,
) -> None:
    sentinel = "z9Q4vL2p7"
    with pytest.raises(ValidationError) as captured:
        make_source(record_url=malformed_url)
    assert sentinel not in repr(captured.value.errors())
    assert sentinel not in str(captured.value)
    assert sentinel not in captured.value.json()
