from __future__ import annotations

import re
import unicodedata

_APOSTROPHES = str.maketrans(
    {
        "\u2018": "'",  # ‘
        "\u2019": "'",  # ’
        "\u02bb": "'",  # ʻ
        "\u02bc": "'",  # ʼ
        "\u201b": "'",  # ‛
        "`": "'",
        "´": "'",
    }
)
_NON_WORD = re.compile(r"[^\w\s]+", re.UNICODE)
_SPACES = re.compile(r"\s+", re.UNICODE)


def normalize_text(value: str) -> str:
    """Normalize for exact lookup without discarding digits or significant words.

    Punctuation is converted to spaces; letters, digits and quoted-name content
    are preserved.  The original value is always retained in the database.
    """
    text = unicodedata.normalize("NFKC", value).translate(_APOSTROPHES).casefold()
    text = _NON_WORD.sub(" ", text)
    text = _SPACES.sub(" ", text).strip()
    return text


def normalize_identifier(value: str) -> str:
    """Trim an identifier's outer whitespace without changing inner characters."""
    return value.strip()


def normalize_region(value: str) -> str:
    return normalize_text(value)
