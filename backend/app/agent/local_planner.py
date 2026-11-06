from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Iterable

from app.agent.query_plan import (
    ClarificationInfo,
    ComparisonPeriod,
    ComparisonSpec,
    EntityReference,
    FilterCondition,
    OrderCondition,
    QueryPlan,
)
from app.config import settings
from app.domain.enums import (
    ClarificationCode,
    CurrencyMode,
    Decision,
    Direction,
    EntityRole,
    FilterField,
    GroupField,
    Intent,
    Language,
    MatchKind,
    Metric,
    Operator,
    OrderField,
    RegionMeaning,
    Script,
    Subject,
)
from app.domain.normalization import normalize_text
from app.domain.regions import REGIONS
from app.language import LanguageDetection, Synonyms, synonyms
from app.language.resources import phrase_present

# ---------------------------------------------------------------------------
# normalization helpers
# ---------------------------------------------------------------------------

_APOSTROPHES = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u02bb": "'",
        "\u02bc": "'",
        "\u201b": "'",
        "`": "'",
        "´": "'",
    }
)
_SPACES = re.compile(r"\s+", re.UNICODE)
_LEADING_FILLER = re.compile(
    r"^(?:а|и|and|then|now|теперь|va|ва)\s+", re.IGNORECASE
)
_YEAR = re.compile(r"\b(20\d{2})\b")
# A year outside the data domain still names a period.  Without this check
# «сколько сделок в 1900 году» silently fell back to the whole-table total.
_FOUR_DIGITS = re.compile(r"\b(\d{4})\b")
_YEAR_CUE = ("год", "йил", "yil")
_YEAR_RANGE = re.compile(
    r"\b(20\d{2})\s*(?:-|–|—|\.\.|по|до|gacha|dan|and|va)\s*(20\d{2})\b"
)
_DMY = re.compile(r"\b(\d{1,2})[./](\d{1,2})[./](\d{4})\b")
_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_BROKER_CODE = re.compile(r"\b(BK\d{2,6})\b", re.IGNORECASE)
_INN = re.compile(r"(?:инн|stir|стир)\D{0,12}(\d{9})", re.IGNORECASE)
_HS_CODE = re.compile(
    r"(?:тн\s*вэд|тнвэд|hs|ҳс|код\s+тн|kod\s+tn)\D{0,12}(\d{4,12})",
    re.IGNORECASE,
)
_TOP_LIMIT = re.compile(
    r"(?:топ|top|рейтинг|reyting)[-\s]?(\d{1,3})\b|"
    r"\b(\d{1,3})\s*(?:крупнейш|самых|самые|самый|лидер|eng\s+yirik|eng\s+katta)\b|"
    r"\b(?:первые|последние|first|last|birinchi|oxirgi)\s+(\d{1,3})\b",
    re.IGNORECASE,
)

# "Пять крупнейших покупателей" names a limit in words.  Keys are stored in
# the same normalized form the planner compares against, so apostrophes and
# case never change the match.
_NUMBER_WORDS: dict[str, int] = {
    normalize_text(word): value
    for word, value in {
        "один": 1,
        "два": 2,
        "три": 3,
        "четыре": 4,
        "пять": 5,
        "шесть": 6,
        "семь": 7,
        "восемь": 8,
        "девять": 9,
        "десять": 10,
        "пятнадцать": 15,
        "двадцать": 20,
        "bir": 1,
        "ikki": 2,
        "uch": 3,
        "to'rt": 4,
        "besh": 5,
        "olti": 6,
        "yetti": 7,
        "sakkiz": 8,
        "to'qqiz": 9,
        "o'n": 10,
        "yigirma": 20,
        "бир": 1,
        "икки": 2,
        "уч": 3,
        "тўрт": 4,
        "беш": 5,
        "олти": 6,
        "етти": 7,
        "саккиз": 8,
        "тўққиз": 9,
        "ўн": 10,
        "йигирма": 20,
    }.items()
}
_TOP_LIMIT_WORD = re.compile(
    r"\b("
    + "|".join(
        re.escape(word) for word in sorted(_NUMBER_WORDS, key=len, reverse=True)
    )
    + r")\s+(?:крупнейш|самых|самые|самый|самую|самая|лидер|"
    r"eng\s+yirik|eng\s+katta|энг\s+(?:катта|йирик))",
    re.IGNORECASE,
)
_COMPARE_YEARS = re.compile(r"\b(20\d{2})\b")
_EXPLICIT_PERIOD = re.compile(
    r"(?:с|dan|from)?\s*(\d{1,2})[./](\d{1,2})[./](\d{4})\s*"
    r"(?:по|до|gacha|to|-|–|—)\s*"
    r"(\d{1,2})[./](\d{1,2})[./](\d{4})",
    re.IGNORECASE,
)
_MONTH_YEAR = re.compile(r"\b(20\d{2})\b")
_LAST_YEAR = re.compile(
    r"(?:последн\w*\s+(?:год|12\s+месяц\w*)|за\s+год|oxirgi\s+yil|so'nggi\s+yil|"
    r"сўнгги\s+йил|охирги\s+йил)",
    re.IGNORECASE,
)

_GROUP_FIELDS: dict[str, GroupField] = {
    "year": GroupField.YEAR,
    "month": GroupField.MONTH,
    "contract_no": GroupField.CONTRACT_NO,
    "product_name": GroupField.PRODUCT_NAME,
    "product_category": GroupField.PRODUCT_CATEGORY,
    "product_group": GroupField.PRODUCT_GROUP,
    "industry": GroupField.INDUSTRY,
    "hs_code": GroupField.HS_CODE,
    "contract_type": GroupField.CONTRACT_TYPE,
    "contract_form": GroupField.CONTRACT_FORM,
    "currency": GroupField.CURRENCY,
    "trade_type": GroupField.TRADE_TYPE,
    "status": GroupField.STATUS,
    "unit_name": GroupField.UNIT_NAME,
    "warehouse_region": GroupField.WAREHOUSE_REGION,
    "seller_inn": GroupField.SELLER_INN,
    "buyer_inn": GroupField.BUYER_INN,
    "seller_broker_code": GroupField.SELLER_BROKER_CODE,
    "buyer_broker_code": GroupField.BUYER_BROKER_CODE,
}

_STATUS_SIDE_EFFECTS = {
    "не оплачен": "Аннулирован(Не оплачен)",
    "не оплачена": "Аннулирован(Не оплачен)",
    "to'lanmagan": "Аннулирован(Не оплачен)",
    "тўланмаган": "Аннулирован(Не оплачен)",
}

# A pronoun with no named entity is not answerable on its own.  In a session
# the orchestrator may resolve it from the previous turn; without history the
# planner must ask instead of returning a total for all companies.
_PRONOUN_RE = re.compile(
    r"(?<!\w)(?:у\s+)?(?:не[её]|него|них|uning|shuning|унинг|шунинг)(?!\w)",
    re.IGNORECASE,
)


def _soft(text: str) -> str:
    """Case-fold and normalize apostrophes, but keep punctuation for dates."""
    return _SPACES.sub(
        " ",
        unicodedata.normalize("NFKC", text or "").translate(_APOSTROPHES).casefold(),
    ).strip()


def _contains(normalized: str, values: Iterable[str]) -> bool:
    return any(value and value in normalized for value in values)


def _mentions(normalized: str, values: Iterable[str]) -> bool:
    """Phrase-aware marker matching used for currencies and vocabulary."""
    return any(phrase_present(normalized, value) for value in values if value)


def _has_any(normalized: str, words: Iterable[str]) -> bool:
    padded = f" {normalized} "
    return any(f" {word} " in padded for word in words if word)


# ---------------------------------------------------------------------------
# periods
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Period:
    start: date
    end: date
    label: str


def _month_bounds(year: int, month: int) -> tuple[date, date]:
    start = date(year, month, 1)
    if month == 12:
        end = date(year, 12, 31)
    else:
        end = date(year, month + 1, 1) - timedelta(days=1)
    return start, end


def _quarter_bounds(year: int, quarter: int) -> tuple[date, date]:
    start_month = (quarter - 1) * 3 + 1
    start = date(year, start_month, 1)
    if quarter == 4:
        end = date(year, 12, 31)
    else:
        end = date(year, start_month + 3, 1) - timedelta(days=1)
    return start, end


def _dmy(match: re.Match[str]) -> date:
    day, month, year = (int(part) for part in match.groups())
    return date(year, month, day)


def _iso(match: re.Match[str]) -> date:
    year, month, day = (int(part) for part in match.groups())
    return date(year, month, day)


def _relative_period(normalized: str) -> Period | None:
    if not _LAST_YEAR.search(normalized):
        return None
    cutoff = date.fromisoformat(settings.dataset_cutoff_date)
    start = cutoff - timedelta(days=365)
    return Period(start, cutoff, "last_12_months")


@dataclass(frozen=True)
class PeriodParse:
    periods: tuple[Period, ...] = ()
    missing: bool = False
    ambiguous: bool = False


