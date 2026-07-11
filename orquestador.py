"""
Orquestador: junta todas las piezas para escanear una temporada completa.

⚠️ ANTES DE TOCAR ESTE ARCHIVO — LEE ESTO PRIMERO ⚠️
Este archivo pasó por una investigación larga y con varios intentos
fallidos de un bug real: escaneos completos que fallaban en masa con
"HTTP Error 504: Gateway Time-out". Resumen para no volver a caer en lo
mismo (el detalle completo está más abajo, en los docstrings de cada
función):

1. EL ESCANEO ESTÁ PARALELIZADO A PROPÓSITO (ThreadPoolExecutor, ver
   escanear_temporada y detectar_animes_faltantes_en_at) — la
   paralelización NO fue la causa del bug. Se descartó con una prueba
   real, completamente secuencial y sin un solo hilo, que falló
   exactamente igual. No "simplificar" a secuencial pensando que eso
   ayuda a la estabilidad; no ayuda, y sí hace todo más lento.

2. EL STATUS DE CADA ANIME ("¿terminó de emitirse?") YA NO SE PIDE al
   endpoint individual de Jikan (jc.obtener_info_mal, /v4/anime/{id}).
   Ese endpoint tiene un bug DOCUMENTADO y externo, propio de Jikan
   (https://github.com/jikan-me/jikan-rest/issues/378): devuelve errores
   persistentes (404, 504...) para animes puntuales — típicamente
   recién agregados o poco populares — incluso cuando la página de MAL
   para ese mismo anime funciona perfecto. Confirmado con corridas
   reales: 14 animes de una temporada fallaban SIEMPRE en ese endpoint
   específico, sin recuperarse ni esperando 30s entre reintentos, ni
   bajando el ritmo de peticiones a 1 cada 4 segundos (el límite que la
   propia documentación de Jikan pide).

   EN VEZ DE ESE ENDPOINT, el status se resuelve en este orden (ver
   escanear_temporada y _procesar_un_anime):
     a) Un listado BULK de la temporada completa
        (jc.obtener_temporada_completa_mal) — UNA sola llamada para
        toda la temporada, no una por anime. Es el mismo endpoint que
        ya usaba detectar_animes_faltantes_en_at sin este problema.
     b) Si un anime no aparece ahí (raro), o si el bulk mismo falla, el
        status se saca DIRECTO de la página de MAL
        (ms.obtener_pagina_mal) — la misma descarga que de todos modos
        hace falta para los temas, sin tocar Jikan para nada en ese
        camino.

   NO reintroducir una llamada a jc.obtener_info_mal en el camino
   principal de escanear_temporada. Es EXACTAMENTE el bug que costó
   varios intentos fallidos (throttle, headers de navegador, slug
   realista, hasta cloudscraper) antes de encontrar la causa real.

3. mal_scraper.py NO usa cloudscraper ni un throttle "1 descarga a la
   vez" — se creyó necesario en su momento (mientras se perseguía la
   pista equivocada de que MAL bloqueaba el tráfico) y se confirmó
   después, con pruebas reales, que MAL tolera bien varias descargas
   concurrentes con urllib simple. Ver el docstring de mal_scraper.py
   para el detalle completo.

SI ALGO DE ESTO VUELVE A FALLAR con 504/errores en masa: no repetir el
ciclo de "ajustar un parámetro y probar de nuevo en la app completa".
Empezar por aislar el problema con un script chico y directo (sin
hilos, sin GUI, una sola llamada) — así se encontró la causa real la
última vez, después de varios intentos de arreglar a ciegas que no
funcionaron.

Flujo por anime (escanear_temporada):
1. Ya viene con sus temas de AnimeThemes (AnimeCompleto.temas) y su mal_id.
2. Si no tiene mal_id -> no podemos verificar nada contra MAL; se reporta
   aparte (no es un error del anime, es una limitación de datos).
3. Status resuelto por el listado bulk de Jikan, o por la página de MAL
   como respaldo (ver punto 2 de la advertencia arriba) -> si no está
   "Finished Airing", se omite (solo interesan los que ya terminaron).
4. Si terminó -> mal_scraper: traemos los temas que MAL documenta (misma
   descarga que ya trajo el status, si vino por el camino de respaldo).
5. comparador: cruzamos AT vs MAL -> discrepancias.
6. Armamos un ResultadoAnime por anime.

Manejo de errores: un fallo de red en un solo anime no debe tronar el
escaneo completo de la temporada. Se reintenta automáticamente unas
pocas veces (ver _con_reintentos) y, si sigue fallando, se reporta ese
anime puntual como error y se sigue con el resto — incluso las llamadas
"masivas" iniciales (listar la temporada completa) están envueltas en
reintentos, ya que un fallo ahí antes tumbaba el escaneo entero sin
producir ningún resultado parcial.

Canario de posible cambio de HTML en MAL (issue #3): un fallo de RED es
ruidoso (se ve arriba), pero un cambio en el MARKUP de la página de MAL
no lo es — mal_scraper.py no lanza excepción si deja de reconocer la
estructura, simplemente devuelve listas vacías (ver su "ADVERTENCIA DE
FRAGILIDAD"), y comparador.py no puede detectar temas faltantes si el
lado de MAL viene vacío. escanear_temporada agrega, sobre toda la
temporada, cuántos animes "Finished Airing" devolvieron 0 temas de MAL;
ResultadoEscaneo.alerta_posible_cambio_html_mal expone esa señal para
que el llamador (gui_pyqt6.py) la muestre, sin abortar el escaneo ni
tocar el parser. Ver _hay_alerta_canario_mal más abajo para el criterio
exacto y por qué se eligió ese umbral.
"""

