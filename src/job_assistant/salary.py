from __future__ import annotations

from decimal import Decimal
from fractions import Fraction

from .models import NormalizedVacancy

RUSSIAN_COUNTRIES = {"ru", "rus", "russia", "russian federation", "россия", "рф", "российская федерация"}


def gross_tax_ratio(vacancy: NormalizedVacancy) -> Fraction:
    """User's screening assumption, not a jurisdiction-specific tax calculation."""
    if vacancy.salary_gross is not True:
        return Fraction(0)
    country = (vacancy.hiring_country or "").strip().casefold()
    currency = (vacancy.salary_currency or "").strip().upper()
    russian = country in RUSSIAN_COUNTRIES if country else currency in {"RUB", "RUR", "₽"}
    # User treats other markets as Georgia and deliberately ignores the stated 1%.
    return Fraction(13, 87) if russian else Fraction(0)


def net_amount(vacancy: NormalizedVacancy, amount: float | None) -> Decimal | None:
    if amount is None:
        return None
    net_ratio = 1 / (1 + gross_tax_ratio(vacancy))
    # Keep 13/87 exact: gross 100 = net 87 + tax 13, without a rounded divisor.
    return Decimal(str(amount)) * net_ratio.numerator / net_ratio.denominator
