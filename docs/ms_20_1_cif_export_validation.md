# Materials Studio 20.1 CIF export validation

## Scope

`validate_crystal_cif_against_spec` remains strict by default. The only
alternative policy is the explicit `materials_studio_20_1_export` literal.
This policy is for a completed CIF export produced by the reviewed Materials
Studio 20.1 CASTEP path. It is not a general structure tolerance and is not
used for CASTEP input CIFs.

No MCP tool name or public input schema changes as part of this policy.

## Numeric contract

The CIF parser retains every lattice and fractional-coordinate numeric lexeme
alongside its parsed float. Export-policy tolerance is derived independently
for each exported token:

```text
min(0.5 * unit_in_last_printed_decimal_place + 1e-12, policy_cap)
```

The decimal place accounts for scientific-notation exponents. A CIF
uncertainty suffix, such as `(2)`, does not add printed numeric precision.
Periodic coordinate residues and lattice deltas are compared from these
decimal lexemes, avoiding binary-float loss at large printed magnitudes.

The fixed caps are:

- Fractional coordinate: `0.000005000001`
- Lattice length or angle: `0.000050000001`

A coarser token never widens either cap. A finer token produces a smaller
per-token tolerance. The strict `coordinate_tolerance` and `lattice_tolerance`
arguments do not participate in export-policy acceptance and therefore cannot
widen these caps.

## Structural contract

Export validation requires the exact atom count and exact elemental
composition before coordinate mapping. It then builds same-element edges from
periodic fractional-coordinate deltas and each exported coordinate token's
tolerance. A deterministic augmenting-path bipartite match computes maximum
coverage. Exported labels do not create matching edges and affect only label
preservation diagnostics.

If the same-element graph has more than one perfect matching, the receipt
reports `mapping_ambiguous=true`. In that case `labels_preserved` is unknown,
and `label_set_preserved` reports the assignment-independent label-set result.
This keeps label diagnostics independent of CIF row order.

Validation fails closed for any of the following:

- Missing, extra, or composition-changing atoms
- Incomplete same-element periodic mapping
- Fractional displacement beyond any corresponding token tolerance
- Lattice length or angle displacement beyond its token tolerance
- Missing or malformed CIF content

The receipt reports the policy, mapping method and coverage, label status,
per-token tolerance derivation, applied caps, mismatch details, and stable
rejection reasons.

## CASTEP artifact boundary

The CASTEP electronic workflow applies the policy in this order:

1. Materialize and validate the CASTEP input CIF with strict validation.
2. Require a successful runner result and a validated `Materials Studio 20.1`
   tagged result before applying the export policy to the result CIF.
3. Preserve the exact validated export as `materials_studio_export.cif`,
   revalidate that copy with `materials_studio_20_1_export`, and require its
   SHA-256 to equal the already validated runner output. The preserved export
   is also included in the revision's hash-verified native-artifact manifest,
   so later file substitution invalidates the electronic receipt binding.
4. Materialize the unchanged result revision's canonical CIF from the source
   `CrystalSpec` and validate that artifact strictly for existing downstream
   receipt verification.

An invalid composition or geometry is rejected before canonical result
materialization and creates no result revision. The preserved fake-runner tests
are contract tests only; they are not real Materials Studio or CASTEP
acceptance evidence.