from __future__ import annotations

import threading
import time
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, TypeVar

import animethemes_client as ac
import comparador
import i18n
import jikan_client as jc
import mal_scraper as ms
from modelos import ResultadoAnime

_T = TypeVar("_T")

REINTENTOS_POR_DEFECTO = 3
PAUSA_ENTRE_REINTENTOS = 2.0  # segundos


class ErrorListadoMALNoDisponible(Exception):
    """
    Se lanza cuando el listado bulk de una temporada en MAL/Jikan
    (jc.obtener_temporada_completa_mal) sigue fallando tras agotar
    _con_reintentos, en un llamador para el que ese dato NO es opcional
    (ver detectar_animes_faltantes_en_at).

    El mensaje de esta excepción (str(e)) es SIEMPRE el texto traducido de
    i18n.py, sin jerga técnica — es lo que termina viendo el usuario en el
    diálogo de error de gui_pyqt6.py (vía _WorkerEscaneo -> error_fatal ->
    _on_error_fatal, que solo hace str(e), sin distinguir tipos de
    excepción). La excepción original de urllib/http.client se encadena
    con 'raise ... from e' y queda disponible en __cause__ para quien
    necesite el detalle técnico real (ej. un traceback de debug), pero
    nunca llega a la UI.
    """


def _con_reintentos(
    funcion: Callable[[], _T],
    intentos: int = REINTENTOS_POR_DEFECTO,
    pausa: float = PAUSA_ENTRE_REINTENTOS,
) -> _T:
    """
    Ejecuta funcion() y reintenta si falla con un error de red transitorio:
    HTTPError, URLError (timeouts, 502/503/504, fallo al abrir la conexión,
    etc.) y ConnectionError.

    Por qué también ConnectionError, y por qué la clase base en vez de
    enumerar subtipos: se confirmó con una corrida real contra un corte de
    Jikan que un servidor que acepta la conexión pero la corta a mitad de
    la respuesta genera http.client.RemoteDisconnected ("Remote end closed
    connection without response"), que NO es subclase de URLError —
    urllib.request solo envuelve en URLError los fallos al ABRIR la
    conexión (dentro de h.request()); los que ocurren después, al LEER la
    respuesta (h.getresponse()), se propagan tal cual. Antes de este fix,
    ese error se saltaba el reintento por completo y salía del primer
    intento sin que _con_reintentos hiciera nada. RemoteDisconnected es
    subclase de ConnectionResetError, que junto con ConnectionRefusedError
    y BrokenPipeError comparten ConnectionError como base común en la
    stdlib — se atrapa esa base en vez de listar los subtipos uno por uno
    para no volver a dejar un hueco si aparece otro subtipo de conexión
    caída. NO se agrega http.client.HTTPException en general (ej.
    IncompleteRead): es una familia de error distinta (respuesta que llegó
    pero está incompleta/mal formada, no una conexión caída) sin evidencia
    todavía de que ocurra en la práctica — agregarla sin un caso real
    concreto sería adivinar.

    Tras el último intento fallido, relanza la excepción para que el
    llamador decida qué hacer (registrar como error, abortar, etc.) —
    este helper solo absorbe fallos TRANSITORIOS reintentando, no decide
    la política de qué hacer si nunca se recupera.
    """
    ultimo_error: Exception | None = None
    for intento in range(1, intentos + 1):
        try:
            return funcion()
        except (urllib.error.HTTPError, urllib.error.URLError, ConnectionError) as e:
            ultimo_error = e
            if intento < intentos:
                time.sleep(pausa)
    raise ultimo_error  # type: ignore[misc] — siempre habrá un ultimo_error si llegamos aquí


