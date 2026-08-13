"""Exchange-value estimation.

Looks for a reference row in `exchange_values`; when the brand/model/year isn't
on file we fall back to a heuristic so the public funnel always returns a number
(clearly flagged as non-reference). Every result is explicitly indicative — the
real figure comes after a physical inspection.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.catalog import ExchangeValue
from app.schemas.public import ExchangeEstimateOut, ExchangeValueRequest

DEFAULT_CONDITION_FACTORS: dict[str, Decimal] = {
    "EXCELLENT": Decimal("1.00"),
    "GOOD": Decimal("0.88"),
    "FAIR": Decimal("0.72"),
    "POOR": Decimal("0.55"),
}

# Used only when no reference row exists.
HEURISTIC_NEW_PRICE = Decimal("95000")
ANNUAL_DEPRECIATION = Decimal("0.12")
MIN_RESIDUAL = Decimal("0.15")

# Beyond this the bike is treated as fully depreciated for age purposes.
MAX_AGE_YEARS = 15
# Per 10,000 km beyond the expected average.
ODOMETER_PENALTY_PER_10K = Decimal("0.04")
EXPECTED_KM_PER_YEAR = 8000


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def _factor(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)


def _condition_factor(row: ExchangeValue | None, condition: str) -> Decimal:
    if row is not None and row.condition_factor_json:
        raw = row.condition_factor_json.get(condition)
        if raw is not None:
            try:
                return Decimal(str(raw))
            except (ValueError, ArithmeticError):
                pass
    return DEFAULT_CONDITION_FACTORS.get(condition, DEFAULT_CONDITION_FACTORS["GOOD"])


def _age_factor(year: int, reference_year: int | None) -> tuple[Decimal, int]:
    """Depreciate for the years elapsed since the reference row's year.

    A reference row already prices a specific year, so we only depreciate the
    gap between that year and the vehicle's year.
    """
    base_year = reference_year if reference_year is not None else date.today().year
    age = max(0, min(base_year - year, MAX_AGE_YEARS))
    factor = (Decimal("1") - ANNUAL_DEPRECIATION) ** age
    return max(factor, MIN_RESIDUAL), age


def _odometer_factor(odometer_km: int | None, age_years: int) -> Decimal:
    if not odometer_km:
        return Decimal("1.000")
    expected = max(EXPECTED_KM_PER_YEAR * max(age_years, 1), EXPECTED_KM_PER_YEAR)
    excess = odometer_km - expected
    if excess <= 0:
        return Decimal("1.000")
    penalty = (Decimal(excess) / Decimal("10000")) * ODOMETER_PENALTY_PER_10K
    return max(Decimal("1.000") - penalty, Decimal("0.60"))


async def estimate(
    session: AsyncSession, payload: ExchangeValueRequest
) -> ExchangeEstimateOut:
    """Compute an indicative exchange value."""
    condition = payload.condition if payload.condition in DEFAULT_CONDITION_FACTORS else "GOOD"

    # Match brand/model case-insensitively, preferring the closest model year.
    row = (
        await session.execute(
            select(ExchangeValue)
            .where(
                func.lower(ExchangeValue.brand) == payload.brand.strip().lower(),
                func.lower(ExchangeValue.model) == payload.model.strip().lower(),
            )
            .order_by(func.abs(ExchangeValue.year - payload.year))
            .limit(1)
        )
    ).scalars().first()

    if row is not None:
        base_value = Decimal(row.base_value)
        age_factor, age_years = _age_factor(payload.year, row.year)
    else:
        base_value = HEURISTIC_NEW_PRICE
        age_factor, age_years = _age_factor(payload.year, date.today().year)

    condition_factor = _condition_factor(row, condition)
    odometer_factor = _odometer_factor(payload.odometer_km, age_years)

    estimated = base_value * age_factor * condition_factor * odometer_factor

    return ExchangeEstimateOut(
        brand=payload.brand.strip(),
        model=payload.model.strip(),
        year=payload.year,
        condition=condition,
        base_value=_money(base_value),
        condition_factor=_factor(condition_factor),
        age_factor=_factor(age_factor),
        odometer_factor=_factor(odometer_factor),
        estimated_value=_money(estimated),
        estimate_low=_money(estimated * Decimal("0.92")),
        estimate_high=_money(estimated * Decimal("1.08")),
        is_reference_match=row is not None,
    )
