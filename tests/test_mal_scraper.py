"""
Tests unitarios de mal_scraper.py.

Mockea la descarga real (`_descargar_html_con_throttle` / `urllib.request.
urlopen`) y el caché en disco (`cache_jikan.obtener`/`guardar`) — nada de
red ni de disco real durante los tests.

Qué se decidió testear y por qué (ver docstring del módulo para el
contexto completo):
- `_ThemeSongsExtractor` es la lógica más crítica y más frágil del archivo
  (scraping de HTML, no una API estable — el propio docstring lo marca con
  "ADVERTENCIA DE FRAGILIDAD"), así que se le da la cobertura más extensa:
  el parseo normal con y sin índice de secuencia, el contenido del popup
  oped-popup que debe ignorarse por completo, el mensaje de relleno de MAL
  cuando no hay temas documentados (sin comillas -> se descarta, no genera
  falso positivo), y el typo real "opnening" (si alguien lo "corrige" a
  "opening" sin querer, el parser se rompe silenciosamente — se deja un
  test que lo deja explícito).
- `_extraer_titulo`, `_normalizar_episodios` y `_extraer_status` son
  funciones puras con reglas no obvias documentadas explícitamente (el
  caso de las comillas, el "Status:" falso del dropdown de MAL) — fáciles
  de testear aisladas y con alto valor si alguien las toca.
- `_slug_aproximado` es trivial pero pura, se cubre con pocos casos.
- `obtener_pagina_mal`/`obtener_temas_mal` son el punto de entrada real:
  se testea el ruteo de caché (hit/miss, y que anime_terminado=False no
  toca el caché para nada) sin tocar red ni disco.
- `_descargar_html` se testea solo para el enriquecimiento de errores HTTP
  con headers de diagnóstico, que es la única lógica propia ahí (el resto
  es urllib puro, ya mockeado).
"""

import urllib.error
from unittest.mock import patch

import mal_scraper as ms
from modelos import TipoTema


# ---------- _ThemeSongsExtractor ----------

class TestThemeSongsExtractor:

    def _parsear(self, html: str):
        parser = ms._ThemeSongsExtractor()
        parser.feed(html)
        return parser.temas

    def test_tema_sin_indice_es_secuencia_1_con_episodios(self):
        html = """
        <div class="theme-songs js-theme-songs opnening">
        <table><tr>
        <td>
        "Yume no Ito (夢の糸)"
        <span class="theme-song-artist"> by Artista A</span>
        <span class="theme-song-episode">(eps 1-12)</span>
        </td>
        </tr></table>
        </div>
        """
        temas = self._parsear(html)
        assert len(temas) == 1
        tema = temas[0]
        assert tema.tipo == TipoTema.OP
        assert tema.secuencia == 1
        # El sub-título japonés entre paréntesis es parte del mismo título
        # (ver docstring del módulo) — no se separa aquí.
        assert tema.titulo_cancion == "Yume no Ito (夢の糸)"
        assert tema.artista == "Artista A"
        assert tema.episodios_texto == "1-12"

    def test_tema_con_indice_explicito_usa_esa_secuencia(self):
        html = """
        <div class="theme-songs js-theme-songs ending">
        <table><tr>
        <td>
        <span class="theme-song-index">2:</span>
        "Segundo ED"
        <span class="theme-song-artist"> by Artista B</span>
        </td>
        </tr></table>
        </div>
        """
        temas = self._parsear(html)
        assert len(temas) == 1
        assert temas[0].tipo == TipoTema.ED
        assert temas[0].secuencia == 2
        assert temas[0].titulo_cancion == "Segundo ED"
        assert temas[0].episodios_texto == ""

    def test_ignora_por_completo_el_contenido_del_popup(self):
        html = """
        <div class="theme-songs js-theme-songs opnening">
        <table><tr>
        <td>
        "Titulo Real"
        <div class="oped-popup">
        <span class="theme-song-artist"> by Deberia Ignorarse</span>
        "Titulo Falso Popup"
        </div>
        <span class="theme-song-artist"> by Artista Real</span>
        </td>
        </tr></table>
        </div>
        """
        temas = self._parsear(html)
        assert len(temas) == 1
        assert temas[0].titulo_cancion == "Titulo Real"
        assert temas[0].artista == "Artista Real"

    def test_mensaje_de_relleno_sin_comillas_no_genera_tema(self):
        # Antes esto caía en un fallback que devolvía el mensaje completo
        # como si fuera un título (falso positivo) — ver docstring de
        # _extraer_titulo.
        html = """
        <div class="theme-songs js-theme-songs opnening">
        <table><tr>
        <td>
        No opening themes have been added to this title. Help improve our
        database by adding an opening theme here.
        </td>
        </tr></table>
        </div>
        """
        assert self._parsear(html) == []

    def test_requiere_el_typo_real_opnening_no_opening_correcto(self):
        # Si alguien "corrige" el typo real de MAL, este bloque deja de
        # reconocerse como el de opening — regression test intencional.
        html = """
        <div class="theme-songs js-theme-songs opening">
        <table><tr><td>"Titulo"</td></tr></table>
        </div>
        """
        assert self._parsear(html) == []

    def test_dos_bloques_opening_y_ending_se_parsean_por_separado(self):
        html = """
        <div class="theme-songs js-theme-songs opnening">
        <table><tr><td>"OP Unico"</td></tr></table>
        </div>
        <div class="theme-songs js-theme-songs ending">
        <table><tr><td>"ED Unico"</td></tr></table>
        </div>
        """
        temas = self._parsear(html)
        assert len(temas) == 2
        assert temas[0].tipo == TipoTema.OP
        assert temas[0].titulo_cancion == "OP Unico"
        assert temas[1].tipo == TipoTema.ED
        assert temas[1].titulo_cancion == "ED Unico"