# ---------- canario: posible cambio de HTML en MAL (issue #3) ----------
# mal_scraper.py scrapea HTML, no una API versionada (ver su docstring,
# sección "ADVERTENCIA DE FRAGILIDAD"). Si MAL cambia su markup, el parser
# NO lanza ninguna excepción: html.parser sigue sin quejarse y
# _ThemeSongsExtractor simplemente no encuentra nada, devolviendo una
# lista de temas vacía. Sin esta señal, un escaneo así terminaría
# "exitoso" pero mintiendo: la Regla A de comparador.py no puede detectar
# temas faltantes si el lado de MAL viene vacío, así que el resultado se
# vería como "0 discrepancias" en vez de un error visible.
#
# UMBRAL_FRACCION_VACIOS_CANARIO = 0.8: un anime puntual sin temas
# documentados en MAL es normal (ocurre con specials/OVAs poco cubiertas),
# así que un umbral bajo generaría falsas alarmas todo el tiempo. Un
# cambio real de HTML rompe el parseo para prácticamente TODOS los animes
# de la temporada a la vez, no para unos pocos — 80% deja margen para que
# unos cuantos casos legítimamente vacíos no disparen la alerta, mientras
# sigue siendo lo bastante bajo para no tapar una rotura real (que en la
# práctica ronda el 100%).
#
# MINIMO_ANIMES_PARA_CANARIO = 5: con muestras chicas (ej. 1 de 1 vacío al
# principio de una temporada reciente, con pocos animes aún terminados),
# la fracción se dispara a 100% por pura casualidad estadística. 5 es un
# piso arbitrario pero razonable: absorbe ese ruido sin retrasar la
# detección de una rotura real, que en cualquier escaneo normal de una
# temporada (decenas de animes) se vería de inmediato.
UMBRAL_FRACCION_VACIOS_CANARIO = 0.8
MINIMO_ANIMES_PARA_CANARIO = 5


def _hay_alerta_canario_mal(total_evaluados: int, total_vacios: int) -> bool:
    """
    True si la fracción de animes "Finished Airing" que devolvieron 0
    temas de MAL en este escaneo es sospechosamente alta — señal de que
    mal_scraper.py pudo haber dejado de reconocer el HTML de MAL (ver el
    comentario arriba de UMBRAL_FRACCION_VACIOS_CANARIO).

    Es solo una señal para que el usuario la revise a mano: no aborta el
    escaneo, no descarta resultados, no toca el parser en absoluto.
    """
    if total_evaluados < MINIMO_ANIMES_PARA_CANARIO:
        return False
    return (total_vacios / total_evaluados) >= UMBRAL_FRACCION_VACIOS_CANARIO


