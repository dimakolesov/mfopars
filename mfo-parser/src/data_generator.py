"""
Генератор фейковых, но синтаксически валидных РФ-данных для тестовой подачи.
Паспорт: серия 4 цифры + номер 6 цифр (без алгоритмической проверки контрольных
сумм — у РФ-паспорта их и нет, проверка только по диапазонам ФМС). Намеренно
используем серию 9999, чтобы данные были легко отличимы как тестовые.
СНИЛС: 11 цифр с корректной контрольной суммой (алгоритм по приказу 192п ПФР).
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, timedelta

from faker import Faker

_FAKER = Faker("ru_RU")


@dataclass
class FakeApplicant:
    last_name: str
    first_name: str
    middle_name: str
    sex: str  # "M" / "F"
    birth_date: date  # >= 21 год для МФО
    passport_series: str  # 4 цифры
    passport_number: str  # 6 цифр
    passport_issued_by: str
    passport_issued_date: date  # ≥ 14 лет назад от рождения, ≤ сейчас
    passport_dep_code: str  # 6 цифр "XXX-XXX"
    snils: str  # 11 цифр (без дефисов)
    inn: str  # 12 цифр физлица
    email: str
    address_region: str
    address_city: str
    address_street: str
    address_house: str
    address_flat: str
    address_postcode: str


def _snils_with_checksum() -> str:
    digits = [random.randint(0, 9) for _ in range(9)]
    s = sum(d * (9 - i) for i, d in enumerate(digits))
    if s < 100:
        check = s
    elif s in (100, 101):
        check = 0
    else:
        s %= 101
        check = 0 if s in (100, 101) else s
    return "".join(map(str, digits)) + f"{check:02d}"


def _inn_fl() -> str:
    """Генерирует 12-значный ИНН физлица с корректными контрольными цифрами."""
    base = [random.randint(0, 9) for _ in range(10)]
    w1 = [7, 2, 4, 10, 3, 5, 9, 4, 6, 8]
    n11 = sum(b * w for b, w in zip(base, w1)) % 11 % 10
    base_with_11 = base + [n11]
    w2 = [3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8]
    n12 = sum(b * w for b, w in zip(base_with_11, w2)) % 11 % 10
    return "".join(map(str, base_with_11 + [n12]))


def generate_applicant() -> FakeApplicant:
    sex = random.choice(["M", "F"])
    if sex == "M":
        first = _FAKER.first_name_male()
        middle = _FAKER.middle_name_male()
        last = _FAKER.last_name_male()
    else:
        first = _FAKER.first_name_female()
        middle = _FAKER.middle_name_female()
        last = _FAKER.last_name_female()

    # 25–55 лет — типичная аудитория МФО
    age_days = random.randint(25 * 365, 55 * 365)
    birth = date.today() - timedelta(days=age_days)
    passport_issued_age = random.randint(20, max(20, (date.today() - birth).days // 365))
    issued = birth + timedelta(days=passport_issued_age * 365)
    if issued > date.today():
        issued = date.today() - timedelta(days=365)

    return FakeApplicant(
        last_name=last,
        first_name=first,
        middle_name=middle,
        sex=sex,
        birth_date=birth,
        # серия 99XX однозначно маркирует данные как тестовые — таких ОВД нет
        passport_series=f"99{random.randint(10, 99)}",
        passport_number=f"{random.randint(100000, 999999)}",
        passport_issued_by="ОТДЕЛЕНИЕМ УФМС РОССИИ (ТЕСТОВЫЕ ДАННЫЕ)",
        passport_issued_date=issued,
        passport_dep_code=f"{random.randint(100, 999)}-{random.randint(100, 999)}",
        snils=_snils_with_checksum(),
        inn=_inn_fl(),
        email=_FAKER.email(),
        address_region="г Москва",
        address_city="Москва",
        address_street=_FAKER.street_name(),
        address_house=str(random.randint(1, 200)),
        address_flat=str(random.randint(1, 250)),
        address_postcode=f"1{random.randint(10000, 99999)}",
    )
