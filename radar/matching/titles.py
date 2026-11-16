"""Títulos alternativos y tipo de producto de cada juego del catálogo.

Títulos alternativos: los nombres con que las otras tiendas venden el mismo juego
(«Helldivers II» para «HELLDIVERS™ 2»), sin el ruido que agrega cada tienda
(«- PS5», «(Renewed)», «| Digital Code»). Solo se toman de ofertas aceptadas con
un puntaje alto, para que un emparejamiento dudoso no se convierta en un nombre
del juego.

Tipo: «game+dlc» si el título canónico es una edición que trae contenido extra
(GOTY, Complete, Definitive…); «dlc» si es un extra; si no, «game».
"""
from __future__ import annotations

import re

from .normalize import DLC_WORDS, flags

MIN_SCORE = 0.90          # solo ofertas aceptadas con mucha seguridad
MAX_ALT_TITLES = 6

# Partes que las tiendas agregan al nombre y que no son parte del título.
_PLATFORM = (r"ps[45]|playstation\s*[45]|xbox(\s*one|\s*series\s*x\|?s|\s*series\s*x|1)?|xsx|"
             r"(nintendo\s*)?switch(\s*2)?|pc|windows|steam|mac")
_STORE_NOISE = re.compile(
    rf"\s*(/|,)\s*({_PLATFORM})\b.*$"                                   # «/PS5», «, Xbox Series X»
    rf"|\s+({_PLATFORM})(\s+(standard|digital|online|game).*)?$"        # «… PS5», «… PS4 Standard Edition»
    r"|\s+(standard(\s+edition)?|digital\s+code|online\s+game\s+code|renewed)$", re.I)
_TRAILING_SEGMENT = re.compile(rf"(\b({_PLATFORM})\b|edition|standard|digital|code|renewed|steam)", re.I)

# Ediciones que suelen incluir el juego más contenido descargable. «Gold», «Complete» o
# «Deluxe» cuentan solo como nombre de edición («… Gold Edition»), no dentro del título
# («Gold Rush», «Le Mans Ultimate»).
_WITH_DLC = re.compile(r"\b(game of the year|goty|director'?s cut|anthology|the complete)\b"
                       r"|\b(complete|ultimate|definitive|gold|premium|deluxe|special|legendary|royal)\s+edition\b"
                       r"|\bcollection\s*$", re.I)


def _key(title: str) -> str:
    """Para comparar: sin marcas, mayúsculas, puntuación ni «The» inicial."""
    t = re.sub(r"[™®©]", "", title).lower()
    t = re.sub(r"^\s*the\s+", "", t)
    return re.sub(r"[^a-z0-9]", "", t)


def clean_store_title(title: str) -> str:
    """El nombre del juego tal como lo pone la tienda, sin plataforma, formato ni estado."""
    t = re.sub(r"[™®©]", "", title)
    t = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", t)
    t = " ".join(t.split())                           # espacios simples: los cortes de abajo son exactos
    t = t.split(" | ")[0]
    parts = re.split(r" [-–—] ", t)
    while len(parts) > 1 and _TRAILING_SEGMENT.search(parts[-1]):
        parts.pop()                                   # «… - PlayStation 4 Standard Edition»
    t = " - ".join(parts)
    for _ in range(3):                                # quitar capas de ruido al final
        t = _STORE_NOISE.sub("", t.strip())
    return re.sub(r"\s+", " ", t).strip(" -:,/")


def alt_titles(canonical: str, store_titles: list[str]) -> list[str]:
    """Títulos alternativos a partir de los nombres que usan las tiendas: distintos del
    canónico (no solo en mayúsculas, signos o un «The»), que no sean el canónico más el
    nombre de una edición, sin extras y sin repetir."""
    base = _key(canonical)
    seen = {base}
    out: list[str] = []
    for raw in store_titles:
        if "dlc_or_bundle" in flags(raw):
            continue
        t = clean_store_title(raw)
        k = _key(t)
        # «Cyberpunk 2077: Ultimate Edition» para «Cyberpunk 2077» es otra edición, no otro nombre.
        if len(k) < 2 or k in seen or k.startswith(base):
            continue
        seen.add(k)
        out.append(t)
        if len(out) == MAX_ALT_TITLES:
            break
    return out


def kind_for(title: str, known_game: bool = False) -> str:
    """Tipo de producto. known_game=True cuando la fuente ya garantiza que es un juego
    (el descubrimiento usa la categoría «juegos» de Steam): entonces nunca es «dlc», aunque
    el título tenga palabras como «Pack» o «Coins» («The Jackbox Party Pack»)."""
    if not known_game and DLC_WORDS.search(title.lower()):
        return "dlc"
    return "game+dlc" if _WITH_DLC.search(title) else "game"
