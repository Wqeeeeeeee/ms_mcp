"""Bounded CIF 1.x parsing for reviewed periodic coordinate evidence."""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from fractions import Fraction

from ._elements import infer_label_symbol, normalize_type_symbol
from .contracts import (
    AffineSymmetryOperation,
    AtomSite,
    Matrix3,
    ParsedCif,
    PeriodicStructure,
    RationalValue,
)
from .errors import CifParseError, LatticeError, SymmetryExpressionError
from .lattice import closest_lattice_image


MAX_CIF_BYTES = 16 * 1024 * 1024
MAX_CIF_LINES = 500_000
MAX_LINE_CHARACTERS = 65_536
MAX_TOKEN_CHARACTERS = 65_536
MAX_CIF_TOKENS = 2_000_000
MAX_LOOP_COLUMNS = 256
MAX_LOOP_ROWS = 200_000
MAX_SYMMETRY_OPERATIONS = 4096
MAX_SYMMETRY_CLOSURE_CHECKS = 2_000_000
MAX_EXPANDED_SITES = 100_000
MAX_DUPLICATE_CHECKS = 10_000_000

_UNCERTAINTY_SUFFIX = re.compile(r"^(.*?)(?:\([0-9]+\))$")
_INTEGER_OR_FRACTION = re.compile(r"^[0-9]+(?:/[0-9]+)?$")
_CIF_DECIMAL_NUMBER = re.compile(
    r"^[+-]?(?:(?:[0-9]+(?:\.[0-9]*)?)|(?:\.[0-9]+))"
    r"(?:[eEdD][+-]?[0-9]+)?(?:\([0-9]+\))?$"
)
_CIF_FRACTION_NUMBER = re.compile(r"^[+-]?[0-9]+/[0-9]+$")
_DATA_NAME = re.compile(r"^_[A-Za-z0-9_.-]+$")
_DATA_BLOCK = re.compile(r"^data_([A-Za-z0-9_.-]+)$", re.IGNORECASE)

_CELL_TAGS = {
    "_cell_length_a",
    "_cell_length_b",
    "_cell_length_c",
    "_cell_angle_alpha",
    "_cell_angle_beta",
    "_cell_angle_gamma",
}
_SYMMETRY_TAGS = {
    "_space_group_symop_operation_xyz",
    "_symmetry_equiv_pos_as_xyz",
}
_FRACTIONAL_TAGS = (
    "_atom_site_fract_x",
    "_atom_site_fract_y",
    "_atom_site_fract_z",
)
_DISORDER_TAGS = frozenset(
    {"_atom_site_disorder_group", "_atom_site_disorder_assembly"}
)


@dataclass(frozen=True)
class _Token:
    value: str
    quoted: bool
    line_number: int


@dataclass(frozen=True)
class _Loop:
    headers: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]


def _fail(message: str, token: _Token | None = None) -> CifParseError:
    if token is None:
        return CifParseError(message)
    return CifParseError(f"{message} at CIF line {token.line_number}")


