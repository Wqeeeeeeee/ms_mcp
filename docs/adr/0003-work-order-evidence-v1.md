# ADR 0003: Versioned Work Order Evidence and Source Pins

- Status: Accepted
- Date: 2026-07-20
- Decision owners: Semiconductor Precision Architecture

## Context

Work Order contract 1.0 binds test identities, changed paths, reference access,
and acceptance criterion IDs. It does not require an executed acceptance result
to include an observed value, and reconciliation does not evaluate that value
against the criterion operator. A result can therefore label a criterion
`PASS` without demonstrating that its declared expectation was met.

Reference ingestion also needs the architect to authorize exact source bytes
before implementation starts. A mutable provider URL plus a digest computed
after download proves storage integrity, but it does not prove that the
downloaded provider revision was the one approved by the Work Order.

Finally, infrastructure and provenance checks are not model-validity states.
Assigning those checks to `structure_valid` weakens the meaning of the five
scientific benchmark states.

## Decision

Keep contract 1.0 valid and introduce opt-in Work Order contract 1.1 with
reconciliation contract `work_order_result_reconciliation_v2`.
The exchange schema accepts only the implemented 1.0 and 1.1 versions, so an
unknown future version cannot silently fall back to weaker reconciliation.

Contract 1.1 requires every acceptance criterion to declare an
`acceptance_domain`. Criteria in `model_validity` retain one of the existing
five validity states. Infrastructure, provenance, runtime, and release criteria
use a null validity state so they cannot be mistaken for scientific model
validation.

A reference-data Work Order using `reference_builder` under contract 1.1 must
include machine-readable `source_pins`. Each pin binds the provider, direct
artifact URL, provider revision, expected SHA-256, expected byte count, media
type, SPDX license, license URL, and redistributability before acquisition.
When `reference_builder` is selected, or a Work Order explicitly declares any
pins, allowed sources and receipt sources must exactly equal the pinned artifact
URL set; extra aliases or unpinned artifacts fail reconciliation. Unpinned
`none`, `metadata_only`, and `task_only` workflows retain the v1 authorized-source
subset rule and bind an empty source-pin list. COD pins use the provider's
immutable `<COD-ID>.cif@<revision>` URL form, and reconciliation requires the URL
revision to equal `provider_revision`.

The first approved pin is COD 1010995 revision 278158 at
`https://www.crystallography.net/cod/1010995.cif@278158`. Independent retrieval
of that fixed revision produced 3387 raw bytes with SHA-256
`7bf61ff721dae3b8fa263506aa85e0de5a83bca822744d58e9d30670200eafbb`.

Every executed acceptance result must include `observed`. Reconciliation v2
evaluates the observation using the Work Order's `eq`, `ne`, `lt`, `lte`, `gt`,
`gte`, `contains`, `set_eq`, or `present` operator. Boolean and integer values
remain distinct, while JSON numeric values compare numerically, so `1` equals
`1.0` for scalar, ordered-list, containment, set, and tolerance comparisons.
Ordered-list equality applies the same rule recursively, so `[1]` equals
`[1.0]` but never `[true]`. Numeric equality
may use an explicit non-negative tolerance. Expected values, observations, and
tolerances must be finite JSON numbers; `NaN`, positive or negative infinity,
and exponent-overflow values fail before reconciliation and canonical JSON
hashing uses `allow_nan=false`. A successful status must satisfy
the declared observation, while `FAIL` cannot carry an observation that
satisfies it. `NOT_RUN` has neither observation nor evidence and is not
merge-eligible. The receipt also repeats all source pins and records explicit
source-pin and acceptance-observation reconciliation booleans.

## Compatibility

Contract 1.0 Work Orders and receipts continue to use reconciliation v1 and do
not require source pins, acceptance domains, v2 binding fields, or observed
acceptance values. Version 1.1-only fields are rejected in a 1.0 Work Order.
The existing five benchmark validity states are unchanged. Contract 1.1 does
not add a benchmark state or change any public MCP tool.

## Consequences

Positive consequences:

- acceptance cannot pass solely from a status label when the observed value
  contradicts the Work Order;
- reference bytes are approved by identity before an agent retrieves them;
- source and license evidence remains bound through the result receipt;
- provenance and contract checks no longer imply structural or scientific
  validation;
- existing 1.0 development history remains valid.

Costs and constraints:

- 1.1 receipt producers must copy source pins exactly and emit v2 binding
  fields;
- evidence paths and observations still require architect review; a receipt is
  not an operating-system sandbox proof, and schema validation does not assert
  that a referenced evidence file exists or proves the stated claim;
- schema-invalid exchange documents return a structured rejection; they do not
  authorize semantic reconciliation and must never terminate validation with an
  uncaught input-shape exception;
- reference/candidate root disjointness remains the responsibility of the
  later blind-evaluator contract, not reference ingestion;
- changing an approved provider revision requires a new Work Order and source
  pin rather than an in-place metadata edit.
