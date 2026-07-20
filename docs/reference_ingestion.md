# Reference Ingestion v1

## Scope

`material_studio_mcp_server.reference_data` is an internal, offline package for
reviewed reference bytes and provenance. It is not imported by the server entry
path, registers no MCP tool, changes no public schema, and has no access to
candidate state, model revisions, GUI control, runners, translators, parsers,
or orchestration.

The package performs these operations only:

1. Hash an explicitly supplied `bytes` object before any content processing.
2. Validate explicit source, retrieval, citation, format, and license metadata.
3. Build deterministic canonical source and manifest JSON.
4. Publish exact evidence beneath an explicitly supplied reference-store root.
5. Reread and verify every published byte before returning a receipt.
6. Project only allowlisted identity, license, hash, count, format, citation,
   retrieval, and relative-location metadata.

It does not fetch URLs, read credentials or environment configuration, inspect
arbitrary local files, parse CIF, normalize structures, infer symmetry, compare
coordinates, construct a `ModelSpec` or `SemanticPatch`, launch a process, or
touch Materials Studio or CASTEP.

## Contracts

All reference models inherit the existing frozen runtime contract base. They
are Pydantic v2 models with `strict=True`, `frozen=True`, and `extra="forbid"`.
Evidence booleans reject integer and string coercion. Retrieval timestamps are
explicit RFC 3339 strings with a required timezone; no clock default exists.

Required provenance includes:

- provider and stable provider record ID;
- direct record and artifact HTTPS URLs;
- optional provider revision and citation;
- caller-supplied retrieval timestamp and purpose, plus an optional exact query;
- bounded, allowlisted non-secret request headers;
- media type and declared structure format;
- license name, optional SPDX ID, license URL, and strict redistributability.

URL validation rejects userinfo, fragments, non-HTTPS URLs, malformed ports,
control characters, backslashes, and credential-bearing query keys. Header
validation permits only `Accept` and `User-Agent`, with at most eight unique,
sorted entries. Secret-bearing rejected values are redacted before Pydantic
constructs validation-error input evidence, so `ValidationError.errors()` does
not echo a credential sentinel.

## Exact-Byte Policy

`fingerprint_raw_bytes` accepts only an exact `bytes` instance. It rejects an
empty artifact and enforces a fixed 16 MiB maximum. SHA-256 and byte length are
computed before decoding, newline conversion, parsing, compression, repair, or
canonicalization. Raw content is never decoded by the package.

If `expected_sha256` is supplied, a mismatch fails before the reference root is
created. The raw object is reread after publication and checked against both the
original byte sequence and its digest. Verification rereads the object without
returning it.

Duplicate classification is intentionally limited to:

```text
same byte length AND same SHA-256 -> exact_raw_duplicate
otherwise                       -> byte_different_unresolved
```

No result claims CIF equivalence, crystallographic equivalence, normalized
equivalence, symmetry equivalence, or structural equivalence. Provider aliases
and byte-different CIFs remain unresolved for later infrastructure.

## Store Layout

Paths use fixed package-owned names and lowercase content digests:

```text
<reference-root>/
  control/store.lock
  raw/sha256/<first-two>/<raw-sha256>.bin
  sources/sha256/<first-two>/<source-sha256>.json
  manifests/sha256/<first-two>/<manifest-sha256>.json
```

The raw path does not use a caller filename, provider ID, URL path, or query.
Source and manifest JSON use the existing sorted-key compact UTF-8 canonical
profile, with no BOM or trailing newline. Receipts contain no absolute path,
UUID, PID, hostname, random value, implicit timestamp, or directory-order
result. The same request returns the same receipt in a fresh, existing, or
separate store.

Publication is create-only and serialized per store. A complete byte-identical
repeat is idempotent. Conflicting content, an incomplete prior transaction, or
corruption fails closed. Published evidence is never overwritten, truncated,
deleted, or repaired in place.

Before each directory creation, file creation, and file read, the store checks
the resolved root boundary, every existing ancestor of the selected root, and
every existing internal component. It rejects traversal, a non-directory root,
symlinks, Windows reparse points, hard-linked files, resolved-path escape, and
unexpected file types. Reads require an explicit byte ceiling and reject an
unexpected size from `fstat` before allocating content. These checks enforce the
package path boundary; they are not an operating-system sandbox and do not claim
blind evaluator isolation.