def _tokenize(text: str) -> tuple[_Token, ...]:
    lines = text.splitlines(keepends=True)
    if len(lines) > MAX_CIF_LINES:
        raise CifParseError("CIF exceeds the line-count bound")

    tokens: list[_Token] = []
    text_field_lines: list[str] | None = None
    text_field_characters = 0
    text_field_start = 0

    def append(value: str, quoted: bool, line_number: int) -> None:
        if not value:
            raise _fail("empty CIF token is unsupported")
        if len(value) > MAX_TOKEN_CHARACTERS:
            raise _fail("CIF token exceeds the character bound")
        if len(tokens) >= MAX_CIF_TOKENS:
            raise CifParseError("CIF exceeds the token-count bound")
        tokens.append(_Token(value=value, quoted=quoted, line_number=line_number))

    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.rstrip("\r\n")
        if len(line) > MAX_LINE_CHARACTERS:
            raise CifParseError("CIF line exceeds the character bound")

        if text_field_lines is not None:
            if line.startswith(";"):
                append("\n".join(text_field_lines), True, text_field_start)
                text_field_lines = None
                text_field_characters = 0
                if line[1:].strip():
                    raise _fail("text-field terminator must occupy its line")
            else:
                text_field_characters += 1 + len(line)
                if text_field_characters > MAX_TOKEN_CHARACTERS:
                    raise CifParseError("CIF text field exceeds the character bound")
                text_field_lines.append(line)
            continue

        if line.startswith(";"):
            text_field_lines = [line[1:]]
            text_field_characters = len(line) - 1
            text_field_start = line_number
            continue

        index = 0
        line_length = len(line)
        while index < line_length:
            while index < line_length and line[index].isspace():
                index += 1
            if index >= line_length or line[index] == "#":
                break
            if line[index] in {"'", '"'}:
                quote = line[index]
                index += 1
                start = index
                while index < line_length and line[index] != quote:
                    index += 1
                if index >= line_length:
                    raise _fail(
                        "unterminated quoted CIF token",
                        _Token("quoted", True, line_number),
                    )
                value = line[start:index]
                index += 1
                if index < line_length and not line[index].isspace() and line[index] != "#":
                    raise _fail(
                        "quoted CIF token is not whitespace-delimited",
                        _Token(value, True, line_number),
                    )
                append(value, True, line_number)
                continue

            start = index
            while index < line_length and not line[index].isspace() and line[index] != "#":
                if line[index] in {"'", '"'}:
                    raise _fail(
                        "quote inside an unquoted CIF token is unsupported",
                        _Token(line[start : index + 1], False, line_number),
                    )
                index += 1
            append(line[start:index], False, line_number)
            if index < line_length and line[index] == "#":
                break

    if text_field_lines is not None:
        raise CifParseError("unterminated CIF semicolon text field")
    return tuple(tokens)


def _is_control(token: _Token) -> bool:
    if token.quoted:
        return False
    value = token.value.casefold()
    return (
        value == "loop_"
        or value == "stop_"
        or value == "global_"
        or value.startswith("save_")
        or value.startswith("data_")
        or value.startswith("_")
    )


def _parse_document(tokens: tuple[_Token, ...]) -> tuple[str, dict[str, str], tuple[_Loop, ...]]:
    if not tokens:
        raise CifParseError("CIF is empty")
    data_match = _DATA_BLOCK.fullmatch(tokens[0].value)
    if tokens[0].quoted or data_match is None:
        raise _fail("CIF must begin with one named data block", tokens[0])
    block_name = data_match.group(1)
    scalars: dict[str, str] = {}
    loops: list[_Loop] = []
    seen_data_names: set[str] = set()
    index = 1

    while index < len(tokens):
        token = tokens[index]
        lowered = token.value.casefold()
        if not token.quoted and lowered.startswith("data_"):
            raise _fail("multiple CIF data blocks are unsupported", token)
        if not token.quoted and (
            lowered in {"stop_", "global_"} or lowered.startswith("save_")
        ):
            raise _fail("CIF save/global/stop controls are unsupported", token)
        if not token.quoted and lowered == "loop_":
            index += 1
            headers: list[str] = []
            while index < len(tokens):
                header = tokens[index]
                if header.quoted or _DATA_NAME.fullmatch(header.value) is None:
                    break
                normalized = header.value.casefold()
                if normalized in seen_data_names:
                    raise _fail("duplicate CIF data name in one block", header)
                headers.append(normalized)
                seen_data_names.add(normalized)
                index += 1
                if len(headers) > MAX_LOOP_COLUMNS:
                    raise _fail("CIF loop exceeds the column bound", header)
            if not headers:
                raise _fail("CIF loop has no headers", token)
            values: list[str] = []
            while index < len(tokens) and not _is_control(tokens[index]):
                values.append(tokens[index].value)
                index += 1
                if len(values) > len(headers) * MAX_LOOP_ROWS:
                    raise _fail("CIF loop exceeds the row bound", token)
            if not values or len(values) % len(headers) != 0:
                raise _fail("CIF loop row width is malformed", token)
            rows = tuple(
                tuple(values[offset : offset + len(headers)])
                for offset in range(0, len(values), len(headers))
            )
            loops.append(_Loop(headers=tuple(headers), rows=rows))
            continue
        if not token.quoted and _DATA_NAME.fullmatch(token.value):
            key = lowered
            if key in seen_data_names:
                raise _fail("duplicate CIF data name in one block", token)
            seen_data_names.add(key)
            index += 1
            if index >= len(tokens) or _is_control(tokens[index]):
                raise _fail("CIF scalar is missing its value", token)
            scalars[key] = tokens[index].value
            index += 1
            continue
        raise _fail("unexpected CIF token outside a data item or loop", token)

    return block_name, scalars, tuple(loops)


