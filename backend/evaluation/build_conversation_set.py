from __future__ import annotations

"""Build the V3 conversational evaluation set.

Every expected value below is a control value already verified against
PostgreSQL (see evaluation/questions.json and the deployment report).  Cases
without ``expected_value`` assert only the public status/locale contract, never
an invented number.

Categories
----------
free_form     50 single-shot, natural questions (RU/UZ mixed)
follow_up     30 short conversations that exercise session context
ru            30 Russian paraphrases, synonyms and grammar variants
uz            30 Uzbek questions (15 Latin + 15 Cyrillic)
ambiguous     20 questions that must clarify instead of guessing
missing       20 questions about absent rows that must return NOT_FOUND
"""

import json
from pathlib import Path
from typing import Any

OUTPUT = Path(__file__).resolve().parent / "conversation_set.json"


def turn(
    question: str,
    status: str,
    value: int | str | None = None,
    *,
    context: bool | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "question": question,
        "expected_status": status,
    }
    if value is not None:
        payload["expected_value"] = value
    if context is not None:
        payload["expect_context"] = context
    return payload


def case(
    case_id: str,
    category: str,
    language: str,
    turns: list[dict[str, Any]],
    *,
    locale: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": case_id,
        "category": category,
        "language": language,
        "turns": turns,
    }
    if locale:
        payload["locale"] = locale
    return payload


