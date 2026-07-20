"""Closed element-symbol table used for deterministic spglib type numbers."""

from __future__ import annotations

import re
from types import MappingProxyType


ELEMENT_SYMBOLS = (
    "H", "He", "Li", "Be", "B", "C", "N", "O", "F", "Ne",
    "Na", "Mg", "Al", "Si", "P", "S", "Cl", "Ar", "K", "Ca",
    "Sc", "Ti", "V", "Cr", "Mn", "Fe", "Co", "Ni", "Cu", "Zn",
    "Ga", "Ge", "As", "Se", "Br", "Kr", "Rb", "Sr", "Y", "Zr",
    "Nb", "Mo", "Tc", "Ru", "Rh", "Pd", "Ag", "Cd", "In", "Sn",
    "Sb", "Te", "I", "Xe", "Cs", "Ba", "La", "Ce", "Pr", "Nd",
    "Pm", "Sm", "Eu", "Gd", "Tb", "Dy", "Ho", "Er", "Tm", "Yb",
    "Lu", "Hf", "Ta", "W", "Re", "Os", "Ir", "Pt", "Au", "Hg",
    "Tl", "Pb", "Bi", "Po", "At", "Rn", "Fr", "Ra", "Ac", "Th",
    "Pa", "U", "Np", "Pu", "Am", "Cm", "Bk", "Cf", "Es", "Fm",
    "Md", "No", "Lr", "Rf", "Db", "Sg", "Bh", "Hs", "Mt", "Ds",
    "Rg", "Cn", "Nh", "Fl", "Mc", "Lv", "Ts", "Og",
)

_ATOMIC_NUMBER_BY_SYMBOL = MappingProxyType({
    symbol: index for index, symbol in enumerate(ELEMENT_SYMBOLS, start=1)
})
_SYMBOL_BY_ATOMIC_NUMBER = MappingProxyType({
    index: symbol for symbol, index in _ATOMIC_NUMBER_BY_SYMBOL.items()
})

_TYPE_SYMBOL = re.compile(r"^([A-Z][a-z]?)(?:(?:[0-9]+)?[+-])?$")
_LABEL_SYMBOL = re.compile(r"^([A-Z][a-z]?)(?:[0-9]+)?$")


def normalize_type_symbol(value: str) -> str:
    """Return one unambiguous element symbol from a CIF type-symbol token."""

    match = _TYPE_SYMBOL.fullmatch(value)
    if match is None or match.group(1) not in _ATOMIC_NUMBER_BY_SYMBOL:
        raise ValueError("atom type symbol is ambiguous or unsupported")
    return match.group(1)


def infer_label_symbol(value: str) -> str:
    """Infer an element only from a closed canonical CIF label form."""

    match = _LABEL_SYMBOL.fullmatch(value)
    if match is None or match.group(1) not in _ATOMIC_NUMBER_BY_SYMBOL:
        raise ValueError("atom label does not identify one unambiguous element")
    return match.group(1)


def atomic_number(symbol: str) -> int:
    try:
        return _ATOMIC_NUMBER_BY_SYMBOL[symbol]
    except KeyError as exc:
        raise ValueError("unsupported element symbol") from exc


def element_symbol(number: int) -> str:
    try:
        return _SYMBOL_BY_ATOMIC_NUMBER[number]
    except KeyError as exc:
        raise ValueError("unsupported atomic number from crystallographic kernel") from exc


__all__ = [
    "ELEMENT_SYMBOLS",
    "atomic_number",
    "element_symbol",
    "infer_label_symbol",
    "normalize_type_symbol",
]