def _number(value: str, *, label: str) -> float:
    if value in {".", "?"}:
        raise CifParseError(f"{label} is missing")
    is_fraction = _CIF_FRACTION_NUMBER.fullmatch(value) is not None
    if not is_fraction and _CIF_DECIMAL_NUMBER.fullmatch(value) is None:
        raise CifParseError(f"{label} is not a supported CIF numeric token")
    match = _UNCERTAINTY_SUFFIX.fullmatch(value)
    if match is not None and not is_fraction:
        value = match.group(1)
    try:
        if is_fraction:
            result = float(Fraction(value))
        else:
            result = float(value.replace("D", "E").replace("d", "e"))
    except (ValueError, ZeroDivisionError) as exc:
        raise CifParseError(f"{label} is not a supported numeric value") from exc
    if not math.isfinite(result):
        raise CifParseError(f"{label} must be finite")
    return result


def _lattice_from_scalars(scalars: dict[str, str]) -> Matrix3:
    missing = sorted(_CELL_TAGS.difference(scalars))
    if missing:
        raise CifParseError("CIF is missing required cell parameters")
    a = _number(scalars["_cell_length_a"], label="cell length a")
    b = _number(scalars["_cell_length_b"], label="cell length b")
    c = _number(scalars["_cell_length_c"], label="cell length c")
    alpha = _number(scalars["_cell_angle_alpha"], label="cell angle alpha")
    beta = _number(scalars["_cell_angle_beta"], label="cell angle beta")
    gamma = _number(scalars["_cell_angle_gamma"], label="cell angle gamma")
    if min(a, b, c) <= 0.0:
        raise CifParseError("cell lengths must be positive")
    if any(not 0.0 < angle < 180.0 for angle in (alpha, beta, gamma)):
        raise CifParseError("cell angles must lie strictly between zero and 180 degrees")

    alpha_r, beta_r, gamma_r = map(math.radians, (alpha, beta, gamma))
    sin_gamma = math.sin(gamma_r)
    if abs(sin_gamma) <= 1.0e-12:
        raise CifParseError("cell gamma produces a singular lattice")
    c_x = c * math.cos(beta_r)
    c_y = c * (
        math.cos(alpha_r) - math.cos(beta_r) * math.cos(gamma_r)
    ) / sin_gamma
    c_z_squared = c * c - c_x * c_x - c_y * c_y
    scale = max(a * a, b * b, c * c, 1.0)
    if c_z_squared <= 1.0e-14 * scale:
        raise CifParseError("cell parameters produce a singular or left-handed lattice")
    lattice: Matrix3 = (
        (a, 0.0, 0.0),
        (b * math.cos(gamma_r), b * sin_gamma, 0.0),
        (c_x, c_y, math.sqrt(c_z_squared)),
    )
    return lattice


def parse_symmetry_expression(value: str) -> AffineSymmetryOperation:
    """Parse one affine x,y,z operation with a closed rational grammar."""

    if not isinstance(value, str):
        raise TypeError("symmetry expression must be a string")
    if len(value) > 512 or any(ord(character) < 32 for character in value):
        raise SymmetryExpressionError("symmetry expression is outside the text bound")
    components = value.split(",")
    if len(components) != 3:
        raise SymmetryExpressionError("symmetry expression must have three components")

    rotation_rows: list[tuple[int, int, int]] = []
    translations: list[RationalValue] = []
    for component in components:
        expression = "".join(component.split()).casefold()
        if not expression or len(expression) > 128:
            raise SymmetryExpressionError("symmetry component is empty or too long")
        coefficients = [0, 0, 0]
        constant = Fraction(0, 1)
        position = 0
        while position < len(expression):
            sign = 1
            if expression[position] in "+-":
                sign = -1 if expression[position] == "-" else 1
                position += 1
            elif position != 0:
                raise SymmetryExpressionError("symmetry terms must be sign-delimited")
            start = position
            while position < len(expression) and expression[position] not in "+-":
                position += 1
            term = expression[start:position]
            if not term:
                raise SymmetryExpressionError("symmetry expression contains an empty term")
            if term in {"x", "y", "z"}:
                axis = {"x": 0, "y": 1, "z": 2}[term]
                coefficients[axis] += sign
                if abs(coefficients[axis]) > 1:
                    raise SymmetryExpressionError(
                        "symmetry variable coefficient is outside -1, 0, 1"
                    )
            elif _INTEGER_OR_FRACTION.fullmatch(term):
                try:
                    constant += sign * Fraction(term)
                except (ValueError, ZeroDivisionError) as exc:
                    raise SymmetryExpressionError("invalid symmetry fraction") from exc
            else:
                raise SymmetryExpressionError("unsupported symmetry term")
        constant %= 1
        if constant.denominator > 1_000_000:
            raise SymmetryExpressionError("symmetry fraction denominator exceeds the bound")
        rotation_rows.append(tuple(coefficients))  # type: ignore[arg-type]
        translations.append(
            RationalValue(
                numerator=constant.numerator,
                denominator=constant.denominator,
            )
        )
    try:
        return AffineSymmetryOperation(
            rotation=tuple(rotation_rows),  # type: ignore[arg-type]
            translation=tuple(translations),  # type: ignore[arg-type]
        )
    except ValueError as exc:
        raise SymmetryExpressionError("symmetry rotation is not unimodular") from exc