@dataclass
class ResultadoEscaneo:
    """Resultado completo de escanear una temporada."""
    resultados: list[ResultadoAnime] = field(default_factory=list)
    omitidos_sin_mal_id: list[str] = field(default_factory=list)       # nombres
    omitidos_no_terminados: list[str] = field(default_factory=list)    # nombres
    errores: list[tuple[str, str]] = field(default_factory=list)       # (nombre, mensaje)

    # Para el canario de posible cambio de HTML en MAL (ver
    # _hay_alerta_canario_mal arriba): cuenta solo animes "Finished
    # Airing" para los que de verdad se intentó scrapear MAL (no cuenta
    # omitidos por falta de mal_id o por no haber terminado, ni animes
    # cuyo intento falló por un error de red, ya reflejado en 'errores').
    total_finished_airing_evaluados: int = 0
    total_finished_airing_con_temas_mal_vacios: int = 0

    @property
    def con_problemas(self) -> list[ResultadoAnime]:
        return [r for r in self.resultados if r.tiene_problemas]

    @property
    def alerta_posible_cambio_html_mal(self) -> bool:
        return _hay_alerta_canario_mal(
            self.total_finished_airing_evaluados,
            self.total_finished_airing_con_temas_mal_vacios,
        )


def _procesar_un_anime(
    anime: ac.AnimeCompleto, estado_conocido: str | None = None
) -> tuple[ResultadoAnime | None, str | None, bool | None]:
    """
    Procesa un solo anime ya traído de AnimeThemes.
    Devuelve (resultado, motivo_omision, temas_mal_vacio). Si resultado es
    None, el anime se omitió y motivo_omision explica por qué (para
    clasificarlo en ResultadoEscaneo). Si hay un error de red que persiste
    tras los reintentos, se relanza para que el llamador lo capture y lo
    registre en 'errores'.

    temas_mal_vacio es None si no llegamos a intentar scrapear MAL (sin
    mal_id, o el anime no terminó de emitirse) — no aplica para el
    canario de _hay_alerta_canario_mal. Si sí se confirmó "Finished
    Airing" y se pidió la página de MAL, es True/False según si
    pagina.temas vino vacío (ver escanear_temporada, que agrega este
    valor sobre toda la temporada).

    estado_conocido: si se da (ver escanear_temporada), es el status del
    anime ("Finished Airing", etc.) ya resuelto desde el listado bulk de
    la temporada en Jikan.

    Si es None (el anime no apareció en ese listado bulk, o el bulk
    mismo falló), YA NO se cae al endpoint individual de Jikan
    (/v4/anime/{id}) como se hacía antes. Confirmado con varias corridas
    reales que ESE endpoint — y a veces incluso el bulk — pueden fallar
    de forma persistente (504) para datos de una temporada reciente, sin
    recuperarse ni bajando el ritmo de peticiones a 1 cada 4 segundos
    (el límite que la propia documentación de Jikan pide). Es un
    problema documentado y externo a nosotros (ver
    https://github.com/jikan-me/jikan-rest/issues/378 y los issues
    abiertos en jikan-me/jikan-rest), no algo que podamos arreglar
    ajustando nuestro cliente.

    En vez de depender de ese endpoint, este camino de respaldo saca el
    status DIRECTO de la página de MAL (mal_scraper.obtener_pagina_mal),
    en la misma descarga que ya hace falta para los temas — eliminando
    la dependencia de Jikan por completo para este caso.
    """
    if anime.mal_id is None:
        return None, "sin_mal_id", None

    if estado_conocido is not None:
        # Ya sabemos el status por el listado bulk; solo scrapeamos MAL
        # si de verdad terminó (si no, ni hace falta la descarga).
        if estado_conocido.strip().lower() != "finished airing":
            return None, "no_terminado", None
        pagina = _con_reintentos(
            lambda: ms.obtener_pagina_mal(anime.mal_id, anime_terminado=True, titulo=anime.name)
        )
        status = estado_conocido
    else:
        # No lo tenemos por bulk — sacamos el status directo de la
        # página de MAL, en la misma descarga que los temas.
        pagina = _con_reintentos(
            lambda: ms.obtener_pagina_mal(anime.mal_id, anime_terminado=False, titulo=anime.name)
        )
        status = pagina.status
        if status is None or status.strip().lower() != "finished airing":
            return None, "no_terminado", None

    temas_mal = pagina.temas

    # En este punto ya se confirmó que el anime terminó, así que pasamos
    # anime_terminado=True explícitamente para que comparador.comparar
    # active la categoría RANGO_ABIERTO_SIN_CERRAR.
    discrepancias = comparador.comparar(anime.temas, temas_mal, anime_terminado=True)

    resultado = ResultadoAnime(
        anime_id=anime.id,
        nombre=anime.name,
        slug=anime.slug,
        mal_id=anime.mal_id,
        status_mal=status,
        discrepancias=discrepancias,
    )
    return resultado, None, len(temas_mal) == 0


