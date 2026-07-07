"""
Tests unitarios de animethemes_client.py.

Mockea `_get_json` (nunca golpea la red real) para poder probar el parseo
del JSON de la API y el camino rápido de listado sin depender de que la API
esté arriba. Casos cubiertos según los docstrings del módulo:
- 'sequence' viene null en la API real; el número de secuencia real está en
  el slug del tema (ej. 'OP1'), y 'type' es solo el respaldo si el slug no
  matchea (ver _tipo_y_secuencia_desde_slug).
- /animeyear/{year} con el include completo ya trae todo (temas y mal_id)
  para armar cada AnimeCompleto en una sola llamada (camino rápido, ver
  _obtener_animes_completos_desde_listado).
"""

from unittest.mock import patch

import pytest

import animethemes_client as ac
from modelos import TipoTema


# ---------- _tipo_y_secuencia_desde_slug ----------

class TestTipoYSecuenciaDesdeSlug:

    def test_slug_op_con_numero(self):
        assert ac._tipo_y_secuencia_desde_slug("OP1", "OP") == (TipoTema.OP, 1)

    def test_slug_ed_con_numero_de_dos_digitos(self):
        assert ac._tipo_y_secuencia_desde_slug("ED12", "ED") == (TipoTema.ED, 12)

    def test_slug_con_sufijo_extra_igual_matchea(self):
        # _SLUG_RE acepta texto extra tras el número; el número se sigue
        # leyendo igual.
        assert ac._tipo_y_secuencia_desde_slug("OP1C", "OP") == (TipoTema.OP, 1)

    def test_slug_no_matchea_usa_type_como_respaldo_con_secuencia_1(self):
        # 'sequence' viene null y a veces el slug no tiene el formato
        # esperado: se cae a 'type' y se asume secuencia 1.
        assert ac._tipo_y_secuencia_desde_slug("", "ED") == (TipoTema.ED, 1)
        assert ac._tipo_y_secuencia_desde_slug("algo-raro", "OP") == (TipoTema.OP, 1)

    def test_ni_slug_ni_type_reconocibles_devuelve_none(self):
        assert ac._tipo_y_secuencia_desde_slug("algo-raro", "XX") is None


# ---------- _parsear_temas ----------

class TestParsearTemas:

    def test_theme_completo_con_slug_valido(self):
        themes = [{
            "type": "OP",
            "sequence": None,
            "slug": "OP1",
            "song": {"title": "Yume no Ito", "artists": [{"name": "Artista A"}]},
            "animethemeentries": [
                {"episodes": "1-12", "version": 1, "videos": [{"id": 1}]},
            ],
        }]
        resultado = ac._parsear_temas(themes)

        assert len(resultado) == 1
        tema = resultado[0]
        assert tema.tipo == TipoTema.OP
        assert tema.secuencia == 1
        assert tema.titulo_cancion == "Yume no Ito"
        assert tema.artista == "Artista A"
        assert tema.episodios_texto == "1-12"
        assert tema.version == 1
        assert tema.tiene_video is True

    def test_theme_usa_fallback_a_type_si_slug_no_matchea(self):
        themes = [{
            "type": "ED",
            "sequence": None,
            "slug": "algo-raro",
            "song": {"title": "Titulo", "artists": []},
            "animethemeentries": [{"episodes": "", "version": 1, "videos": []}],
        }]
        resultado = ac._parsear_temas(themes)
        assert len(resultado) == 1
        assert resultado[0].tipo == TipoTema.ED
        assert resultado[0].secuencia == 1

    def test_theme_sin_slug_ni_type_reconocible_se_omite(self):
        # No debe tronar el escaneo completo de la temporada por un tema raro.
        themes = [{
            "type": "XX",
            "slug": "algo-raro",
            "song": {"title": "Fantasma"},
            "animethemeentries": [],
        }]
        assert ac._parsear_temas(themes) == []

    def test_multiples_artistas_se_unen_con_coma(self):
        themes = [{
            "type": "OP", "slug": "OP1",
            "song": {"title": "T", "artists": [{"name": "A"}, {"name": "B"}]},
            "animethemeentries": [{"episodes": "1-", "version": 1, "videos": []}],
        }]
        resultado = ac._parsear_temas(themes)
        assert resultado[0].artista == "A, B"

    def test_sin_song_deja_titulo_y_artista_vacios(self):
        # Requiere que el include haya pedido animethemes.song.artists; si no,
        # 'song' viene ausente del todo (ver docstring de _parsear_temas).
        themes = [{
            "type": "OP", "slug": "OP1",
            "animethemeentries": [{"episodes": "1-", "version": 1, "videos": []}],
        }]
        resultado = ac._parsear_temas(themes)
        assert resultado[0].titulo_cancion == ""
        assert resultado[0].artista == ""

    def test_sin_entries_genera_un_solo_tema_sin_episodios(self):
        themes = [{
            "type": "OP", "slug": "OP1",
            "song": {"title": "T", "artists": []},
            "animethemeentries": [],
        }]
        resultado = ac._parsear_temas(themes)
        assert len(resultado) == 1
        assert resultado[0].episodios_texto == ""

    def test_multiples_entries_generan_multiples_temas(self):
        themes = [{
            "type": "OP", "slug": "OP1",
            "song": {"title": "T", "artists": []},
            "animethemeentries": [
                {"episodes": "1-6", "version": 1, "videos": [{"id": 1}]},
                {"episodes": "7-12", "version": 2, "videos": []},
            ],
        }]
        resultado = ac._parsear_temas(themes)
        assert len(resultado) == 2
        assert resultado[0].tiene_video is True
        assert resultado[1].tiene_video is False
        assert resultado[1].version == 2

    def test_version_ausente_o_falsy_usa_1_por_defecto(self):
        themes = [{
            "type": "OP", "slug": "OP1",
            "song": {"title": "T", "artists": []},
            "animethemeentries": [{"episodes": "1-", "version": None, "videos": []}],
        }]
        resultado = ac._parsear_temas(themes)
        assert resultado[0].version == 1


