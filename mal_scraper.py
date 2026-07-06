"""
Scraper del bloque "Opening Theme / Ending Theme" de la página de un anime en
MyAnimeList (https://myanimelist.net/anime/{mal_id}/...).

⚠️ ANTES DE TOCAR EL THROTTLE/CONCURRENCIA DE ESTE ARCHIVO — resumen:
- MAL NO bloquea nuestro tráfico. Se pensó lo contrario durante una
  investigación larga (throttle "1 a la vez", cloudscraper para imitar
  un navegador real), pero se confirmó con pruebas reales que era
  innecesario — ver "IMPORTANTE — Historial de diagnóstico" más abajo.
- El bug real de los 504 en masa estaba en Jikan (jikan_client.py /
  orquestador.py), no acá. Si vuelve a pasar, revisar ahí primero.
- MAX_DESCARGAS_MAL_SIMULTANEAS = 4 está confirmado seguro (probado con
  hasta 5 concurrentes, sin throttle, 29/29 éxitos). No hace falta
  volver a bajarlo a 1 "por las dudas" — eso ya se probó y no era la
  causa de nada.

Este dato (rango de episodios por tema, ej. "eps 7-12") NO existe en ninguna
API limpia (ni Jikan ni AniList lo expone) — solo vive en el HTML de la
página web de MAL. Por eso scrapeamos directamente.

Estructura HTML confirmada con datos reales (Mato Seihei no Slave 1 y 2,
junio 2026):

    <div class="theme-songs js-theme-songs opnening">   <!-- sic: "opnening" -->
      <table>
        <tr>
          <td>...</td>
          <td>
            <span class="theme-song-index">2:</span>          <!-- opcional -->
            "Título de la canción"
            <span class="theme-song-artist"> by Artista</span>
            <span class="theme-song-episode">(eps 7-12)</span> <!-- opcional -->
          </td>
          <td>...</td>
        </tr>
        ...
      </table>
    </div>
    <div class="theme-songs js-theme-songs ending">
      ...misma estructura...
    </div>

Reglas observadas:
- Si NO hay <span class="theme-song-index">, el tema es el único de su tipo
  (equivalente a secuencia 1) y normalmente no trae theme-song-episode
  tampoco (se asume que cubre todos los episodios).
- Si hay varios temas del mismo tipo, cada uno trae su índice ("1:", "2:"...)
  y, cuando MAL lo sabe, su rango de episodios.
- El título puede incluir un sub-título entre paréntesis en otro idioma
  (ej. 'LOVE LOVE Beam (LOVE LOVE ビーム)'); esto es parte del MISMO título,
  no algo a separar.

ADVERTENCIA DE FRAGILIDAD:
Esto es scraping de HTML, no una API estable. Si MAL cambia su markup, este
parser puede romperse silenciosamente (devolver listas vacías) o con error.
Conviene revisarlo de vez en cuando contra un caso conocido.

IMPORTANTE — Historial de diagnóstico de los 504 en masa (ya resuelto):
En su momento se pensó que MAL (o un WAF delante suyo, tipo Cloudflare)
estaba bloqueando nuestro tráfico, y este módulo llegó a acumular bastante
blindaje por precaución: throttle serializado (nunca 2 descargas de MAL a
la vez en todo el programa) y la librería `cloudscraper` con sesión nueva
por petición, pensada para imitar la huella TLS de un navegador real.

Ese blindaje resultó INNECESARIO: el 504 en masa nunca vino de MAL — venía
del endpoint de Jikan que se usaba para preguntar el status de cada anime
(ver jikan_client.py y orquestador.py), un bug documentado y externo de
Jikan (https://github.com/jikan-me/jikan-rest/issues/378), no un bloqueo
de MAL. Una vez identificada la causa real y sacada la dependencia de ese
endpoint de Jikan, se hicieron pruebas reales directas contra MAL (ver
diagnostico_mal_concurrencia.py) con urllib simple, SIN cloudscraper y SIN
ningún throttle: 29/29 páginas reales descargadas con éxito usando 5 hilos
simultáneos — MAL nunca tuvo problema con esto. Por eso este módulo volvió
a urllib simple, y el throttle pasó de "1 a la vez, serializado" a un
límite de concurrencia más generoso (ver _semaforo_concurrencia_mal),
acorde a lo que se confirmó que MAL tolera bien.
"""

from __future__ import annotations

import re
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass, asdict
from html.parser import HTMLParser

import cache_jikan
from modelos import TemaMAL, TipoTema