@dataclass
class AnimeFaltanteEnAT:
    """Un anime que MAL reporta y AnimeThemes no tiene en absoluto."""
    mal_id: int
    titulo: str
    status: str | None
    tipo: str | None
    confirmado_con_tema: bool  # True si vimos al menos 1 tema real en MAL


@dataclass
class ResultadoFaltantes:
    """
    Resultado completo de detectar_animes_faltantes_en_at.

    datos_de_temporada_desde_cache_vencido / antiguedad_cache_dias: ver el
    docstring de detectar_animes_faltantes_en_at para el fallback que los
    produce. True solo cuando el listado bulk de MAL/Jikan en vivo falló
    persistentemente y se sirvió una entrada de caché vencida en su lugar
    (último recurso) — en el camino normal (Jikan responde bien), o en
    cualquier código que construya ResultadoFaltantes() a mano (ej.
    tests), quedan en sus valores por defecto.
    """
    faltantes: list[AnimeFaltanteEnAT] = field(default_factory=list)
    errores: list[tuple[str, str]] = field(default_factory=list)  # (titulo, mensaje)
    datos_de_temporada_desde_cache_vencido: bool = False
    antiguedad_cache_dias: int | None = None


# Tipos que de entrada NO consideramos candidatos a faltarle a AnimeThemes:
# son anuncios, PVs, music videos sueltos o cortos promocionales, no
# contenido con OP/ED propio que tenga sentido subir.
TIPOS_EXCLUIDOS = {"MUSIC", "CM", "PV"}