FREE_FORM = [
    case("FF-01", "free_form", "ru", [turn("Сколько всего сделок?", "FOUND", 500000)]),
    case("FF-02", "free_form", "ru", [turn("Сколько сделок было в 2022 году?", "FOUND", 84299)]),
    case("FF-03", "free_form", "ru", [turn("Сколько сделок было в 2023 году?", "FOUND", 93985)]),
    case("FF-04", "free_form", "ru", [turn("Сколько сделок было в 2024 году?", "FOUND", 106647)]),
    case("FF-05", "free_form", "ru", [turn("Сколько сделок было в 2025 году?", "FOUND", 115763)]),
    case("FF-06", "free_form", "ru", [turn("Сколько сделок было в 2026 году?", "FOUND", 99306)]),
    case("FF-07", "free_form", "ru", [turn("Сколько экспортных сделок?", "FOUND", 6590)]),
    case("FF-08", "free_form", "ru", [turn("Сколько внутренних сделок в 2024?", "FOUND")]),
    case("FF-09", "free_form", "ru", [turn("Сколько сделок было в январе 2024?", "FOUND")]),
    case("FF-10", "free_form", "ru", [turn("Сколько сделок в 1 квартале 2024?", "FOUND")]),
    case("FF-11", "free_form", "ru", [turn("Сколько сделок по статусу Исполнен?", "FOUND")]),
    case("FF-12", "free_form", "ru", [turn("Сколько сделок у продавца с ИНН 937772675?", "FOUND")]),
    case("FF-13", "free_form", "ru", [turn("Сколько сделок по региону Ташкентская область?", "FOUND")]),
    case("FF-14", "free_form", "ru", [turn("Сумма сделок", "FOUND")]),
    case("FF-15", "free_form", "ru", [turn("Сумма сделок в 2024 году", "FOUND")]),
    case("FF-16", "free_form", "ru", [turn("Сумма сделок в долларах", "FOUND", "1137509729.39")]),
    case("FF-17", "free_form", "ru", [turn("Сумма сделок в сумах", "FOUND", "206835219986242.97")]),
    case("FF-18", "free_form", "ru", [turn("Средняя сумма сделки", "FOUND")]),
    case("FF-19", "free_form", "ru", [turn("Средняя сумма сделки в 2024", "FOUND")]),
    case("FF-20", "free_form", "ru", [turn("Минимальная сумма сделки", "FOUND")]),
    case("FF-21", "free_form", "ru", [turn("Максимальная сумма сделки в 2024", "FOUND")]),
    case("FF-22", "free_form", "ru", [turn("Объём сделок в 2024", "FOUND")]),
    case("FF-23", "free_form", "ru", [turn("Поставлено по единицам измерения", "FOUND")]),
    case("FF-24", "free_form", "ru", [turn("Объём экспортных сделок", "FOUND")]),
    case("FF-25", "free_form", "ru", [turn("Топ-5 продавцов по количеству сделок", "FOUND")]),
    case("FF-26", "free_form", "ru", [turn("Топ-5 покупателей по сумме сделок", "FOUND")]),
    case("FF-27", "free_form", "ru", [turn("Топ-5 брокеров продавца по количеству сделок", "FOUND")]),
    case("FF-28", "free_form", "ru", [turn("Топ-10 покупателей по количеству сделок", "FOUND")]),
    case("FF-29", "free_form", "ru", [turn("Динамика количества сделок по годам", "FOUND")]),
    case("FF-30", "free_form", "ru", [turn("Сумма сделок по годам", "FOUND")]),
    case("FF-31", "free_form", "ru", [turn("Сравни 2023 и 2024 годы по количеству сделок", "FOUND")]),
    case("FF-32", "free_form", "ru", [turn("Сравни сумму сделок за 2022 и 2026", "FOUND")]),
    case("FF-33", "free_form", "ru", [turn("Покажи сделку 100001", "FOUND")]),
    case("FF-34", "free_form", "ru", [turn("Покажи контракт 100001", "FOUND")]),
    case("FF-35", "free_form", "ru", [turn('Покажи участника "Diyi Un" XK', "FOUND")]),
    case("FF-36", "free_form", "ru", [turn("Покажи брокера BK002", "FOUND")]),
    case("FF-37", "free_form", "ru", [turn("Покажи участника с ИНН 929863757", "FOUND")]),
    case("FF-38", "free_form", "ru", [turn("Покажи последние 10 сделок", "FOUND")]),
    case("FF-39", "free_form", "ru", [turn("Покажи 5 экспортных сделок", "FOUND")]),
    case("FF-40", "free_form", "ru", [turn("Покажи сделки по статусу Оплачен", "FOUND")]),
    case("FF-41", "free_form", "ru", [turn("Покажи самую крупную сделку", "FOUND")]),
    case("FF-42", "free_form", "ru", [turn("Покажи самую маленькую сделку", "FOUND")]),
    case("FF-43", "free_form", "ru", [turn("Какая компания больше всех покупала?", "FOUND")]),
    case("FF-44", "free_form", "ru", [turn("Кто больше всего продавал?", "FOUND")]),
    case("FF-45", "free_form", "ru", [turn("Есть ли данные за 2024 год?", "FOUND")]),
    case("FF-46", "free_form", "ru", [turn("Есть ли данные за 2003 год?", "NOT_FOUND")]),
    case("FF-47", "free_form", "uz_latn", [turn("Jami qancha bitim bor?", "FOUND", 500000)], locale="uz_latn"),
    case("FF-48", "free_form", "uz_latn", [turn("2024-yilda nechta bitim bo'ldi?", "FOUND", 106647)], locale="uz_latn"),
    case("FF-49", "free_form", "uz_latn", [turn("Bitimlar summasi dollarda qancha?", "FOUND", "1137509729.39")], locale="uz_latn"),
    case("FF-50", "free_form", "uz_latn", [turn("Eng katta bitimni ko'rsat", "FOUND")], locale="uz_latn"),
]

