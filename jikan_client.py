"""
Cliente para Jikan v4 (https://api.jikan.moe/v4), API no oficial de MyAnimeList.

Responsabilidades:
- Dado un mal_id (sacado de los 'resources' de AnimeThemes), traer:
  - status ("Finished Airing", "Currently Airing", etc.)
  - episodes (total de episodios, puede venir null si MAL no lo sabe)

⚠️ obtener_info_mal() TIENE UN BUG CONOCIDO — YA NO SE USA EN EL FLUJO
PRINCIPAL. Ver su docstring más abajo para el detalle completo antes de
volver a llamarla desde orquestador.py.

IMPORTANTE — Rate limit:
La documentación pública de Jikan (Apiary) pide explícitamente una pausa
de 4 SEGUNDOS entre peticiones — no 1 segundo, ni "60/min" como sugieren
varios wrappers de terceros (ese número es más optimista de lo que Jikan
realmente pide). PAUSA_ENTRE_LLAMADAS refleja el valor de 4s confirmado
en su doc oficial.

El throttle es GLOBAL (un solo reloj compartido, protegido por un lock),
no por hilo — esto importa porque orquestador.py paraleliza el
procesamiento de animes con varios hilos (ver escanear_temporada). Si
cada hilo respetara la pausa solo respecto de sí mismo, el ritmo AGREGADO
de llamadas a Jikan podría multiplicarse por la cantidad de hilos
simultáneos. El throttle centralizado aquí garantiza que, sin importar
cuántos hilos lo llamen a la vez, las llamadas reales de red queden
espaciadas por PAUSA_ENTRE_LLAMADAS de forma agregada. Los hits de caché
NO pasan por el throttle (no hacen red, no hace falta esperar).

NOTA: subir PAUSA_ENTRE_LLAMADAS por encima de 4s NO resuelve el bug de
obtener_info_mal descrito abajo — se probó con 1s y con 4s, mismo
resultado exacto (14/14 fallos, sin recuperarse). El problema no era de
ritmo de peticiones.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

import cache_jikan

API_BASE = "https://api.jikan.moe/v4"
HEADERS = {"User-Agent": "Mozilla/5.0 (AnimeThemesChecker/0.1)"}

PAUSA_ENTRE_LLAMADAS = 4.0  # segundos — ver "IMPORTANTE — Rate limit" arriba

_lock_throttle = threading.Lock()
_ultima_llamada = {"momento": 0.0}


def _esperar_turno() -> None:
    """
    Bloquea el hilo actual lo necesario para que, desde la última llamada
    real a Jikan (de CUALQUIER hilo), hayan pasado al menos
    PAUSA_ENTRE_LLAMADAS segundos. Debe llamarse justo antes de cada
    petición de red real a Jikan, nunca antes de un hit de caché.
    """
    with _lock_throttle:
        ahora = time.monotonic()
        espera = _ultima_llamada["momento"] + PAUSA_ENTRE_LLAMADAS - ahora
        if espera > 0:
            time.sleep(espera)
        _ultima_llamada["momento"] = time.monotonic()


@dataclass
class InfoMAL:
    mal_id: int
    status: str | None          # ej. "Finished Airing", "Currently Airing"
    episodios_totales: int | None
    titulo: str | None


def _get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def obtener_info_mal(mal_id: int) -> InfoMAL | None:
    """
    Trae status y total de episodios desde Jikan para un mal_id dado.
    Devuelve None si Jikan responde 404 (mal_id no encontrado).

    ⚠️ ESTA FUNCIÓN TIENE UN BUG CONOCIDO Y DOCUMENTADO EN JIKAN MISMO
    (https://github.com/jikan-me/jikan-rest/issues/378): el endpoint
    /v4/anime/{id} devuelve errores persistentes (404, 504...) para
    animes puntuales — típicamente recién agregados o poco populares —
    de forma consistente y reproducible, aunque la página de MAL para
    ese mismo anime funcione perfecto. Confirmado con corridas reales:
    14/14 animes de una temporada fallaban SIEMPRE en este endpoint, sin
    recuperarse ni esperando 30s entre reintentos, ni bajando el ritmo
    de peticiones a 1 cada 4 segundos. No es un problema de rate-limit
    ni de nuestro cliente — es este endpoint específico de Jikan.

    Por eso YA NO se llama desde el flujo principal de orquestador.py
    (ver escanear_temporada y _procesar_un_anime ahí): el status se
    resuelve primero con el listado bulk (obtener_temporada_completa_mal,
    más abajo en este archivo, que SÍ es confiable), y si no está
    disponible, se saca directo de la página de MAL
    (mal_scraper.obtener_pagina_mal) en vez de pasar por aquí.

    Esta función se conserva por si hace falta en el futuro para algún
    otro propósito puntual, pero NO reintroducirla como la fuente
    principal de status en un escaneo — es exactamente el bug que costó
    varios intentos fallidos resolver la última vez.

    Usa caché en disco (ver cache_jikan.py), pero SOLO se guarda en caché
    cuando el anime ya tiene status "Finished Airing" — mientras sigue en
    emisión, su status puede cambiar en cualquier momento (puede terminar
    mañana), así que cachear ese estado intermedio podría servir un dato
    desactualizado durante días. Una vez terminado, el dato es estable y
    sí vale la pena cachearlo.
    """
    cacheado = cache_jikan.obtener("info_mal", mal_id)
    if cacheado is not None:
        return InfoMAL(**cacheado)

    _esperar_turno()
    url = f"{API_BASE}/anime/{mal_id}"
    try:
        data = _get_json(url)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise

    # Forma esperada según doc pública de Jikan v4: { "data": { ... } }
    anime = data.get("data", {})

    info = InfoMAL(
        mal_id=mal_id,
        status=anime.get("status"),
        episodios_totales=anime.get("episodes"),
        titulo=anime.get("title"),
    )

    if info.status and info.status.strip().lower() == "finished airing":
        cache_jikan.guardar("info_mal", mal_id, info)

    return info


def esta_terminado(info: InfoMAL) -> bool:
    """True si el status de MAL indica que el anime ya terminó de emitirse."""
    if info is None or info.status is None:
        return False
    return info.status.strip().lower() == "finished airing"


@dataclass
class AnimeDeTemporadaMAL:
    """Una entrada mínima de /seasons/{year}/{season}, para detectar qué le falta a AnimeThemes."""
    mal_id: int
    titulo: str
    status: str | None
    tipo: str | None  # "TV", "Movie", "OVA", "ONA", "Special", "Music", etc.


def _animes_temporada_desde_cache(lista_cacheada: list[dict]) -> list[AnimeDeTemporadaMAL]:
    """Reconstruye una list[AnimeDeTemporadaMAL] a partir de la lista plana guardada en cache_jikan."""
    return [AnimeDeTemporadaMAL(**item) for item in lista_cacheada]


def obtener_temporada_completa_mal(
    year: int, season: str, pausa_entre_paginas: float = 1.0
) -> list[AnimeDeTemporadaMAL]:
    """
    Trae TODOS los animes de una temporada según Jikan, paginando hasta
    agotar resultados. Usado para detectar qué animes le faltan por completo
    a AnimeThemes (no solo qué OP/ED le falta a uno que ya tiene).

    season debe ser uno de: winter, spring, summer, fall (en minúsculas,
    a diferencia de animethemes_client que usa Capitalizado).

    Usa caché en disco por (year, season): la lista de qué animes existen
    en una temporada no cambia una vez publicada (solo podría cambiar el
    'status' de un anime particular dentro de la lista, pero esa
    verificación definitiva ya la hace por separado obtener_info_mal, que
    tiene su propia protección contra cachear estados aún no definitivos).

    Cada página respeta _esperar_turno() (el throttle global de
    PAUSA_ENTRE_LLAMADAS, ver docstring del módulo) además de
    pausa_entre_paginas: esta última es un espaciado propio de esta función
    entre páginas consecutivas, pero no protegía por sí sola contra el
    ritmo agregado de llamadas de otros hilos golpeando Jikan al mismo
    tiempo (ej. vía obtener_info_mal en paralelo).
    """
    season = season.lower()
    clave_cache = f"{year}_{season}"

    cacheado = cache_jikan.obtener("temporada_completa_mal", clave_cache)
    if cacheado is not None:
        return _animes_temporada_desde_cache(cacheado)

    animes: list[AnimeDeTemporadaMAL] = []
    pagina = 1

    while True:
        url = f"{API_BASE}/seasons/{year}/{season}?page={pagina}"
        _esperar_turno()
        data = _get_json(url)

        for item in data.get("data", []):
            animes.append(AnimeDeTemporadaMAL(
                mal_id=item.get("mal_id"),
                titulo=item.get("title") or "",
                status=item.get("status"),
                tipo=item.get("type"),
            ))

        paginacion = data.get("pagination", {})
        if not paginacion.get("has_next_page"):
            break

        pagina += 1
        time.sleep(pausa_entre_paginas)

    # Jikan puede devolver el mismo anime más de una vez entre páginas de
    # /seasons/{year}/{season} (comportamiento observado con datos reales:
    # el mismo mal_id aparece repetido, no necesariamente en páginas
    # consecutivas). Deduplicamos por mal_id, conservando la primera
    # ocurrencia, para que el resto del pipeline (que asume una lista sin
    # duplicados) no termine reportando el mismo candidato varias veces.
    vistos: set[int] = set()
    animes_unicos: list[AnimeDeTemporadaMAL] = []
    for anime in animes:
        if anime.mal_id in vistos:
            continue
        vistos.add(anime.mal_id)
        animes_unicos.append(anime)

    cache_jikan.guardar("temporada_completa_mal", clave_cache, animes_unicos)

    return animes_unicos


def obtener_temporada_completa_mal_desde_cache_vencido(
    year: int, season: str
) -> tuple[list[AnimeDeTemporadaMAL], int] | None:
    """
    Devuelve (animes, dias_de_antiguedad) desde caché, ignorando si
    venció, o None si nunca se cacheó esta temporada. NO hace ninguna
    llamada de red — ver cache_jikan.obtener_ignorando_expiracion.

    Uso: fallback de último recurso para detectar_animes_faltantes_en_at
    cuando el listado bulk en vivo falla persistentemente (ver
    orquestador.py) — solo sirve si esa temporada ya se escaneó con éxito
    alguna vez antes; en un escaneo en frío durante un corte de Jikan no
    hay nada que devolver acá.
    """
    clave_cache = f"{year}_{season.lower()}"
    resultado = cache_jikan.obtener_ignorando_expiracion("temporada_completa_mal", clave_cache)
    if resultado is None:
        return None
    valor, dias = resultado
    return _animes_temporada_desde_cache(valor), dias