def _find_one_loop(loops: tuple[_Loop, ...], required_tag: str) -> _Loop:
    matches = tuple(loop for loop in loops if required_tag in loop.headers)
    if len(matches) != 1:
        raise CifParseError(f"CIF requires exactly one loop containing {required_tag}")
    return matches[0]


def _reject_disorder_data_names(
    scalars: dict[str, str],
    loops: tuple[_Loop, ...],
) -> None:
    present = set(scalars)
    present.update(header for loop in loops for header in loop.headers)
    if present.intersection(_DISORDER_TAGS):
        raise CifParseError("CIF disorder data names are unsupported")


def _symmetry_operations(loops: tuple[_Loop, ...]) -> tuple[AffineSymmetryOperation, ...]:
    matches = [
        (loop, tag)
        for loop in loops
        for tag in _SYMMETRY_TAGS
        if tag in loop.headers
    ]
    if len(matches) != 1:
        raise CifParseError("CIF requires exactly one supported symmetry-operation loop")
    loop, tag = matches[0]
    column = loop.headers.index(tag)
    if len(loop.rows) > MAX_SYMMETRY_OPERATIONS:
        raise CifParseError("symmetry-operation loop exceeds the operation bound")
    operations = tuple(parse_symmetry_expression(row[column]) for row in loop.rows)
    identities = tuple((operation.rotation, operation.translation) for operation in operations)
    if len(identities) != len(set(identities)):
        raise CifParseError("symmetry-operation loop contains duplicate operations")
    identity_key = (
        ((1, 0, 0), (0, 1, 0), (0, 0, 1)),
        (Fraction(0), Fraction(0), Fraction(0)),
    )

    def key(operation: AffineSymmetryOperation) -> tuple[
        tuple[tuple[int, int, int], tuple[int, int, int], tuple[int, int, int]],
        tuple[Fraction, Fraction, Fraction],
    ]:
        return (
            operation.rotation,
            tuple(
                Fraction(value.numerator, value.denominator) % 1
                for value in operation.translation
            ),  # type: ignore[return-value]
        )

    operation_keys = {key(operation) for operation in operations}
    if identity_key not in operation_keys:
        raise CifParseError("symmetry-operation set is missing the identity operation")
    closure_checks = len(operations) * len(operations)
    if closure_checks > MAX_SYMMETRY_CLOSURE_CHECKS:
        raise CifParseError("symmetry group closure exceeds the configured work bound")
    for left in operations:
        left_translation = tuple(
            Fraction(value.numerator, value.denominator) for value in left.translation
        )
        for right in operations:
            rotation = tuple(
                tuple(
                    sum(left.rotation[row][axis] * right.rotation[axis][column] for axis in range(3))
                    for column in range(3)
                )
                for row in range(3)
            )
            right_translation = tuple(
                Fraction(value.numerator, value.denominator)
                for value in right.translation
            )
            translation = tuple(
                (
                    left_translation[row]
                    + sum(
                        left.rotation[row][axis] * right_translation[axis]
                        for axis in range(3)
                    )
                )
                % 1
                for row in range(3)
            )
            composed = (rotation, translation)
            if composed not in operation_keys:
                raise CifParseError("symmetry-operation set is not closed")
    return operations


