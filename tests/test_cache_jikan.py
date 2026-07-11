"""
Tests unitarios de cache_jikan.py.

Mockea el archivo en disco (`_cargar_archivo`/`_guardar_archivo`) para no
tocar cache_jikan.json real durante los tests.

Casos cubiertos:
- Regresión: la sección "pagina_mal" (usada por mal_scraper.obtener_pagina_mal)
  no estaba en la lista hardcodeada de secciones conocidas de
  limpiar_expirados(), así que sus entradas vencidas nunca se eliminaban.
  Ver SECCIONES_CONOCIDAS en el módulo.
- obtener_ignorando_expiracion: a diferencia de obtener() (que nunca sirve
  una entrada vencida), esta función siempre sirve lo que haya, vigente o
  no, junto con su antigüedad en días — es el fallback de último recurso
  para cuando Jikan falla en vivo (ver jikan_client.py y orquestador.py).
"""

import datetime
from unittest.mock import patch

import cache_jikan


def _fecha_vencida() -> str:
    return (datetime.date.today() - datetime.timedelta(days=cache_jikan.DIAS_EXPIRACION + 1)).isoformat()


class TestLimpiarExpirados:

    def test_elimina_entradas_vencidas_de_pagina_mal(self):
        data = {
            "info_mal": {},
            "temporada_completa_mal": {},
            "temas_mal": {},
            "pagina_mal": {
                "123": {"fecha": _fecha_vencida(), "valor": {"status": "Finished Airing"}},
                "456": {"fecha": datetime.date.today().isoformat(), "valor": {"status": "Finished Airing"}},
            },
        }
        with patch("cache_jikan._cargar_archivo", return_value=data), \
             patch("cache_jikan._guardar_archivo") as mock_guardar:
            eliminadas = cache_jikan.limpiar_expirados()

        assert eliminadas == 1
        assert "123" not in data["pagina_mal"]
        assert "456" in data["pagina_mal"]
        mock_guardar.assert_called_once_with(data)


# ---------- obtener_ignorando_expiracion ----------

class TestObtenerIgnorandoExpiracion:

    def test_entrada_vigente_devuelve_valor_y_antiguedad_en_dias(self):
        data = {
            "temporada_completa_mal": {
                "2026_winter": {"fecha": datetime.date.today().isoformat(), "valor": [{"mal_id": 1}]},
            },
        }
        with patch("cache_jikan._cargar_archivo", return_value=data):
            resultado = cache_jikan.obtener_ignorando_expiracion("temporada_completa_mal", "2026_winter")

        assert resultado == ([{"mal_id": 1}], 0)

    def test_entrada_vencida_se_devuelve_igual_con_su_antiguedad_real(self):
        # A diferencia de obtener(), acá una entrada vencida NO se descarta.
        dias_vencidos = cache_jikan.DIAS_EXPIRACION + 5
        fecha = (datetime.date.today() - datetime.timedelta(days=dias_vencidos)).isoformat()
        data = {
            "temporada_completa_mal": {
                "2026_winter": {"fecha": fecha, "valor": [{"mal_id": 1}]},
            },
        }
        with patch("cache_jikan._cargar_archivo", return_value=data):
            resultado = cache_jikan.obtener_ignorando_expiracion("temporada_completa_mal", "2026_winter")

        assert resultado == ([{"mal_id": 1}], dias_vencidos)

    def test_clave_inexistente_devuelve_none(self):
        data = {"temporada_completa_mal": {}}
        with patch("cache_jikan._cargar_archivo", return_value=data):
            resultado = cache_jikan.obtener_ignorando_expiracion("temporada_completa_mal", "2026_winter")

        assert resultado is None