RU = [
    case("RU-01", "ru", "ru", [turn("Сколько всего было сделок?", "FOUND", 500000)]),
    case("RU-02", "ru", "ru", [turn("Подсчитай количество сделок", "FOUND", 500000)]),
    case("RU-03", "ru", "ru", [turn("Сколько сделок в 2024 г.", "FOUND", 106647)]),
    case("RU-04", "ru", "ru", [turn("Сколько сделок за 2024 год было?", "FOUND", 106647)]),
    case("RU-05", "ru", "ru", [turn("Сумма всех сделок", "FOUND")]),
    case("RU-06", "ru", "ru", [turn("Общая сумма сделок", "FOUND")]),
    case("RU-07", "ru", "ru", [turn("Сумма сделок долларах", "FOUND")]),
    case("RU-08", "ru", "ru", [turn("Сколько экспортных сделок было", "FOUND", 6590)]),
    case("RU-09", "ru", "ru", [turn("Сколько внутренних сделок", "FOUND")]),
    case("RU-10", "ru", "ru", [turn("Топ 5 продавцов", "FOUND")]),
    case("RU-11", "ru", "ru", [turn("Пять крупнейших покупателей", "FOUND")]),
    case("RU-12", "ru", "ru", [turn("Динамика сделок по месяцам", "FOUND")]),
    case("RU-13", "ru", "ru", [turn("Сравни количество сделок 2022 и 2026", "FOUND")]),
    case("RU-14", "ru", "ru", [turn("Покажи сделку номер 100001", "FOUND")]),
    case("RU-15", "ru", "ru", [turn("Найди контракт 100001", "FOUND")]),
    case("RU-16", "ru", "ru", [turn("Найди участника по ИНН 929863757", "FOUND")]),
    case("RU-17", "ru", "ru", [turn("Информация о брокере BK002", "FOUND")]),
    case("RU-18", "ru", "ru", [turn("Сколько записей о сделках", "FOUND", 500000)]),
    case("RU-19", "ru", "ru", [turn("Есть ли сделки в 2024 году", "FOUND")]),
    case("RU-20", "ru", "ru", [turn("Имеются ли данные за 2003 год", "NOT_FOUND")]),
    case("RU-21", "ru", "ru", [turn("Какое количество сделок в 2023", "FOUND", 93985)]),
    case("RU-22", "ru", "ru", [turn("Чему равна сумма сделок в 2024", "FOUND")]),
    case("RU-23", "ru", "ru", [turn("Средняя сумма по сделкам", "FOUND")]),
    case("RU-24", "ru", "ru", [turn("Максимальная сделка 2024", "FOUND")]),
    case("RU-25", "ru", "ru", [turn("Минимальная сделка", "FOUND")]),
    case("RU-26", "ru", "ru", [turn("Объём сделок 2024", "FOUND")]),
    case("RU-27", "ru", "ru", [turn("Поставки по единицам измерения", "FOUND")]),
    case("RU-28", "ru", "ru", [turn("Последние 10 сделок", "FOUND")]),
    case("RU-29", "ru", "ru", [turn("Сколько сделок было в 2024 году всего", "FOUND", 106647)]),
    case("RU-30", "ru", "ru", [turn("Сколько сделок в 2024 по региону Ташкентская область", "FOUND")]),
]