def parse_period(question: str, normalized: str, syn: Synonyms) -> PeriodParse:
    soft = _soft(question)

    # Explicit date ranges have priority over years that appear inside them.
    explicit = list(_EXPLICIT_PERIOD.finditer(soft))
    if explicit:
        match = explicit[0]
        start = date(int(match.group(3)), int(match.group(2)), int(match.group(1)))
        end = date(int(match.group(6)), int(match.group(5)), int(match.group(4)))
        if start > end:
            return PeriodParse(ambiguous=True)
        return PeriodParse((Period(start, end, f"{start.isoformat()}..{end.isoformat()}"),))

    dmy_dates = [_dmy(match) for match in _DMY.finditer(soft)]
    iso_dates = [_iso(match) for match in _ISO.finditer(soft)]
    dates = sorted({*dmy_dates, *iso_dates})
    if len(dates) >= 2:
        if len(dates) > 2:
            return PeriodParse(ambiguous=True)
        return PeriodParse((Period(dates[0], dates[1], f"{dates[0].isoformat()}..{dates[1].isoformat()}"),))
    if len(dates) == 1:
        return PeriodParse((Period(dates[0], dates[0], dates[0].isoformat()),))

    year_range = _YEAR_RANGE.search(soft)
    years = [int(value) for value in _YEAR.findall(soft)]
    unique_years = sorted(set(years))
    if year_range:
        first, second = int(year_range.group(1)), int(year_range.group(2))
        start_year, end_year = sorted((first, second))
        return PeriodParse(
            (
                Period(
                    date(start_year, 1, 1),
                    date(end_year, 12, 31),
                    f"{start_year}..{end_year}",
                ),
            )
        )

    month = syn.month_from_text(normalized)
    quarter = syn.quarter_from_text(normalized)
    if month is not None:
        if not unique_years:
            return PeriodParse(ambiguous=True)
        year = unique_years[0]
        start, end = _month_bounds(year, month)
        return PeriodParse((Period(start, end, f"{year}-{month:02d}"),))
    if quarter is not None:
        if not unique_years:
            return PeriodParse(ambiguous=True)
        year = unique_years[0]
        start, end = _quarter_bounds(year, quarter)
        return PeriodParse((Period(start, end, f"{year}-Q{quarter}"),))

    if len(unique_years) == 1:
        year = unique_years[0]
        return PeriodParse((Period(date(year, 1, 1), date(year, 12, 31), str(year)),))
    if len(unique_years) > 1:
        first, last = unique_years[0], unique_years[-1]
        return PeriodParse(
            (Period(date(first, 1, 1), date(last, 12, 31), f"{first}..{last}"),)
        )

    relative = _relative_period(normalized)
    if relative is not None:
        return PeriodParse((relative,))

    # An explicit year-like number outside 20xx (for example 1900 or 2100)
    # must become a real period filter so the handler returns NOT_FOUND with
    # matched_rows=0 instead of the all-time total.
    if _contains(normalized, _YEAR_CUE):
        for match in _FOUR_DIGITS.finditer(soft):
            year = int(match.group(1))
            if 1000 <= year <= 2999 and not (2000 <= year <= 2099):
                return PeriodParse(
                    (Period(date(year, 1, 1), date(year, 12, 31), str(year)),)
                )

    if _contains(normalized, ("за период", "период", "davr", "давр")):
        return PeriodParse(missing=True)
    return PeriodParse()


def _compare_periods(
    question: str, normalized: str, syn: Synonyms
) -> tuple[ComparisonPeriod, ComparisonPeriod] | None:
    soft = _soft(question)
    explicit = list(_EXPLICIT_PERIOD.finditer(soft))
    if len(explicit) >= 2:
        first, second = explicit[0], explicit[1]
        left = ComparisonPeriod(
            label="period_1",
            date_from=date(int(first.group(3)), int(first.group(2)), int(first.group(1))),
            date_to=date(int(first.group(6)), int(first.group(5)), int(first.group(4))),
        )
        right = ComparisonPeriod(
            label="period_2",
            date_from=date(int(second.group(3)), int(second.group(2)), int(second.group(1))),
            date_to=date(int(second.group(6)), int(second.group(5)), int(second.group(4))),
        )
        if left.date_from <= right.date_to and right.date_from <= left.date_to:
            return None
        return left, right

    years: list[int] = []
    for value in _COMPARE_YEARS.findall(soft):
        year = int(value)
        if year not in years:
            years.append(year)
    if len(years) == 2:
        first, second = years
        return (
            ComparisonPeriod(
                label=str(first),
                date_from=date(first, 1, 1),
                date_to=date(first, 12, 31),
            ),
            ComparisonPeriod(
                label=str(second),
                date_from=date(second, 1, 1),
                date_to=date(second, 12, 31),
            ),
        )
    return None


# ---------------------------------------------------------------------------
# entity extraction
# ---------------------------------------------------------------------------

_REGION_ALIASES: list[tuple[str, int, str]] = []
_REGION_ROOTS: list[tuple[str, int, str]] = []
for _region in REGIONS:
    for _raw, _lang in (
        (_region.name_ru, "ru"),
        (_region.name_uz, "uz_latn"),
        *_region.aliases,
    ):
        _normalized_alias = normalize_text(_raw)
        _REGION_ALIASES.append((_normalized_alias, _region.region_id, _raw))
        # A stem allows safe recognition of inflected forms such as
        # «в Ташкенте» or «в Бухаре».  It is used only to locate a possible
        # region mention; the database resolver still decides whether the name
        # is unique and otherwise asks for clarification.
        _stop = {
            "область",
            "вилояти",
            "вилоят",
            "shahri",
            "shahar",
            "республика",
            "respublikasi",
            "республикаси",
            "город",
            "г",
        }
        _words = [
            word
            for word in _normalized_alias.split()
            if len(word) >= 4 and word not in _stop
        ]
        if _words:
            _root = _words[0]
            for _suffix in (
                "ская",
                "ский",
                "ской",
                "скую",
                "ском",
                "ая",
                "ий",
                "ой",
                "ый",
                "ую",
                "ом",
                "е",
                "и",
                "а",
                "я",
            ):
                if _root.endswith(_suffix) and len(_root) - len(_suffix) >= 4:
                    _root = _root[: -len(_suffix)]
                    break
            if _root.endswith("й") and len(_root) > 5:
                _root = _root[:-1]
            _REGION_ROOTS.append((_root, _region.region_id, _raw))
_REGION_ALIASES.sort(key=lambda item: -len(item[0]))
_REGION_ROOTS.sort(key=lambda item: -len(item[0]))

_REGION_STOP_WORDS = {
    "область",
    "области",
    "вилояти",
    "вилоят",
    "shahri",
    "shahar",
    "республика",
    "respublikasi",
    "республикаси",
    "город",
    "г",
}


def _root_word(word: str) -> str:
    for suffix in (
        "ская",
        "ский",
        "ской",
        "скую",
        "ском",
        "ая",
        "ий",
        "ой",
        "ый",
        "ую",
        "ом",
        "е",
        "и",
        "а",
        "я",
    ):
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            word = word[: -len(suffix)]
            break
    if word.endswith("й") and len(word) > 5:
        word = word[:-1]
    return word


_REGION_FLEXIBLE: list[tuple[re.Pattern[str], int, str]] = []
for _normalized_alias, _region_id, _raw in _REGION_ALIASES:
    _words = [
        word
        for word in _normalized_alias.split()
        if len(word) >= 4 and word not in _REGION_STOP_WORDS
    ]
    if not _words:
        continue
    _pattern = r"(?<!\w)" + r"\w*\s+".join(
        re.escape(_root_word(word)) for word in _words
    ) + r"\w*"
    _REGION_FLEXIBLE.append((re.compile(_pattern, re.IGNORECASE), _region_id, _raw))
_REGION_FLEXIBLE.sort(key=lambda item: -len(item[0].pattern))



