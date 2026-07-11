"""
Tests unitarios de anilist_client.py.

Mockea `_consultar_pagina` (el único punto que toca red) — nada de estos
tests hace una llamada HTTP real. Casos cubiertos según el docstring del
módulo:
- paginación hasta agotar pageInfo.hasNextPage.
- mapeo de status y format de AniList al vocabulario de MAL/Jikan.
- animes sin idMal se excluyen del resultado pero se cuentan aparte.
- season se pasa en MAYÚSCULAS al enum MediaSeason de AniList.
- un fallo en _consultar_pagina se propaga tal cual (no hay fallback
  dentro de este módulo).
"""

import json
from unittest.mock import MagicMock, patch

import pytest

import anilist_client as al
import jikan_client as jc


def _media(idMal=1, romaji="Anime de Prueba", status="RELEASING", format_="TV"):
    return {
        "idMal": idMal,
        "title": {"romaji": romaji},
        "status": status,
        "format": format_,
    }


def _pagina(media_list, has_next_page=False):
    return {
        "data": {
            "Page": {
                "pageInfo": {"hasNextPage": has_next_page},
                "media": media_list,
            }
        }
    }


class TestObtenerTemporadaCompletaAnilist:

    def test_pagina_hasta_agotar_resultados(self):
        pagina_1 = _pagina([_media(idMal=1, romaji="Anime A")], has_next_page=True)
        pagina_2 = _pagina([_media(idMal=2, romaji="Anime B")], has_next_page=False)

        with patch("anilist_client._consultar_pagina", side_effect=[pagina_1, pagina_2]) as mock_consultar:
            animes, omitidos = al.obtener_temporada_completa_anilist(2026, "winter")

        assert [a.mal_id for a in animes] == [1, 2]
        assert omitidos == 0
        assert mock_consultar.call_count == 2
        # segunda llamada debe pedir pagina=2
        assert mock_consultar.call_args_list[0].args[2] == 1
        assert mock_consultar.call_args_list[1].args[2] == 2

    def test_season_se_pasa_en_mayusculas(self):
        pagina = _pagina([], has_next_page=False)
        with patch("anilist_client._consultar_pagina", return_value=pagina) as mock_consultar:
            al.obtener_temporada_completa_anilist(2026, "winter")

        mock_consultar.assert_called_once_with("WINTER", 2026, 1)

    def test_animes_sin_idmal_se_excluyen_pero_se_cuentan(self):
        media_list = [
            _media(idMal=1, romaji="Con vinculo"),
            _media(idMal=None, romaji="Sin vinculo 1"),
            _media(idMal=None, romaji="Sin vinculo 2"),
        ]
        pagina = _pagina(media_list, has_next_page=False)
        with patch("anilist_client._consultar_pagina", return_value=pagina):
            animes, omitidos = al.obtener_temporada_completa_anilist(2026, "winter")

        assert [a.mal_id for a in animes] == [1]
        assert omitidos == 2

    @pytest.mark.parametrize("status_anilist,status_mal_esperado", [
        ("RELEASING", "Currently Airing"),
        ("FINISHED", "Finished Airing"),
        ("NOT_YET_RELEASED", "Not yet aired"),
        ("CANCELLED", None),
        ("HIATUS", None),
        ("ALGO_DESCONOCIDO", None),
    ])
    def test_mapeo_de_status(self, status_anilist, status_mal_esperado):
        pagina = _pagina([_media(status=status_anilist)], has_next_page=False)
        with patch("anilist_client._consultar_pagina", return_value=pagina):
            animes, _ = al.obtener_temporada_completa_anilist(2026, "winter")

        assert animes[0].status == status_mal_esperado

    @pytest.mark.parametrize("format_anilist,tipo_mal_esperado", [
        ("TV", "TV"),
        ("TV_SHORT", "TV"),
        ("MOVIE", "Movie"),
        ("OVA", "OVA"),
        ("ONA", "ONA"),
        ("SPECIAL", "Special"),
        ("MUSIC", "Music"),
        ("ALGO_DESCONOCIDO", None),
    ])
    def test_mapeo_de_formato_a_tipo(self, format_anilist, tipo_mal_esperado):
        pagina = _pagina([_media(format_=format_anilist)], has_next_page=False)
        with patch("anilist_client._consultar_pagina", return_value=pagina):
            animes, _ = al.obtener_temporada_completa_anilist(2026, "winter")

        assert animes[0].tipo == tipo_mal_esperado

    def test_titulo_usa_romaji(self):
        pagina = _pagina([_media(romaji="Mato Seihei no Slave")], has_next_page=False)
        with patch("anilist_client._consultar_pagina", return_value=pagina):
            animes, _ = al.obtener_temporada_completa_anilist(2026, "winter")

        assert animes[0].titulo == "Mato Seihei no Slave"

    def test_devuelve_animedetemporadamal_reusando_la_dataclass_de_jikan_client(self):
        pagina = _pagina([_media(idMal=42)], has_next_page=False)
        with patch("anilist_client._consultar_pagina", return_value=pagina):
            animes, _ = al.obtener_temporada_completa_anilist(2026, "winter")

        assert isinstance(animes[0], jc.AnimeDeTemporadaMAL)

    def test_fallo_en_consultar_pagina_se_propaga_sin_fallback(self):
        error = ConnectionError("Remote end closed connection without response")
        with patch("anilist_client._consultar_pagina", side_effect=error):
            with pytest.raises(ConnectionError):
                al.obtener_temporada_completa_anilist(2026, "winter")


# ---------- _consultar_pagina: forma real del POST ----------

class TestConsultarPagina:

    def test_hace_post_con_query_y_variables_correctas(self):
        respuesta = _pagina([], has_next_page=False)
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(respuesta).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp

        with patch("anilist_client.urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
            resultado = al._consultar_pagina("WINTER", 2026, 1)

        assert resultado == respuesta
        req = mock_urlopen.call_args.args[0]
        assert req.full_url == al.API_URL
        assert req.get_method() == "POST"
        assert req.headers.get("Content-type") == "application/json"

        body = json.loads(req.data.decode("utf-8"))
        assert body["variables"] == {
            "season": "WINTER", "seasonYear": 2026, "page": 1, "perPage": al.POR_PAGINA,
        }
        # paginado vía Page { media(...) }, no una consulta Media(id: ...) de un solo resultado.
        assert "Page(" in body["query"]
        assert "media(" in body["query"]
