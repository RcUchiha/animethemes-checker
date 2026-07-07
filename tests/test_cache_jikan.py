"""
Tests unitarios de cache_jikan.py.

Mockea el archivo en disco (`_cargar_archivo`/`_guardar_archivo`) para no
tocar cache_jikan.json real durante los tests.

Caso cubierto: regresión del bug donde la sección "pagina_mal" (usada por
mal_scraper.obtener_pagina_mal) no estaba en la lista hardcodeada de
secciones conocidas de limpiar_expirados(), así que sus entradas vencidas
nunca se eliminaban. Ver SECCIONES_CONOCIDAS en el módulo.
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