_COMPANY_CUE = (
    r"(?:компани[яиюе]|компаний|участник[аеу]?|участников|фирм[аыуе]?|"
    r"организаци[яию]|организаций|предприяти[яею]|поставщик[аеу]?|"
    r"контрагент[аеу]?|продав(?:ец|ца|цу|цов|цом)?|"
    r"покупател[ьяюей]?|sotuvchi(?:lar)?|xaridor(?:lar)?|kompaniya(?:lar)?|"
    r"ishtirokchi(?:lar)?|tashkilot|korxona|сотувчи(?:лар)?|харидор(?:лар)?|"
    r"компания(?:лар)?|иштирокчи(?:лар)?|ташкилот|корхона)"
)
_BROKER_CUE = r"(?:брокер(?:а|у|ы|ов|лар)?|broker(?:lar)?|брокер(?:лар)?)"
_PRODUCT_CUE = (
    r"(?:товар[а-я]*|продукт[а-я]*|product|tovar(?:lar)?|mahsulot(?:lar)?|"
    r"товар(?:лар)?|маҳсулот(?:лар)?|product(?:lar)?)"
)
_QUOTED_AFTER_CUE = re.compile(
    rf"(?:{_COMPANY_CUE}|{_BROKER_CUE}|{_PRODUCT_CUE})[^«»\"']{{0,25}}"
    r"[«\"']([^»\"']{1,200})[»\"']",
    re.IGNORECASE,
)
_QUOTED_BROKER = re.compile(
    rf"(?:{_BROKER_CUE})[^«»\"']{{0,25}}[«\"']([^»\"']{{1,200}})[»\"']",
    re.IGNORECASE,
)
_QUOTED_PRODUCT = re.compile(
    rf"(?:{_PRODUCT_CUE})[^«»\"']{{0,25}}[«\"']([^»\"']{{1,200}})[»\"']",
    re.IGNORECASE,
)
_CORP_SUFFIX = r"(?:XK|MCHJ|AJ|LLC|LTD|JSC|ИП|ЧП|ФХ|ООО|АО|МЧЖ|ХК)"
_QUOTED_COMPANY = re.compile(
    rf"(?:{_COMPANY_CUE})[^«»\"']{{0,25}}[«\"']([^»\"']{{1,200}})[»\"']"
    rf"(?:\s+({_CORP_SUFFIX}))?",
    re.IGNORECASE,
)
_UNQUOTED_AFTER_COMPANY = re.compile(
    rf"(?:{_COMPANY_CUE})\s*(?:под\s+названием|с\s+названием|nomli|nomidagi|degan)?\s+"
    r"(.{2,200})$",
    re.IGNORECASE,
)
_UNQUOTED_AFTER_BROKER = re.compile(
    rf"(?:{_BROKER_CUE})\s*(?:под\s+названием|с\s+названием|nomli|nomidagi|degan)?\s+"
    r"(.{2,200})$",
    re.IGNORECASE,
)
_UNQUOTED_AFTER_PRODUCT = re.compile(
    rf"(?:{_PRODUCT_CUE})\s*(?:под\s+названием|с\s+названием|nomli|nomidagi|degan)?\s+"
    r"(.{2,200})$",
    re.IGNORECASE,
)
_ROLE_BEFORE = re.compile(
    r"(?:продав(?:ец|ца|цу|цов|цом)|поставщик[аеу]?|sotuvchi(?:lar)?|"
    r"сотувчи(?:лар)?)(?:.{0,40})$",
    re.IGNORECASE,
)
_BUYER_BEFORE = re.compile(
    r"(?:покупател[ьяюей]|харидор(?:лар)?|xaridor(?:lar)?)(?:.{0,40})$",
    re.IGNORECASE,
)
_TRAILING_STOP = re.compile(
    r"\s+(?:за|в|во|на|по|с|со|из|для|от|до|и|или|пожалуйста|"
    r"uchun|bo'yicha|bilan|va|yoki|dan|gacha|yil|yilda|oy|oyda|davr|iltimos|"
    r"учун|бўйича|билан|ва|ёки|дан|гача|йил|йилда|ой|ойда|давр|илтимос)\b.*$",
    re.IGNORECASE,
)
_DATE_LIKE_TAIL = re.compile(r"\s+(?:20\d{2}|\d{1,2}[./]\d{1,2}[./]\d{4}).*$")


def _clean_mention(value: str) -> str:
    text = unicodedata.normalize("NFKC", value).strip(" \t\r\n,;:.?!«»\"'")
    # Keep internal quotes/apostrophes: they are part of official names.
    text = re.sub(r"\s+", " ", text).strip()
    text = _TRAILING_STOP.sub("", text).strip()
    text = _DATE_LIKE_TAIL.sub("", text).strip(" \t\r\n,;:.?!«»\"'")
    return text


def _looks_like_mention(value: str) -> bool:
    if len(value) < 2:
        return False
    if value.isdigit():
        return False
    normalized = normalize_text(value)
    generic = {
        "сделки",
        "сделка",
        "контракты",
        "контракт",
        "товар",
        "товары",
        "продукт",
        "продукты",
        "брокер",
        "брокеры",
        "компания",
        "компании",
        "участник",
        "участники",
        "bitim",
        "shartnoma",
        "tovar",
        "broker",
        "kompaniya",
        "битим",
        "шартнома",
        "товар",
        "брокер",
        "компания",
        # Interrogatives after a generic cue ("компания какая?") must never be
        # treated as the company's name.
        "какой",
        "какая",
        "какие",
        "какую",
        "который",
        "которая",
        "которые",
        "кто",
        "qaysi",
        "qanday",
        "kim",
        "қайси",
        "қандай",
        "ким",
        "which",
        "всех",
        "все",
        "всей",
        "весь",
        "всего",
        "этих",
        "данных",
    }
    return normalized not in generic and not normalized.isdigit()


def _extract_company_mentions(question: str, normalized: str) -> list[str]:
    mentions: list[str] = []
    for match in _QUOTED_COMPANY.finditer(question):
        value = _clean_mention(match.group(1))
        suffix = match.group(2)
        if suffix:
            value = f"{value} {suffix}"
        if _looks_like_mention(value):
            mentions.append(value)
    if mentions:
        return _dedupe_strings(mentions)

    for match in _UNQUOTED_AFTER_COMPANY.finditer(question):
        value = _clean_mention(match.group(1))
        if _looks_like_mention(value) and not _is_generic_question_tail(value):
            mentions.append(value)
    return _dedupe_strings(mentions)


def _extract_broker_mentions(question: str, normalized: str) -> list[str]:
    mentions = [match.group(1).upper() for match in _BROKER_CODE.finditer(question)]
    code_mention = mentions[0] if mentions else None
    if code_mention:
        return [code_mention]

    quoted: list[str] = []
    for match in _QUOTED_BROKER.finditer(question):
        value = _clean_mention(match.group(1))
        if _looks_like_mention(value):
            quoted.append(value)
    if quoted:
        return _dedupe_strings(quoted)

    for match in _UNQUOTED_AFTER_BROKER.finditer(question):
        value = _clean_mention(match.group(1))
        # «брокер продавца» is a side request, not a broker name.
        if re.match(
            r"^(?:продав|покупател|sotuvchi|xaridor|сотувчи|харидор)",
            normalize_text(value),
        ):
            continue
        if _looks_like_mention(value) and not _is_generic_question_tail(value):
            mentions.append(value)
    return _dedupe_strings(mentions)


def _extract_product_mentions(question: str, normalized: str) -> list[str]:
    mentions: list[str] = []
    for match in _QUOTED_PRODUCT.finditer(question):
        value = _clean_mention(match.group(1))
        if _looks_like_mention(value):
            mentions.append(value)
    if mentions:
        return _dedupe_strings(mentions)

    for match in _UNQUOTED_AFTER_PRODUCT.finditer(question):
        value = _clean_mention(match.group(1))
        if _looks_like_mention(value) and not _is_generic_question_tail(value):
            mentions.append(value)
    if mentions:
        return _dedupe_strings(mentions)

    # Conservative fallback for natural genitive forms such as
    # «цена карбамида» or «поставлено тонн карбамида».  The candidate is only
    # an entity mention: the resolver still has to confirm an exact product
    # name and otherwise asks for clarification.
    generic = {
        "сделка",
        "сделки",
        "сделок",
        "сделку",
        "всех",
        "все",
        "всей",
        "весь",
        "всего",
        "этих",
        "данных",
        "контракт",
        "контракта",
        "контрактов",
        "товар",
        "товара",
        "товаров",
        "продукт",
        "продукта",
        "продуктов",
        "сумма",
        "суммы",
        "сумму",
        "стоимость",
        "стоимости",
        "объем",
        "объема",
        "объемы",
        "объём",
        "объёма",
        "объёмы",
        "количество",
        "количества",
        "поставка",
        "поставки",
        "поставок",
        "продажа",
        "продажи",
        "регион",
        "региона",
        "регионов",
        "склад",
        "склада",
        "складов",
        "валюта",
        "валюты",
        "валюте",
        "доллар",
        "долларах",
        "сум",
        "сумах",
        "год",
        "года",
        "году",
        "годы",
        "годов",
        "месяц",
        "месяца",
        "месяцев",
        "период",
        "периода",
        "статус",
        "статуса",
        "экспорт",
        "экспорта",
        "экспортный",
        "экспортная",
        "экспортные",
        "экспортных",
        "импорт",
        "импорта",
        "импортный",
        "импортная",
        "импортные",
        "импортных",
        "внутренний",
        "внутренняя",
        "внутренние",
        "внутренних",
        "внешний",
        "внешняя",
        "внешние",
        "внешних",
        "иностранный",
        "иностранная",
        "иностранные",
        "иностранных",
        "eksport",
        "import",
        "ichki",
        "tashqi",
        "тонн",
        "тонна",
        "тонны",
        "тоннах",
        "кг",
        "килограмм",
        "литр",
        "литров",
        "метр",
        "метров",
        "штук",
        "дона",
        "bitim",
        "bitimlar",
        "shartnoma",
        "shartnomalar",
        "tovar",
        "tovarlar",
        "mahsulot",
        "mahsulotlar",
        "summa",
        "hajm",
        "davr",
        "yil",
        "oy",
    }
    patterns = (
        re.compile(
            r"(?:цена|стоимость|объём|объем|количество|сумма|поставлено|"
            r"поставка|продажа|отгрузка|narx|hajm|summa|qiymat|yetkazilgan)"
            r"\s+(?:(?:товара|товар|продукта|продукт|tovar|mahsulot)\s+)?"
            r"([a-zа-яё][a-zа-яё'\-]{3,})",
            re.IGNORECASE,
        ),
        re.compile(
            r"(?:тонн\w*|тонна\w*|кг|килограмм\w*|литр\w*|метр\w*|"
            r"штук\w*|dona|kg|tonna\w*)\s+"
            r"([a-zа-яё][a-zа-яё'\-]{3,})",
            re.IGNORECASE,
        ),
    )
    for pattern in patterns:
        for match in pattern.finditer(question):
            value = _clean_mention(match.group(1))
            if not value or len(value) < 4:
                continue
            if normalize_text(value) in generic:
                continue
            if value.isdigit():
                continue
            mentions.append(value)
    return _dedupe_strings(mentions)


