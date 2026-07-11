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
- limpiar_expirados() EXCLUYE a "temporada_completa_mal" de lo que borra
  del disco (ver SECCIONES_EXCLUIDAS_DE_LIMPIEZA), para que ese fallback
  siga teniendo algo que servir sin importar cuánto tiempo pase entre
  sesiones — el resto de las secciones sigue limpiándose igual que antes.
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

    def test_no_elimina_entradas_vencidas_de_temporada_completa_mal(self):
        # temporada_completa_mal está EXCLUIDA de la limpieza a propósito
        # (ver SECCIONES_EXCLUIDAS_DE_LIMPIEZA): el fallback de caché
        # vencido de detectar_animes_faltantes_en_at depende de que estas
        # entradas sobrevivan en disco. 100 días -- bien por encima de
        # DIAS_EXPIRACION -- para dejar claro que no es un umbral distinto,
        # es una exclusión total de la sección.
        fecha_muy_vieja = (datetime.date.today() - datetime.timedelta(days=100)).isoformat()
        data = {
            "info_mal": {},
            "temporada_completa_mal": {
                "2025_winter": {"fecha": fecha_muy_vieja, "valor": [{"mal_id": 1}]},
            },
            "temas_mal": {},
            "pagina_mal": {},
        }
        with patch("cache_jikan._cargar_archivo", return_value=data), \
             patch("cache_jikan._guardar_archivo") as mock_guardar:
            eliminadas = cache_jikan.limpiar_expirados()

        assert eliminadas == 0
        assert "2025_winter" in data["temporada_completa_mal"]
        mock_guardar.assert_not_called()

    def test_sigue_eliminando_entradas_vencidas_de_info_mal_y_temas_mal(self):
        # No-regresión: el resto de las secciones se sigue limpiando
        # exactamente igual que antes de excluir temporada_completa_mal.
        data = {
            "info_mal": {
                "111": {"fecha": _fecha_vencida(), "valor": {"status": "Finished Airing"}},
            },
            "temporada_completa_mal": {},
            "temas_mal": {
                "222": {"fecha": _fecha_vencida(), "valor": []},
            },
            "pagina_mal": {},
        }
        with patch("cache_jikan._cargar_archivo", return_value=data), \
             patch("cache_jikan._guardar_archivo") as mock_guardar:
            eliminadas = cache_jikan.limpiar_expirados()

        assert eliminadas == 2
        assert "111" not in data["info_mal"]
        assert "222" not in data["temas_mal"]
        mock_guardar.assert_called_once_with(data)

    def test_mezcla_de_secciones_solo_temporada_completa_mal_sobrevive(self):
        # Las 4 secciones con una entrada vencida cada una a la vez: solo
        # temporada_completa_mal debe quedar intacta.
        data = {
            "info_mal": {"111": {"fecha": _fecha_vencida(), "valor": {}}},
            "temporada_completa_mal": {"2025_winter": {"fecha": _fecha_vencida(), "valor": []}},
            "temas_mal": {"222": {"fecha": _fecha_vencida(), "valor": []}},
            "pagina_mal": {"333": {"fecha": _fecha_vencida(), "valor": {}}},
        }
        with patch("cache_jikan._cargar_archivo", return_value=data), \
             patch("cache_jikan._guardar_archivo"):
            eliminadas = cache_jikan.limpiar_expirados()

        assert eliminadas == 3
        assert "2025_winter" in data["temporada_completa_mal"]
        assert "111" not in data["info_mal"]
        assert "222" not in data["temas_mal"]
        assert "333" not in data["pagina_mal"]


# ---------- obtener(): comportamiento sin cambios para temporada_completa_mal ----------

class TestObtenerTemporadaCompletaMalEntradaVencida:
    """
    Este cambio es SOLO sobre qué borra limpiar_expirados() del disco:
    obtener() (la lectura estricta normal) debe seguir tratando una
    entrada vencida de "temporada_completa_mal" exactamente igual que
    antes -- como si no existiera -- aunque ya no se elimine físicamente.
    """

    def test_obtener_devuelve_none_para_entrada_vencida_aunque_siga_en_disco(self):
        dias_vencidos = cache_jikan.DIAS_EXPIRACION + 5
        fecha = (datetime.date.today() - datetime.timedelta(days=dias_vencidos)).isoformat()
        data = {
            "temporada_completa_mal": {
                "2026_winter": {"fecha": fecha, "valor": [{"mal_id": 1}]},
            },
        }
        with patch("cache_jikan._cargar_archivo", return_value=data):
            resultado = cache_jikan.obtener("temporada_completa_mal", "2026_winter")

        assert resultado is None


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
