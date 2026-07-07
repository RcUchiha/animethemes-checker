"""
Tests unitarios de comparador.py.

comparador.py no toca red ni disco (solo compara listas de TemaAT/TemaMAL ya
cargadas en memoria), así que estos tests no mockean nada — son puros.

La única dependencia externa es i18n.t(), que sí lee config.json al importar
el módulo. Para no depender de qué idioma quedó guardado en la máquina que
corre los tests, se fija _idioma_actual a "es" directamente (sin pasar por
i18n.cambiar_idioma, que escribiría en disco) y se restaura al terminar.
"""

import math

import pytest

import i18n
import comparador
from modelos import Discrepancia, TemaAT, TemaMAL, TipoDiscrepancia, TipoTema


@pytest.fixture(autouse=True)
def _idioma_fijo_es():
    idioma_previo = i18n._idioma_actual
    i18n._idioma_actual = "es"
    yield
    i18n._idioma_actual = idioma_previo


def _tema_at(tipo=TipoTema.OP, secuencia=1, titulo="Titulo", artista="Artista",
             episodios="1-", version=1, tiene_video=True) -> TemaAT:
    return TemaAT(
        tipo=tipo,
        secuencia=secuencia,
        titulo_cancion=titulo,
        artista=artista,
        episodios_texto=episodios,
        version=version,
        tiene_video=tiene_video,
    )


def _tema_mal(tipo=TipoTema.OP, secuencia=1, titulo="Titulo", artista="Artista",
              episodios="eps 1-12") -> TemaMAL:
    return TemaMAL(
        tipo=tipo,
        secuencia=secuencia,
        titulo_cancion=titulo,
        artista=artista,
        episodios_texto=episodios,
    )


# ---------- _normalizar_titulo ----------

class TestNormalizarTitulo:

    def test_quita_sufijo_japones_al_final(self):
        # Caso real: Mato Seihei no Slave 2, OP1 (ver docstring de
        # _quitar_sufijo_japones).
        resultado = comparador._normalizar_titulo("Hikari yo, Boku ni. (光よ、僕に。)")
        assert resultado == "hikari yo, boku ni."

    def test_no_quita_parentesis_latino(self):
        # Un paréntesis sin script japonés es parte legítima del título y no
        # se debe tocar.
        resultado = comparador._normalizar_titulo("Some Title (Special Edit)")
        assert resultado == "some title (special edit)"

    def test_colapsa_espacios_alrededor_de_virgulilla(self):
        con_espacios = comparador._normalizar_titulo("Title ~Sub~")
        sin_espacios = comparador._normalizar_titulo("Title~Sub~")
        assert con_espacios == sin_espacios == "title~sub~"

    def test_colapsa_espacios_alrededor_de_guion(self):
        con_espacios = comparador._normalizar_titulo("Title - Sub")
        sin_espacios = comparador._normalizar_titulo("Title-Sub")
        assert con_espacios == sin_espacios == "title-sub"

    def test_minusculas_y_espacios_extra(self):
        resultado = comparador._normalizar_titulo("  Some   TITLE  ")
        assert resultado == "some title"

    def test_titulo_vacio_o_none(self):
        assert comparador._normalizar_titulo("") == ""
        assert comparador._normalizar_titulo(None) == ""


# ---------- _limpiar_texto ----------

class TestLimpiarTexto:

    def test_quita_parentesis_japones_con_feat(self):
        resultado = comparador._limpiar_texto("Hanaikada (花筏) feat. Kase")
        assert resultado == "Hanaikada feat. Kase"

    def test_quita_parentesis_japones_al_final(self):
        resultado = comparador._limpiar_texto("LOVE LOVE Beam (LOVE LOVE ビーム)")
        assert resultado == "LOVE LOVE Beam"

    def test_no_toca_parentesis_latino_special_edition(self):
        resultado = comparador._limpiar_texto("Gundam (Special Edition)")
        assert resultado == "Gundam (Special Edition)"

    def test_no_toca_parentesis_latino_cv(self):
        resultado = comparador._limpiar_texto("Mahiru Shina (CV: Manaka Iwami)")
        assert resultado == "Mahiru Shina (CV: Manaka Iwami)"

    def test_texto_vacio_o_none(self):
        assert comparador._limpiar_texto("") == ""
        assert comparador._limpiar_texto(None) == ""


# ---------- _parsear_rango_episodios ----------

