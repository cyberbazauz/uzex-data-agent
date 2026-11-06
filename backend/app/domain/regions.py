from __future__ import annotations

from dataclasses import dataclass

from app.domain.normalization import normalize_region


@dataclass(frozen=True)
class Region:
    region_id: int
    name_ru: str
    name_uz: str
    aliases: tuple[tuple[str, str], ...] = ()


# The 14 warehouse regions confirmed from the source archive.  Region IDs are
# stable and the pairings are exactly those listed in the architecture spec.
REGIONS: tuple[Region, ...] = (
    Region(1, "Андижанская область", "Andijon viloyati", (("Андижон вилояти", "uz_cyrl"),)),
    Region(2, "Бухарская область", "Buxoro viloyati", (("Бухоро вилояти", "uz_cyrl"),)),
    Region(3, "Ферганская область", "Farg‘ona viloyati", (("Фарғона вилояти", "uz_cyrl"),)),
    Region(4, "Джизакская область", "Jizzax viloyati", (("Жиззах вилояти", "uz_cyrl"),)),
    Region(5, "Наманганская область", "Namangan viloyati", (("Наманган вилояти", "uz_cyrl"),)),
    Region(6, "Навоийская область", "Navoiy viloyati", (("Навоий вилояти", "uz_cyrl"),)),
    Region(7, "Кашкадарьинская область", "Qashqadaryo viloyati", (("Қашқадарё вилояти", "uz_cyrl"),)),
    Region(
        8,
        "Республика Каракалпакстан",
        "Qoraqalpog‘iston Respublikasi",
        (("Қорақалпоғистон Республикаси", "uz_cyrl"),),
    ),
    Region(9, "Самаркандская область", "Samarqand viloyati", (("Самарқанд вилояти", "uz_cyrl"),)),
    Region(10, "Сырдарьинская область", "Sirdaryo viloyati", (("Сирдарё вилояти", "uz_cyrl"),)),
    Region(11, "Сурхандарьинская область", "Surxondaryo viloyati", (("Сурхондарё вилояти", "uz_cyrl"),)),
    Region(12, "г. Ташкент", "Toshkent shahri", (("Тошкент шаҳри", "uz_cyrl"),)),
    Region(13, "Ташкентская область", "Toshkent viloyati", (("Тошкент вилояти", "uz_cyrl"),)),
    Region(14, "Хорезмская область", "Xorazm viloyati", (("Хоразм вилояти", "uz_cyrl"),)),
)

REGION_BY_ID = {region.region_id: region for region in REGIONS}


def build_region_alias_map() -> dict[str, list[tuple[int, str, str]]]:
    """Return normalized alias -> [(region_id, raw alias, language)]."""
    result: dict[str, list[tuple[int, str, str]]] = {}
    for region in REGIONS:
        entries = [(region.name_uz, "uz_latn"), (region.name_ru, "ru"), *region.aliases]
        for raw, language in entries:
            result.setdefault(normalize_region(raw), []).append(
                (region.region_id, raw, language)
            )
    return result


REGION_ALIAS_MAP = build_region_alias_map()


def resolve_region_raw(raw: str) -> int | None:
    candidates = REGION_ALIAS_MAP.get(normalize_region(raw), [])
    ids = {item[0] for item in candidates}
    if len(ids) == 1:
        return next(iter(ids))
    return None


def region_display(region_id: int, language: str) -> str:
    region = REGION_BY_ID.get(region_id)
    if region is None:
        return ""
    if language == "ru":
        return region.name_ru
    if language == "uz_cyrl":
        return next(
            (raw for raw, lang in region.aliases if lang == "uz_cyrl"),
            region.name_uz,
        )
    return region.name_uz