## Safe Projection

`verify_ingestion` returns `ReferenceMetadataProjection`. Its closed field set
contains source identity and URLs, retrieval context, citation, license,
SHA-256 values, byte count, media type, structure format, and immutable relative
locations. It contains no raw bytes, atom sites, coordinate fields, coordinate
excerpts, lattice vectors, or lattice-derived values.

The ingestion receipt likewise contains only deterministic hashes, counts,
relative locations, and strict safety evidence. Neither object is a candidate
template or modeler prompt payload.

A completed receipt is constructed only after the raw object, canonical source,
and canonical manifest have been persisted and reread successfully. The package
does not export a receipt-construction helper; `ingest_reference` is the only
package-level API that issues completed ingestion evidence.

## COD Development Snapshot

The development fixture is an immutable snapshot of COD record 1010995,
revision 278158:

- record URL: `https://www.crystallography.net/cod/1010995.html`
- artifact URL: `https://www.crystallography.net/cod/1010995.cif@278158`
- license: `CC0-1.0`
- raw byte count: `3387`
- raw SHA-256: `7bf61ff721dae3b8fa263506aa85e0de5a83bca822744d58e9d30670200eafbb`
- source SHA-256: `31c04bc038b7d4ce3bfced24c189e1c2e3939ef23c4c7eae8d384cb80402ed6b`
- manifest SHA-256: `97bf9304eeffad3bdbe1d58d272719dc1a33ee18660e9f06961ad30b917882b1`
- deterministic receipt SHA-256: `43b75cb8d19616ddb6c2bc549cba6fcc19e1663fd56813d58c4063f2ca96f7dc`

The retrieval timestamp is explicitly pinned from the reviewed local source
transfer evidence. The raw object was copied only through the ingestion API
after its architect-pinned length and SHA-256 were rechecked. Its local
`.gitattributes` marks that exact object as binary so Git clean filters cannot
rewrite line endings when `core.autocrlf=true`.

This fixture is development evidence only. It is not validation data, a hidden
holdout, a candidate template, a reconstructed structure, or Materials Studio
or CASTEP acceptance evidence.

## API

```python
from material_studio_mcp_server.reference_data import (
    ReferenceLicense,
    ReferenceSource,
    RetrievalContext,
    ingest_reference,
    verify_ingestion,
)

source = ReferenceSource(
    source_id="reviewed-source",
    provider="Reviewed Provider",
    provider_record_id="record-1",
    provider_revision="1",
    record_url="https://provider.example/records/1",
    artifact_url="https://provider.example/artifacts/1.cif",
    retrieval=RetrievalContext(
        retrieved_at="2026-07-20T00:00:00Z",
        retrieval_purpose="Reviewed development reference",
    ),
    media_type="chemical/x-cif",
    structure_format="cif",
    citation=None,
    license=ReferenceLicense(
        name="Reviewed license",
        spdx_id=None,
        url="https://provider.example/license",
        redistributable=True,
    ),
)

receipt = ingest_reference(
    reference_store_root=reference_root,
    raw_bytes=reviewed_bytes,
    source=source,
    expected_sha256=reviewed_sha256,
)
safe_metadata = verify_ingestion(
    reference_store_root=reference_root,
    receipt=receipt,
)
```

No acquisition helper is provided. Supplying `reviewed_bytes` and reviewed
metadata is an explicit authoring step outside package execution.

## Verification Boundary

The required test commands are defined by `WO-REFERENCE-001` and must use the
repository virtual environment. The focused reference tests cover strict
validation, unsafe timestamps/URLs/headers, secret-error redaction, digest and
size failures, repeat ingestion, collisions, interrupted residue, concurrent
thread and independent-process ingestion, deterministic evidence, traversal,
Windows ancestor/internal junction/reparse and hard-link attacks, bounded
corruption reads, raw-only deduplication, development fixture integrity,
coordinate-free projection, candidate non-write evidence, offline imports, and
the unchanged public MCP tool inventory and modeling-request schema.

Quantitative 3C-SiC reconstruction metrics, canonicalization, blind evaluator
root isolation, validation data, hidden holdouts, real Materials Studio, and
real CASTEP are `NOT_RUN` for this infrastructure change. No modeling precision
improvement or scientific verification is claimed.
