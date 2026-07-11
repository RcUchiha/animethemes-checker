"""
Módulo de internacionalización (i18n) para AnimeThemes Mod Checker.

Contiene todas las cadenas de texto visibles de la UI en español e inglés.
El idioma activo se guarda en config.json (misma carpeta que el código)
y se puede cambiar en tiempo real desde la GUI sin reiniciar el programa.

Uso:
    from i18n import t, idioma_actual, cambiar_idioma

    t("scan_button")       → "Escanear" o "Scan"
    t("season_label")      → "Temporada:" o "Season:"
"""

from __future__ import annotations

import json
import os

_RUTA_CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

IDIOMAS = {"es": "Español", "en": "English"}
_idioma_actual: str = "en"

# Callbacks registrados por la GUI para recibir notificación de cambio de idioma
_callbacks: list = []

TEXTOS: dict[str, dict[str, str]] = {
    # ---- ventana principal ----
    "window_title":             {"es": "AnimeThemes Mod Checker", "en": "AnimeThemes Mod Checker"},
    "language_label":           {"es": "Idioma:", "en": "Language:"},

    # ---- panel de controles (compartido) ----
    "year_label":               {"es": "Año:", "en": "Year:"},
    "season_label":             {"es": "Temporada:", "en": "Season:"},
    "filter_label":             {"es": "Filtrar por:", "en": "Filter by:"},
    "scan_button":              {"es": "Escanear", "en": "Scan"},
    "export_button":            {"es": "Exportar CSV", "en": "Export CSV"},

    # ---- pestañas principales ----
    "tab_discrepancias":        {"es": "Discrepancias", "en": "Discrepancies"},
    "tab_faltantes":            {"es": "Animes Faltantes", "en": "Missing Anime"},

    # ---- sub-pestañas de Discrepancias ----
    "subtab_discrepancias":     {"es": "Resultados", "en": "Results"},
    "subtab_omitidos":          {"es": "Omitidos / Errores", "en": "Omitted / Errors"},

    # ---- sub-pestañas de Animes Faltantes ----
    "subtab_faltantes":         {"es": "Resultados", "en": "Results"},
    "subtab_errores":           {"es": "Errores", "en": "Errors"},

    # ---- encabezados de tabla: Discrepancias ----
    "col_anime":                {"es": "Anime", "en": "Anime"},
    "col_tipo":                 {"es": "Tipo", "en": "Type"},
    "col_descripcion":          {"es": "Descripción", "en": "Description"},

    # ---- encabezados de tabla: Animes Faltantes ----
    "col_tipo_af":              {"es": "Tipo", "en": "Type"},
    "col_titulo":               {"es": "Título", "en": "Title"},
    "col_mal_id":               {"es": "MAL ID", "en": "MAL ID"},
    "col_status":               {"es": "Status", "en": "Status"},
    "col_status_mal":           {"es": "Status MAL", "en": "MAL Status"},
    "col_link":                 {"es": "Link a MAL", "en": "MAL Link"},

    # ---- valores de status tal como los reporta MAL/Jikan ----
    # NOTA: estos son solo para mostrar en la UI/CSV. La comparación de
    # lógica (¿terminó de emitirse?) en comparador.py/orquestador.py
    # SIEMPRE usa el string crudo en inglés que devuelve MAL — nunca
    # comparar contra el texto traducido de aquí.
    "status_finished_airing":   {"es": "Terminó de emitirse", "en": "Finished Airing"},
    "status_currently_airing":  {"es": "En emisión", "en": "Currently Airing"},
    "status_not_yet_aired":     {"es": "Aún no se emite", "en": "Not yet aired"},

    # ---- encabezados de tabla: Omitidos / Errores ----
    "col_motivo":               {"es": "Motivo", "en": "Reason"},
    "col_error":                {"es": "Error", "en": "Error"},

    # ---- opciones del filtro de tipo ----
    "filter_all":               {"es": "Todos", "en": "All"},
    "filter_tema_faltante":     {"es": "Tema faltante", "en": "Missing theme"},
    "filter_rango_abierto":     {"es": "Rango sin cerrar", "en": "Open range"},
    "filter_video_faltante":    {"es": "Video faltante", "en": "Missing video"},

    # ---- etiquetas cortas de tipo en tabla ----
    "tipo_tema_faltante":       {"es": "tema_faltante", "en": "missing_theme"},
    "tipo_rango_abierto":       {"es": "rango_abierto", "en": "open_range"},
    "tipo_video_faltante":      {"es": "video_faltante", "en": "missing_video"},

    # ---- mensajes de estado ----
    "status_ready":             {"es": "Listo.", "en": "Ready."},
    "status_starting":          {"es": "Iniciando escaneo de {season} {year}...",
                                 "en": "Starting scan for {season} {year}..."},
    "status_scanning":          {"es": "({i}/{total}) Revisando: {name}",
                                 "en": "({i}/{total}) Checking: {name}"},
    "status_verifying":         {"es": "({i}/{total}) Verificando: {name}",
                                 "en": "({i}/{total}) Verifying: {name}"},
    "status_done_disc":         {"es": "Escaneo completo: {n} de {total} animes terminados tienen algo que revisar.",
                                 "en": "Scan complete: {n} of {total} finished anime have something to review."},
    "status_done_falt":         {"es": "Escaneo completo: {n} animes le faltan a AnimeThemes.",
                                 "en": "Scan complete: {n} anime are missing from AnimeThemes."},
    "status_done_falt_errors":  {"es": " ({n_err} con error, revisar pestaña Errores)",
                                 "en": " ({n_err} with errors, check Errors tab)"},
    "status_cached":            {"es": "Mostrando resultado guardado de {age} — presiona Escanear para actualizar.",
                                 "en": "Showing saved result from {age} — press Scan to refresh."},
    "status_no_seasons":        {"es": "Ese año aún no ha empezado — no hay temporadas que escanear.",
                                 "en": "That year hasn't started yet — no seasons to scan."},
    "status_error":             {"es": "Error durante el escaneo.", "en": "Error during scan."},
    "status_ready_note":        {"es": "Listo. Nota: este escaneo scrapea MAL anime por anime, puede tomar varios minutos.",
                                 "en": "Ready. Note: this scan scrapes MAL anime by anime, may take several minutes."},

    # ---- antigüedad del resultado guardado ----
    "age_seconds":              {"es": "hace unos segundos", "en": "a few seconds ago"},
    "age_minutes_1":            {"es": "hace 1 minuto", "en": "1 minute ago"},
    "age_minutes_n":            {"es": "hace {n} minutos", "en": "{n} minutes ago"},
    "age_hours_1":              {"es": "hace 1 hora", "en": "1 hour ago"},
    "age_hours_n":              {"es": "hace {n} horas", "en": "{n} hours ago"},

    # ---- motivos de omisión ----
    "omit_no_mal_id":           {"es": "Sin MAL id en AnimeThemes", "en": "No MAL id in AnimeThemes"},
    "omit_not_finished":        {"es": "Aún no termina de emitirse", "en": "Still airing"},
    "omit_error":               {"es": "Error: {msg}", "en": "Error: {msg}"},

    # ---- diálogos ----
    "dlg_scan_error_title":     {"es": "Error durante el escaneo", "en": "Scan error"},
    "dlg_exported_title":       {"es": "Exportado", "en": "Exported"},
    "dlg_exported_msg":         {"es": "CSV guardado en:\n{path}", "en": "CSV saved to:\n{path}"},

    # ---- errores de red conocidos ----
    # Mensaje deliberadamente sin jerga técnica (nada de "HTTPError", "504"
    # ni "urllib") — ver orquestador.ErrorListadoMALNoDisponible. El
    # detalle técnico real queda en la excepción encadenada (__cause__),
    # no en este texto, que es lo único que llega a la GUI.
    "error_listado_mal_no_disponible": {
        "es": "No se pudo obtener el listado completo de la temporada desde MAL (vía Jikan) "
              "después de varios intentos. Este servicio externo es conocido por tener cortes "
              "intermitentes — probá de nuevo en unos minutos.",
        "en": "Could not get the full season list from MAL (via Jikan) after several attempts. "
              "This external service is known to have intermittent outages — try again in a few minutes.",
    },

    # ---- aviso: datos de temporada servidos desde caché vencido ----
    # Se muestra con QMessageBox.warning (no critical), mismo criterio que
    # la alerta de canario: no es un error de red que bloquea el escaneo,
    # es un aviso sobre la frescura de un resultado que YA se mostró. Ver
    # orq.ResultadoFaltantes.datos_de_temporada_desde_cache_vencido y el
    # docstring de orq.detectar_animes_faltantes_en_at.
    "dlg_cache_vencido_title": {"es": "Datos de la temporada posiblemente desactualizados",
                                 "en": "Season data may be outdated"},
    "dlg_cache_vencido_msg":   {"es": "No se pudo obtener el listado actualizado de la temporada desde MAL "
                                       "(vía Jikan) — se está usando un resultado guardado de hace {dias} días. "
                                       "Animes nuevos o cambios recientes en MAL podrían no reflejarse todavía. "
                                       "Probá escanear de nuevo más tarde para actualizar.",
                                 "en": "Could not get the up-to-date season list from MAL (via Jikan) — showing "
                                       "a saved result from {dias} days ago. New anime or recent MAL changes "
                                       "might not be reflected yet. Try scanning again later to refresh."},

    # ---- aviso: datos de temporada servidos desde AniList (último recurso) ----
    # Mismo criterio visual que dlg_cache_vencido (QMessageBox.warning, no
    # critical), pero es un aviso DISTINTO y más fuerte: acá no es "el
    # mismo dato, más viejo" — es una fuente externa distinta, con su
    # propio riesgo de mapeo (ver anilist_client.py y el docstring de
    # orq.detectar_animes_faltantes_en_at). Solo se muestra cuando ni
    # Jikan en vivo ni ningún caché (ni vencido) tenían nada que ofrecer.
    "dlg_anilist_title": {"es": "Usando AniList como respaldo",
                           "en": "Using AniList as a fallback"},
    "dlg_anilist_msg":   {"es": "MAL (vía Jikan) no respondió y no había ningún resultado guardado de esta "
                                 "temporada para usar como respaldo, así que se usó AniList como última opción. "
                                 "{n} animes se omitieron por no tener vínculo a MAL cargado en AniList. Este "
                                 "resultado depende de una fuente distinta a MAL y puede tener imprecisiones — "
                                 "probá escanear de nuevo más tarde para confirmarlo contra MAL directamente.",
                           "en": "MAL (via Jikan) did not respond and there was no saved result for this season "
                                 "to fall back on, so AniList was used as a last resort. {n} anime were skipped "
                                 "for not having a MAL link in AniList. This result relies on a different source "
                                 "than MAL and may be imprecise — try scanning again later to confirm against "
                                 "MAL directly."},

    # ---- nombres de estaciones ----
    # NOTA: estos son solo para mostrar en la UI. El valor interno que se
    # manda a la API de AnimeThemes (Winter/Spring/Summer/Fall) SIEMPRE va
    # en inglés sin importar el idioma activo — ver SEASON_A_CLAVE_I18N en
    # gui_pyqt6.py.
    "season_winter":            {"es": "Invierno", "en": "Winter"},
    "season_spring":            {"es": "Primavera", "en": "Spring"},
    "season_summer":            {"es": "Verano", "en": "Summer"},
    "season_fall":              {"es": "Otoño", "en": "Fall"},

    # ---- mensajes de comparador (usados al generar descripciones) ----
    "desc_missing_theme":       {"es": "Falta subir {type}{seq}:", "en": "Missing {type}{seq}:"},
    "desc_open_range_one":      {"es": "Rango abierto sin cerrar en {type} (\"{range}\") — revisar si falta completar el dato de episodios",
                                 "en": "Open range in {type} (\"{range}\") — check if episode data needs completing"},
    "desc_open_range_both_same":{"es": "Rangos abiertos sin cerrar en {type_a} y {type_b} (\"{range}\") — revisar si falta completar el dato de episodios",
                                 "en": "Open ranges in {type_a} and {type_b} (\"{range}\") — check if episode data needs completing"},
    "desc_open_range_both_diff":{"es": "Rangos abiertos sin cerrar en {type_a} (\"{range_a}\") y {type_b} (\"{range_b}\") — revisar si falta completar el dato de episodios",
                                 "en": "Open ranges in {type_a} (\"{range_a}\") and {type_b} (\"{range_b}\") — check if episode data needs completing"},
    "desc_missing_video":       {"es": "Entrada sin video: {entry} — revisar si falta subir",
                                 "en": "Entry without video: {entry} — check if upload is needed"},

    # ---- alerta canario: posible cambio de HTML en MAL (issue #3) ----
    # Deliberadamente distinta del diálogo de error de red
    # (dlg_scan_error_title): esto NO es un error de conexión ni una lista
    # de discrepancias reales — es una sospecha de que mal_scraper.py dejó
    # de reconocer el HTML de MAL, así que el escaneo pudo haber terminado
    # "limpio" sin serlo. Se muestra con QMessageBox.warning (no critical)
    # en gui_pyqt6.py para diferenciarla visualmente también.
    "dlg_canario_title":        {"es": "Posible cambio de estructura en MAL",
                                 "en": "Possible MAL structure change"},
    "dlg_canario_msg":          {"es": "{vacios} de {evaluados} animes terminados devolvieron 0 temas al "
                                        "scrapear MAL en este escaneo. Esto no es un error de red: puede "
                                        "indicar que MAL cambió el HTML de su página y mal_scraper.py dejó "
                                        "de reconocerlo. Antes de confiar en este resultado, revisa manualmente "
                                        "algún caso conocido.",
                                 "en": "{vacios} of {evaluados} finished anime returned 0 themes when "
                                        "scraping MAL in this scan. This is not a network error: it may mean "
                                        "MAL changed its page HTML and mal_scraper.py stopped recognizing it. "
                                        "Before trusting this result, manually check a known case."},
}


