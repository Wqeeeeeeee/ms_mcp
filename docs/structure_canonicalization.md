# Structure Canonicalization and Comparison v1

## Scope

`material_studio_mcp_server.canonicalization` is internal evaluator
infrastructure for periodic crystals. It accepts explicit immutable bytes or
in-memory structures, canonicalizes them, maps atoms by species, and reports
measurements. It is not imported by the MCP server entry path, registers no
tool, changes no public schema, and does not access a workspace, environment
configuration, network, process, GUI, runner, translator, model revision, or
candidate output path.

The implementation was produced for `WO-CANONICAL-001`. Its validator
canonical-JSON SHA-256, distinct from the raw file-byte digest, is
`e32182cf19cc5acc4b45ce15b46c7ead3cf00e15aa8dcc0623ab0740079d2aac`.

## Dependencies and Contracts

The numerical kernel is NumPy `>=1.26,<3`. Crystallographic standardization,
space-group classification, and Wyckoff classification use spglib `>=2.7,<3`,
which is BSD-3-Clause licensed. No substitute symmetry implementation is used.
Each spglib call is wrapped locally. The wrapper catches `SpglibError`, retains
the spglib 2.7 `None` checks, and suppresses only the known 2.7 transition
warning. It does not mutate spglib global error state.

Contracts use a package-local Pydantic v2 base with `strict=True`,
`frozen=True`, `extra="forbid"`, `allow_inf_nan=False`, default validation, and
instance revalidation. This base intentionally permits repeated vector and
matrix components. Evidence booleans additionally require exact `bool`
literals. Canonical JSON bytes and contract digests reuse the runtime canonical
serialization helpers without modifying runtime code.

Cross-field validators bind settings, structure and projection identities;
composition and atom counts; mapping coverage, indices and species; derived
norm, RMS, maximum, lattice and strain values; canonical coordinate range and
site order; and reference evidence paths. Derived arithmetic must remain
finite. Reference paths are exact digest-derived POSIX paths under `raw`,
`sources`, and `manifests`; absolute, non-canonical, device, ADS, validation,
and hidden-holdout forms fail closed.

## Bounded CIF Input

The parser implements a closed CIF 1.x subset for scalar cell parameters,
fractional atom-site loops, full occupancy, and explicit symmetry-operation
loops. It does not reuse the generated-P1 parser in `parsers/cif.py`.

Input limits are enforced before expensive operations:

- CIF input: 16 MiB, 500,000 lines, 2,000,000 tokens;
- line or token text: 65,536 characters;
- loop width: 256 columns and 200,000 rows;
- symmetry operations: 4,096, with at most 2,000,000 closure checks;
- expanded sites: 100,000;
- source or manifest JSON supplied to artifact construction: 1 MiB each;
- canonical artifact JSON supplied to verification: 32 MiB.

The semicolon text-field limit uses a running character count, so tokenization
is linear in input size. CIF data names are unique across the complete block,
including scalar and loop occurrences. Numeric tokens must match the supported
CIF lexical grammar before conversion; Python-only numeric forms are rejected.
Supported atom-site disorder data names are rejected wherever they occur in the
block, including detached scalars and separate loops.

Symmetry expressions use a closed rational parser over `x`, `y`, `z`, signs,
integers, and fractions. There is no `eval`, `exec`, AST evaluation, dynamic
import, or shell helper. The operation set must contain the affine identity and
must be closed under composition modulo lattice translations. Expansion may
collapse stabilizer repeats generated from one asymmetric row. A periodic
collision originating from a different asymmetric row is malformed input and
is rejected, including when the species match.

Duplicate-site budgets count the actual exact minimum-image candidates
examined, not only site pairs. Every search receives only the remaining
aggregate allowance. A skew cell that requires more geometric work therefore
fails before the configured budget can be exceeded.

## Canonicalization

The default profile requests a conventional standard cell, no idealization,
spglib symmetry precision of `1e-5` angstrom, automatic angle tolerance,
right-handed row-vector lattices, wrapping into `[0,1)`, and 12-decimal output
quantization. Primitive mode is separately supported. Settings are immutable
and SHA-256 bound to every canonical structure.

Canonicalization rejects non-finite data, nonpositive or singular lattice
determinants, partial occupancy, duplicate periodic sites, malformed spglib
results, quantization outside the declared error limits, and unsupported atom
counts. Symmetry and Wyckoff labels are recomputed from the final standardized
cell rather than retained from input.

Only the 24 determinant-positive signed axis permutations are enumerated.
Cartesian orientation removal also verifies a determinant-positive rotation.
Reflections and inversion are never orientation equivalences. A cubic bulk
direction and its opposite can still be related by a proper cubic rotation;
fixed surface-normal polarity and termination remain later domain-semantic
gates.

For each proper basis, origin normalization uses the least-populated species.
Basis and origin selection use settings-bound quantized lattice and periodic
coordinate values as the primary key. When complete primary keys are equal,
exact finite wrapped or oriented values provide a deterministic secondary key
so input traversal order cannot choose the representative. The secondary key
is considered only after quantized equality, while calculation geometry stays
unquantized until the selected representative reaches the bounded quantization
stage. This preserves the boundary-roundoff stabilization while resolving true
selection ties deterministically. Quantization
normalizes negative zero, then sites are ordered by atomic number and exact
canonical coordinates. A `CanonicalStructure` contract accepts only wrapped,
deterministically ordered content, and the comparator and exported direct
mapping entry both require a supplied canonical object to reproduce exactly
under its declared settings. Nonzero finite components are never
tolerance-zeroed during wrapping or serialization.
Raw spglib equivalence indices must be integral, in range, and idempotently
self-representative before remapping. Equivalence classes use gap-free
first-occurrence numbering (`0, 1, ...`), and each class is restricted to one
species and one Wyckoff letter.

