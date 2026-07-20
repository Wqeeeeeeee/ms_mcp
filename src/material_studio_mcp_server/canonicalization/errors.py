"""Closed error surface for offline crystal canonicalization."""

from __future__ import annotations


class CanonicalizationError(ValueError):
    """Base class for rejected canonicalization inputs or results."""


class CifParseError(CanonicalizationError):
    """Raised when a CIF is outside the bounded supported subset."""


class SymmetryExpressionError(CifParseError):
    """Raised when a symmetry expression is malformed or unsupported."""


class LatticeError(CanonicalizationError):
    """Raised when periodic lattice geometry is invalid or exceeds a bound."""


class StandardizationError(CanonicalizationError):
    """Raised when crystallographic standardization cannot be verified."""


class CompositionMismatchError(CanonicalizationError):
    """Raised before assignment when atom counts or species differ."""


class MappingAmbiguityError(CanonicalizationError):
    """Raised when an equal-cost mapping changes scientific metrics or identity."""


class AssignmentWorkLimitError(CanonicalizationError):
    """Raised when deterministic assignment exceeds its configured work limit."""


class ArtifactBindingError(CanonicalizationError):
    """Raised when canonical reference evidence does not reconcile exactly."""


__all__ = [
    "ArtifactBindingError",
    "AssignmentWorkLimitError",
    "CanonicalizationError",
    "CifParseError",
    "CompositionMismatchError",
    "LatticeError",
    "MappingAmbiguityError",
    "StandardizationError",
    "SymmetryExpressionError",
]