def idioma_actual() -> str:
    return _idioma_actual


def t(clave: str, **kwargs) -> str:
    """
    Devuelve el texto en el idioma actual para la clave dada.
    Si la clave no existe, devuelve la clave misma (para detectar strings
    faltantes fácilmente durante el desarrollo). Acepta kwargs para
    interpolación: t("status_scanning", i=3, total=10, name="Akane").
    """
    global _idioma_actual
    entrada = TEXTOS.get(clave)
    if entrada is None:
        return clave  # clave no registrada — visible como bug en desarrollo
    texto = entrada.get(_idioma_actual, entrada.get("es", clave))
    if kwargs:
        try:
            texto = texto.format(**kwargs)
        except KeyError:
            pass  # interpolación parcial: devolver el texto sin formatear completo
    return texto


def cambiar_idioma(nuevo_idioma: str) -> None:
    """
    Cambia el idioma activo y notifica a todos los callbacks registrados
    (normalmente los widgets de la GUI que necesitan re-renderizar sus textos).
    """
    global _idioma_actual
    if nuevo_idioma not in IDIOMAS:
        return
    _idioma_actual = nuevo_idioma
    _guardar_config()
    for cb in _callbacks:
        try:
            cb()
        except Exception:
            pass


def registrar_callback(cb) -> None:
    """Registra una función que se llamará cuando cambie el idioma."""
    if cb not in _callbacks:
        _callbacks.append(cb)


def _cargar_config() -> None:
    global _idioma_actual
    if not os.path.exists(_RUTA_CONFIG):
        return
    try:
        with open(_RUTA_CONFIG, "r", encoding="utf-8") as f:
            data = json.load(f)
        idioma = data.get("idioma", "en")
        if idioma in IDIOMAS:
            _idioma_actual = idioma
    except (json.JSONDecodeError, OSError):
        pass


def _guardar_config() -> None:
    try:
        data = {}
        if os.path.exists(_RUTA_CONFIG):
            try:
                with open(_RUTA_CONFIG, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                pass
        data["idioma"] = _idioma_actual
        tmp = _RUTA_CONFIG + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, _RUTA_CONFIG)
    except OSError:
        pass


# Cargar preferencia guardada al importar el módulo
_cargar_config()