UZ = [
    case("UZ-01", "uz", "uz_latn", [turn("Jami nechta bitim bor?", "FOUND", 500000)], locale="uz_latn"),
    case("UZ-02", "uz", "uz_latn", [turn("2022-yilda nechta bitim bo'ldi?", "FOUND", 84299)], locale="uz_latn"),
    case("UZ-03", "uz", "uz_latn", [turn("2023-yilda nechta bitim bo'ldi?", "FOUND", 93985)], locale="uz_latn"),
    case("UZ-04", "uz", "uz_latn", [turn("2024-yilda nechta bitim bo'ldi?", "FOUND", 106647)], locale="uz_latn"),
    case("UZ-05", "uz", "uz_latn", [turn("2025-yilda nechta bitim bo'ldi?", "FOUND", 115763)], locale="uz_latn"),
    case("UZ-06", "uz", "uz_latn", [turn("2026-yilda nechta bitim bo'ldi?", "FOUND", 99306)], locale="uz_latn"),
    case("UZ-07", "uz", "uz_latn", [turn("2027-yilda nechta bitim bo'ldi?", "NOT_FOUND")], locale="uz_latn"),
    case("UZ-08", "uz", "uz_latn", [turn("Bitimlar summasi dollarda qancha?", "FOUND", "1137509729.39")], locale="uz_latn"),
    case("UZ-09", "uz", "uz_latn", [turn("Bitimlar summasi so'mda qancha?", "FOUND", "206835219986242.97")], locale="uz_latn"),
    case("UZ-10", "uz", "uz_latn", [turn("Eksport bitimlari soni qancha?", "FOUND", 6590)], locale="uz_latn"),
    case("UZ-11", "uz", "uz_latn", [turn("Eng katta bitimni ko'rsat", "FOUND")], locale="uz_latn"),
    case("UZ-12", "uz", "uz_latn", [turn("Eng kichik bitimni ko'rsat", "FOUND")], locale="uz_latn"),
    case("UZ-13", "uz", "uz_latn", [turn("2024-yil uchun bitimlar dinamikasi", "FOUND")], locale="uz_latn"),
    case("UZ-14", "uz", "uz_latn", [turn("Eng ko'p sotgan kompaniya qaysi?", "FOUND")], locale="uz_latn"),
    case("UZ-15", "uz", "uz_latn", [turn("Eng ko'p sotib olgan kompaniya qaysi?", "FOUND")], locale="uz_latn"),
    case("UZ-16", "uz", "uz_cyrl", [turn("Жами қанча битим бор?", "FOUND", 500000)], locale="uz_cyrl"),
    case("UZ-17", "uz", "uz_cyrl", [turn("2023-йилда нечта битим бўлди?", "FOUND", 93985)], locale="uz_cyrl"),
    case("UZ-18", "uz", "uz_cyrl", [turn("2024-йилда нечта битим бўлди?", "FOUND", 106647)], locale="uz_cyrl"),
    case("UZ-19", "uz", "uz_cyrl", [turn("2027-йилда нечта битим бўлди?", "NOT_FOUND")], locale="uz_cyrl"),
    case("UZ-20", "uz", "uz_cyrl", [turn("Битимлар суммаси долларда қанча?", "FOUND", "1137509729.39")], locale="uz_cyrl"),
    case("UZ-21", "uz", "uz_cyrl", [turn("Битимлар суммаси сўмда қанча?", "FOUND", "206835219986242.97")], locale="uz_cyrl"),
    case("UZ-22", "uz", "uz_cyrl", [turn("Экспорт битимлари сони қанча?", "FOUND", 6590)], locale="uz_cyrl"),
    case("UZ-23", "uz", "uz_cyrl", [turn("Энг катта битимни кўрсат", "FOUND")], locale="uz_cyrl"),
    case("UZ-24", "uz", "uz_cyrl", [turn("Энг кичик битимни кўрсат", "FOUND")], locale="uz_cyrl"),
    case("UZ-25", "uz", "uz_cyrl", [turn("2024-йил учун битимлар динамикаси", "FOUND")], locale="uz_cyrl"),
    case("UZ-26", "uz", "uz_cyrl", [turn("Энг кўп сотган компания қайси?", "FOUND")], locale="uz_cyrl"),
    case("UZ-27", "uz", "uz_cyrl", [turn("Энг кўп сотиб олган компания қайси?", "FOUND")], locale="uz_cyrl"),
    case("UZ-28", "uz", "uz_cyrl", [turn("Битим рақами 100001", "FOUND")], locale="uz_cyrl"),
    case("UZ-29", "uz", "uz_cyrl", [turn("Шартнома рақами 100001", "FOUND")], locale="uz_cyrl"),
    case("UZ-30", "uz", "uz_cyrl", [turn("2023- ва 2024-йилларни битимлар сони бўйича таққосла", "FOUND")], locale="uz_cyrl"),
]