def detectar_animes_faltantes_en_at(
    year: int,
    season: str,
    progreso_callback=None,
) -> ResultadoFaltantes:
    """
    Detecta animes que MAL reporta para la temporada y que NO existen en
    absoluto en AnimeThemes.

    Filtro previo: se descartan tipos en TIPOS_EXCLUIDOS (anuncios, PVs,
    music videos sueltos) antes de cualquier otra cosa, ya que dominan estas
    listas y casi nunca son candidatos reales.

    Para el resto, SIEMPRE se scrapea su página de MAL para confirmar que
    tiene al menos 1 tema real antes de reportarlo (sin importar el tipo).
    Antes había un atajo que asumía type == "TV" como "tiene tema" sin
    verificar, pero se confirmó con casos reales (Kkoma Bus Tayo 7, DoReMi
    Friends: ambos TV, ambos sin temas en MAL) que esa asunción no siempre
    se cumple, así que se eliminó para evitar falsos positivos.

    Todas las llamadas de red (incluidas las 2 masivas iniciales) usan
    _con_reintentos. Un fallo persistente en cualquiera de las llamadas
    masivas iniciales se relanza (no hay nada que escanear sin esa data
    base — a diferencia de escanear_temporada, acá el listado de MAL/Jikan
    no es opcional: es la base entera de la comparación, no hay camino de
    respaldo posible sin él): el listado de AnimeThemes se relanza tal
    cual (ver más abajo por qué el de MAL/Jikan no); un fallo persistente
    al scrapear un candidato puntual se registra en resultado.errores y se
    sigue con el resto, en vez de tronar todo.

    El listado bulk de Jikan recibe más paciencia que el resto (5
    intentos, 3 segundos entre cada uno, en vez de los 3/2s por defecto)
    porque es una llamada de alto valor y sin respaldo posible — mismo
    criterio que ya se usa en escanear_temporada. Aun con eso, sigue
    siendo un servicio externo, gratuito y a veces inestable (ver
    jikan_client.py y https://github.com/jikan-me/jikan-rest/issues/378):
    si falla incluso con esta paciencia extra, no hay forma de continuar.

    Si el listado bulk en vivo falla persistentemente, ANTES de rendirse
    se intenta un último recurso: jc.obtener_temporada_completa_mal_desde_cache_vencido,
    que sirve la última entrada cacheada para esa (year, season) aunque
    haya superado sus 15 días de vigencia (ver cache_jikan.obtener_ignorando_expiracion).
    Si existe, se usa esa lista y ResultadoFaltantes queda marcado con
    datos_de_temporada_desde_cache_vencido=True y
    antiguedad_cache_dias=<días reales>, para que el llamador (gui_pyqt6.py)
    avise al usuario que el resultado puede estar desactualizado, en vez
    de fallar directamente.

    Límite honesto de este fallback: SOLO ayuda si esa temporada ya se
    había escaneado con éxito alguna vez antes (por eso hay algo cacheado
    para servir). En un escaneo en frío de una temporada que nunca se
    escaneó, durante un corte de Jikan, no hay nada que devolver — ahí
    sigue aplicando lo de siempre: en vez de dejar que la excepción cruda
    de urllib/http.client (ej. "HTTP Error 504: Gateway Time-out" o
    "Remote end closed connection without response") llegue tal cual
    hasta la GUI, se relanza como ErrorListadoMALNoDisponible con un
    mensaje traducido y sin jerga técnica (ver i18n.py, clave
    "error_listado_mal_no_disponible") — la excepción original queda
    encadenada (raise ... from e) para quien necesite el detalle técnico
    real. Este 504 en particular es un problema conocido y documentado en
    el propio repo de Jikan (https://github.com/jikan-me/jikan-rest/issues/607,
    intermitente y sin ETA de fix de los mantenedores al momento de
    escribir esto), no algo que podamos arreglar desde acá — por eso el
    mensaje invita a reintentar más tarde en vez de sugerir que hay algo
    mal en la app.
    """
    datos_de_temporada_desde_cache_vencido = False
    antiguedad_cache_dias = None
    try:
        animes_mal = _con_reintentos(
            lambda: jc.obtener_temporada_completa_mal(year, season), intentos=5, pausa=3.0
        )
    except (urllib.error.HTTPError, urllib.error.URLError, ConnectionError) as e:
        respaldo = jc.obtener_temporada_completa_mal_desde_cache_vencido(year, season)
        if respaldo is None:
            raise ErrorListadoMALNoDisponible(i18n.t("error_listado_mal_no_disponible")) from e
        animes_mal, antiguedad_cache_dias = respaldo
        datos_de_temporada_desde_cache_vencido = True

    animes_at = _con_reintentos(lambda: ac.obtener_animes_completos_de_temporada(year, season, max_hilos=4))

    candidatos_brutos = comparador.detectar_animes_faltantes_en_animethemes(animes_at, animes_mal)
    candidatos = [
        c for c in candidatos_brutos
        if (c.tipo or "").upper() not in TIPOS_EXCLUIDOS
    ]

    salida = ResultadoFaltantes(
        datos_de_temporada_desde_cache_vencido=datos_de_temporada_desde_cache_vencido,
        antiguedad_cache_dias=antiguedad_cache_dias,
    )
    total = len(candidatos)
    salida_lock = threading.Lock()
    contador_lock = threading.Lock()
    contador = {"hechos": 0}

    def _procesar_candidato(candidato):
        candidato_terminado = (candidato.status or "").strip().lower() == "finished airing"
        try:
            temas = _con_reintentos(
                lambda: ms.obtener_temas_mal(candidato.mal_id, anime_terminado=candidato_terminado, titulo=candidato.titulo)
            )
        except (urllib.error.HTTPError, urllib.error.URLError) as e:
            # Tras los reintentos, sigue fallando: no descartamos
            # silenciosamente — lo registramos como error para que el
            # usuario lo revise a mano en la pestaña de Errores.
            with salida_lock:
                salida.errores.append((candidato.titulo, f"{e} (mal_id={candidato.mal_id}, https://myanimelist.net/anime/{candidato.mal_id})"))
        else:
            if temas:
                with salida_lock:
                    salida.faltantes.append(AnimeFaltanteEnAT(
                        mal_id=candidato.mal_id,
                        titulo=candidato.titulo,
                        status=candidato.status,
                        tipo=candidato.tipo,
                        confirmado_con_tema=True,
                    ))
            # si no tiene temas, se descarta silenciosamente: no aplica para AT.

        if progreso_callback is not None:
            with contador_lock:
                contador["hechos"] += 1
                indice_actual = contador["hechos"]
            progreso_callback(indice_actual, total, candidato.titulo)

    # Paralelizado con un pool moderado (4 hilos): el scraping de MAL es el
    # cuello de botella más grande de esta función (100-200+ candidatos,
    # cada uno una página HTML completa), y la mayoría del tiempo es espera
    # de red. 4 hilos simultáneos reduce el tiempo total sustancialmente
    # sin arriesgar tantos bloqueos como un paralelismo más agresivo.
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(_procesar_candidato, candidatos))

    return salida


