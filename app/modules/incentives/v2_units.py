"""The unit boundary between stored rates and calculated rates.

WHY THIS MODULE EXISTS
----------------------
Prodculator stores rates and caps as percentages. ``rate_gross`` is ``36.0`` and
``qualifying_spend_cap_pct`` is ``80.0``.

The v2 reference implementation stores them as fractions. Its ``base_rate`` is
``.36`` and its ``qs_percentage_cap`` is ``0.8``, and its demo data carries
``rate:.30``, ``.3975``, ``.36`` throughout.

Both conventions are defensible. Mixing them is not, and nothing in either
codebase announces which one a given number uses. Porting the reference formula
``min(local_core, pct * global_core)`` against our column would compute 8000
percent of global core expenditure, and ``qs * base_rate`` against our
``rate_gross`` would be out by a factor of 100. Neither raises. Both produce a
plausible number in a document a financier reads.

So conversion happens here, explicitly, with a guard that rejects a value that
cannot be the unit it claims to be. A fraction above 1 and a percentage above 100
are both refused rather than silently converted, because either almost certainly
means the wrong convention arrived.
"""
from __future__ import annotations

from typing import Any

#: A rebate rate or a qualifying-spend cap above 100 percent is not a real
#: programme term. It is the signature of a fraction and a percentage being
#: confused, so it is treated as an error rather than clamped.
_MAX_PERCENT = 100.0
_MAX_FRACTION = 1.0

#: Floating point tolerance, so 100.00000000000001 from a division is accepted.
_EPSILON = 1e-9


class UnitError(ValueError):
    """A rate arrived in the wrong unit, or in no recognisable unit."""


def _coerce(value: Any, field: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise UnitError(f"{field} received a boolean, which is never a rate")
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().rstrip("%").replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        raise UnitError(f"{field} received {value!r}, which is not a number") from None


def percent_to_fraction(value: Any, *, field: str = "rate") -> float | None:
    """Convert a stored percentage to the fraction the engines calculate with.

    ``36.0`` becomes ``0.36``. ``None`` stays ``None``, because an absent rate is
    not a zero rate.
    """
    number = _coerce(value, field)
    if number is None:
        return None
    if number < 0:
        raise UnitError(f"{field} cannot be negative, got {number}")
    if number > _MAX_PERCENT + _EPSILON:
        raise UnitError(
            f"{field} is {number}, which is above 100 percent. A rate or "
            f"qualifying-spend cap cannot exceed 100 percent, so this is almost "
            f"certainly a value that is already a fraction, or a unit mix-up."
        )
    return number / 100.0


def fraction_to_percent(value: Any, *, field: str = "rate") -> float | None:
    """Convert an engine fraction back to the stored percentage.

    ``0.36`` becomes ``36.0``. Rejects anything above 1, which is the signature of
    a percentage arriving where a fraction was expected: passing ``80.0`` here
    would otherwise produce ``8000.0``.
    """
    number = _coerce(value, field)
    if number is None:
        return None
    if number < 0:
        raise UnitError(f"{field} cannot be negative, got {number}")
    if number > _MAX_FRACTION + _EPSILON:
        raise UnitError(
            f"{field} is {number}, which is above 1.0. A fraction cannot exceed "
            f"1.0, so this is almost certainly already a percentage. Passing it "
            f"through would multiply it by a hundred."
        )
    return number * 100.0


def apply_rate(base: float, rate_percent: Any, *, field: str = "rate") -> float:
    """Apply a stored percentage rate to a base amount.

    The one place a rate meets an amount. Callers pass the stored percentage and
    never divide by a hundred themselves, so the convention cannot drift into a
    call site.
    """
    fraction = percent_to_fraction(rate_percent, field=field)
    if fraction is None:
        raise UnitError(f"{field} is absent, so no amount can be calculated")
    return base * fraction


def apply_cap_percent(base: float, cap_percent: Any, *, field: str = "cap") -> float:
    """Apply a stored percentage cap to a base amount.

    A cap of ``None`` or ``100`` leaves the base untouched, which is the same
    answer by two different routes: no cap recorded, and a cap that restricts
    nothing.
    """
    fraction = percent_to_fraction(cap_percent, field=field)
    if fraction is None:
        return base
    return base * fraction


def looks_like_a_fraction(value: Any) -> bool:
    """Whether a number is in the range a fraction occupies.

    For diagnostics and migration checks rather than conversion. A stored ``0.36``
    where a percentage was expected means the row is 100 times too small and the
    calculation will silently under-report, which is harder to notice than an
    over-report.
    """
    number = _coerce(value, "value")
    return number is not None and 0 < number <= _MAX_FRACTION