class TestParsearRangoEpisodios:

    def test_rango_con_comas_y_subrangos(self):
        resultado = comparador._parsear_rango_episodios("1-4,6-10,12")
        assert resultado == [(1, 4.0), (6, 10.0), (12, 12.0)]

    def test_rango_abierto(self):
        resultado = comparador._parsear_rango_episodios("1-")
        assert resultado == [(1, math.inf)]

    def test_numero_unico(self):
        resultado = comparador._parsear_rango_episodios("7")
        assert resultado == [(7, 7.0)]

    def test_texto_vacio_devuelve_none(self):
        assert comparador._parsear_rango_episodios("") is None
        assert comparador._parsear_rango_episodios(None) is None

    def test_texto_no_reconocible_devuelve_none(self):
        assert comparador._parsear_rango_episodios("abc") is None

    def test_un_subrango_invalido_invalida_todo_el_texto(self):
        # Si un solo sub-rango de la lista separada por comas no matchea,
        # se descarta el texto completo (no se ignora solo esa parte).
        assert comparador._parsear_rango_episodios("1-4,abc,6-10") is None


# ---------- Regla A: _detectar_temas_faltantes ----------

class TestDetectarTemasFaltantes:

    def test_tema_mal_sin_match_en_at_es_faltante(self):
        temas_at = [_tema_at(titulo="Existente")]
        tema_faltante = _tema_mal(titulo="Nuevo")
        resultado = comparador._detectar_temas_faltantes(temas_at, [tema_faltante])

        assert len(resultado) == 1
        disc = resultado[0]
        assert disc.tipo == TipoDiscrepancia.TEMA_FALTANTE
        assert disc.tema_mal is tema_faltante
        assert disc.tema_at is None
        assert disc.tipo_tema == tema_faltante.tipo
        assert disc.secuencia == tema_faltante.secuencia

    def test_tema_con_mismo_titulo_normalizado_no_es_faltante(self):
        # Regla A solo mira el título normalizado, ignora tipo y secuencia:
        # AT lo tiene como OP1, MAL lo pide como ED3, sigue sin ser faltante.
        temas_at = [_tema_at(tipo=TipoTema.OP, secuencia=1, titulo="Mismo Titulo")]
        temas_mal = [_tema_mal(tipo=TipoTema.ED, secuencia=3, titulo="mismo   titulo")]
        resultado = comparador._detectar_temas_faltantes(temas_at, temas_mal)
        assert resultado == []

    def test_match_ignora_sufijo_japones_de_mal(self):
        temas_at = [_tema_at(titulo="Hikari yo, Boku ni.")]
        temas_mal = [_tema_mal(titulo="Hikari yo, Boku ni. (光よ、僕に。)")]
        resultado = comparador._detectar_temas_faltantes(temas_at, temas_mal)
        assert resultado == []

    def test_titulo_duplicado_en_mal_se_reporta_una_sola_vez(self):
        temas_mal = [
            _tema_mal(tipo=TipoTema.OP, secuencia=1, titulo="Repetido"),
            _tema_mal(tipo=TipoTema.ED, secuencia=1, titulo="Repetido"),
        ]
        resultado = comparador._detectar_temas_faltantes([], temas_mal)
        assert len(resultado) == 1

    def test_descripcion_incluye_tipo_secuencia_titulo_y_artista(self):
        tema = _tema_mal(tipo=TipoTema.OP, secuencia=1, titulo="Foo", artista="Bar")
        resultado = comparador._detectar_temas_faltantes([], [tema])
        assert resultado[0].descripcion == 'Falta subir OP1: "Foo" by Bar'

    def test_sin_temas_mal_no_hay_discrepancias(self):
        assert comparador._detectar_temas_faltantes([_tema_at()], []) == []


# ---------- Regla B: _detectar_posibles_huecos_de_cobertura ----------