# ---------- _extraer_titulo ----------

class TestExtraerTitulo:

    def test_titulo_entre_comillas(self):
        assert ms._ThemeSongsExtractor._extraer_titulo('"Yume no Ito (夢の糸)"') == "Yume no Ito (夢の糸)"

    def test_texto_con_prefijo_antes_de_las_comillas(self):
        assert ms._ThemeSongsExtractor._extraer_titulo('2: "Titulo"') == "Titulo"

    def test_sin_comillas_devuelve_vacio(self):
        texto = "No opening themes have been added to this title."
        assert ms._ThemeSongsExtractor._extraer_titulo(texto) == ""


# ---------- _normalizar_episodios ----------

class TestNormalizarEpisodios:

    def test_rango_de_episodios(self):
        assert ms._ThemeSongsExtractor._normalizar_episodios("(eps 7-12)") == "7-12"

    def test_episodio_unico(self):
        assert ms._ThemeSongsExtractor._normalizar_episodios("(eps 4)") == "4"

    def test_sin_match_devuelve_vacio(self):
        assert ms._ThemeSongsExtractor._normalizar_episodios("") == ""
        assert ms._ThemeSongsExtractor._normalizar_episodios("texto sin episodios") == ""


# ---------- _extraer_status ----------

class TestExtraerStatus:

    def test_extrae_status_del_bloque_real(self):
        html = """
        <div class="spaceit_pad">
        <span class="dark_text">Status:</span>
        Finished Airing
        </div>
        """
        assert ms._extraer_status(html) == "Finished Airing"

    def test_ignora_el_status_falso_del_dropdown_myinfo(self):
        # El dropdown para agregar a tu lista personal tiene su propio
        # "Status:" con class="spaceit" (sin "_pad" ni "dark_text") — el
        # regex no debe confundirlo con el real.
        html = """
        <select name="myinfo_status">
        <span class="spaceit">Status:</span>
        Watching
        </select>
        <div class="spaceit_pad">
        <span class="dark_text">Status:</span>
        Finished Airing
        </div>
        """
        assert ms._extraer_status(html) == "Finished Airing"

    def test_sin_bloque_de_status_devuelve_none(self):
        assert ms._extraer_status("<html><body>nada aquí</body></html>") is None


# ---------- _slug_aproximado ----------

class TestSlugAproximado:

    def test_titulo_none_devuelve_guion_bajo(self):
        assert ms._slug_aproximado(None) == "_"

    def test_titulo_normal_reemplaza_espacios(self):
        assert ms._slug_aproximado("Mato Seihei no Slave") == "Mato_Seihei_no_Slave"

    def test_caracteres_invalidos_consecutivos_colapsan_a_un_guion_bajo(self):
        assert ms._slug_aproximado("Titan: Final Season") == "Titan_Final_Season"

    def test_titulo_solo_simbolos_cae_al_fallback(self):
        assert ms._slug_aproximado("!!!") == "_"


# ---------- _descargar_html ----------

class TestDescargarHtml:

    def test_enriquece_error_http_con_headers_de_diagnostico(self):
        headers = {"CF-RAY": "abc123"}
        error = urllib.error.HTTPError("http://x", 503, "Service Unavailable", headers, None)
        with patch("mal_scraper.urllib.request.urlopen", side_effect=error):
            try:
                ms._descargar_html(12345)
                assert False, "debería haber lanzado HTTPError"
            except urllib.error.HTTPError as exc:
                assert "CF-RAY: abc123" in str(exc)


