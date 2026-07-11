"""
Tests unitarios de jikan_client.py.

Mockea `_get_json`, el throttle (time.monotonic/time.sleep) y cache_jikan
(obtener/guardar) — nada de esto debe tocar red ni disco real durante los
tests. Casos cubiertos según los docstrings del módulo:
- el throttle _esperar_turno es GLOBAL (un reloj compartido) y debe
  respetar PAUSA_ENTRE_LLAMADAS entre llamadas reales, sin importar qué
  hilo llame.
- obtener_info_mal devuelve None ante un 404 de Jikan (mal_id no
  encontrado) — es el bug conocido documentado en el módulo.
- obtener_temporada_completa_mal deduplica mal_ids repetidos entre páginas
  de /seasons/{year}/{season}, conservando la primera ocurrencia.
- obtener_temporada_completa_mal_desde_cache_vencido NUNCA hace red (ni
  siquiera llama a _get_json) — solo lee cache_jikan.obtener_ignorando_expiracion.
"""

import urllib.error
from unittest.mock import patch

import pytest

import jikan_client as jc


# ---------- _esperar_turno ----------

@pytest.fixture(autouse=True)
def _reset_throttle_global():
    # _ultima_llamada es un dict global compartido entre tests; sin resetear
    # esto, el orden de ejecución de los tests podría filtrarse entre casos.
    momento_previo = jc._ultima_llamada["momento"]
    jc._ultima_llamada["momento"] = 0.0
    yield
    jc._ultima_llamada["momento"] = momento_previo


class TestEsperarTurno:

    def test_espera_lo_que_falta_si_la_llamada_previa_fue_reciente(self):
        jc._ultima_llamada["momento"] = 100.0
        with patch("jikan_client.time.monotonic", side_effect=[101.0, 105.0]) as mock_mono, \
             patch("jikan_client.time.sleep") as mock_sleep:
            jc._esperar_turno()

        # espera = 100.0 + 4.0 - 101.0 = 3.0
        mock_sleep.assert_called_once_with(3.0)
        assert mock_mono.call_count == 2
        assert jc._ultima_llamada["momento"] == 105.0

    def test_no_espera_si_ya_paso_suficiente_tiempo(self):
        jc._ultima_llamada["momento"] = 0.0
        with patch("jikan_client.time.monotonic", side_effect=[10.0, 10.0]), \
             patch("jikan_client.time.sleep") as mock_sleep:
            jc._esperar_turno()

        mock_sleep.assert_not_called()
        assert jc._ultima_llamada["momento"] == 10.0


# ---------- obtener_info_mal ----------

class TestObtenerInfoMal:

    def test_404_devuelve_none_sin_cachear(self):
        error = urllib.error.HTTPError("http://x", 404, "Not Found", None, None)
        with patch("jikan_client.cache_jikan.obtener", return_value=None), \
             patch("jikan_client._esperar_turno"), \
             patch("jikan_client._get_json", side_effect=error), \
             patch("jikan_client.cache_jikan.guardar") as mock_guardar:
            resultado = jc.obtener_info_mal(12345)

        assert resultado is None
        mock_guardar.assert_not_called()

    def test_error_http_distinto_de_404_se_propaga(self):
        error = urllib.error.HTTPError("http://x", 500, "Server Error", None, None)
        with patch("jikan_client.cache_jikan.obtener", return_value=None), \
             patch("jikan_client._esperar_turno"), \
             patch("jikan_client._get_json", side_effect=error):
            with pytest.raises(urllib.error.HTTPError):
                jc.obtener_info_mal(12345)

    def test_respuesta_valida_no_terminada_no_se_cachea(self):
        data = {"data": {"status": "Currently Airing", "episodes": None, "title": "Anime X"}}
        with patch("jikan_client.cache_jikan.obtener", return_value=None), \
             patch("jikan_client._esperar_turno"), \
             patch("jikan_client._get_json", return_value=data), \
             patch("jikan_client.cache_jikan.guardar") as mock_guardar:
            resultado = jc.obtener_info_mal(555)

        assert resultado == jc.InfoMAL(mal_id=555, status="Currently Airing",
                                        episodios_totales=None, titulo="Anime X")
        mock_guardar.assert_not_called()

    def test_respuesta_finished_airing_se_cachea(self):
        # Case-insensitive y con espacios: ver esta_terminado().
        data = {"data": {"status": "Finished Airing", "episodes": 12, "title": "Anime Y"}}
        with patch("jikan_client.cache_jikan.obtener", return_value=None), \
             patch("jikan_client._esperar_turno"), \
             patch("jikan_client._get_json", return_value=data), \
             patch("jikan_client.cache_jikan.guardar") as mock_guardar:
            resultado = jc.obtener_info_mal(777)

        mock_guardar.assert_called_once_with("info_mal", 777, resultado)
        assert resultado.status == "Finished Airing"

    def test_cache_hit_no_llama_a_la_red_ni_al_throttle(self):
        cacheado = {"mal_id": 999, "status": "Finished Airing",
                    "episodios_totales": 24, "titulo": "Anime Cacheado"}
        with patch("jikan_client.cache_jikan.obtener", return_value=cacheado), \
             patch("jikan_client._esperar_turno") as mock_esperar, \
             patch("jikan_client._get_json") as mock_get:
            resultado = jc.obtener_info_mal(999)

        assert resultado == jc.InfoMAL(**cacheado)
        mock_esperar.assert_not_called()
        mock_get.assert_not_called()