class TestDetectarPosiblesHuecosDeCobertura:

    def test_sin_temas_at_no_hay_discrepancias(self):
        assert comparador._detectar_posibles_huecos_de_cobertura([]) == []

    def test_tipo_sin_ningun_tema_en_at_no_se_evalua(self):
        # Si AT no tiene ningún tema ED, ese tipo no se evalúa para la Regla B
        # (lo cubriría la Regla A si MAL sí lo tiene); solo se reporta el OP,
        # que sí tiene tema y está abierto.
        temas_at = [_tema_at(tipo=TipoTema.OP, episodios="1-")]
        resultado = comparador._detectar_posibles_huecos_de_cobertura(temas_at)
        assert len(resultado) == 1
        assert resultado[0].tipo_tema == TipoTema.OP

    def test_todos_abiertos_se_reporta_como_hueco(self):
        temas_at = [_tema_at(tipo=TipoTema.OP, episodios="1-")]
        resultado = comparador._detectar_posibles_huecos_de_cobertura(temas_at)
        assert len(resultado) == 1
        assert resultado[0].tipo == TipoDiscrepancia.RANGO_ABIERTO_SIN_CERRAR

    def test_al_menos_uno_cerrado_no_se_reporta(self):
        # El episodio final del anime puede no tener tema — un rango cerrado
        # ya es señal suficiente de que el dato está completo, aunque otro
        # tema del mismo tipo siga abierto.
        temas_at = [
            _tema_at(tipo=TipoTema.OP, secuencia=1, episodios="1-12"),
            _tema_at(tipo=TipoTema.OP, secuencia=2, episodios="13-"),
        ]
        resultado = comparador._detectar_posibles_huecos_de_cobertura(temas_at)
        assert resultado == []

    def test_texto_no_parseable_no_cuenta_como_cierre(self):
        temas_at = [_tema_at(tipo=TipoTema.OP, episodios="texto-invalido")]
        resultado = comparador._detectar_posibles_huecos_de_cobertura(temas_at)
        assert len(resultado) == 1

    def test_un_solo_tipo_afectado_usa_mensaje_de_uno_solo(self):
        temas_at = [_tema_at(tipo=TipoTema.OP, episodios="1-")]
        resultado = comparador._detectar_posibles_huecos_de_cobertura(temas_at)
        assert resultado[0].descripcion == (
            'Rango abierto sin cerrar en OP ("1-") — '
            'revisar si falta completar el dato de episodios'
        )

    def test_op_y_ed_afectados_con_mismo_rango_se_combinan(self):
        temas_at = [
            _tema_at(tipo=TipoTema.OP, episodios="1-"),
            _tema_at(tipo=TipoTema.ED, episodios="1-"),
        ]
        resultado = comparador._detectar_posibles_huecos_de_cobertura(temas_at)
        assert len(resultado) == 1  # una sola Discrepancia, no una por tipo
        assert resultado[0].descripcion == (
            'Rangos abiertos sin cerrar en OP y ED ("1-") — '
            'revisar si falta completar el dato de episodios'
        )

    def test_op_y_ed_afectados_con_rangos_distintos_se_listan_separados(self):
        temas_at = [
            _tema_at(tipo=TipoTema.OP, episodios="1-"),
            _tema_at(tipo=TipoTema.ED, episodios="7-"),
        ]
        resultado = comparador._detectar_posibles_huecos_de_cobertura(temas_at)
        assert len(resultado) == 1
        assert resultado[0].descripcion == (
            'Rangos abiertos sin cerrar en OP ("1-") y ED ("7-") — '
            'revisar si falta completar el dato de episodios'
        )


# ---------- Regla D: _detectar_entradas_sin_video ----------

class TestDetectarEntradasSinVideo:

    def test_tema_con_video_no_se_reporta(self):
        temas_at = [_tema_at(tiene_video=True)]
        assert comparador._detectar_entradas_sin_video(temas_at) == []

    def test_tema_sin_video_se_reporta(self):
        temas_at = [_tema_at(tiene_video=False)]
        resultado = comparador._detectar_entradas_sin_video(temas_at)
        assert len(resultado) == 1
        disc = resultado[0]
        assert disc.tipo == TipoDiscrepancia.VIDEO_FALTANTE
        assert disc.tema_at is temas_at[0]
        assert disc.tema_mal is None

    def test_descripcion_incluye_tipo_secuencia_version_episodios_y_titulo(self):
        tema = _tema_at(
            tipo=TipoTema.ED, secuencia=2, titulo="Cancion", episodios="5-8",
            version=2, tiene_video=False,
        )
        resultado = comparador._detectar_entradas_sin_video([tema])
        assert resultado[0].descripcion == (
            'Entrada sin video: ED2 v2 (eps 5-8) "Cancion" — revisar si falta subir'
        )

    def test_descripcion_omite_version_1_y_campos_vacios(self):
        tema = _tema_at(
            tipo=TipoTema.OP, secuencia=1, titulo="", episodios="",
            version=1, tiene_video=False,
        )
        resultado = comparador._detectar_entradas_sin_video([tema])
        assert resultado[0].descripcion == "Entrada sin video: OP1 — revisar si falta subir"


# ---------- comparar: junta las 3 reglas ----------

class TestComparar:

    def test_junta_discrepancias_de_las_tres_reglas(self):
        temas_at = [
            _tema_at(tipo=TipoTema.OP, secuencia=1, titulo="Existe", episodios="1-",
                     tiene_video=False),
        ]
        temas_mal = [_tema_mal(tipo=TipoTema.ED, secuencia=1, titulo="Falta")]

        resultado = comparador.comparar(temas_at, temas_mal)

        tipos = {d.tipo for d in resultado}
        assert tipos == {
            TipoDiscrepancia.TEMA_FALTANTE,
            TipoDiscrepancia.RANGO_ABIERTO_SIN_CERRAR,
            TipoDiscrepancia.VIDEO_FALTANTE,
        }
