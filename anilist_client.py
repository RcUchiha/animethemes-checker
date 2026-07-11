"""
Cliente para la API GraphQL pública de AniList (https://graphql.anilist.co).

⚠️ ESTE MÓDULO ES UN ÚLTIMO RECURSO — NO UN REEMPLAZO DE JIKAN/MAL.
Ver orquestador.detectar_animes_faltantes_en_at para el orden exacto de
la cascada de resiliencia (Jikan en vivo -> caché vencido de Jikan ->
AniList). Se usa SOLO cuando ambas fuentes de MAL fallan: Jikan en vivo
no responde, Y no hay ninguna entrada cacheada (ni vencida) para esa
temporada — típicamente la primera vez que se escanea una temporada
recién anunciada, justo durante un corte de Jikan.

Por qué es el ÚLTIMO recurso y no una alternativa a la misma altura que
el caché vencido de Jikan:
- El caché vencido sigue siendo la MISMA fuente (MAL), solo desactualizada
  — el dato en sí es correcto, solo puede estar incompleto (animes nuevos
  no reflejados). AniList es una fuente DISTINTA con su propio mapeo de
  datos hacia MAL.
- El campo idMal de AniList (el vínculo hacia MyAnimeList que este módulo
  necesita para poder comparar contra AnimeThemes, que identifica animes
  por mal_id) es dato crowd-sourced: puede faltar (algunos animes no
  tienen el vínculo cargado todavía) O directamente estar mal cargado.
  Lo primero se maneja contando y excluyendo (ver
  obtener_temporada_completa_anilist) — lo segundo es un riesgo real que
  NO se puede detectar programáticamente acá, por eso este módulo nunca
  se usa si hay una alternativa mejor disponible.
- Los resultados que dependen de esta fuente deben quedar CLARAMENTE
  marcados para el usuario en la GUI (ver
  orq.ResultadoFaltantes.usando_anilist_como_fuente), nunca mezclados en
  silencio con resultados de Jikan.

NO cachea nada, a propósito (mismo criterio de cache_jikan.py: ver su
docstring). Cachear un mapeo que puede estar mal sería peor que no
cachear nada — cada vez que se necesita este último recurso, se vuelve a
consultar en vivo.

Mapeos hacia el vocabulario de MAL/Jikan (ver jikan_client.AnimeDeTemporadaMAL):
- status: RELEASING/FINISHED/NOT_YET_RELEASED se traducen a los mismos
  strings que ya usa MAL en el resto del programa ("Currently Airing" /
  "Finished Airing" / "Not yet aired", ver mal_scraper.py y
  gui_pyqt6.CLAVES_I18N_STATUS). CANCELLED y HIATUS NO tienen un status
  equivalente real en la página de un anime en MAL (MAL no distingue esos
  casos con un status propio del mismo estilo) — se mapean a None en vez
  de inventar un string que no existe en ningún otro lugar del programa.
- format: TV/TV_SHORT/MOVIE/OVA/ONA/SPECIAL/MUSIC se traducen al 'tipo'
  de Jikan ("TV"/"Movie"/"OVA"/"ONA"/"Special"/"Music"). TV_SHORT no
  tiene equivalente propio en Jikan, así que se mapea a "TV" (mismo
  criterio que usa MAL: un TV Short sigue siendo type=TV en su listado).
"""

from __future__ import annotations

import json
import urllib.request

import jikan_client

API_URL = "https://graphql.anilist.co"
HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (AnimeThemesChecker/0.1)",
}

POR_PAGINA = 50

# Ver docstring del módulo, sección "Mapeos hacia el vocabulario de MAL/Jikan".
_STATUS_ANILIST_A_MAL = {
    "RELEASING": "Currently Airing",
    "FINISHED": "Finished Airing",
    "NOT_YET_RELEASED": "Not yet aired",
    "CANCELLED": None,
    "HIATUS": None,
}

_FORMATO_ANILIST_A_TIPO = {
    "TV": "TV",
    "TV_SHORT": "TV",
    "MOVIE": "Movie",
    "OVA": "OVA",
    "ONA": "ONA",
    "SPECIAL": "Special",
    "MUSIC": "Music",
}

# format_not_in: [MUSIC] en la query ya excluye la mayoría de estos casos
# en origen, pero TIPOS_EXCLUIDOS en orquestador.py filtra de nuevo por
# las dudas (mismo criterio defensivo que con datos de Jikan).
_QUERY = """
query ($season: MediaSeason, $seasonYear: Int, $page: Int, $perPage: Int) {
  Page(page: $page, perPage: $perPage) {
    pageInfo {
      hasNextPage
    }
    media(season: $season, seasonYear: $seasonYear, type: ANIME, format_not_in: [MUSIC]) {
      idMal
      title {
        romaji
      }
      status
      format
    }
  }
}
"""


def _consultar_pagina(season_anilist: str, year: int, pagina: int) -> dict:
    body = json.dumps({
        "query": _QUERY,
        "variables": {
            "season": season_anilist,
            "seasonYear": year,
            "page": pagina,
            "perPage": POR_PAGINA,
        },
    }).encode("utf-8")
    req = urllib.request.Request(API_URL, data=body, headers=HEADERS, method="POST")
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def obtener_temporada_completa_anilist(
    year: int, season: str
) -> tuple[list[jikan_client.AnimeDeTemporadaMAL], int]:
    """
    Trae TODOS los animes de una temporada según AniList, paginando hasta
    agotar resultados (page/perPage=50, siguiendo pageInfo.hasNextPage —
    mismo criterio de paginación que jikan_client.obtener_temporada_completa_mal).

    Devuelve (animes, cantidad_omitidos_sin_mal_id). Un anime de AniList
    sin idMal (el vínculo hacia MyAnimeList) NO se incluye en la lista —
    no hay forma de compararlo contra AnimeThemes sin un mal_id — pero SÍ
    se cuenta, para que el llamador pueda mostrarle esa cifra al usuario
    (ver orq.ResultadoFaltantes.animes_omitidos_por_fuente_alterna).

    season se pasa en MAYÚSCULAS (WINTER/SPRING/SUMMER/FALL) para el enum
    MediaSeason de AniList — a diferencia de jikan_client, que usa
    minúsculas para el path de Jikan.

    NO cachea nada (ver docstring del módulo). Si esta llamada también
    falla, la excepción se propaga tal cual — este es el último recurso
    de la cascada en orquestador.detectar_animes_faltantes_en_at, no hay
    ningún fallback después de este.
    """
    season_anilist = season.upper()
    animes: list[jikan_client.AnimeDeTemporadaMAL] = []
    omitidos_sin_mal_id = 0
    pagina = 1

    while True:
        data = _consultar_pagina(season_anilist, year, pagina)
        media_pagina = data["data"]["Page"]["media"]

        for media in media_pagina:
            mal_id = media.get("idMal")
            if mal_id is None:
                omitidos_sin_mal_id += 1
                continue
            animes.append(jikan_client.AnimeDeTemporadaMAL(
                mal_id=mal_id,
                titulo=(media.get("title") or {}).get("romaji") or "",
                status=_STATUS_ANILIST_A_MAL.get(media.get("status")),
                tipo=_FORMATO_ANILIST_A_TIPO.get(media.get("format")),
            ))

        if not data["data"]["Page"]["pageInfo"].get("hasNextPage"):
            break
        pagina += 1

    return animes, omitidos_sin_mal_id