# ---------- _extraer_mal_id ----------

class TestExtraerMalId:

    def test_encuentra_myanimelist_entre_varios_recursos(self):
        resources = [
            {"site": "Anilist", "external_id": 999},
            {"site": "MyAnimeList", "external_id": 12345},
        ]
        assert ac._extraer_mal_id(resources) == 12345

    def test_sin_myanimelist_devuelve_none(self):
        assert ac._extraer_mal_id([{"site": "Anilist", "external_id": 999}]) is None

    def test_lista_vacia_o_none_devuelve_none(self):
        assert ac._extraer_mal_id([]) is None
        assert ac._extraer_mal_id(None) is None


# ---------- _obtener_animes_completos_desde_listado (camino rápido) ----------

def _json_temporada():
    return {
        "spring": [
            {
                "id": 1, "name": "Anime Uno", "slug": "anime_uno",
                "year": 2026, "season": "Spring",
                "resources": [{"site": "MyAnimeList", "external_id": 111}],
                "animethemes": [{
                    "type": "OP", "slug": "OP1",
                    "song": {"title": "Cancion 1", "artists": []},
                    "animethemeentries": [{"episodes": "1-", "version": 1, "videos": []}],
                }],
            },
            {
                "id": 2, "name": "Anime Dos", "slug": "anime_dos",
                "year": 2026, "season": "Spring",
                "resources": [],
                "animethemes": [],
            },
        ],
        # temporada distinta: no debe aparecer en el resultado al pedir "spring"
        "winter": [{"id": 99, "name": "No debe salir", "slug": "x", "resources": [], "animethemes": []}],
    }


class TestObtenerAnimesCompletosDesdeListado:

    def test_una_sola_llamada_trae_todo_y_respeta_temporada(self):
        with patch("animethemes_client._get_json", return_value=_json_temporada()) as mock_get:
            resultado = ac._obtener_animes_completos_desde_listado(2026, "spring")

        mock_get.assert_called_once()
        assert len(resultado) == 2
        assert resultado[0].name == "Anime Uno"
        assert resultado[0].mal_id == 111
        assert resultado[0].temas[0].titulo_cancion == "Cancion 1"
        assert resultado[1].mal_id is None
        assert resultado[1].temas == []

    def test_conserva_el_orden_del_listado(self):
        # A diferencia del camino de respaldo con hilos, aquí el orden del
        # listado original se conserva (ver docstring de la función).
        with patch("animethemes_client._get_json", return_value=_json_temporada()):
            resultado = ac._obtener_animes_completos_desde_listado(2026, "spring")
        assert [a.id for a in resultado] == [1, 2]

    def test_llama_progreso_callback_por_cada_anime(self):
        llamadas = []
        with patch("animethemes_client._get_json", return_value=_json_temporada()):
            ac._obtener_animes_completos_desde_listado(
                2026, "spring",
                progreso_callback=lambda i, total, name: llamadas.append((i, total, name)),
            )
        assert llamadas == [(1, 2, "Anime Uno"), (2, 2, "Anime Dos")]

    def test_temporada_invalida_lanza_value_error_sin_llamar_a_la_red(self):
        with patch("animethemes_client._get_json") as mock_get:
            with pytest.raises(ValueError):
                ac._obtener_animes_completos_desde_listado(2026, "invierno-mal-escrito")
        mock_get.assert_not_called()