AMBIGUOUS = [
    case("AM-01", "ambiguous", "ru", [turn("Сколько сделок было в Ташкенте?", "NEEDS_CLARIFICATION")]),
    case("AM-02", "ambiguous", "ru", [turn("Покажи брокера KAHIR", "NEEDS_CLARIFICATION")]),
    case("AM-03", "ambiguous", "ru", [turn("Сколько сделок у компании sujo plus mchj", "NEEDS_CLARIFICATION")]),
    case("AM-04", "ambiguous", "ru", [turn("Покажи участника sujo plus mchj", "NEEDS_CLARIFICATION")]),
    case("AM-05", "ambiguous", "ru", [turn("Средняя цена карбамида", "NEEDS_CLARIFICATION")]),
    case("AM-06", "ambiguous", "ru", [turn("Топ-5 компаний по количеству сделок", "NEEDS_CLARIFICATION")]),
    case("AM-07", "ambiguous", "ru", [turn("Сколько сделок компаний из Ташкента", "NEEDS_CLARIFICATION")]),
    case("AM-08", "ambiguous", "ru", [turn("Сколько сделок по товару карбамид", "NEEDS_CLARIFICATION")]),
    case("AM-09", "ambiguous", "ru", [turn("Сколько сделок по региону Ташкент", "NEEDS_CLARIFICATION")]),
    case("AM-10", "ambiguous", "ru", [turn("Покажи сделки по региону Ташкент", "NEEDS_CLARIFICATION")]),
    case("AM-11", "ambiguous", "ru", [turn("Сколько сделок у брокера", "NEEDS_CLARIFICATION")]),
    case("AM-12", "ambiguous", "ru", [turn("Сумма сделок у брокера", "NEEDS_CLARIFICATION")]),
    case("AM-13", "ambiguous", "ru", [turn("Поставлено тонн карбамида в 2024", "NEEDS_CLARIFICATION")]),
    case("AM-14", "ambiguous", "ru", [turn("Средняя цена карбамида в 2024", "NEEDS_CLARIFICATION")]),
    case("AM-15", "ambiguous", "ru", [turn("Сколько сделок по товару карбамид в 2024", "NEEDS_CLARIFICATION")]),
    case("AM-16", "ambiguous", "ru", [turn("Топ-5 компаний по сумме сделок", "NEEDS_CLARIFICATION")]),
    case("AM-17", "ambiguous", "uz_latn", [turn("Toshkentda nechta bitim bo'ldi?", "NEEDS_CLARIFICATION")], locale="uz_latn"),
    case("AM-18", "ambiguous", "uz_cyrl", [turn("Тошкентда нечта битим бўлди?", "NEEDS_CLARIFICATION")], locale="uz_cyrl"),
    case("AM-19", "ambiguous", "ru", [turn("Сколько сделок у компании с ИНН 929863757", "NEEDS_CLARIFICATION")]),
    case("AM-20", "ambiguous", "ru", [turn("Покажи брокера продавца", "NEEDS_CLARIFICATION")]),
]

MISSING = [
    case("MS-01", "missing", "ru", [turn("Есть данные за 2003 год?", "NOT_FOUND")]),
    case("MS-02", "missing", "uz_latn", [turn("2003-yilda nechta bitim bo'ldi?", "NOT_FOUND")], locale="uz_latn"),
    case("MS-03", "missing", "uz_cyrl", [turn("2003-йилда нечта битим бўлди?", "NOT_FOUND")], locale="uz_cyrl"),
    case("MS-04", "missing", "ru", [turn("Сколько сделок в 1900 году?", "NOT_FOUND")]),
    case("MS-05", "missing", "ru", [turn("Сколько сделок в 2100 году?", "NOT_FOUND")]),
    case("MS-06", "missing", "ru", [turn("Сколько сделок в 2027 году?", "NOT_FOUND")]),
    case("MS-07", "missing", "uz_latn", [turn("2027-yilda nechta bitim bo'ldi?", "NOT_FOUND")], locale="uz_latn"),
    case("MS-08", "missing", "uz_cyrl", [turn("2027-йилда нечта битим бўлди?", "NOT_FOUND")], locale="uz_cyrl"),
    case("MS-09", "missing", "ru", [turn("Покажи сделку 999999", "NOT_FOUND")]),
    case("MS-10", "missing", "ru", [turn("Покажи контракт 999999", "NOT_FOUND")]),
    case("MS-11", "missing", "ru", [turn("Покажи брокера BK999", "NOT_FOUND")]),
    case("MS-12", "missing", "ru", [turn("Покажи участника Несуществующая Компания", "NOT_FOUND")]),
    case("MS-13", "missing", "ru", [turn("Покажи участника с ИНН 000000000", "NOT_FOUND")]),
    case("MS-14", "missing", "ru", [turn("Покажи товар Несуществующий Товар", "NOT_FOUND")]),
    case("MS-15", "missing", "ru", [turn("Сумма сделок в 2027 году", "NOT_FOUND")]),
    case("MS-16", "missing", "ru", [turn("Средняя сумма сделки в 2027", "NOT_FOUND")]),
    case("MS-17", "missing", "ru", [turn("Максимальная сумма сделки в 2027", "NOT_FOUND")]),
    case("MS-18", "missing", "ru", [turn("Объём сделок в 2027", "NOT_FOUND")]),
    case("MS-19", "missing", "ru", [turn("Покажи сделки по статусу Не подписан в 2024", "NOT_FOUND")]),
    case("MS-20", "missing", "ru", [turn("Сколько сделок было 01.01.2024", "NOT_FOUND")]),
]