def _atom_sites(loops: tuple[_Loop, ...]) -> tuple[AtomSite, ...]:
    loop = _find_one_loop(loops, "_atom_site_fract_x")
    required = set(_FRACTIONAL_TAGS) | {"_atom_site_occupancy"}
    if not required.issubset(loop.headers):
        raise CifParseError("atom-site loop is missing coordinates or occupancy")
    has_type = "_atom_site_type_symbol" in loop.headers
    has_label = "_atom_site_label" in loop.headers
    if not has_type and not has_label:
        raise CifParseError("atom-site loop lacks an unambiguous species field")
    if len(loop.rows) > MAX_EXPANDED_SITES:
        raise CifParseError("atom-site loop exceeds the site bound")

    index_by_header = {header: index for index, header in enumerate(loop.headers)}
    sites: list[AtomSite] = []
    seen_labels: set[str] = set()
    for row in loop.rows:
        label = row[index_by_header["_atom_site_label"]] if has_label else None
        if label in {".", "?"}:
            label = None
        if label is not None:
            if label in seen_labels:
                raise CifParseError("atom-site labels must be unique")
            seen_labels.add(label)
        try:
            species = (
                normalize_type_symbol(row[index_by_header["_atom_site_type_symbol"]])
                if has_type
                else infer_label_symbol(label or "")
            )
            if has_type and label is not None:
                inferred = infer_label_symbol(label)
                if inferred != species:
                    raise ValueError("atom label and type symbol disagree")
        except ValueError as exc:
            raise CifParseError("atom-site species is ambiguous") from exc

        occupancy = _number(
            row[index_by_header["_atom_site_occupancy"]],
            label="atom-site occupancy",
        )
        if occupancy != 1.0:
            raise CifParseError("partial or overfull atom-site occupancy is unsupported")
        coordinates = tuple(
            _number(row[index_by_header[tag]], label=tag) for tag in _FRACTIONAL_TAGS
        )
        sites.append(
            AtomSite(
                species=species,
                fractional_coordinates=coordinates,  # type: ignore[arg-type]
                occupancy=1.0,
                label=label,
            )
        )
    return tuple(sites)


def parse_cif_bytes(
    raw_bytes: bytes,
    *,
    expected_sha256: str | None = None,
    expected_byte_count: int | None = None,
) -> ParsedCif:
    """Hash, bound, decode, and parse exact CIF bytes without side effects."""

    if type(raw_bytes) is not bytes:
        raise TypeError("raw_bytes must be an exact bytes instance")
    byte_count = len(raw_bytes)
    if byte_count < 1 or byte_count > MAX_CIF_BYTES:
        raise CifParseError("CIF byte count is outside the supported bound")
    digest = hashlib.sha256(raw_bytes).hexdigest()
    if expected_byte_count is not None:
        if type(expected_byte_count) is not int or isinstance(expected_byte_count, bool):
            raise TypeError("expected_byte_count must be a strict integer")
        if byte_count != expected_byte_count:
            raise CifParseError("CIF byte count does not match the expected value")
    if expected_sha256 is not None:
        if not isinstance(expected_sha256, str) or re.fullmatch(
            r"[0-9a-f]{64}", expected_sha256
        ) is None:
            raise TypeError("expected_sha256 must be a lowercase SHA-256 string")
        if digest != expected_sha256:
            raise CifParseError("CIF SHA-256 does not match the expected value")
    if raw_bytes.startswith(b"\xef\xbb\xbf"):
        raise CifParseError("UTF-8 BOM is unsupported")
    if b"\x00" in raw_bytes:
        raise CifParseError("CIF contains a NUL byte")
    try:
        text = raw_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise CifParseError("CIF must be valid UTF-8") from exc
    if any(ord(character) < 32 and character not in "\t\r\n" for character in text):
        raise CifParseError("CIF contains an unsupported control character")

    block_name, scalars, loops = _parse_document(_tokenize(text))
    _reject_disorder_data_names(scalars, loops)
    return ParsedCif(
        raw_sha256=digest,
        raw_byte_count=byte_count,
        data_block_name=block_name,
        lattice=_lattice_from_scalars(scalars),
        asymmetric_sites=_atom_sites(loops),
        symmetry_operations=_symmetry_operations(loops),
    )