# Cabecera parecida a un navegador real — buena práctica general, aunque
# ya confirmamos que MAL no la exige para responder bien.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
}

# Para parsear "(eps 7-12)" -> ("7", "12"); también soporta un solo número "(eps 4)"
_EPISODIO_RE = re.compile(r"eps?\s*([\d,\-]+)", re.IGNORECASE)

# Para armar un slug aproximado a partir del título real del anime.
_CARACTER_INVALIDO_EN_SLUG_RE = re.compile(r"[^a-zA-Z0-9]+")

# Cuántas descargas de MAL pueden estar en curso AL MISMO TIEMPO en todo
# el programa. Confirmado con prueba real (diagnostico_mal_concurrencia.py)
# que 5 simultáneas, sin ninguna pausa, funcionan sin problema — se deja
# en 4 (coincide con max_hilos del resto del programa) como margen
# razonable, no porque haga falta ser más conservador.
MAX_DESCARGAS_MAL_SIMULTANEAS = 4

_semaforo_concurrencia_mal = threading.Semaphore(MAX_DESCARGAS_MAL_SIMULTANEAS)


def _slug_aproximado(titulo: "str | None") -> str:
    """
    Genera un slug aproximado a partir del título real del anime, solo
    para que la URL se vea como la visita de un usuario real (ej.
    /anime/12345/Mato_Seihei_no_Slave) en vez de siempre el mismo
    marcador genérico "_" repetido para miles de IDs distintos. No hace
    falta para que MAL funcione bien (confirmado), pero es buena
    práctica general no repetir siempre el mismo patrón de URL.
    """
    if not titulo:
        return "_"
    slug = _CARACTER_INVALIDO_EN_SLUG_RE.sub("_", titulo).strip("_")
    return slug or "_"


def _descargar_html(mal_id: int, titulo: "str | None" = None) -> str:
    """
    Descarga el HTML de la página de detalle del anime en MAL con
    urllib simple. Si un error HTTP ocurre, se enriquece el mensaje con
    headers de diagnóstico (Server, CF-RAY, Akamai-Cache-Status, Via,
    X-Cache) para poder distinguir a futuro qué infraestructura está
    respondiendo.
    """
    slug = _slug_aproximado(titulo)
    url = f"https://myanimelist.net/anime/{mal_id}/{slug}"
    req = urllib.request.Request(url, headers=HEADERS)

    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        detalle = e.reason or ""
        for header_diagnostico in ("Server", "CF-RAY", "Akamai-Cache-Status", "Via", "X-Cache"):
            valor = e.headers.get(header_diagnostico) if e.headers else None
            if valor:
                detalle += f" [{header_diagnostico}: {valor}]"
        raise urllib.error.HTTPError(url, e.code, detalle, e.headers, e.fp)


def _descargar_html_con_throttle(mal_id: int, titulo: "str | None" = None) -> str:
    """
    Descarga respetando el límite de concurrencia (ver
    MAX_DESCARGAS_MAL_SIMULTANEAS) — el nombre se conserva por
    compatibilidad con el resto del código, aunque ya no es un throttle
    de "1 a la vez": ahora permite varias descargas simultáneas dentro
    del límite confirmado seguro.
    """
    with _semaforo_concurrencia_mal:
        return _descargar_html(mal_id, titulo)