FOLLOW_UP = [
    case("FU-01", "follow_up", "ru", [
        turn("Сколько сделок было в 2024 году?", "FOUND", 106647, context=False),
        turn("А за 2023?", "FOUND", 93985, context=True),
    ]),
    case("FU-02", "follow_up", "ru", [
        turn("Сколько сделок было в 2023 году?", "FOUND", 93985, context=False),
        turn("А за 2024?", "FOUND", 106647, context=True),
    ]),
    case("FU-03", "follow_up", "ru", [
        turn("Сколько сделок было в 2024 году?", "FOUND", 106647, context=False),
        turn("А по месяцам?", "FOUND", context=True),
    ]),
    case("FU-04", "follow_up", "ru", [
        turn("Сколько сделок было в 2024 году?", "FOUND", 106647, context=False),
        turn("А по кварталам?", "UNSUPPORTED", context=True),
    ]),
    case("FU-05", "follow_up", "ru", [
        turn("Сумма сделок в 2024 году", "FOUND", context=False),
        turn("А в долларах?", "FOUND", "219306395.35", context=True),
    ]),
    case("FU-06", "follow_up", "ru", [
        turn("Сумма сделок в долларах", "FOUND", "1137509729.39", context=False),
        turn("А в сумах?", "FOUND", "206835219986242.97", context=True),
    ]),
    case("FU-07", "follow_up", "ru", [
        turn("Сколько сделок за 2024?", "FOUND", 106647, context=False),
        turn("А теперь только экспортные", "FOUND", 1327, context=True),
    ]),
    case("FU-08", "follow_up", "ru", [
        turn("Сколько экспортных сделок?", "FOUND", 6590, context=False),
        turn("А теперь только внутренние", "FOUND", 493410, context=True),
    ]),
    case("FU-09", "follow_up", "ru", [
        turn("Сколько сделок было в 2024 году?", "FOUND", 106647, context=False),
        turn("А если взять предыдущий год?", "FOUND", 93985, context=True),
    ]),
    case("FU-10", "follow_up", "ru", [
        turn("Сколько сделок было в 2025 году?", "FOUND", 115763, context=False),
        turn("А предыдущий год?", "FOUND", 106647, context=True),
    ]),
    case("FU-11", "follow_up", "ru", [
        turn("Топ-10 покупателей по количеству сделок", "FOUND", context=False),
        turn("Сколько у неё сделок?", "NEEDS_CLARIFICATION", context=True),
    ]),
    case("FU-12", "follow_up", "ru", [
        turn("Топ-10 продавцов по количеству сделок", "FOUND", context=False),
        turn("Сколько у неё сделок?", "NEEDS_CLARIFICATION", context=True),
    ]),
    case("FU-13", "follow_up", "ru", [
        turn('Покажи участника "Diyi Un" XK', "FOUND", context=False),
        turn("Сколько у неё сделок?", "FOUND", 1028, context=True),
    ]),
    case("FU-14", "follow_up", "ru", [
        turn("Покажи участника с ИНН 929863757", "FOUND", context=False),
        turn("Сколько у него сделок?", "FOUND", 5, context=True),
    ]),
    case("FU-15", "follow_up", "ru", [
        turn("Сколько сделок было в 2024 году?", "FOUND", 106647, context=False),
        turn("Сколько всего сделок?", "FOUND", 500000, context=False),
    ]),
    case("FU-16", "follow_up", "ru", [
        turn("Сколько сделок было в 2024 году?", "FOUND", 106647, context=False),
        turn("Покажи динамику по годам", "FOUND", context=False),
    ]),
    case("FU-17", "follow_up", "ru", [
        turn("Сколько сделок было в 2024 году?", "FOUND", 106647, context=False),
        turn("А в 2025?", "FOUND", 115763, context=True),
    ]),
    case("FU-18", "follow_up", "ru", [
        turn("Сумма сделок в 2024 году", "FOUND", context=False),
        turn("А за 2023 год?", "FOUND", context=True),
    ]),
    case("FU-19", "follow_up", "uz_latn", [
        turn("2024-yilda nechta bitim bo'ldi?", "FOUND", 106647, context=False),
        turn("2023-chi?", "FOUND", 93985, context=True),
    ], locale="uz_latn"),
    case("FU-20", "follow_up", "uz_latn", [
        turn("2024-yilda nechta bitim bo'ldi?", "FOUND", 106647, context=False),
        turn("Va 2025-yil?", "FOUND", 115763, context=True),
    ], locale="uz_latn"),
    case("FU-21", "follow_up", "ru", [
        turn("Сумма сделок в 2024 году", "FOUND", context=False),
        turn("А в сумах?", "FOUND", context=True),
    ]),
    case("FU-22", "follow_up", "ru", [
        turn("Сколько внутренних сделок в 2024", "FOUND", 105320, context=False),
        turn("А экспортных?", "FOUND", 1327, context=True),
    ]),
    case("FU-23", "follow_up", "ru", [
        turn("Топ-5 продавцов по количеству сделок", "FOUND", context=False),
        turn("А покупателей?", "FOUND", context=True),
    ]),
    case("FU-24", "follow_up", "ru", [
        turn("Сколько сделок было в 2024 году?", "FOUND", 106647, context=False),
        turn("а за 1900?", "NOT_FOUND", context=True),
    ]),
    case("FU-25", "follow_up", "ru", [
        turn("Сколько сделок было в 2024 году?", "FOUND", 106647, context=False),
        turn("а за 2003?", "NOT_FOUND", context=True),
    ]),
    case("FU-26", "follow_up", "ru", [
        turn("Покажи самую крупную сделку", "FOUND", context=False),
        turn("А самую маленькую?", "FOUND", context=True),
    ]),
    case("FU-27", "follow_up", "ru", [
        turn("Средняя сумма сделки в 2024", "FOUND", context=False),
        turn("А в 2023?", "FOUND", context=True),
    ]),
    case("FU-28", "follow_up", "ru", [
        turn("Динамика количества сделок по годам", "FOUND", context=False),
        turn("Только экспортные", "FOUND", context=True),
    ]),
    case("FU-29", "follow_up", "ru", [
        turn("Сколько сделок у продавца с ИНН 937772675", "FOUND", 1028, context=False),
        turn("А у покупателя с ИНН 929863757?", "FOUND", 5, context=True),
    ]),
    case("FU-30", "follow_up", "ru", [
        turn("Сколько сделок было в 2024 году?", "FOUND", 106647, context=False),
        turn("А за 2026?", "FOUND", 99306, context=True),
    ]),
]