def escanear_temporada(
    year: int,
    season: str,
    progreso_callback=None,
    max_hilos: int = 4,
) -> ResultadoEscaneo:
    """
    Escanea una temporada completa y devuelve un ResultadoEscaneo.

    Paralelizado con un pool moderado de hilos (max_hilos, default 4),
    mismo patrón que detectar_animes_faltantes_en_at: la mayor parte del
    tiempo por anime es espera de red (scraping de MAL), así que varios
    hilos simultáneos reducen bastante el tiempo total de esta fase
    (antes era estrictamente secuencial, un anime a la vez).

    IMPORTANTE — por qué ya NO se usa el endpoint de Jikan por-anime:
    antes, el status de cada anime ("¿terminó de emitirse?") se pedía
    individualmente a /v4/anime/{id} para cada uno. Ese endpoint tiene un
    bug DOCUMENTADO y conocido de la propia Jikan (ver
    https://github.com/jikan-me/jikan-rest/issues/378: problemas de caché
    en su capa nginx que devuelven errores persistentes — 404 ahí, 504 en
    nuestro caso — para animes puntuales, típicamente menos populares o
    recién agregados, AUNQUE la página de MAL para ese mismo anime
    funcione perfecto). Confirmado con una corrida real: 14 animes de una
    temporada fallaban SIEMPRE con 504 en ese endpoint específico, sin
    recuperarse ni esperando 30s entre reintentos — no era un problema de
    nuestro cliente, de nuestra velocidad, ni de MAL directamente.

    En vez de eso, se pide UNA SOLA VEZ el listado completo de la
    temporada (obtener_temporada_completa_mal — el mismo endpoint bulk
    que ya usa detectar_animes_faltantes_en_at sin este problema) y se
    arma un diccionario mal_id -> status para consultar ahí. Esto
    también reduce drásticamente la cantidad de llamadas a Jikan (1 en
    vez de hasta ~40+). Si un anime de AnimeThemes no aparece en ese
    listado (raro, pero posible si su 'season' no coincide exactamente
    entre AnimeThemes y MAL), _procesar_un_anime cae a una llamada
    individual de respaldo para ese caso puntual.

    Las llamadas reales a Jikan quedan protegidas por un throttle GLOBAL
    (ver jikan_client._esperar_turno) que respeta su límite de 60/min sin
    importar cuántos hilos las disparen a la vez. MAL no publica un
    límite oficial, así que su scraping se deja sin throttle adicional
    más allá del límite natural de max_hilos concurrentes (mismo criterio
    que ya se usaba en detectar_animes_faltantes_en_at).

    progreso_callback, si se da, se llama como progreso_callback(indice,
    total, nombre_anime_actual) — con hilos, el orden de llegada ya NO
    corresponde al orden de la lista de animes (los hilos terminan en
    orden variable), así que el callback se dispara al TERMINAR de
    procesar cada anime, no antes de empezar (igual que ya hace
    detectar_animes_faltantes_en_at). El nombre mostrado en la barra de
    progreso pasa a ser "el último anime que terminó", no "el próximo a
    revisar".

    El orden de 'resultados' en el ResultadoEscaneo final tampoco está
    garantizado en el mismo orden que la lista de animes — quien use este
    resultado no debe asumir orden estable.
    """
    animes = _con_reintentos(lambda: ac.obtener_animes_completos_de_temporada(year, season, max_hilos=4))

    # Le damos más paciencia que al resto (más intentos, pausa más larga)
    # porque esta llamada es de alto valor: si falla y abortamos, NADIE
    # se procesa. Pero aun así, si Jikan también falla acá (puede pasar
    # — ver el docstring de más arriba sobre sus problemas conocidos),
    # NO abortamos todo el escaneo: cada anime simplemente cae al camino
    # de respaldo individual en _procesar_un_anime (estado_por_mal_id
    # queda vacío, como si el bulk nunca hubiera estado disponible). Más
    # lento y expuesto al bug conocido del endpoint por-anime, pero
    # muchísimo mejor que no poder escanear nada en absoluto.
    try:
        animes_mal_temporada = _con_reintentos(
            lambda: jc.obtener_temporada_completa_mal(year, season), intentos=5, pausa=3.0
        )
        estado_por_mal_id = {a.mal_id: a.status for a in animes_mal_temporada}
    except (urllib.error.HTTPError, urllib.error.URLError):
        estado_por_mal_id = {}

    salida = ResultadoEscaneo()
    total = len(animes)

    salida_lock = threading.Lock()
    contador_lock = threading.Lock()
    contador = {"hechos": 0}

    def _procesar_y_clasificar(anime: ac.AnimeCompleto) -> None:
        try:
            estado_conocido = estado_por_mal_id.get(anime.mal_id) if anime.mal_id is not None else None
            resultado, motivo_omision, temas_mal_vacio = _procesar_un_anime(anime, estado_conocido=estado_conocido)
        except (urllib.error.HTTPError, urllib.error.URLError) as e:
            with salida_lock:
                salida.errores.append((anime.name, f"{e} (mal_id={anime.mal_id}, https://myanimelist.net/anime/{anime.mal_id})"))
        else:
            with salida_lock:
                if resultado is not None:
                    salida.resultados.append(resultado)
                elif motivo_omision == "sin_mal_id":
                    salida.omitidos_sin_mal_id.append(anime.name)
                elif motivo_omision == "no_terminado":
                    salida.omitidos_no_terminados.append(anime.name)

                # Canario de posible cambio de HTML en MAL (issue #3): solo
                # cuenta animes para los que de verdad se intentó scrapear
                # MAL (temas_mal_vacio es None si se omitió antes de eso).
                if temas_mal_vacio is not None:
                    salida.total_finished_airing_evaluados += 1
                    if temas_mal_vacio:
                        salida.total_finished_airing_con_temas_mal_vacios += 1

        if progreso_callback is not None:
            with contador_lock:
                contador["hechos"] += 1
                indice_actual = contador["hechos"]
            progreso_callback(indice_actual, total, anime.name)

    with ThreadPoolExecutor(max_workers=max_hilos) as executor:
        list(executor.map(_procesar_y_clasificar, animes))

    return salida