## Exact Periodic Geometry

`closest_lattice_image` solves the three-dimensional closest lattice-vector
problem for a non-orthogonal row-vector lattice. It starts from a valid rounded
translation and derives a finite search radius from a conservative lower bound
on the smallest singular value. If floating-point error leaves no reliable
positive lower bound, the call fails. Search endpoints are checked against the
exact-integer range, converted to Python integers, and multiplied as Python
integers before iteration. The candidate-count limit is checked first.

Every representable decrease in squared distance replaces the current minimum;
numeric tolerance never preserves a farther lexical translation. Tolerance is
used only to report numerical distance degeneracy, while an exactly equal
minimum selects the lexical translation deterministically.

`MinimumImageResult` rechecks that Cartesian norm and declared distance agree.
It does not carry a lattice, so its fractional-to-Cartesian relation cannot be
rederived by the standalone Pydantic contract. That relation is
function-produced evidence: `closest_lattice_image` constructs both vectors
from the same validated lattice and displacement. Callers must not manufacture
this result as independent evidence.

## Comparator

Comparison rejects atom-count or composition differences before mapping and
does not mutate either input. Periodic inputs are canonicalized. Canonical
inputs must match the comparator settings and reproduce as actual canonical
results.

The mapper enumerates global origins through one deterministic anchor species,
retaining exact finite origin components after a rounded lexical prefix.
Per-species cost matrices use exact bounded minimum images and charge every
examined image candidate to the shared assignment work budget. A deterministic
Hungarian solver finds the primary minimum, checks all finite intermediate
potentials and totals, and enumerates bounded dual-tight assignments using a
local reduced-cost tolerance. All candidate totals are recomputed, the strict
numeric minimum is identified without matrix-outlier scaling, and only totals
numerically indistinguishable from that minimum are retained as degenerate
candidates. They are ordered by exact recomputed total first and lexical
identity second, so a near-tied higher-cost mapping cannot replace the strict
minimum. The reported assignment cost is recomputed for the selected
representative.
Standalone assignment matrices are limited to 4,096 rows and 16,777,216
entries. Their minimum inspection and solver work is checked before any row is
materialized or NumPy storage is allocated. Matrix inspection and tight-edge
construction spend the same work budget, and equal-cost traversal uses an
iterative deterministic backtracker rather than Python recursion.

Under amended decision C14, symmetry-equivalent mapping degeneracy is retained
instead of rejected. The deterministic representative is accepted only when all tied
mappings preserve the same species/Wyckoff/equivalence identity and produce the
same displacement metrics within the configured numeric tolerance. A semantic
or material metric difference fails closed. Stored displacements are ordered by
reference index, and the global fractional origin shift is wrapped into
`[0, 1)` so periodic-equivalent mappings have one contract identity.

Reported measurements include mapping coverage, per-atom fractional and
Cartesian minimum-image displacement, RMS and maximum displacement, lattice
lengths and angles, relative length and angle differences, deformation
gradient, symmetric strain, and determinant ratio. These are measurements only;
the comparator always reports scientific verification as `not_assessed`.

## Reference Artifact

The authorized development artifact is coordinate-bearing evaluator evidence
and is confined to:

`benchmarks/references/development/sic_3c_bulk/canonical/sha256/bb/bba4b03dd57d55816c21cdc32cd687362af6d32ad3da6823749c59abf019a781.json`

Its evidence identities are:

- raw byte count: `3387`;
- raw SHA-256: `7bf61ff721dae3b8fa263506aa85e0de5a83bca822744d58e9d30670200eafbb`;
- source SHA-256: `31c04bc038b7d4ce3bfced24c189e1c2e3939ef23c4c7eae8d384cb80402ed6b`;
- manifest SHA-256: `97bf9304eeffad3bdbe1d58d272719dc1a33ee18660e9f06961ad30b917882b1`;
- settings SHA-256: `6c1aca62ddd2ce2862e670c259c0accfbc0789130fa973d65e75c307fc3161b8`;
- canonical structure SHA-256: `fdb07c9079a70220a1319e3ca95171d23b975a55ea53b32ed219007b41e6b759`;
- canonical artifact SHA-256: `bba4b03dd57d55816c21cdc32cd687362af6d32ad3da6823749c59abf019a781`;
- canonical artifact byte count: `3643`.

Artifact construction and verification accept explicit bytes only. They check
all byte-size limits before hashing or JSON parsing, validate canonical source
and manifest records, bind exact digest-derived relative paths, rebuild the
canonical structure, and require canonical artifact bytes to reproduce exactly.
They perform no filesystem discovery or publication. The original raw, source,
and manifest objects remain unchanged.

## Projection and Isolation

Coordinate-free projections expose identities, counts, composition, symmetry,
coverage, and aggregate metrics only. They contain no coordinate vectors,
lattice vectors, atom mapping, or coordinate excerpts. The coordinate-bearing
canonical artifact is not a candidate template and must not enter a modeler
prompt, ordinary log, result receipt, public MCP response, or test identifier.

Development tests access only the declared development evidence paths.
Validation and hidden-holdout roots are neither read nor created. Isolation
tests block network, subprocess, GUI/state integration, and candidate writes at
the package boundary and verify that the public server does not import this
package. This is an API and side-effect boundary, not an operating-system
sandbox.

No Materials Studio or CASTEP run is performed here. Surface polarity,
termination semantics, method comparability, quantitative acceptance
thresholds, and blind benchmark verdicts belong to later Work Orders.