CONTROL = [
    turn("Сколько сделок было в 2024 году?", "FOUND", 106647),
    turn("Сколько сделок было в 2023 году?", "FOUND", 93985),
    turn("Сколько сделок было в 2003 году?", "NOT_FOUND"),
    turn("Есть данные за 2003 год?", "NOT_FOUND"),
    turn("2024-yilda nechta bitim bo'ldi?", "FOUND", 106647),
    turn("2023-yilda nechta bitim bo'ldi?", "FOUND", 93985),
    turn("2024-йилда нечта битим бўлди?", "FOUND", 106647),
    turn("2023-йилда нечта битим бўлди?", "FOUND", 93985),
    turn("2003-yilda nechta bitim bo'ldi?", "NOT_FOUND"),
    turn("2003-йилда нечта битим бўлди?", "NOT_FOUND"),
]


def build() -> dict[str, Any]:
    cases = [*FREE_FORM, *FOLLOW_UP, *RU, *UZ, *AMBIGUOUS, *MISSING]
    counts: dict[str, int] = {}
    for item in cases:
        counts[item["category"]] = counts.get(item["category"], 0) + 1
    return {
        "schema_version": "3.0",
        "counts": dict(sorted(counts.items())),
        "control": CONTROL,
        "cases": cases,
    }


def main() -> None:
    payload = build()
    OUTPUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {OUTPUT} ({len(payload['cases'])} cases)")


if __name__ == "__main__":
    main()
