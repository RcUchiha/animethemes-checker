"""
Caché en disco para datos de Jikan/MAL, con expiración automática.

Por qué solo Jikan/MAL y no AnimeThemes:
AnimeThemes es la fuente que el usuario y otros contribuidores actualizan
activamente (suben temas faltantes, cierran rangos de episodios) — por eso
SIEMPRE se consulta en vivo, sin caché, para que cualquier escaneo refleje
el estado más reciente. Jikan/MAL, en cambio, describe datos de una
temporada que ya pasó (status, episodios totales, temas documentados) y
rara vez cambia una vez que el anime terminó — cachearlo ahorra tiempo
real sin sacrificar precisión en la práctica.

Qué se cachea:
- info_mal(mal_id): status y episodios totales de un anime puntual.
- temporada_completa_mal(year, season): listado paginado completo.
- temas_mal(mal_id): temas OP/ED scrapeados de la página de MAL.

Formato en disco: un único archivo JSON (cache_jikan.json) con 3
secciones, cada entrada con su fecha de guardado para poder expirarla.

Expiración: 15 días por defecto (DIAS_EXPIRACION). Una entrada vencida se
trata como si no existiera — se vuelve a pedir en vivo y se sobreescribe
con la fecha nueva.

Acceso alternativo a caché vencido: obtener_ignorando_expiracion() existe
aparte de obtener() (que NUNCA sirve una entrada vencida) para un caso
puntual y explícito — el fallback de último recurso en
detectar_animes_faltantes_en_at (orquestador.py) cuando el listado bulk de
MAL/Jikan en vivo falla persistentemente y esa temporada ya se había
escaneado con éxito antes. Se mantiene como una función separada, no como
un parámetro opcional de obtener(), para que ningún otro uso del caché
(info_mal, temas_mal, pagina_mal) pueda terminar sirviendo un dato vencido
"sin querer" — cada llamador que de verdad quiera ese comportamiento tiene
que pedirlo explícitamente por su nombre.
"""

from __future__ import annotations

import datetime
import json
import os
import threading
from dataclasses import asdict, is_dataclass

DIAS_EXPIRACION = 15

# Única fuente de verdad para las secciones válidas del caché — usada tanto
# al cargar el archivo (para inicializar secciones ausentes) como al limpiar
# expirados. Antes estaba duplicada en ambos lugares, y "pagina_mal" (usada
# por mal_scraper.obtener_pagina_mal) faltaba en limpiar_expirados(): sus
# entradas nunca se revisaban ni se eliminaban aunque vencieran.
SECCIONES_CONOCIDAS = ("info_mal", "temporada_completa_mal", "temas_mal", "pagina_mal")

_RUTA_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache_jikan.json")

_lock = threading.Lock()  # el caché se lee/escribe desde varios hilos (ThreadPoolExecutor)


def _cargar_archivo() -> dict:
    if not os.path.exists(_RUTA_CACHE):
        return {seccion: {} for seccion in SECCIONES_CONOCIDAS}
    try:
        with open(_RUTA_CACHE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        # archivo corrupto o ilegible: empezamos de cero en vez de tronar
        return {seccion: {} for seccion in SECCIONES_CONOCIDAS}

    for clave in SECCIONES_CONOCIDAS:
        data.setdefault(clave, {})
    return data


def _guardar_archivo(data: dict) -> None:
    tmp = _RUTA_CACHE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, _RUTA_CACHE)  # escritura atómica: evita corromper el archivo si falla a mitad


def _dias_desde(fecha_guardado_iso: str) -> int | None:
    """Días transcurridos entre fecha_guardado_iso y hoy, o None si la fecha no es parseable."""
    try:
        fecha_guardado = datetime.date.fromisoformat(fecha_guardado_iso)
    except ValueError:
        return None
    return (datetime.date.today() - fecha_guardado).days


def _esta_vigente(fecha_guardado_iso: str) -> bool:
    dias = _dias_desde(fecha_guardado_iso)
    return dias is not None and dias < DIAS_EXPIRACION


def _serializar(valor):
    """Convierte dataclasses (y listas de ellas) a dict/list planos para guardar en JSON."""
    if is_dataclass(valor) and not isinstance(valor, type):
        return asdict(valor)
    if isinstance(valor, list):
        return [_serializar(v) for v in valor]
    return valor


def obtener(seccion: str, clave: str):
    """
    Devuelve el valor cacheado si existe y sigue vigente, o None si no hay
    nada útil (no existe, o expiró). El llamador es responsable de saber
    reconstruir el objeto real a partir del dict/list plano devuelto.
    """
    with _lock:
        data = _cargar_archivo()
        entrada = data.get(seccion, {}).get(str(clave))

    if entrada is None:
        return None
    if not _esta_vigente(entrada.get("fecha", "")):
        return None
    return entrada.get("valor")


def obtener_ignorando_expiracion(seccion: str, clave: str) -> tuple[object, int] | None:
    """
    Devuelve (valor, dias_de_antiguedad) si existe una entrada para esta
    clave, SIN IMPORTAR si venció o no. None solo si la clave nunca
    existió (la única excepción práctica: una fecha guardada corrupta/no
    parseable, en cuyo caso tampoco hay una antigüedad real que devolver
    — mismo criterio defensivo que _esta_vigente).

    Uso: fallback explícito de último recurso cuando el llamador prefiere
    datos viejos a no tener nada — el llamador es responsable de
    comunicarle al usuario que los datos pueden estar desactualizados;
    esta función no lo hace por sí sola. A diferencia de obtener() (que
    NUNCA sirve una entrada vencida), esta siempre la sirve si existe.
    """
    with _lock:
        data = _cargar_archivo()
        entrada = data.get(seccion, {}).get(str(clave))

    if entrada is None:
        return None
    dias = _dias_desde(entrada.get("fecha", ""))
    if dias is None:
        return None
    return entrada.get("valor"), dias


def guardar(seccion: str, clave: str, valor) -> None:
    """Guarda valor (dataclass, lista de dataclasses, o dict/list plano) en el caché con la fecha de hoy."""
    valor_plano = _serializar(valor)

    with _lock:
        data = _cargar_archivo()
        data.setdefault(seccion, {})
        data[seccion][str(clave)] = {
            "fecha": datetime.date.today().isoformat(),
            "valor": valor_plano,
        }
        _guardar_archivo(data)


def limpiar_expirados() -> int:
    """
    Recorre el caché y elimina entradas vencidas. Devuelve cuántas se
    eliminaron. No es necesario llamarla manualmente para que el caché
    funcione correctamente (obtener() ya ignora entradas vencidas), pero
    sirve para no dejar crecer el archivo indefinidamente con basura vieja.
    """
    eliminadas = 0
    with _lock:
        data = _cargar_archivo()
        for seccion in SECCIONES_CONOCIDAS:
            claves_vencidas = [
                clave for clave, entrada in data.get(seccion, {}).items()
                if not _esta_vigente(entrada.get("fecha", ""))
            ]
            for clave in claves_vencidas:
                del data[seccion][clave]
                eliminadas += 1
        if eliminadas:
            _guardar_archivo(data)
    return eliminadas