# ---------- obtener_pagina_mal ----------

_HTML_EJEMPLO = """
<div class="theme-songs js-theme-songs opnening">
<table><tr>
<td>
"Yume no Ito (夢の糸)"
<span class="theme-song-artist"> by Artista A</span>
<span class="theme-song-episode">(eps 1-12)</span>
</td>
</tr></table>
</div>
<div class="spaceit_pad">
<span class="dark_text">Status:</span>
Finished Airing
</div>
"""


class TestObtenerPaginaMal:

    def test_cache_hit_no_descarga_nada(self):
        cacheado = {
            "temas": [{"tipo": "OP", "secuencia": 1, "titulo_cancion": "X",
                       "artista": "Y", "episodios_texto": "1-12"}],
            "status": "Finished Airing",
        }
        with patch("mal_scraper.cache_jikan.obtener", return_value=cacheado), \
             patch("mal_scraper._descargar_html_con_throttle") as mock_descarga:
            resultado = ms.obtener_pagina_mal(123, anime_terminado=True)

        mock_descarga.assert_not_called()
        assert resultado.status == "Finished Airing"
        assert resultado.temas[0].tipo == TipoTema.OP
        assert resultado.temas[0].titulo_cancion == "X"

    def test_cache_miss_descarga_parsea_y_guarda(self):
        with patch("mal_scraper.cache_jikan.obtener", return_value=None), \
             patch("mal_scraper._descargar_html_con_throttle", return_value=_HTML_EJEMPLO) as mock_descarga, \
             patch("mal_scraper.cache_jikan.guardar") as mock_guardar:
            resultado = ms.obtener_pagina_mal(123, anime_terminado=True)

        mock_descarga.assert_called_once_with(123, None)
        assert resultado.status == "Finished Airing"
        assert resultado.temas[0].titulo_cancion == "Yume no Ito (夢の糸)"
        mock_guardar.assert_called_once()
        seccion, clave, valor = mock_guardar.call_args[0]
        assert seccion == "pagina_mal"
        assert clave == 123
        assert valor["status"] == "Finished Airing"

    def test_anime_no_terminado_no_toca_el_cache(self):
        with patch("mal_scraper.cache_jikan.obtener") as mock_obtener, \
             patch("mal_scraper._descargar_html_con_throttle", return_value=_HTML_EJEMPLO), \
             patch("mal_scraper.cache_jikan.guardar") as mock_guardar:
            ms.obtener_pagina_mal(123, anime_terminado=False)

        mock_obtener.assert_not_called()
        mock_guardar.assert_not_called()


# ---------- obtener_temas_mal ----------

class TestObtenerTemasMal:

    def test_cache_hit_no_descarga_nada(self):
        cacheado = [{"tipo": "ED", "secuencia": 1, "titulo_cancion": "X",
                     "artista": "Y", "episodios_texto": ""}]
        with patch("mal_scraper.cache_jikan.obtener", return_value=cacheado), \
             patch("mal_scraper._descargar_html_con_throttle") as mock_descarga:
            resultado = ms.obtener_temas_mal(123, anime_terminado=True)

        mock_descarga.assert_not_called()
        assert len(resultado) == 1
        assert resultado[0].tipo == TipoTema.ED

    def test_cache_miss_descarga_parsea_y_guarda(self):
        with patch("mal_scraper.cache_jikan.obtener", return_value=None), \
             patch("mal_scraper._descargar_html_con_throttle", return_value=_HTML_EJEMPLO), \
             patch("mal_scraper.cache_jikan.guardar") as mock_guardar:
            resultado = ms.obtener_temas_mal(123, anime_terminado=True)

        assert len(resultado) == 1
        assert resultado[0].titulo_cancion == "Yume no Ito (夢の糸)"
        mock_guardar.assert_called_once_with("temas_mal", 123, resultado)

    def test_anime_no_terminado_no_toca_el_cache(self):
        with patch("mal_scraper.cache_jikan.obtener") as mock_obtener, \
             patch("mal_scraper._descargar_html_con_throttle", return_value=_HTML_EJEMPLO), \
             patch("mal_scraper.cache_jikan.guardar") as mock_guardar:
            ms.obtener_temas_mal(123, anime_terminado=False)

        mock_obtener.assert_not_called()
        mock_guardar.assert_not_called()