# ---------- esta_terminado ----------

class TestEstaTerminado:

    def test_finished_airing_case_insensitive_y_con_espacios(self):
        info = jc.InfoMAL(mal_id=1, status="  Finished Airing  ", episodios_totales=12, titulo="T")
        assert jc.esta_terminado(info) is True

    def test_status_distinto_no_esta_terminado(self):
        info = jc.InfoMAL(mal_id=1, status="Currently Airing", episodios_totales=None, titulo="T")
        assert jc.esta_terminado(info) is False

    def test_info_none_o_status_none_no_esta_terminado(self):
        assert jc.esta_terminado(None) is False
        assert jc.esta_terminado(jc.InfoMAL(mal_id=1, status=None, episodios_totales=None, titulo="T")) is False


# ---------- obtener_temporada_completa_mal ----------

class TestObtenerTemporadaCompletaMal:

    def test_cache_hit_devuelve_sin_llamar_a_la_red(self):
        cacheado = [
            {"mal_id": 1, "titulo": "Anime A", "status": "Finished Airing", "tipo": "TV"},
        ]
        with patch("jikan_client.cache_jikan.obtener", return_value=cacheado), \
             patch("jikan_client._get_json") as mock_get:
            resultado = jc.obtener_temporada_completa_mal(2026, "spring")

        assert resultado == [jc.AnimeDeTemporadaMAL(**cacheado[0])]
        mock_get.assert_not_called()

    def test_dedup_de_mal_ids_repetidos_conserva_primera_ocurrencia(self):
        # Comportamiento real observado: el mismo mal_id puede repetirse
        # entre páginas de /seasons/{year}/{season}.
        pagina_unica = {
            "data": [
                {"mal_id": 1, "title": "Anime A (primera vez)", "status": "Finished Airing", "type": "TV"},
                {"mal_id": 2, "title": "Anime B", "status": "Finished Airing", "type": "TV"},
                {"mal_id": 1, "title": "Anime A (repetido)", "status": "Finished Airing", "type": "TV"},
            ],
            "pagination": {"has_next_page": False},
        }
        with patch("jikan_client.cache_jikan.obtener", return_value=None), \
             patch("jikan_client._get_json", return_value=pagina_unica), \
             patch("jikan_client.cache_jikan.guardar") as mock_guardar:
            resultado = jc.obtener_temporada_completa_mal(2026, "spring")

        assert [a.mal_id for a in resultado] == [1, 2]
        assert resultado[0].titulo == "Anime A (primera vez)"
        mock_guardar.assert_called_once()
        _, clave_cache, valor_guardado = mock_guardar.call_args[0]
        assert clave_cache == "2026_spring"
        assert [a.mal_id for a in valor_guardado] == [1, 2]

    def test_pagina_hasta_agotar_resultados_con_pausa_entre_paginas(self):
        pagina_1 = {
            "data": [{"mal_id": 1, "title": "Anime A", "status": "Finished Airing", "type": "TV"}],
            "pagination": {"has_next_page": True},
        }
        pagina_2 = {
            "data": [{"mal_id": 2, "title": "Anime B", "status": "Finished Airing", "type": "TV"}],
            "pagination": {"has_next_page": False},
        }
        with patch("jikan_client.cache_jikan.obtener", return_value=None), \
             patch("jikan_client._get_json", side_effect=[pagina_1, pagina_2]) as mock_get, \
             patch("jikan_client._esperar_turno") as mock_esperar_turno, \
             patch("jikan_client.time.sleep") as mock_sleep, \
             patch("jikan_client.cache_jikan.guardar"):
            resultado = jc.obtener_temporada_completa_mal(2026, "spring", pausa_entre_paginas=1.0)

        assert [a.mal_id for a in resultado] == [1, 2]
        assert mock_get.call_count == 2
        urls_llamadas = [call.args[0] for call in mock_get.call_args_list]
        assert "page=1" in urls_llamadas[0]
        assert "page=2" in urls_llamadas[1]
        # el throttle global debe respetarse en cada página, además de la
        # pausa propia de esta función entre páginas (ver docstring).
        assert mock_esperar_turno.call_count == 2
        mock_sleep.assert_called_once_with(1.0)