def _is_generic_question_tail(value: str) -> bool:
    normalized = normalize_text(value)
    generic_starts = (
        "по ",
        "за ",
        "в ",
        "во ",
        "на ",
        "с ",
        "со ",
        "из ",
        "и ",
        "или ",
        "какой ",
        "какая ",
        "какие ",
        "сколько ",
        "больше ",
        "кто ",
        "что ",
        "где ",
        "когда ",
        "qaysi ",
        "qancha ",
        "nechta ",
        # Analytical phrasing is not a product name.  Without these a
        # question such as «... товара чаще всего встречалось ...» would
        # resolve the mention «чаще всего встречалось».
        "чаще ",
        "реже ",
        "как част",
        "наименован",
        "полное ",
        "встречал",
        "eng ko",
        "necha marta",
        "nomlanish",
        "to'liq nom",
        "энг кўп учра",
        "неча марта",
        "номланиш",
        "тўлиқ ном",
    )
    if normalized.startswith(generic_starts):
        return True
    if not normalized:
        return True
    first = normalized.split()[0]
    return first in {
        "за",
        "в",
        "во",
        "на",
        "по",
        "с",
        "со",
        "из",
        "и",
        "или",
        "какой",
        "какая",
        "какие",
        "сколько",
        "больше",
        "кто",
        "что",
        "где",
        "когда",
        "qaysi",
        "qancha",
        "nechta",
        "чаще",
        "реже",
        "встречалось",
        "встречался",
        "наименование",
        "полное",
        "eng",
        "nomlanishi",
        "тўлиқ",
        "номи",
        "uchun",
        "bo'yicha",
        "bilan",
        "dan",
        "gacha",
    }


