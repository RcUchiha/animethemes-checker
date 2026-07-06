"""
Comparador: cruza los temas que YA existen en AnimeThemes (TemaAT) contra los
temas que MAL dice que deberían existir (TemaMAL), y produce una lista de
Discrepancia para revisar.

Historial de diseño (por qué quedó así):
Las primeras versiones comparaban por (tipo, secuencia) exacto, pero esto
generaba falsos positivos en casos reales donde AT y MAL no se ponen de
acuerdo en si un tema es "opening" o "ending" (ej. Akane-banashi: el mismo
tema suena al final del episodio 1, AT lo cuenta como parte del OP, MAL lo
cuenta como un ED aparte). Intentar "emparejar" temas por título +
solapamiento de episodios para reconciliar esa diferencia de criterio
resultó ser un sistema demasiado intrincado y con sus propios bugs (no
soportaba rangos con coma como "1-4,6-10,12", y generaba ambigüedad cuando
un mismo título aparecía dos veces en MAL).

El usuario (contribuidor activo de AnimeThemes) aclaró el criterio real,
mucho más simple, y este módulo quedó reescrito sobre esa base:

REGLA A — Tema realmente faltante:
    Un tema de MAL (identificado SOLO por su título, normalizado) está
    "faltante" si NINGÚN tema en AT tiene ese mismo título. No importa el
    tipo (OP/ED), la secuencia, ni el rango de episodios para esta
    decisión — solo existencia del título.

REGLA B — Posible hueco de cobertura (independiente de MAL):
    Para cada tipo (OP, ED) que SÍ tiene al menos un tema en AT, se mira
    si el rango combinado de episodios de AT queda con apariencia de
    incompleto: específicamente, si TODOS los temas de ese tipo en AT
    tienen su segmento final abierto (ej. "1-", nunca cierra en un
    número), es señal de que probablemente falte cerrar el dato — no
    necesariamente significa que falten episodios reales (el episodio
    final del anime puede no tener tema), pero un rango que nunca cierra
    es sospechoso y vale la pena que el usuario lo revise a mano.
    Esto NO exige que el rango llegue hasta el último episodio del anime
    (eso generaría falsos positivos: el último episodio puede no tener
    tema en absoluto).

Aparte de A y B, se mantiene:
REGLA C — Hueco de SECUENCIA dentro de AT (ej. existe OP1 y OP3, no OP2).
    Esto es independiente de A/B y de MAL: solo mira la numeración interna
    de AT.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import i18n
from modelos import Discrepancia, TemaAT, TemaMAL, TipoDiscrepancia, TipoTema

if TYPE_CHECKING:
    from jikan_client import AnimeDeTemporadaMAL


# ---------- utilidades de texto ----------

_SUFIJO_JAPONES_RE = re.compile(r"\s*\([^()]*[\u3040-\u30ff\u3400-\u9fff][^()]*\)\s*$")


def _quitar_sufijo_japones(titulo: str) -> str:
    """
    MAL suele dar el título como 'Romaji (Japonés)', ej.
    'Hikari yo, Boku ni. (光よ、僕に。)', mientras que AnimeThemes solo
    guarda el romaji ('Hikari yo, Boku ni.'). Sin quitar ese sufijo, la
    comparación de títulos falla aunque el tema sea el mismo (caso real:
    Mato Seihei no Slave 2, OP1).

    Solo se quita el último paréntesis si su contenido tiene al menos un
    carácter de script japonés (hiragana/katakana/kanji) — así no se
    arriesga a cortar un paréntesis que sea parte legítima de un título en
    inglés/romaji (ej. "Some Title (Special Edit)").
    """
    return _SUFIJO_JAPONES_RE.sub("", titulo or "").strip()


def _normalizar_titulo(titulo: str) -> str:
    """
    Normaliza un título de canción para comparar de forma laxa:
    - quita el sufijo japonés entre paréntesis si lo hay (MAL lo agrega,
      AnimeThemes no — ver _quitar_sufijo_japones)
    - colapsa espacios alrededor de '~' y '-' decorativos (ej.
      "Title ~Sub~" vs "Title~Sub~"), ya que distintas fuentes son
      inconsistentes en si ponen espacio ahí o no
    - pasa a minúsculas y colapsa espacios extra

    No intenta deshacer romanizaciones distintas, solo diferencias
    triviales de formato.
    """
    sin_sufijo = _quitar_sufijo_japones(titulo)
    sin_espacios_decorativos = re.sub(r"\s*([~\-])\s*", r"\1", sin_sufijo)
    return " ".join(sin_espacios_decorativos.lower().split())


_PARENTESIS_JAPONES_RE = re.compile(r"\s*\([^()]*[\u3040-\u30ff\u3400-\u9fff][^()]*\)")


def _limpiar_texto(texto: str) -> str:
    """
    Limpia el texto de un título o artista proveniente de MAL:
    - Quita TODOS los paréntesis que contengan japonés, en cualquier
      posición del texto (no solo al final):
      'Hanaikada (花筏) feat. Kase' → 'Hanaikada feat. Kase'
      'LOVE LOVE Beam (LOVE LOVE ビーム)' → 'LOVE LOVE Beam'
    - Paréntesis en texto latino puro NO se tocan:
      'Gundam (Special Edition)' → 'Gundam (Special Edition)'
      'Mahiru Shina (CV: Manaka Iwami)' → 'Mahiru Shina (CV: Manaka Iwami)'
    """
    resultado = _PARENTESIS_JAPONES_RE.sub("", texto or "")
    return " ".join(resultado.split())


def _formatear_titulo_y_artista(tema: TemaMAL) -> str:
    titulo = _limpiar_texto(tema.titulo_cancion)
    partes = f'"{titulo}"'
    if tema.artista:
        artista = _limpiar_texto(tema.artista)
        partes += f" by {artista}"
    return partes

def _formatear_episodios(episodios_texto: str) -> str:
    # Quitado a petición del usuario — el rango de episodios ya no se
    # incluye en la descripción de tema_faltante.
    return ""


# ---------- utilidades de rangos de episodios ----------
# Formato real soportado: listas separadas por coma, cada sub-rango puede
# ser "N", "N-", o "N-M". Ej: "1-4,6-10,12" o "2-".

_SUBRANGO_RE = re.compile(r"^(\d+)(-(\d+)?)?$")


def _parsear_rango_episodios(episodios_texto: str) -> "list[tuple[int, float]] | None":
    """
    Convierte un texto de episodios en una lista de intervalos (inicio, fin).
    '1-4,6-10,12' -> [(1,4), (6,10), (12,12)]. '1-' -> [(1, inf)].
    Devuelve None si el texto está vacío o algún sub-rango no es
    reconocible.
    """
    texto = (episodios_texto or "").strip()
    if not texto:
        return None

    intervalos: list[tuple[int, float]] = []
    for parte in texto.split(","):
        parte = parte.strip()
        m = _SUBRANGO_RE.match(parte)
        if not m:
            return None

        inicio = int(m.group(1))
        if m.group(2) is None:
            intervalos.append((inicio, float(inicio)))
            continue
        fin_str = m.group(3)
        fin = float(fin_str) if fin_str else float("inf")
        intervalos.append((inicio, fin))

    return intervalos or None


# ---------- Regla A: tema faltante por título ----------

def _detectar_temas_faltantes(
    temas_at: list[TemaAT], temas_mal: list[TemaMAL]
) -> list[Discrepancia]:
    """
    REGLA A: un tema de MAL está faltante si su título normalizado no
    existe en NINGÚN tema de AT, sin importar tipo, secuencia, ni rango.
    """
    titulos_en_at = {_normalizar_titulo(t.titulo_cancion) for t in temas_at}

    discrepancias = []
    ya_reportados: set[str] = set()  # evita reportar el mismo título 2 veces si MAL lo repite

    for tema_mal in temas_mal:
        titulo_normalizado = _normalizar_titulo(tema_mal.titulo_cancion)
        if titulo_normalizado in titulos_en_at:
            continue
        if titulo_normalizado in ya_reportados:
            continue
        ya_reportados.add(titulo_normalizado)

        descripcion = (
            f"{i18n.t('desc_missing_theme', type=tema_mal.tipo.value, seq=tema_mal.secuencia)} "
            f"{_formatear_titulo_y_artista(tema_mal)}".strip()
        )
        discrepancias.append(Discrepancia(
            tipo=TipoDiscrepancia.TEMA_FALTANTE,
            tipo_tema=tema_mal.tipo,
            secuencia=tema_mal.secuencia,
            descripcion=descripcion,
            tema_mal=tema_mal,
            tema_at=None,
        ))

    return discrepancias


# ---------- Regla B: posible hueco de cobertura, solo dentro de AT ----------

def _todos_los_segmentos_abiertos(temas_de_un_tipo: list[TemaAT]) -> bool:
    """
    True si CADA tema de la lista (todos del mismo tipo, ej. todos los OP
    de un anime) tiene su último sub-rango abierto (sin número final).
    Si algún tema sí cierra en algún punto, devuelve False — ya hay al
    menos una pista de dónde termina la cobertura real.
    """
    if not temas_de_un_tipo:
        return False

    for tema in temas_de_un_tipo:
        intervalos = _parsear_rango_episodios(tema.episodios_texto)
        if intervalos is None:
            continue  # texto vacío o no parseable: no cuenta como "cierra", pero tampoco lo evaluamos aquí
        if intervalos[-1][1] != float("inf"):
            return False  # este tema sí cierra en un número -> no todos están abiertos

    return True


def _rango_representativo(temas_de_un_tipo: list[TemaAT]) -> str:
    """
    Para el mensaje combinado, se necesita UN texto de rango por tipo. Si
    todos los temas de ese tipo comparten el mismo texto de episodios, se
    usa ese. Si varían (ej. dos versiones distintas, cada una abierta en
    un punto distinto), se listan todos separados por coma.
    """
    textos = list(dict.fromkeys(t.episodios_texto for t in temas_de_un_tipo))  # preserva orden, sin duplicados
    return ", ".join(textos)


def _detectar_posibles_huecos_de_cobertura(temas_at: list[TemaAT]) -> list[Discrepancia]:
    """
    REGLA B: para cada tipo (OP, ED) con al menos un tema en AT, si TODOS
    los temas de ese tipo tienen rango abierto sin cerrar nunca (ej. solo
    "1-", nunca "1-12"), se marca como posible hueco a revisar. No exige
    que el rango llegue al último episodio del anime — solo que en algún
    punto se establezca un cierre, lo cual ya es una señal razonable de
    que el dato fue completado.

    Genera UNA SOLA Discrepancia por anime (no una por tipo), combinando
    OP y ED en un mensaje compacto cuando ambos están afectados — evita
    redundancia como "OP: ... | ED: ..." al agruparse en la GUI, y evita
    que la columna "Tipo" sea ambigua cuando en realidad cubre ambos.
    """
    tipos_afectados: list[tuple[TipoTema, str, TemaAT]] = []  # (tipo, rango_repr, tema_de_referencia)

    for tipo in (TipoTema.OP, TipoTema.ED):
        temas_de_este_tipo = [t for t in temas_at if t.tipo == tipo]
        if not temas_de_este_tipo:
            continue  # no hay tema de este tipo en AT; lo cubre la Regla A si MAL sí lo tiene

        if not _todos_los_segmentos_abiertos(temas_de_este_tipo):
            continue  # al menos un tema cierra en algún número; no hay hueco evidente

        tipos_afectados.append((tipo, _rango_representativo(temas_de_este_tipo), temas_de_este_tipo[0]))

    if not tipos_afectados:
        return []

    if len(tipos_afectados) == 1:
        tipo, rango, tema_ref = tipos_afectados[0]
        descripcion = i18n.t("desc_open_range_one", type=tipo.value, range=rango)
    else:
        (tipo_a, rango_a, tema_ref), (tipo_b, rango_b, _) = tipos_afectados
        tema_ref = tipos_afectados[0][2]
        if rango_a == rango_b:
            descripcion = i18n.t("desc_open_range_both_same",
                                 type_a=tipo_a.value, type_b=tipo_b.value, range=rango_a)
        else:
            descripcion = i18n.t("desc_open_range_both_diff",
                                 type_a=tipo_a.value, range_a=rango_a,
                                 type_b=tipo_b.value, range_b=rango_b)

    return [Discrepancia(
        tipo=TipoDiscrepancia.RANGO_ABIERTO_SIN_CERRAR,
        tipo_tema=tipos_afectados[0][0],
        secuencia=tema_ref.secuencia,
        descripcion=descripcion,
        tema_mal=None,
        tema_at=tema_ref,
    )]


# ---------- Regla C: hueco de secuencia dentro de AT ----------


# ---------- punto de entrada principal ----------

def _detectar_entradas_sin_video(temas_at: list[TemaAT]) -> list[Discrepancia]:
    """
    REGLA D: detecta entradas (animethemeentry) que existen en AnimeThemes
    pero no tienen ningún video asociado (videos: []).

    Esto ocurre cuando un video fue eliminado o perdido y nadie lo ha
    vuelto a subir — la entrada queda "huérfana" con sus metadatos pero
    sin contenido reproducible. Se detecta directamente desde el campo
    tiene_video de TemaAT, que el cliente ya parsea desde el array de
    videos de la API.

    La descripción incluye tipo, secuencia, versión y episodios para que
    sea fácil localizar exactamente cuál entry está afectada.
    """
    discrepancias = []
    for tema in temas_at:
        if tema.tiene_video:
            continue

        partes = [f"{tema.tipo.value}{tema.secuencia}"]
        if tema.version > 1:
            partes.append(f"v{tema.version}")
        if tema.episodios_texto:
            partes.append(f"(eps {tema.episodios_texto})")
        if tema.titulo_cancion:
            partes.append(f'"{tema.titulo_cancion}"')

        descripcion = i18n.t("desc_missing_video", entry=" ".join(partes))
        discrepancias.append(Discrepancia(
            tipo=TipoDiscrepancia.VIDEO_FALTANTE,
            tipo_tema=tema.tipo,
            secuencia=tema.secuencia,
            descripcion=descripcion,
            tema_mal=None,
            tema_at=tema,
        ))

    return discrepancias


def comparar(
    temas_at: list[TemaAT], temas_mal: list[TemaMAL], anime_terminado: bool = True
) -> list[Discrepancia]:
    """Punto de entrada principal: junta las 3 reglas (A, B, D)."""
    discrepancias: list[Discrepancia] = []
    discrepancias += _detectar_temas_faltantes(temas_at, temas_mal)
    discrepancias += _detectar_posibles_huecos_de_cobertura(temas_at)
    discrepancias += _detectar_entradas_sin_video(temas_at)
    return discrepancias


def detectar_animes_faltantes_en_animethemes(
    animes_at: list,  # list[ac.AnimeBasico], evitamos el import circular con un tipo flexible
    animes_mal: "list[AnimeDeTemporadaMAL]",
) -> "list[AnimeDeTemporadaMAL]":
    """
    Dado lo que YA está en AnimeThemes (animes_at, con su mal_id si lo tienen)
    y la lista completa de la temporada según MAL (animes_mal), devuelve los
    animes de MAL que NO aparecen en absoluto en AnimeThemes.

    Nota: animes_at necesita tener mal_id disponible (AnimeCompleto, no
    AnimeBasico) para poder cruzar por id. Si solo se tiene AnimeBasico
    (sin mal_id), este cruce no es posible y hay que usar AnimeCompleto.
    """
    mal_ids_en_at = {a.mal_id for a in animes_at if getattr(a, "mal_id", None) is not None}
    return [anime for anime in animes_mal if anime.mal_id not in mal_ids_en_at]
