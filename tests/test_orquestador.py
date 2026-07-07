"""
Tests unitarios de la alerta canario de posible cambio de HTML en MAL
(issue #3) en orquestador.py.

mal_scraper.py no lanza ninguna excepción si deja de reconocer el HTML de
MAL (ver su "ADVERTENCIA DE FRAGILIDAD"): simplemente devuelve una lista de
temas vacía. Por eso _procesar_un_anime expone un tercer valor de retorno
(temas_mal_vacio) que ResultadoEscaneo.alerta_posible_cambio_html_mal usa
para detectar si eso está pasando de forma masiva en el escaneo actual.

_hay_alerta_canario_mal y la property de ResultadoEscaneo son puras (no
tocan red). _procesar_un_anime sí hace red (a través de
ms.obtener_pagina_mal), así que se mockea igual que en el resto de la
suite — no se toca el parser de mal_scraper.py en absoluto.
"""

from unittest.mock import patch

import animethemes_client as ac
import mal_scraper as ms
import orquestador as orq
from modelos import TemaMAL, TipoTema


def _anime_completo(mal_id=1, temas=None) -> ac.AnimeCompleto:
    return ac.AnimeCompleto(
        id=1, name="Anime de Prueba", slug="anime_de_prueba",
        year=2026, season="Spring", mal_id=mal_id, temas=temas or [],
    )


# ---------- _hay_alerta_canario_mal ----------

class TestHayAlertaCanarioMal:

    def test_sin_animes_evaluados_no_alerta(self):
        assert orq._hay_alerta_canario_mal(0, 0) is False

    def test_por_debajo_del_minimo_de_muestra_no_alerta_aunque_todo_este_vacio(self):
        # Con muy pocos animes evaluados, un solo caso legítimamente sin
        # temas en MAL ya superaría el umbral — no queremos falsos
        # positivos por pura casualidad estadística en muestras chicas.
        n = orq.MINIMO_ANIMES_PARA_CANARIO - 1
        assert orq._hay_alerta_canario_mal(n, n) is False

    def test_justo_en_el_minimo_con_todo_vacio_si_alerta(self):
        n = orq.MINIMO_ANIMES_PARA_CANARIO
        assert orq._hay_alerta_canario_mal(n, n) is True

    def test_fraccion_baja_es_normal_no_alerta(self):
        # 2 de 10 vacíos (20%) es el tipo de caso normal (specials/OVAs sin
        # temas documentados), no una señal de cambio de HTML.
        assert orq._hay_alerta_canario_mal(10, 2) is False

    def test_fraccion_en_el_umbral_exacto_alerta(self):
        total = 10
        vacios = int(total * orq.UMBRAL_FRACCION_VACIOS_CANARIO)
        assert orq._hay_alerta_canario_mal(total, vacios) is True

    def test_fraccion_justo_debajo_del_umbral_no_alerta(self):
        assert orq._hay_alerta_canario_mal(10, 7) is False  # 70% < 80%

    def test_fraccion_alta_con_muestra_grande_alerta(self):
        assert orq._hay_alerta_canario_mal(50, 48) is True


# ---------- ResultadoEscaneo.alerta_posible_cambio_html_mal ----------

class TestResultadoEscaneoAlertaCanario:

    def test_property_delega_en_hay_alerta_canario_mal(self):
        resultado = orq.ResultadoEscaneo(
            total_finished_airing_evaluados=20,
            total_finished_airing_con_temas_mal_vacios=18,
        )
        assert resultado.alerta_posible_cambio_html_mal is True

    def test_property_false_por_defecto_en_resultado_recien_creado(self):
        assert orq.ResultadoEscaneo().alerta_posible_cambio_html_mal is False

    def test_property_false_con_fraccion_normal(self):
        resultado = orq.ResultadoEscaneo(
            total_finished_airing_evaluados=30,
            total_finished_airing_con_temas_mal_vacios=3,
        )
        assert resultado.alerta_posible_cambio_html_mal is False


# ---------- _procesar_un_anime: origen del tercer valor (temas_mal_vacio) ----------

class TestProcesarUnAnimeTemasMalVacio:

    def test_sin_mal_id_temas_mal_vacio_es_none(self):
        anime = _anime_completo(mal_id=None)
        resultado, motivo, temas_mal_vacio = orq._procesar_un_anime(anime)
        assert resultado is None
        assert motivo == "sin_mal_id"
        assert temas_mal_vacio is None

    def test_no_terminado_segun_estado_conocido_temas_mal_vacio_es_none(self):
        anime = _anime_completo(mal_id=1)
        resultado, motivo, temas_mal_vacio = orq._procesar_un_anime(
            anime, estado_conocido="Currently Airing"
        )
        assert resultado is None
        assert motivo == "no_terminado"
        assert temas_mal_vacio is None

    def test_no_terminado_por_status_de_la_pagina_temas_mal_vacio_es_none(self):
        anime = _anime_completo(mal_id=1)
        pagina = ms.PaginaMAL(temas=[], status="Currently Airing")
        with patch("orquestador.ms.obtener_pagina_mal", return_value=pagina):
            resultado, motivo, temas_mal_vacio = orq._procesar_un_anime(anime, estado_conocido=None)

        assert resultado is None
        assert motivo == "no_terminado"
        assert temas_mal_vacio is None

    def test_terminado_con_temas_mal_vacios_marca_true(self):
        anime = _anime_completo(mal_id=1)
        pagina_vacia = ms.PaginaMAL(temas=[], status="Finished Airing")
        with patch("orquestador.ms.obtener_pagina_mal", return_value=pagina_vacia):
            resultado, motivo, temas_mal_vacio = orq._procesar_un_anime(
                anime, estado_conocido="Finished Airing"
            )

        assert resultado is not None
        assert motivo is None
        assert temas_mal_vacio is True

    def test_terminado_con_temas_mal_no_vacios_marca_false(self):
        anime = _anime_completo(mal_id=1)
        temas = [TemaMAL(tipo=TipoTema.OP, secuencia=1, titulo_cancion="X",
                          artista="Y", episodios_texto="1-")]
        pagina_con_datos = ms.PaginaMAL(temas=temas, status="Finished Airing")
        with patch("orquestador.ms.obtener_pagina_mal", return_value=pagina_con_datos):
            resultado, motivo, temas_mal_vacio = orq._procesar_un_anime(
                anime, estado_conocido="Finished Airing"
            )

        assert resultado is not None
        assert temas_mal_vacio is False

    def test_camino_de_respaldo_sin_estado_conocido_tambien_marca_vacio(self):
        # Mismo criterio cuando el status se saca directo de la página de
        # MAL (estado_conocido=None), no solo cuando viene del bulk de Jikan.
        anime = _anime_completo(mal_id=1)
        pagina_vacia = ms.PaginaMAL(temas=[], status="Finished Airing")
        with patch("orquestador.ms.obtener_pagina_mal", return_value=pagina_vacia):
            resultado, motivo, temas_mal_vacio = orq._procesar_un_anime(anime, estado_conocido=None)

        assert resultado is not None
        assert temas_mal_vacio is True
