# CASTEP Energy native-artifact policy

## Purpose

Materials Studio 20.1 may emit a native `.bands` file during a CASTEP
`Energy` task. File-extension presence alone therefore cannot establish that a
`BandStructure` task ran.

`validate_castep_energy_artifact_policy` is an internal, read-only acceptance
gate. It reads the latest electronic receipt from one immutable `ModelSpec`,
calls the official receipt verifier itself, and combines that result with the
exact result revision's `native_artifacts` directory. It does not register an
MCP tool, execute CASTEP, or modify project state.

## PASS contract

An Energy artifact set passes only when:

- the electronic receipt is already reported as bound and verified;
- the receipt, verification summary, and native audit all identify `Energy`;
- every native artifact is opened without following links where the platform
  supports it, remains bound to the same single-link regular-file handle
  through the final root scan, is confined to the supplied native artifact
  root, and matches its manifest size and SHA-256;
- recursive discovery validates each directory entry before descending, never
  follows a link or Windows reparse point, and rechecks the complete directory
  identity and `.bands` path snapshot before releasing artifact handles;
- on Windows, each directory is opened with reparse-point semantics and a
  read-shared native enumeration handle that prevents deletion, rename, or
  junction replacement until traversal and final identity checks finish;
- Windows artifacts use read-only native handles that do not share writes or
  deletion; every retained artifact descriptor is hashed again immediately
  before the policy can pass;
- on POSIX, directory enumeration, child-directory opens, entry metadata, and
  artifact opens remain relative to retained `O_NOFOLLOW` directory file
  descriptors rather than returning to replaceable absolute paths;
- every `.bands` file under that root appears exactly once in the manifest;
- at most one native `.bands` artifact exists and its audit source binding
  matches the same file;
- no native band k-point path or numeric curve is exported;
- the numeric curve kind is absent;
- derived artifact manifests and counts are empty;
- scientific band-gap and convergence claims and verification flags remain
  false.

The receipt and verifier summary must also be canonical finite JSON. The
returned policy receipt contains only normalized task values, stable reason
codes, counts, relative artifact paths, sizes, and digests. It never returns
rejected input strings, absolute paths, or native file content.

## Failure boundary

The policy rejects `BandStructure`, unverified receipts, path escape, unsafe or
missing files, duplicate manifest paths, unmanifested `.bands` files, binding
mismatches, multiple `.bands` files, derived data, numeric property exports,
and scientific claims.

This contract does not change scientific convergence thresholds or convert
sampled Fermi-referenced band data into a verified band gap. It is an artifact
classification and provenance gate only.

## Acceptance integration

After this shared policy is merged, the private real CASTEP acceptance branch
may replace only its workspace-wide assertion that no `.bands` file exists.
The caller must pass the current result revision's exact native artifact root,
plus the immutable current `ModelSpec`; the policy invokes
`verify_castep_electronic_receipt` internally. Execution, structure identity,
result revision, GUI, benchmark, and evidence publication gates remain
unchanged.
