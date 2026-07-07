"""
Cliente para la API pública de AnimeThemes (https://api.animethemes.moe).

Responsabilidades:
- Traer todos los animes de una temporada/año dado (usando /animeyear/{year}).
- Para cada anime, exponer sus temas (OP/ED) ya convertidos a TemaAT.
- Exponer el mal_id de cada anime (sacado de 'resources').

Notas de diseño:
- Usamos /animeyear/{year}?include=... porque agrupa por temporada de una sola
  vez, evitando tener que adivinar los valores aceptados de filter[season]. Se
  confirmó con una corrida real (Spring 2026, 65/65 animes, ver
  verificar_include_animeyear.py) que el include completo
  (animethemes.animethemeentries.videos, animethemes.song.artists, resources)
  ya viene anidado ahí, así que una sola llamada trae todo lo necesario para
  toda la temporada — ver _obtener_animes_completos_desde_listado.
- Si en algún momento se detecta que el include del listado deja de traer
  todo (ej. la API cambia de comportamiento, o falla en una temporada
  puntual), NECESITA_DETALLE_INDIVIDUAL=True reactiva el camino de respaldo:
  una llamada a /anime/{slug}?include=... por anime. Es más lento pero no
  depende de que el listado anide todo correctamente.
  Verificado (issue #2, corrida real, Winter 2025, 55/55 animes,
  06/07/2026): con NECESITA_DETALLE_INDIVIDUAL=True y =False contra la
  misma temporada, ambos caminos devolvieron exactamente el mismo
  conjunto de animes (mismos slugs), mismo mal_id por anime, y los mismos
  temas OP/ED (tipo, secuencia, título, artista, episodios y video) en
  cada uno — comparando por contenido ordenado por slug, no por posición,
  ya que el camino de respaldo no garantiza el mismo orden. El camino de
  respaldo sigue funcionando igual que el rápido, solo que ~2x más lento
  (21.4s vs. 9.7s para esas 55 llamadas): no hay evidencia de que se haya
  desactualizado, así que se mantiene tal cual, como red de respaldo real
  y no solo teórica.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from modelos import TemaAT, TipoTema

API_BASE = "https://api.animethemes.moe"

# Confirmado con corrida real (ver docstring del módulo): el include de
# /animeyear ya trae todo. Si algún día se sospecha que dejó de ser así
# (discrepancias raras en un escaneo, ej. artistas o videos vacíos que no
# deberían estarlo), cambiar a True reactiva el camino de respaldo más
# lento pero más solidamente probado.
NECESITA_DETALLE_INDIVIDUAL = False

INCLUDE_COMPLETO = "animethemes.animethemeentries.videos,animethemes.song.artists,resources"

HEADERS = {"User-Agent": "Mozilla/5.0 (AnimeThemesChecker/0.1)"}

SEASONS_VALIDAS = ("Winter", "Spring", "Summer", "Fall")


@dataclass
class AnimeBasico:
    """Lo mínimo que sacamos del listado por temporada."""
    id: int
    name: str
    slug: str
    year: int
    season: str


@dataclass
class AnimeCompleto:
    """Anime con sus temas y mal_id ya resueltos, listo para comparar con MAL."""
    id: int
    name: str
    slug: str
    year: int
    season: str
    mal_id: int | None
    temas: list[TemaAT]


def _get_json(url: str) -> dict:
    """GET simple con manejo de errores básico. Sin reintentos todavía:
    eso lo añadimos en el siguiente paso si vemos que la API da rate-limit."""
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def obtener_animes_de_temporada(year: int, season: str) -> list[AnimeBasico]:
    """
    Devuelve los animes de una temporada/año usando /animeyear/{year},
    que ya viene agrupado por temporada.
    """
    season = season.capitalize()
    if season not in SEASONS_VALIDAS:
        raise ValueError(f"Temporada inválida: {season!r}. Debe ser una de {SEASONS_VALIDAS}")

    url = f"{API_BASE}/animeyear/{year}"
    data = _get_json(url)

    # La forma esperada, según la doc:
    # { "winter": [...], "spring": [...], "summer": [...], "fall": [...] }
    clave = season.lower()
    items = data.get(clave, [])

    return [
        AnimeBasico(
            id=item["id"],
            name=item["name"],
            slug=item["slug"],
            year=item.get("year", year),
            season=item.get("season", season),
        )
        for item in items
    ]


import re

_SLUG_RE = re.compile(r"^(OP|ED)(\d+)([A-Za-z].*)?$")


def _tipo_y_secuencia_desde_slug(slug: str, type_raw: str) -> "tuple[TipoTema, int] | None":
    """
    El campo 'sequence' de la API viene en null; el número real está en el
    slug del tema (ej. 'OP1', 'ED2'). 'type' sí es confiable para OP/ED pero
    lo usamos como respaldo si el slug no matchea por algún formato raro.
    """
    m = _SLUG_RE.match(slug or "")
    if m:
        tipo_str, numero, _resto = m.groups()
        try:
            return TipoTema(tipo_str), int(numero)
        except ValueError:
            pass

    # Respaldo: usamos 'type' y asumimos secuencia 1 si no pudimos leerla del slug.
    try:
        return TipoTema(type_raw), 1
    except ValueError:
        return None


def _parsear_temas(animethemes_json: list[dict]) -> list[TemaAT]:
    """
    Convierte el array 'animethemes' (con song y animethemeentries anidadas)
    del JSON de AnimeThemes a una lista plana de TemaAT.

    Forma real confirmada (anime mato_seihei_no_slave, junio 2026):
    {
        "type": "OP" | "ED",
        "sequence": null,            # NO confiable, viene null
        "slug": "OP1" | "ED1",       # aquí sí está el número de secuencia
        "song": {
            "title": "...",
            "artists": [{"name": "..."}, ...]
        },
        "animethemeentries": [
            {"episodes": "1-12", "version": 1, ...}
        ]
    }

    Requiere que el include haya pedido animethemes.song.artists además de
    animethemes.animethemeentries, o 'song' vendrá ausente.
    """
    temas: list[TemaAT] = []
    for theme in animethemes_json:
        resultado = _tipo_y_secuencia_desde_slug(theme.get("slug", ""), theme.get("type", ""))
        if resultado is None:
            # ni el slug ni el type fueron reconocibles; saltamos este tema
            # en vez de tronar todo el escaneo de la temporada.
            continue
        tipo, secuencia = resultado

        song = theme.get("song") or {}
        titulo = song.get("title", "") or ""
        artistas = song.get("artists") or []
        artista = ", ".join(a.get("name", "") for a in artistas if a.get("name"))

        entries = theme.get("animethemeentries") or []
        if not entries:
            # tema sin entradas (raro, pero posible si solo hay metadata)
            temas.append(TemaAT(
                tipo=tipo, secuencia=secuencia,
                titulo_cancion=titulo, artista=artista,
                episodios_texto="",
            ))
            continue

        for entry in entries:
            videos = entry.get("videos") or []
            temas.append(TemaAT(
                tipo=tipo,
                secuencia=secuencia,
                titulo_cancion=titulo,
                artista=artista,
                episodios_texto=entry.get("episodes") or "",
                version=entry.get("version") or 1,
                tiene_video=len(videos) > 0,
            ))

    return temas


def _extraer_mal_id(resources_json: list[dict]) -> int | None:
    """Busca el resource cuyo 'site' sea MyAnimeList y devuelve su external_id."""
    for res in resources_json or []:
        if res.get("site") == "MyAnimeList":
            return res.get("external_id")
    return None


def obtener_detalle_anime(slug: str) -> AnimeCompleto:
    """Trae el detalle completo de un anime por su slug, con temas y mal_id."""
    # IMPORTANTE: las comas del include deben quedar literales (no %2C) para
    # que la API de AnimeThemes las interprete como separadores de includes.
    # urllib.parse.urlencode las codifica por defecto, así que construimos
    # la query string manualmente con safe=','.
    url = f"{API_BASE}/anime/{urllib.parse.quote(slug)}?include={urllib.parse.quote(INCLUDE_COMPLETO, safe=',')}"
    data = _get_json(url)
    anime = data["anime"]

    return AnimeCompleto(
        id=anime["id"],
        name=anime["name"],
        slug=anime["slug"],
        year=anime.get("year"),
        season=anime.get("season"),
        mal_id=_extraer_mal_id(anime.get("resources", [])),
        temas=_parsear_temas(anime.get("animethemes", [])),
    )


def _obtener_animes_completos_desde_listado(
    year: int,
    season: str,
    progreso_callback=None,
) -> list[AnimeCompleto]:
    """
    Camino rápido (default): una sola llamada a /animeyear/{year} con el
    include completo ya trae todo lo necesario para armar cada
    AnimeCompleto (temas y mal_id), sin tener que golpear /anime/{slug}
    anime por anime. Confirmado con corrida real — ver
    verificar_include_animeyear.py y el docstring de este módulo.

    El orden de la lista devuelta SÍ respeta el orden del listado de
    /animeyear (a diferencia del camino con hilos, aquí no hay
    concurrencia que lo desordene).
    """
    season_cap = season.capitalize()
    if season_cap not in SEASONS_VALIDAS:
        raise ValueError(f"Temporada inválida: {season!r}. Debe ser una de {SEASONS_VALIDAS}")

    url = f"{API_BASE}/animeyear/{year}?include={urllib.parse.quote(INCLUDE_COMPLETO, safe=',')}"
    data = _get_json(url)
    items = data.get(season_cap.lower(), [])
    total = len(items)

    completos: list[AnimeCompleto] = []
    for i, item in enumerate(items, start=1):
        completo = AnimeCompleto(
            id=item["id"],
            name=item["name"],
            slug=item["slug"],
            year=item.get("year", year),
            season=item.get("season", season_cap),
            mal_id=_extraer_mal_id(item.get("resources", [])),
            temas=_parsear_temas(item.get("animethemes") or []),
        )
        completos.append(completo)
        if progreso_callback is not None:
            progreso_callback(i, total, completo.name)

    return completos


def obtener_animes_completos_de_temporada(
    year: int,
    season: str,
    pausa_entre_llamadas: float = 0.3,
    max_hilos: int = 4,
    progreso_callback=None,
) -> list[AnimeCompleto]:
    """
    Función de alto nivel: trae todos los animes de la temporada, ya
    completos con temas y mal_id, listos para comparar con MAL.

    Por defecto (NECESITA_DETALLE_INDIVIDUAL=False) usa el camino rápido:
    una sola llamada a /animeyear con include completo (ver
    _obtener_animes_completos_desde_listado). Si ese flag se reactiva en
    True, cae al camino de respaldo: lista la temporada y completa cada
    anime con una llamada individual, paralelizado con un pool moderado
    de hilos (max_hilos, default 4): la mayoría del tiempo de cada llamada
    es espera de red (I/O), así que varios hilos simultáneos reducen el
    tiempo total sin saturar tanto a la API como para arriesgar bloqueos —
    4 es un punto medio razonable, más agresivo que secuencial pero lejos
    de mandar decenas de peticiones a la vez. pausa_entre_llamadas se
    aplica POR HILO antes de cada llamada (no globalmente), por lo que el
    ritmo efectivo de peticiones es aproximadamente max_hilos veces más
    rápido que la versión secuencial, no instantáneo.

    El orden de la lista devuelta en el camino de respaldo NO está
    garantizado en el mismo orden que basicos (los hilos terminan en
    orden variable) — quien llame a esta función no debe asumir orden
    estable en ese caso. En el camino rápido sí se conserva el orden del
    listado.
    """
    if not NECESITA_DETALLE_INDIVIDUAL:
        return _obtener_animes_completos_desde_listado(year, season, progreso_callback)

    basicos = obtener_animes_de_temporada(year, season)
    total = len(basicos)

    completos: list[AnimeCompleto] = []
    contador_lock = threading.Lock()
    contador = {"hechos": 0}

    def _procesar(basico: AnimeBasico) -> AnimeCompleto:
        time.sleep(pausa_entre_llamadas)
        resultado = obtener_detalle_anime(basico.slug)
        if progreso_callback is not None:
            with contador_lock:
                contador["hechos"] += 1
                indice_actual = contador["hechos"]
            progreso_callback(indice_actual, total, basico.name)
        return resultado

    with ThreadPoolExecutor(max_workers=max_hilos) as executor:
        for resultado in executor.map(_procesar, basicos):
            completos.append(resultado)

    return completos