# ---------- obtener_temporada_completa_mal_desde_cache_vencido ----------

class TestObtenerTemporadaCompletaMalDesdeCacheVencido:

    def test_cache_vencido_presente_devuelve_animes_y_antiguedad(self):
        cacheado = [
            {"mal_id": 1, "titulo": "Anime A", "status": "Finished Airing", "tipo": "TV"},
        ]
        with patch("jikan_client.cache_jikan.obtener_ignorando_expiracion",
                    return_value=(cacheado, 20)) as mock_obtener, \
             patch("jikan_client._get_json") as mock_get:
            resultado = jc.obtener_temporada_completa_mal_desde_cache_vencido(2026, "winter")

        assert resultado == ([jc.AnimeDeTemporadaMAL(**cacheado[0])], 20)
        mock_obtener.assert_called_once_with("temporada_completa_mal", "2026_winter")
        mock_get.assert_not_called()

    def test_cache_vigente_presente_tambien_devuelve_animes_y_antiguedad(self):
        # obtener_ignorando_expiracion no distingue vigente/vencido -- esta
        # función tampoco: simplemente pasa lo que reciba.
        cacheado = [
            {"mal_id": 2, "titulo": "Anime B", "status": "Finished Airing", "tipo": "TV"},
        ]
        with patch("jikan_client.cache_jikan.obtener_ignorando_expiracion",
                    return_value=(cacheado, 0)), \
             patch("jikan_client._get_json") as mock_get:
            resultado = jc.obtener_temporada_completa_mal_desde_cache_vencido(2026, "winter")

        assert resultado == ([jc.AnimeDeTemporadaMAL(**cacheado[0])], 0)
        mock_get.assert_not_called()

    def test_sin_cache_en_absoluto_devuelve_none(self):
        with patch("jikan_client.cache_jikan.obtener_ignorando_expiracion", return_value=None), \
             patch("jikan_client._get_json") as mock_get:
            resultado = jc.obtener_temporada_completa_mal_desde_cache_vencido(2026, "winter")

        assert resultado is None
        mock_get.assert_not_called()

    def test_clave_de_cache_usa_year_y_season_en_minuscula(self):
        with patch("jikan_client.cache_jikan.obtener_ignorando_expiracion",
                    return_value=None) as mock_obtener:
            jc.obtener_temporada_completa_mal_desde_cache_vencido(2026, "WINTER")

        mock_obtener.assert_called_once_with("temporada_completa_mal", "2026_winter")