def _dedupe_strings(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def _role_from_context(question: str, mention: str) -> EntityRole | None:
    soft = _soft(question)
    position = soft.find(mention.casefold())
    if position < 0:
        position = soft.find(_soft(mention))
    window = soft[max(0, position - 80) : position] if position >= 0 else soft
    if _BUYER_BEFORE.search(window):
        return EntityRole.BUYER
    if _ROLE_BEFORE.search(window):
        return EntityRole.SELLER
    return None


def _broker_role_from_context(normalized: str) -> EntityRole | None:
    if _contains(
        normalized,
        (
            "брокер продавца",
            "брокера продавца",
            "брокеры продавца",
            "брокер продавцов",
            "брокеров продавца",
            "брокеров продавцов",
            "sotuvchi broker",
            "sotuvchi brokerlari",
            "сотувчи брокер",
            "сотувчи брокерлари",
        ),
    ):
        return EntityRole.BROKER_SELLER
    if _contains(
        normalized,
        (
            "брокер покупателя",
            "брокера покупателя",
            "брокеры покупателя",
            "брокер покупателей",
            "брокеров покупателя",
            "брокеров покупателей",
            "xaridor broker",
            "xaridor brokerlari",
            "харидор брокер",
            "харидор брокерлари",
        ),
    ):
        return EntityRole.BROKER_BUYER
    return None


def _region_mention(question: str, normalized: str) -> tuple[str, int] | None:
    padded = f" {normalized} "
    for normalized_alias, region_id, raw in _REGION_ALIASES:
        if f" {normalized_alias} " in padded:
            # Return the original name from the question when possible, so the
            # resolver sees the user's spelling rather than a rewritten value.
            pattern = re.compile(re.escape(raw), re.IGNORECASE)
            match = pattern.search(question)
            return (match.group(0) if match else raw, region_id)

    # Inflected forms («в Ташкенте», «в Бухаре», «Ташкентской области»).  This
    # only locates a possible mention; the database resolver still performs the
    # exact/fuzzy check and never auto-selects an ambiguous region.
    for pattern, region_id, _raw in _REGION_FLEXIBLE:
        match = pattern.search(normalized)
        if match:
            return (match.group(0), region_id)
    return None


def _region_entity(
    question: str, normalized: str, syn: Synonyms
) -> EntityReference | None:
    found = _region_mention(question, normalized)
    if found is None:
        return None
    mention, _ = found

    company_context = _contains(
        normalized,
        (
            "компани",
            "участник",
            "фирм",
            "организаци",
            "предприяти",
            "sotuvchi",
            "xaridor",
            "sotgan",
            "sotib olgan",
            "kompaniya",
            "ishtirokchi",
            "продав",
            "покупател",
            "сотган",
            "сотиб олган",
            "ташкилот",
            "корхона",
        ),
    )
    warehouse_context = _contains(
        normalized,
        (
            "склад",
            "регион",
            "где хран",
            "ombor",
            "омбор",
        ),
    )
    if company_context:
        role = _role_from_context(question, mention)
        return EntityReference(
            kind=MatchKind.REGION,
            mention=mention,
            meaning=RegionMeaning.COMPANY,
            role=role,
        )
    if warehouse_context:
        return EntityReference(
            kind=MatchKind.REGION,
            mention=mention,
            meaning=RegionMeaning.WAREHOUSE,
        )
    # A bare place («в Ташкенте») can mean the warehouse region or the company
    # region.  Leave the meaning empty so the resolver asks for clarification.
    return EntityReference(kind=MatchKind.REGION, mention=mention)


# ---------------------------------------------------------------------------
# plan construction
# ---------------------------------------------------------------------------


@dataclass
class _Draft:
    decision: Decision = Decision.EXECUTE
    intent: Intent = Intent.COUNT
    subject: Subject = Subject.DEALS
    metric: Metric = Metric.DEAL_COUNT
    filters: list[FilterCondition] = field(default_factory=list)
    entities: list[EntityReference] = field(default_factory=list)
    group_by: list[GroupField] = field(default_factory=list)
    order_by: list[OrderCondition] = field(default_factory=list)
    limit: int | None = None
    currency_mode: CurrencyMode = CurrencyMode.NOT_APPLICABLE
    clarification: ClarificationInfo | None = None
    comparison: ComparisonSpec | None = None

    def add_filter(
        self, field: FilterField, operator: Operator, value: object
    ) -> None:
        condition = FilterCondition(field=field, operator=operator, value=value)  # type: ignore[arg-type]
        key = (condition.field.value, condition.operator.value, repr(condition.value))
        if any(
            (item.field.value, item.operator.value, repr(item.value)) == key
            for item in self.filters
        ):
            return
        self.filters.append(condition)

    def add_entity(self, entity: EntityReference | None) -> None:
        if entity is None:
            return
        key = f"{entity.kind.value}:{entity.mention.strip().casefold()}"
        if any(
            f"{item.kind.value}:{item.mention.strip().casefold()}" == key
            for item in self.entities
        ):
            return
        self.entities.append(entity)

    def add_group(self, field: GroupField) -> None:
        if field not in self.group_by and len(self.group_by) < 3:
            self.group_by.append(field)


def _make_plan(draft: _Draft, detection: LanguageDetection) -> QueryPlan:
    return QueryPlan(
        schema_version="1.0",
        decision=draft.decision,
        language=detection.language,
        script=detection.script,
        intent=draft.intent,
        subject=draft.subject,
        metric=draft.metric,
        filters=draft.filters,
        group_by=draft.group_by,
        order_by=draft.order_by,
        limit=draft.limit,
        currency_mode=draft.currency_mode,
        entity_candidates=draft.entities,
        clarification=draft.clarification,
        comparison=draft.comparison,
    )


def _unsupported(detection: LanguageDetection) -> QueryPlan:
    return _make_plan(_Draft(decision=Decision.UNSUPPORTED), detection)


def _clarify(
    detection: LanguageDetection,
    code: ClarificationCode,
    options: list[str] | None = None,
) -> QueryPlan:
    return _make_plan(
        _Draft(
            decision=Decision.CLARIFY,
            clarification=ClarificationInfo(code=code, options=options or []),
        ),
        detection,
    )


# ---------------------------------------------------------------------------
# planner
# ---------------------------------------------------------------------------


class LocalPlanner:
    """Deterministic, offline planner for the supported question shapes.

    It intentionally only understands vocabulary that is declared in the
    versioned language resources.  Anything else becomes UNSUPPORTED.  It never
    reads the database and never produces SQL.
    """

    def plan(self, question: str, detection: LanguageDetection) -> QueryPlan:
        text = (question or "").strip()
        # "А у покупателя с ИНН ...?" is a full question with a discourse
        # marker, not a new ellipsis.  Dropping only that leading marker keeps
        # the rest of the question intact.
        text = _LEADING_FILLER.sub("", text)
        normalized = normalize_text(text)
        syn = synonyms(detection.locale)

        if _contains(normalized, syn.injection):
            return _unsupported(detection)
        if _contains(normalized, syn.unsupported):
            return _unsupported(detection)

        lookup = self._lookup_plan(text, normalized, syn, detection)
        if lookup is not None:
            return lookup

        return self._analytics_plan(text, normalized, syn, detection)

    # -- exact lookups ---------------------------------------------------
    def _lookup_plan(
        self,
        question: str,
        normalized: str,
        syn: Synonyms,
        detection: LanguageDetection,
    ) -> QueryPlan | None:
        soft = _soft(question)

        # Contract lookup requires an explicit lookup cue or a short
        # "контракт <value>" command.  Analytics questions such as
        # «сколько контрактов» must not be mistaken for a contract card.
        contract_number = self._extract_contract_number(question, soft)
        if contract_number is not None:
            draft = _Draft(
                intent=Intent.CONTRACT_LOOKUP,
                subject=Subject.CONTRACTS,
                metric=Metric.RECORD_LOOKUP,
            )
            draft.add_filter(FilterField.CONTRACT_NO, Operator.EQ, contract_number)
            return _make_plan(draft, detection)

        # Deal lookup by an explicit deal cue and an exact 6-digit deal number.
        deal_number = self._extract_deal_number(soft)
        if deal_number is not None:
            draft = _Draft(
                intent=Intent.DEAL_LOOKUP,
                subject=Subject.DEALS,
                metric=Metric.RECORD_LOOKUP,
            )
            draft.add_filter(FilterField.DEAL_NO, Operator.EQ, deal_number)
            return _make_plan(draft, detection)

        # Broker lookup: explicit code, or a clearly named broker card.
        broker_code = self._extract_broker_code(soft)
        broker_lookup_cue = _contains(
            normalized,
            (
                "информаци",
                "кто такой",
                "кто такая",
                "карточка",
                "найди брокера",
                "покажи брокера",
                "данные о брокере",
                "сведения о брокере",
                "ma'lumot",
                "toping",
                "kartochka",
                "маълумот",
                "топинг",
                "карточка",
            ),
        )
        if broker_code and broker_lookup_cue:
            draft = _Draft(
                intent=Intent.BROKER_LOOKUP,
                subject=Subject.BROKERS,
                metric=Metric.RECORD_LOOKUP,
            )
            draft.add_filter(FilterField.SELLER_BROKER_CODE, Operator.EQ, broker_code)
            return _make_plan(draft, detection)
        if broker_code and _contains(normalized, ("брокер", "broker", "брокер")):
            # A bare broker code asks for the broker card; analytics questions
            # about a broker require a seller/buyer side and are handled below.
            if not _contains(
                normalized,
                (
                    "сделк",
                    "сумм",
                    "объём",
                    "объем",
                    "количество",
                    "сколько",
                    "bitim",
                    "summa",
                    "hajm",
                    "nechta",
                    "qancha",
                ),
            ):
                draft = _Draft(
                    intent=Intent.BROKER_LOOKUP,
                    subject=Subject.BROKERS,
                    metric=Metric.RECORD_LOOKUP,
                )
                draft.add_filter(
                    FilterField.SELLER_BROKER_CODE, Operator.EQ, broker_code
                )
                return _make_plan(draft, detection)

        # Participant lookup by INN or by a clearly requested company card.
        inn = _INN.search(soft)
        participant_lookup_cue = _contains(
            normalized,
            (
                "информаци",
                "кто такой",
                "кто такая",
                "карточка",
                "найди компанию",
                "покажи компанию",
                "найди участника",
                "покажи участника",
                "данные о компании",
                "сведения о компании",
                "ma'lumot",
                "toping",
                "kartochka",
                "маълумот",
                "топинг",
            ),
        )
        if inn and participant_lookup_cue:
            draft = _Draft(
                intent=Intent.PARTICIPANT_LOOKUP,
                subject=Subject.PARTICIPANTS,
                metric=Metric.RECORD_LOOKUP,
            )
            draft.add_filter(FilterField.SELLER_INN, Operator.EQ, inn.group(1))
            return _make_plan(draft, detection)
        if participant_lookup_cue and _contains(
            normalized,
            (
                "компани",
                "участник",
                "фирм",
                "организаци",
                "предприяти",
                "kompaniya",
                "ishtirokchi",
                "компания",
                "иштирокчи",
            ),
        ):
            mentions = _extract_company_mentions(question, normalized)
            if len(mentions) == 1:
                draft = _Draft(
                    intent=Intent.PARTICIPANT_LOOKUP,
                    subject=Subject.PARTICIPANTS,
                    metric=Metric.RECORD_LOOKUP,
                )
                draft.add_filter(FilterField.SELLER_INN, Operator.EQ, mentions[0])
                return _make_plan(draft, detection)

        return None

    def _extract_deal_number(self, soft: str) -> str | None:
        pattern = re.compile(
            r"(?:номер\s+сделки|№\s*сделки|bitim\s+raqami|битим\s+рақами|"
            r"(?<!\w)(?:сделка|сделку|bitim|битим)(?!\w))"
            r"\D{0,15}(\d{6})(?!\d)",
            re.IGNORECASE,
        )
        match = pattern.search(soft)
        return match.group(1) if match else None

    def _extract_contract_number(
        self, question: str, soft: str
    ) -> str | None:
        lookup_cue = re.search(
            r"(?:номер|№|карточк|информаци|сведени|данн|найди|покажи|открой|"
            r"contract\s+(?:number|no)|shartnoma\s+raqami|маълумот|топинг|"
            r"кўрсат|ko'rsat)",
            soft,
            re.IGNORECASE,
        )
        short_command = re.match(
            r"^\s*(?:покажи|найди|открой|карточка|ko'rsat|toping)?\s*"
            r"(?:контракт|шартнома|shartnoma|contract)\b",
            soft,
            re.IGNORECASE,
        )
        if not lookup_cue and not short_command:
            return None
        if re.search(
            r"(?:сколько|количество|число|nechta|qancha|сони|нечта|қанча)",
            soft,
            re.IGNORECASE,
        ):
            return None

        pattern = re.compile(
            r"(?:контракт[а-я]*|шартнома(?:си)?|shartnoma(?:si)?|contract)"
            r"\D{0,25}([a-zа-я0-9][a-zа-я0-9_/-]{2,30})",
            re.IGNORECASE,
        )
        for match in pattern.finditer(soft):
            value = match.group(1).strip(" .,;:!?")
            if not re.search(r"\d", value):
                continue
            if value.isdigit() and len(value) == 4 and 2020 <= int(value) <= 2035:
                continue
            if normalize_text(value) in {
                "за",
                "в",
                "во",
                "на",
                "по",
                "с",
                "со",
                "из",
                "для",
                "от",
                "до",
                "и",
                "или",
            }:
                continue
            return value
        return None

    def _extract_broker_code(self, soft: str) -> str | None:
        match = _BROKER_CODE.search(soft)
        return match.group(1).upper() if match else None

    # -- analytics -------------------------------------------------------
    def _analytics_plan(
        self,
        question: str,
        normalized: str,
        syn: Synonyms,
        detection: LanguageDetection,
    ) -> QueryPlan:
        draft = _Draft()

        # Period parsing is shared by all analytics intents.
        period_parse = parse_period(question, normalized, syn)

        compare_cue = syn.has_any(normalized, syn.intent_keywords("compare"))
        trend_cue = syn.has_any(normalized, syn.intent_keywords("trend"))
        top_cue = syn.has_any(normalized, syn.intent_keywords("top"))
        min_cue = syn.has_any(normalized, syn.intent_keywords("min"))
        max_cue = syn.has_any(normalized, syn.intent_keywords("max"))
        average_cue = syn.has_any(normalized, syn.intent_keywords("average"))
        volume_cue = syn.has_any(normalized, syn.intent_keywords("volume"))
        delivered_cue = syn.has_any(normalized, syn.intent_keywords("delivered"))
        sum_cue = syn.has_any(normalized, syn.intent_keywords("sum"))
        count_cue = syn.has_any(normalized, syn.intent_keywords("count"))
        list_cue = syn.has_any(normalized, syn.intent_keywords("list")) or bool(
            re.match(r"^\s*(?:сделки|битимлар|bitimlar)\b", normalized)
        )
        presence_cue = _contains(
            normalized,
            (
                "есть ",
                "имеются",
                "имеется",
                "данные",
                "запросы",
                "наличи",
                "mavjud",
                "bor ",
                "мавжуд",
                "бор ",
                "маълумот",
            ),
        )

        # "Самые крупные сделки" is a min/max question, not a ranking of
        # arbitrary groups.  The top_n contract for deals requires a grouping.
        superlative_deal = bool(
            re.search(
                r"(?:крупнейш\w*|сам\w*\s+(?:больш\w*|крупн\w*|маленьк\w*|мелк\w*)|"
                r"eng\s+(?:katta|kichik)\w*|энг\s+(?:катта|йирик|кичик)\w*).{0,30}"
                r"(?:сделк\w*|bitim\w*|битим\w*)",
                normalized,
            )
        )

        # -- broker mentioned without any identifier ----------------------
        # «Сколько сделок у брокера» names no broker, so returning the total
        # for all deals would be a fabricated attribution.  Ranking questions
        # ("топ брокеров продавца") do not need one specific broker id.
        broker_cue = _contains(normalized, ("брокер", "broker", "брокерлик"))
        if (
            broker_cue
            and not _BROKER_CODE.search(_soft(question))
            and not _extract_broker_mentions(question, normalized)
            and not top_cue
            and not trend_cue
            and not compare_cue
        ):
            return _clarify(detection, ClarificationCode.BROKER_AMBIGUOUS)

        # -- comparison --------------------------------------------------
        if compare_cue:
            periods = _compare_periods(question, normalized, syn)
            if periods is None:
                if len({int(value) for value in _YEAR.findall(_soft(question))}) > 2:
                    return _clarify(
                        detection,
                        ClarificationCode.PERIOD_AMBIGUOUS,
                        ["Укажите ровно два периода", "Ikkita davrni ko‘rsating"],
                    )
                return _clarify(
                    detection,
                    ClarificationCode.PERIOD_AMBIGUOUS,
                )
            left, right = periods
            draft.comparison = ComparisonSpec(left=left, right=right)
            draft.intent = Intent.COMPARE
            metric = self._metric_for_analysis(
                normalized, syn, draft, volume_cue, delivered_cue, sum_cue
            )
            draft.metric = metric
            draft.subject = Subject.DEALS
            draft.currency_mode = self._currency_mode(normalized, syn, metric)
            if metric in (
                Metric.UNIT_PRICE_SIMPLE_AVERAGE,
                Metric.DELIVERED_VOLUME_BY_UNIT,
            ):
                return _unsupported(detection)
            self._apply_filters(
                draft, question, normalized, syn, period_parse, include_date=False
            )
            return _make_plan(draft, detection)

        # -- trend -------------------------------------------------------
        if trend_cue:
            draft.intent = Intent.TREND
            draft.subject = Subject.DEALS
            groups = syn.groups_for(normalized)
            group_fields = [
                _GROUP_FIELDS[name]
                for name in groups
                if name in ("year", "month")
            ]
            if len(group_fields) > 1:
                return _unsupported(detection)
            if not group_fields:
                # A single-year trend is shown by month; otherwise by year.
                if len(period_parse.periods) == 1:
                    start, end = (
                        period_parse.periods[0].start,
                        period_parse.periods[0].end,
                    )
                    if start.year == end.year and start.month == 1 and end.month == 12:
                        group_fields = [GroupField.MONTH]
                    else:
                        group_fields = [GroupField.MONTH]
                else:
                    group_fields = [GroupField.YEAR]
            draft.group_by = [group_fields[0]]
            metric = self._metric_for_analysis(
                normalized, syn, draft, volume_cue, delivered_cue, sum_cue
            )
            draft.metric = metric
            draft.currency_mode = self._currency_mode(normalized, syn, metric)
            if metric in (
                Metric.UNIT_PRICE_SIMPLE_AVERAGE,
                Metric.DELIVERED_VOLUME_BY_UNIT,
            ):
                return _unsupported(detection)
            self._apply_filters(
                draft, question, normalized, syn, period_parse, include_date=True
            )
            return _make_plan(draft, detection)

        # -- top N -------------------------------------------------------
        if top_cue and not superlative_deal:
            shaped = self._shape_top_n(draft, normalized, syn, detection)
            if shaped is not None:
                return shaped
            metric = self._top_metric(normalized, syn, draft)
            if metric is None:
                return _unsupported(detection)
            draft.metric = metric
            if draft.subject in (Subject.PARTICIPANTS, Subject.BROKERS):
                if metric not in (
                    Metric.DEAL_COUNT,
                    Metric.AMOUNT_SUM_UZS_CONVERTED,
                ):
                    return _unsupported(detection)
            draft.currency_mode = self._currency_mode(normalized, syn, metric)
            self._apply_filters(
                draft, question, normalized, syn, period_parse, include_date=True
            )
            limit = self._parse_limit(normalized)
            if limit is None:
                return _unsupported(detection)
            draft.limit = limit
            direction = (
                Direction.ASC
                if _contains(normalized, ("наименьш", "возраст", "eng kichik"))
                else Direction.DESC
            )
            draft.order_by = [
                OrderCondition(field=OrderField.METRIC, direction=direction)
            ]
            return _make_plan(draft, detection)

        # -- min / max ---------------------------------------------------
        if min_cue or max_cue or superlative_deal:
            if min_cue and not max_cue:
                metric = Metric.AMOUNT_MIN
            elif max_cue and not min_cue:
                metric = Metric.AMOUNT_MAX
            elif superlative_deal:
                metric = Metric.AMOUNT_MAX
            else:
                # Both words present: the question is ambiguous about direction.
                return _clarify(
                    detection,
                    ClarificationCode.PERIOD_AMBIGUOUS,
                    ["Минимум или максимум?", "Minimum yoki maksimum?"],
                )
            draft.intent = Intent.MIN_MAX
            draft.subject = Subject.DEALS
            draft.metric = metric
            draft.currency_mode = CurrencyMode.NATIVE_BY_CURRENCY
            self._apply_filters(
                draft, question, normalized, syn, period_parse, include_date=True
            )
            return _make_plan(draft, detection)

        # -- average -----------------------------------------------------
        if average_cue:
            if volume_cue and not _contains(
                normalized, ("цен", "price", "narx", "нарх")
            ):
                return _unsupported(detection)
            draft.intent = Intent.AVERAGE
            draft.subject = Subject.DEALS
            price_cue = _contains(normalized, ("цен", "price", "narx", "нарх"))
            if price_cue:
                weighted = not _contains(
                    normalized, ("прост", "невзвеш", "simple", "oddiy")
                )
                draft.metric = (
                    Metric.UNIT_PRICE_WEIGHTED_AVERAGE
                    if weighted
                    else Metric.UNIT_PRICE_SIMPLE_AVERAGE
                )
                draft.currency_mode = CurrencyMode.NATIVE_BY_CURRENCY
            else:
                if _contains(normalized, syn.usd_markers):
                    return _unsupported(detection)
                draft.metric = Metric.AMOUNT_AVERAGE_UZS_CONVERTED
                draft.currency_mode = CurrencyMode.UZS_CONVERTED
            self._apply_filters(
                draft, question, normalized, syn, period_parse, include_date=True
            )
            return _make_plan(draft, detection)

        # -- volume ------------------------------------------------------
        if volume_cue or delivered_cue:
            if sum_cue and delivered_cue:
                return _unsupported(detection)
            draft.intent = Intent.SUM_VOLUME
            draft.subject = Subject.DEALS
            draft.metric = (
                Metric.DELIVERED_VOLUME_BY_UNIT
                if delivered_cue
                else Metric.VOLUME_SUM_BY_UNIT
            )
            draft.currency_mode = CurrencyMode.NOT_APPLICABLE
            self._apply_filters(
                draft, question, normalized, syn, period_parse, include_date=True
            )
            return _make_plan(draft, detection)

        # -- amount sum --------------------------------------------------
        # "Jami qancha bitim bor?" asks for a total count.  «Jami» is a sum
        # cue only when the question also names money; otherwise it means
        # "in total" and must not turn a count into a UZS amount.
        total_count_question = (
            count_cue
            and _contains(normalized, ("jami", "жами"))
            and not _contains(
                normalized,
                (
                    "сумм",
                    "стоимост",
                    "оборот",
                    "summa",
                    "summasi",
                    "qiymat",
                    "so m",
                    "сўм",
                    "dollar",
                    "доллар",
                    "usd",
                    "uzs",
                    "курс",
                ),
            )
        )
        if sum_cue and not total_count_question:
            draft.intent = Intent.SUM_AMOUNT
            draft.subject = Subject.DEALS
            metric = self._sum_metric(normalized, syn)
            draft.metric = metric
            draft.currency_mode = self._currency_mode(normalized, syn, metric)
            if metric is Metric.AMOUNT_SUM_UZS_CONVERTED and _contains(
                normalized, syn.native_currency_markers
            ):
                return _unsupported(detection)
            self._apply_filters(
                draft, question, normalized, syn, period_parse, include_date=True
            )
            return _make_plan(draft, detection)

        # -- pronoun without a named entity -------------------------------
        # "Сколько у неё сделок?" is only answerable with conversation
        # context.  Standalone it must not silently return the total for the
        # whole dataset.
        if (
            _PRONOUN_RE.search(normalized)
            and count_cue
            and _contains(normalized, ("сдел", "bitim", "битим"))
            and not _extract_company_mentions(question, normalized)
            and not _INN.search(_soft(question))
        ):
            return _clarify(
                detection, ClarificationCode.PARTICIPANT_AMBIGUOUS
            )

        # -- count -------------------------------------------------------
        if count_cue or presence_cue:
            draft.intent = Intent.COUNT
            draft.subject = Subject.DEALS
            draft.metric = Metric.DEAL_COUNT
            draft.currency_mode = CurrencyMode.NOT_APPLICABLE
            self._apply_filters(
                draft, question, normalized, syn, period_parse, include_date=True
            )
            return _make_plan(draft, detection)

        # -- list --------------------------------------------------------
        if list_cue:
            draft.intent = Intent.FILTER_LIST
            draft.subject = Subject.DEALS
            draft.metric = Metric.RECORD_LOOKUP
            draft.currency_mode = CurrencyMode.NOT_APPLICABLE
            self._apply_filters(
                draft, question, normalized, syn, period_parse, include_date=True
            )
            limit = self._parse_limit(normalized)
            draft.limit = limit if limit is not None else 10
            return _make_plan(draft, detection)

        return _unsupported(detection)

    # -- metric helpers --------------------------------------------------
    def _sum_metric(self, normalized: str, syn: Synonyms) -> Metric:
        if _mentions(normalized, syn.native_currency_markers):
            return Metric.AMOUNT_SUM_NATIVE_BY_CURRENCY
        if _mentions(normalized, syn.usd_markers):
            return Metric.AMOUNT_SUM_NATIVE_BY_CURRENCY
        if _mentions(normalized, syn.uzs_markers) and not _mentions(
            normalized, syn.converted_markers
        ):
            return Metric.AMOUNT_SUM_NATIVE_BY_CURRENCY
        return Metric.AMOUNT_SUM_UZS_CONVERTED

    def _metric_for_analysis(
        self,
        normalized: str,
        syn: Synonyms,
        draft: _Draft,
        volume_cue: bool,
        delivered_cue: bool,
        sum_cue: bool,
    ) -> Metric:
        # "объём поставок" is a volume request; the volume cue wins so a
        # comparison of volume is not mislabelled as delivered volume.
        if volume_cue and not sum_cue:
            return Metric.VOLUME_SUM_BY_UNIT
        if delivered_cue and not sum_cue:
            return Metric.DELIVERED_VOLUME_BY_UNIT
        if sum_cue:
            return self._sum_metric(normalized, syn)
        return Metric.DEAL_COUNT

    def _top_metric(
        self, normalized: str, syn: Synonyms, draft: _Draft
    ) -> Metric | None:
        if _contains(normalized, ("объём", "объем", "hajm", "ҳажм")):
            return Metric.VOLUME_SUM_BY_UNIT
        if _contains(normalized, ("поставлен", "yetkazilgan", "етказилган")):
            return Metric.DELIVERED_LOTS_SUM
        if _contains(
            normalized,
            ("сумм", "стоимост", "оборот", "summa", "qiymat", "жами", "қиймат"),
        ):
            return self._sum_metric(normalized, syn)
        if _contains(
            normalized,
            ("количеств", "сколько", "nechta", "qancha", "сони", "нечта", "қанча"),
        ):
            return Metric.DEAL_COUNT
        # A ranking without an explicit metric is a ranking by deal count.
        return Metric.DEAL_COUNT

    def _currency_mode(
        self, normalized: str, syn: Synonyms, metric: Metric
    ) -> CurrencyMode:
        if metric is Metric.AMOUNT_SUM_NATIVE_BY_CURRENCY:
            if _mentions(normalized, syn.native_currency_markers):
                return CurrencyMode.NATIVE_BY_CURRENCY
            if _mentions(normalized, syn.usd_markers):
                return CurrencyMode.USD_NATIVE
            if _mentions(normalized, syn.uzs_markers) and not _mentions(
                normalized, syn.converted_markers
            ):
                return CurrencyMode.UZS_NATIVE
            return CurrencyMode.NATIVE_BY_CURRENCY
        if metric in (
            Metric.AMOUNT_SUM_UZS_CONVERTED,
            Metric.AMOUNT_AVERAGE_UZS_CONVERTED,
        ):
            return CurrencyMode.UZS_CONVERTED
        if metric in (
            Metric.AMOUNT_MIN,
            Metric.AMOUNT_MAX,
            Metric.UNIT_PRICE_SIMPLE_AVERAGE,
            Metric.UNIT_PRICE_WEIGHTED_AVERAGE,
        ):
            return CurrencyMode.NATIVE_BY_CURRENCY
        return CurrencyMode.NOT_APPLICABLE

    # -- filters and entities -------------------------------------------
    def _apply_filters(
        self,
        draft: _Draft,
        question: str,
        normalized: str,
        syn: Synonyms,
        period_parse: PeriodParse,
        *,
        include_date: bool,
    ) -> None:
        if include_date:
            if period_parse.ambiguous:
                # The caller converts this draft into a clarification.
                draft.decision = Decision.CLARIFY
                draft.clarification = ClarificationInfo(
                    code=ClarificationCode.PERIOD_AMBIGUOUS
                )
            elif period_parse.missing:
                draft.decision = Decision.CLARIFY
                draft.clarification = ClarificationInfo(
                    code=ClarificationCode.PERIOD_AMBIGUOUS
                )
            elif len(period_parse.periods) == 1:
                period = period_parse.periods[0]
                if period.start == period.end:
                    draft.add_filter(
                        FilterField.DEAL_DATE, Operator.EQ, period.start.isoformat()
                    )
                else:
                    draft.add_filter(
                        FilterField.DEAL_DATE,
                        Operator.BETWEEN,
                        [period.start.isoformat(), period.end.isoformat()],
                    )

        for field_name, field in (
            ("contract_type", FilterField.CONTRACT_TYPE),
            ("contract_form", FilterField.CONTRACT_FORM),
            ("trade_type", FilterField.TRADE_TYPE),
        ):
            values = syn.filter_matches(field_name, normalized)
            if values:
                unique = _dedupe_strings(values)
                draft.add_filter(
                    field,
                    Operator.IN if len(unique) > 1 else Operator.EQ,
                    unique if len(unique) > 1 else unique[0],
                )

        # Statuses need special handling for negation phrases.
        negated = next(
            (value for phrase, value in _STATUS_SIDE_EFFECTS.items() if phrase in normalized),
            None,
        )
        if negated:
            draft.add_filter(FilterField.STATUS, Operator.EQ, negated)
        else:
            statuses = syn.status_matches(normalized)
            if statuses:
                unique = _dedupe_strings(statuses)
                draft.add_filter(
                    FilterField.STATUS,
                    Operator.IN if len(unique) > 1 else Operator.EQ,
                    unique if len(unique) > 1 else unique[0],
                )

        # Currency is a data filter for counts/volumes and for native amounts.
        # For converted UZS totals it must not exclude export deals.
        currency_values = syn.filter_matches("currency", normalized)
        if currency_values and draft.metric not in (
            Metric.AMOUNT_SUM_UZS_CONVERTED,
            Metric.AMOUNT_AVERAGE_UZS_CONVERTED,
        ):
            unique = _dedupe_strings(currency_values)
            draft.add_filter(
                FilterField.CURRENCY,
                Operator.IN if len(unique) > 1 else Operator.EQ,
                unique if len(unique) > 1 else unique[0],
            )

        units = syn.unit_matches(normalized)
        if units:
            unique = _dedupe_strings(units)
            if len(unique) > 1:
                draft.decision = Decision.CLARIFY
                draft.clarification = ClarificationInfo(
                    code=ClarificationCode.UNIT_AMBIGUOUS, options=unique[:5]
                )
            else:
                draft.add_filter(FilterField.UNIT_NAME, Operator.EQ, unique[0])

        hs_match = _HS_CODE.search(_soft(question))
        if hs_match:
            draft.add_filter(FilterField.HS_CODE, Operator.EQ, hs_match.group(1))

        # Participant and INN entities.
        participant_role = None
        if _contains(
            normalized,
            ("продав", "сотувчи", "sotuvchi", "sotdi", "sotgan", "сотган"),
        ):
            participant_role = EntityRole.SELLER
        elif _contains(
            normalized,
            (
                "покупател",
                "покупа",
                "приобрет",
                "харидор",
                "xaridor",
                "sotib oldi",
                "sotib olgan",
                "сотиб олган",
            ),
        ):
            participant_role = EntityRole.BUYER

        inn = _INN.search(_soft(question))
        if inn:
            draft.add_entity(
                EntityReference(
                    kind=MatchKind.PARTICIPANT,
                    mention=inn.group(1),
                    role=participant_role,
                )
            )
        else:
            companies = _extract_company_mentions(question, normalized)
            for mention in companies:
                role = _role_from_context(question, mention) or participant_role
                draft.add_entity(
                    EntityReference(
                        kind=MatchKind.PARTICIPANT, mention=mention, role=role
                    )
                )

        # Broker entities.  A broker code without a side is ambiguous for an
        # analytics question, but the validator/resolver will ask for the side.
        broker_code = self._extract_broker_code(_soft(question))
        broker_role = _broker_role_from_context(normalized)
        if broker_code:
            draft.add_entity(
                EntityReference(
                    kind=MatchKind.BROKER,
                    mention=broker_code,
                    role=broker_role,
                )
            )
        else:
            brokers = _extract_broker_mentions(question, normalized)
            for mention in brokers:
                draft.add_entity(
                    EntityReference(
                        kind=MatchKind.BROKER,
                        mention=mention,
                        role=broker_role,
                    )
                )

        # Product entities.
        for mention in _extract_product_mentions(question, normalized):
            draft.add_entity(EntityReference(kind=MatchKind.PRODUCT, mention=mention))

        region_entity = _region_entity(question, normalized, syn)
        if region_entity is not None:
            draft.add_entity(region_entity)

        # Generic grouping.  Top-N shape owns its grouping.
        if draft.intent not in (Intent.TOP_N, Intent.COMPARE):
            constrained: set[FilterField] = {item.field for item in draft.filters}
            for entity in draft.entities:
                if entity.kind is MatchKind.PRODUCT:
                    constrained.update(
                        {FilterField.PRODUCT_NAME, FilterField.DEAL_PRODUCT_NAME}
                    )
                elif entity.kind is MatchKind.REGION:
                    if entity.meaning is RegionMeaning.WAREHOUSE:
                        constrained.add(FilterField.WAREHOUSE_REGION)
                    elif entity.role is EntityRole.SELLER:
                        constrained.add(FilterField.SELLER_COMPANY_REGION)
                    elif entity.role is EntityRole.BUYER:
                        constrained.add(FilterField.BUYER_COMPANY_REGION)
                elif entity.kind is MatchKind.PARTICIPANT:
                    if entity.role is EntityRole.SELLER:
                        constrained.add(FilterField.SELLER_INN)
                    elif entity.role is EntityRole.BUYER:
                        constrained.add(FilterField.BUYER_INN)
                elif entity.kind is MatchKind.BROKER:
                    if entity.role is EntityRole.BROKER_SELLER:
                        constrained.add(FilterField.SELLER_BROKER_CODE)
                    elif entity.role is EntityRole.BROKER_BUYER:
                        constrained.add(FilterField.BUYER_BROKER_CODE)
            for name in syn.groups_for(normalized):
                field = _GROUP_FIELDS.get(name)
                if field is not None and field not in constrained:
                    draft.add_group(field)

        if draft.decision is Decision.CLARIFY:
            return

        # A participant/broker/region without an explicit side or meaning is
        # ambiguous for an analytical query, unless the question is a lookup
        # (handled earlier).
        for entity in draft.entities:
            if entity.kind is MatchKind.REGION:
                if entity.meaning is None:
                    draft.decision = Decision.CLARIFY
                    draft.clarification = ClarificationInfo(
                        code=ClarificationCode.REGION_MEANING_AMBIGUOUS
                    )
                    return
                if entity.meaning is RegionMeaning.COMPANY and entity.role is None:
                    draft.decision = Decision.CLARIFY
                    draft.clarification = ClarificationInfo(
                        code=ClarificationCode.REGION_MEANING_AMBIGUOUS
                    )
                    return
            if entity.kind is MatchKind.PARTICIPANT and entity.role is None:
                draft.decision = Decision.CLARIFY
                draft.clarification = ClarificationInfo(
                    code=ClarificationCode.PARTICIPANT_ROLE_AMBIGUOUS
                )
                return
            if entity.kind is MatchKind.BROKER and entity.role is None:
                draft.decision = Decision.CLARIFY
                draft.clarification = ClarificationInfo(
                    code=ClarificationCode.BROKER_SIDE_AMBIGUOUS
                )
                return

    # -- top-N shape -----------------------------------------------------
    def _shape_top_n(
        self,
        draft: _Draft,
        normalized: str,
        syn: Synonyms,
        detection: LanguageDetection,
    ) -> QueryPlan | None:
        seller = _contains(
            normalized,
            ("продав", "сотувчи", "sotuvchi", "sotdi", "sotgan", "сотган"),
        )
        buyer = _contains(
            normalized,
            (
                "покупател",
                "покупа",
                "приобрет",
                "харидор",
                "xaridor",
                "sotib oldi",
                "sotib olgan",
                "сотиб олган",
            ),
        )
        broker = _contains(normalized, ("брокер", "broker", "брокер"))
        participant = _contains(
            normalized,
            (
                "компани",
                "участник",
                "фирм",
                "организаци",
                "kompaniya",
                "ishtirokchi",
                "компания",
                "иштирокчи",
            ),
        )

        if broker:
            draft.subject = Subject.BROKERS
            draft.intent = Intent.TOP_N
            if _contains(
                normalized,
                (
                    "брокер продавца",
                    "брокеров продавца",
                    "брокеры продавца",
                    "sotuvchi broker",
                    "сотувчи брокер",
                ),
            ):
                draft.add_group(GroupField.SELLER_BROKER_CODE)
            elif _contains(
                normalized,
                (
                    "брокер покупателя",
                    "брокеров покупателя",
                    "брокеры покупателя",
                    "xaridor broker",
                    "харидор брокер",
                ),
            ):
                draft.add_group(GroupField.BUYER_BROKER_CODE)
            else:
                draft.decision = Decision.CLARIFY
                draft.clarification = ClarificationInfo(
                    code=ClarificationCode.BROKER_SIDE_AMBIGUOUS
                )
            return None

        if participant or seller or buyer:
            draft.subject = Subject.PARTICIPANTS
            draft.intent = Intent.TOP_N
            if seller and not buyer:
                draft.add_group(GroupField.SELLER_INN)
            elif buyer and not seller:
                draft.add_group(GroupField.BUYER_INN)
            else:
                draft.decision = Decision.CLARIFY
                draft.clarification = ClarificationInfo(
                    code=ClarificationCode.PARTICIPANT_ROLE_AMBIGUOUS
                )
            return None

        draft.subject = Subject.DEALS
        draft.intent = Intent.TOP_N
        generic_groups = syn.groups_for(normalized)
        if any(
            name in ("product_name", "product_category", "product_group", "industry", "hs_code")
            for name in generic_groups
        ) or _contains(
            normalized,
            (
                "товар",
                "продукт",
                "tovar",
                "mahsulot",
                "товар",
                "маҳсулот",
            ),
        ):
            draft.add_group(GroupField.PRODUCT_NAME)
        elif "contract_no" in generic_groups or _contains(
            normalized, ("контракт", "шартнома", "contract")
        ):
            draft.add_group(GroupField.CONTRACT_NO)
        elif "warehouse_region" in generic_groups or _contains(
            normalized,
            ("регион", "склад", "hudud", "viloyat", "ҳудуд", "вилоят", "омбор"),
        ):
            draft.add_group(GroupField.WAREHOUSE_REGION)
        else:
            for name in generic_groups:
                field = _GROUP_FIELDS.get(name)
                if field is not None:
                    draft.add_group(field)
                    break
        if not draft.group_by:
            draft.decision = Decision.UNSUPPORTED
        return None

    # -- limit -----------------------------------------------------------
    def _parse_limit(self, normalized: str) -> int | None:
        word_match = _TOP_LIMIT_WORD.search(normalized)
        if word_match is not None:
            limit = _NUMBER_WORDS.get(word_match.group(1))
            if limit is None or limit < 1 or limit > 20:
                return None
            return limit
        match = _TOP_LIMIT.search(normalized)
        if not match:
            return 10
        value = next((group for group in match.groups() if group), None)
        if value is None:
            return 10
        limit = int(value)
        if limit < 1 or limit > 20:
            return None
        return limit


def plan_question(question: str, detection: LanguageDetection) -> QueryPlan:
    return LocalPlanner().plan(question, detection)