def _apply_operation(
    operation: AffineSymmetryOperation,
    coordinates: tuple[float, float, float],
) -> tuple[float, float, float]:
    result: list[float] = []
    for row, translation in zip(operation.rotation, operation.translation, strict=True):
        value = sum(coefficient * coordinate for coefficient, coordinate in zip(row, coordinates, strict=True))
        value += translation.numerator / translation.denominator
        wrapped = value - math.floor(value)
        if wrapped == 0.0 or wrapped == 1.0:
            wrapped = 0.0
        result.append(wrapped)
    return tuple(result)  # type: ignore[return-value]


def expand_cif_symmetry(
    parsed: ParsedCif,
    *,
    duplicate_tolerance_angstrom: float = 1.0e-7,
    max_duplicate_checks: int = MAX_DUPLICATE_CHECKS,
) -> PeriodicStructure:
    """Expand explicit CIF symmetry and reject conflicting periodic sites."""

    if not isinstance(parsed, ParsedCif):
        raise TypeError("parsed must be a ParsedCif")
    if not isinstance(duplicate_tolerance_angstrom, float):
        raise TypeError("duplicate_tolerance_angstrom must be a strict float")
    if not math.isfinite(duplicate_tolerance_angstrom) or not (
        0.0 < duplicate_tolerance_angstrom <= 1.0e-3
    ):
        raise ValueError("duplicate_tolerance_angstrom is outside the supported bound")
    if type(max_duplicate_checks) is not int or isinstance(max_duplicate_checks, bool):
        raise TypeError("max_duplicate_checks must be a strict integer")
    if max_duplicate_checks < 1:
        raise ValueError("max_duplicate_checks must be positive")
    if len(parsed.asymmetric_sites) * len(parsed.symmetry_operations) > MAX_EXPANDED_SITES:
        raise CifParseError("expanded atom count exceeds the supported bound")

    expanded: list[AtomSite] = []
    expanded_source_indices: list[int] = []
    duplicate_candidate_work = 0
    for source_index, source_site in enumerate(parsed.asymmetric_sites):
        for operation in parsed.symmetry_operations:
            coordinates = _apply_operation(operation, source_site.fractional_coordinates)
            duplicate_index: int | None = None
            for index, existing in enumerate(expanded):
                remaining_work = max_duplicate_checks - duplicate_candidate_work
                if remaining_work < 1:
                    raise CifParseError(
                        "symmetry expansion exceeds duplicate-check candidate-work bound"
                    )
                displacement = tuple(
                    first - second
                    for first, second in zip(coordinates, existing.fractional_coordinates, strict=True)
                )
                try:
                    image = closest_lattice_image(
                        displacement,  # type: ignore[arg-type]
                        parsed.lattice,
                        max_candidates=min(100_000, remaining_work),
                    )
                except LatticeError as exc:
                    raise CifParseError(
                        "symmetry expansion exceeds duplicate-check candidate-work bound"
                    ) from exc
                duplicate_candidate_work += image.candidates_examined
                if duplicate_candidate_work > max_duplicate_checks:
                    raise CifParseError(
                        "symmetry expansion exceeds duplicate-check candidate-work bound"
                    )
                if image.distance_angstrom <= duplicate_tolerance_angstrom:
                    duplicate_index = index
                    if existing.species != source_site.species:
                        raise CifParseError(
                            "different species occupy the same expanded periodic site"
                        )
                    if expanded_source_indices[index] != source_index:
                        raise CifParseError(
                            "distinct asymmetric rows occupy the same periodic site"
                        )
                    break
            if duplicate_index is None:
                expanded.append(
                    AtomSite(
                        species=source_site.species,
                        fractional_coordinates=coordinates,
                        occupancy=1.0,
                        label=source_site.label,
                    )
                )
                expanded_source_indices.append(source_index)
    return PeriodicStructure(lattice=parsed.lattice, sites=tuple(expanded))


def parse_cif_structure(
    raw_bytes: bytes,
    *,
    expected_sha256: str | None = None,
    expected_byte_count: int | None = None,
) -> PeriodicStructure:
    """Parse and explicitly symmetry-expand one bounded CIF artifact."""

    parsed = parse_cif_bytes(
        raw_bytes,
        expected_sha256=expected_sha256,
        expected_byte_count=expected_byte_count,
    )
    return expand_cif_symmetry(parsed)


__all__ = [
    "MAX_CIF_BYTES",
    "expand_cif_symmetry",
    "parse_cif_bytes",
    "parse_cif_structure",
    "parse_symmetry_expression",
]