class _ThemeSongsExtractor(HTMLParser):
    """
    Parser minimalista basado en html.parser (sin dependencias externas).

    Estrategia: en vez de construir un árbol DOM completo, vamos rastreando
    en qué bloque estamos (opening/ending/ninguno) según la clase del <div>,
    y dentro de cada <tr> acumulamos el texto y los spans relevantes que
    encontramos, hasta cerrar el <tr> y entonces armamos un TemaMAL.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.temas: list[TemaMAL] = []

        self._tipo_actual: TipoTema | None = None  # None = fuera de un bloque theme-songs
        self._profundidad_div_theme_songs = 0

        self._dentro_de_tr = False
        self._span_actual: str | None = None  # nombre de la clase del span abierto
        self._profundidad_popup = 0  # >0 mientras estemos dentro de div.oped-popup

        # buffers del <tr> en curso
        self._buffer_texto_general = ""   # incluye título y signos de puntuación crudos
        self._indice = None
        self._artista = ""
        self._episodios_texto = ""

    # --- manejo de apertura de tags ---

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        clases = attrs_dict.get("class", "")

        if tag == "div" and "theme-songs" in clases:
            if "opnening" in clases:  # sic, typo real de MAL
                self._tipo_actual = TipoTema.OP
            elif "ending" in clases:
                self._tipo_actual = TipoTema.ED
            self._profundidad_div_theme_songs = 1
            return

        if self._tipo_actual is not None and tag == "div":
            # cualquier otro div anidado dentro del bloque (ej. el popup):
            # contamos profundidad para saber cuándo salimos del bloque real.
            self._profundidad_div_theme_songs += 1
            if "oped-popup" in clases:
                self._profundidad_popup += 1
            return

        if self._tipo_actual is None:
            return  # nada de lo siguiente importa fuera de un bloque theme-songs

        if self._profundidad_popup > 0:
            return  # estamos dentro del popup oculto; ignorar todo su contenido

        if tag == "tr":
            self._dentro_de_tr = True
            self._buffer_texto_general = ""
            self._indice = None
            self._artista = ""
            self._episodios_texto = ""
            return

        if not self._dentro_de_tr:
            return

        if tag == "span":
            if "theme-song-index" in clases:
                self._span_actual = "index"
            elif "theme-song-artist" in clases:
                self._span_actual = "artist"
            elif "theme-song-episode" in clases:
                self._span_actual = "episode"
            else:
                self._span_actual = None

    def handle_endtag(self, tag):
        if tag == "div" and self._tipo_actual is not None:
            self._profundidad_div_theme_songs -= 1
            if self._profundidad_popup > 0:
                self._profundidad_popup -= 1
            if self._profundidad_div_theme_songs <= 0:
                self._tipo_actual = None
            return

        if self._profundidad_popup > 0:
            return  # seguimos dentro del popup; ignorar cierres de tr/span aquí

        if tag == "span":
            self._span_actual = None
            return

        if tag == "tr" and self._dentro_de_tr:
            self._cerrar_fila()
            self._dentro_de_tr = False

    def handle_data(self, data):
        if self._profundidad_popup > 0:
            return  # texto dentro del popup (Spotify, Apple Music, etc.); ignorar

        if not self._dentro_de_tr:
            return

        if self._span_actual == "index":
            # viene como "2:" -> nos quedamos solo con el número
            m = re.search(r"\d+", data)
            if m:
                self._indice = int(m.group())
        elif self._span_actual == "artist":
            # viene como " by Nombre (kanji)"; quitamos el "by " inicial
            texto = data.strip()
            texto = re.sub(r"^by\s+", "", texto, flags=re.IGNORECASE)
            self._artista += texto
        elif self._span_actual == "episode":
            self._episodios_texto += data
        else:
            # texto suelto: aquí cae el título entre comillas
            self._buffer_texto_general += data

    def _cerrar_fila(self):
        titulo = self._extraer_titulo(self._buffer_texto_general)
        if not titulo:
            # fila sin título reconocible (ej. separadores raros); ignorar
            return

        secuencia = self._indice if self._indice is not None else 1
        episodios = self._normalizar_episodios(self._episodios_texto)

        self.temas.append(TemaMAL(
            tipo=self._tipo_actual,
            secuencia=secuencia,
            titulo_cancion=titulo,
            artista=self._artista.strip(),
            episodios_texto=episodios,
        ))

    @staticmethod
    def _extraer_titulo(texto_crudo: str) -> str:
        """
        El título viene entre comillas dobles: '"Yume no Ito (夢の糸)"'.

        IMPORTANTE: si no hay comillas, NO es un tema real — es el mensaje
        de relleno que pone MAL cuando no hay temas documentados, ej.:
        'No opening themes have been added to this title. Help improve our
        database by adding an opening theme here.'
        Antes este caso caía en un fallback que devolvía ese texto completo
        como si fuera un título, generando falsos positivos. Ahora se
        devuelve '' explícitamente para que _cerrar_fila lo descarte.
        """
        m = re.search(r'"([^"]+)"', texto_crudo)
        if m:
            return m.group(1).strip()
        return ""

    @staticmethod
    def _normalizar_episodios(texto_crudo: str) -> str:
        """De '(eps 7-12)' nos quedamos con '7-12'. Si no hay match, devuelve ''."""
        m = _EPISODIO_RE.search(texto_crudo)
        if m:
            return m.group(1).strip()
        return ""


# ---------- extracción de status ("Finished Airing", etc.) ----------
# Markup real confirmado (Awajima Hyakkei, mal_id=58820, julio 2026):
#
#     <div class="spaceit_pad">
#       <span class="dark_text">Status:</span>
#       Finished Airing
#       </div>
#
# OJO: la página también tiene un "Status:" DISTINTO más arriba, dentro
# de un <select name="myinfo_status"> (el dropdown para agregarlo a tu
# lista personal en MAL) — ese usa class="spaceit" (sin el "_pad") y no
# tiene "dark_text", así que el regex de abajo no lo confunde con el real.
_STATUS_RE = re.compile(
    r'<span class="dark_text">Status:</span>\s*([^<]+?)\s*</div>',
    re.IGNORECASE,
)


def _extraer_status(html: str) -> "str | None":
    """Extrae el status ("Finished Airing", "Currently Airing", etc.) del bloque Information de MAL."""
    m = _STATUS_RE.search(html)
    if m:
        return m.group(1).strip()
    return None


@dataclass
class PaginaMAL:
    """Resultado de scrapear la página de un anime en MAL: temas Y status, en una sola descarga."""
    temas: list[TemaMAL]
    status: "str | None"


def obtener_pagina_mal(mal_id: int, anime_terminado: bool = False, titulo: "str | None" = None) -> PaginaMAL:
    """
    Descarga y parsea, en UNA SOLA descarga, tanto los temas OP/ED como
    el status del anime en MAL.

    Por qué existe separado de obtener_temas_mal (que sigue usando
    detectar_animes_faltantes_en_at sin cambios): escanear_temporada
    necesita AMBOS datos, y antes pedía el status a Jikan por separado —
    Jikan tiene un bug/inestabilidad DOCUMENTADA (ver jikan_client.py y
    orquestador.py) que hace que ese status falle de forma persistente
    para ciertos animes — confirmado con corridas reales donde ni
    esperando ni bajando el ritmo de peticiones se resolvía — aunque la
    página de MAL para ese mismo anime funcione perfecto. Sacar el
    status de la MISMA página que ya se descarga para los temas elimina
    esa dependencia por completo para este camino.

    Usa un caché propio ("pagina_mal", separado de "temas_mal") para no
    mezclar esquemas con caché ya existente de antes de este cambio.
    """
    if anime_terminado:
        cacheado = cache_jikan.obtener("pagina_mal", mal_id)
        if cacheado is not None:
            temas = [TemaMAL(tipo=TipoTema(item["tipo"]), secuencia=item["secuencia"],
                             titulo_cancion=item["titulo_cancion"], artista=item["artista"],
                             episodios_texto=item["episodios_texto"]) for item in cacheado["temas"]]
            return PaginaMAL(temas=temas, status=cacheado["status"])

    html = _descargar_html_con_throttle(mal_id, titulo)
    parser = _ThemeSongsExtractor()
    parser.feed(html)
    temas = parser.temas
    status = _extraer_status(html)

    if anime_terminado:
        cache_jikan.guardar("pagina_mal", mal_id, {
            "temas": [asdict(t) for t in temas],
            "status": status,
        })

    return PaginaMAL(temas=temas, status=status)


def obtener_temas_mal(mal_id: int, anime_terminado: bool = False, titulo: "str | None" = None) -> list[TemaMAL]:
    """
    Descarga y parsea los temas OP/ED de la página de un anime en MAL.

    anime_terminado controla el caché (ver cache_jikan.py): por defecto
    False, para no cambiar el comportamiento de código que no sabe si el
    anime terminó. El orquestador SÍ pasa este valor explícitamente en
    ambos puntos donde llama a esta función, porque ya tiene esa
    información a mano. Solo se cachea cuando ya terminó — mientras el
    anime sigue en emisión, MAL puede agregar temas nuevos conforme
    avanza la serie, así que cachear ese estado intermedio podría servir
    una lista incompleta durante días.

    titulo, si se da, se usa para armar un slug realista en la URL en
    vez de un placeholder fijo (ver _slug_aproximado) — ayuda a que el
    tráfico se vea menos como scraping automatizado.
    """
    if anime_terminado:
        cacheado = cache_jikan.obtener("temas_mal", mal_id)
        if cacheado is not None:
            return [TemaMAL(tipo=TipoTema(item["tipo"]), secuencia=item["secuencia"],
                             titulo_cancion=item["titulo_cancion"], artista=item["artista"],
                             episodios_texto=item["episodios_texto"]) for item in cacheado]

    html = _descargar_html_con_throttle(mal_id, titulo)
    parser = _ThemeSongsExtractor()
    parser.feed(html)
    temas = parser.temas

    if anime_terminado:
        cache_jikan.guardar("temas_mal", mal_id, temas)

    return temas
