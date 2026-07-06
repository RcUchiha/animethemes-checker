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
"""

from __future__ import annotations

import datetime
import json
import os
import threading
from dataclasses import asdict, is_dataclass

DIAS_EXPIRACION = 15

_RUTA_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache_jikan.json")

_lock = threading.Lock()  # el caché se lee/escribe desde varios hilos (ThreadPoolExecutor)


def _cargar_archivo() -> dict:
    if not os.path.exists(_RUTA_CACHE):
        return {"info_mal": {}, "temporada_completa_mal": {}, "temas_mal": {}}
    try:
        with open(_RUTA_CACHE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        # archivo corrupto o ilegible: empezamos de cero en vez de tronar
        return {"info_mal": {}, "temporada_completa_mal": {}, "temas_mal": {}}

    for clave in ("info_mal", "temporada_completa_mal", "temas_mal"):
        data.setdefault(clave, {})
    return data


def _guardar_archivo(data: dict) -> None:
    tmp = _RUTA_CACHE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, _RUTA_CACHE)  # escritura atómica: evita corromper el archivo si falla a mitad


def _esta_vigente(fecha_guardado_iso: str) -> bool:
    try:
        fecha_guardado = datetime.date.fromisoformat(fecha_guardado_iso)
    except ValueError:
        return False
    return (datetime.date.today() - fecha_guardado).days < DIAS_EXPIRACION


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
        for seccion in ("info_mal", "temporada_completa_mal", "temas_mal"):
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
