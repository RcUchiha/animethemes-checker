"""
GUI en PyQt6 para el AnimeThemes Checker, con tema oscuro.

Ventana principal con selector de idioma y un QTabWidget con 2 pestañas,
cada una con su propio panel de controles (año, temporada, escanear,
exportar), barra de progreso y sub-pestañas de resultados:
- "Discrepancias" (PestanaDiscrepancias): corre orq.escanear_temporada en
  un hilo aparte (_WorkerEscaneo) y muestra las discrepancias encontradas
  agrupadas por anime, más los omitidos/errores y la alerta canario de
  posible cambio de HTML en MAL (ver orquestador.py, issue #3).
- "Animes Faltantes" (PestanaAnimesFaltantes): corre
  orq.detectar_animes_faltantes_en_at en un hilo aparte (_WorkerEscaneo)
  y muestra los animes que MAL reporta y AnimeThemes no tiene.

Ambas pestañas cachean resultados en memoria por (año, temporada) para no
tener que re-escanear solo por cambiar el selector, exportan a CSV, y
traducen todo su texto vía i18n.py (nunca hardcodeado acá, ver
CONTRIBUTING.md).

Requiere: pip install PyQt6
"""

from __future__ import annotations

import csv
import datetime
import sys

from PyQt6.QtCore import QThread, pyqtSignal, Qt, QUrl, QRect, QModelIndex
from PyQt6.QtGui import QDesktopServices, QColor, QBrush, QPainter, QFontMetrics
from PyQt6.QtWidgets import (
    QApplication,
    QMainWindow,
    QTabWidget,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QSpinBox,
    QComboBox,
    QPushButton,
    QProgressBar,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QFileDialog,
    QMessageBox,
    QAbstractItemView,
    QStyledItemDelegate,
    QStyleOptionViewItem,
)

import cache_jikan
import i18n
import orquestador as orq


COLORES_TIPO = {
    "tema_faltante":            QColor(30,  70, 120),
    "video_faltante":           QColor(100, 60,  10),
    "rango_abierto_sin_cerrar": QColor(50,  50,  55),
}

# Antes esto era un dict estático con la etiqueta corta ya escrita en
# español ({"rango_abierto_sin_cerrar": "rango_abierto"}), y para los
# otros dos tipos ni siquiera había entrada — caían al fallback de
# _etiqueta_tipo_corta, que devolvía el valor crudo del enum
# (tema_faltante, video_faltante), que también está en español. Por eso
# la columna "Tipo" nunca traducía a inglés. i18n.py YA tenía las claves
# correctas (tipo_tema_faltante, tipo_rango_abierto, tipo_video_faltante)
# pero esta GUI nunca las usaba. Ahora _etiqueta_tipo_corta llama a
# i18n.t() en el momento de dibujar, así que respeta el idioma activo.
CLAVES_I18N_TIPO = {
    "tema_faltante":            "tipo_tema_faltante",
    "rango_abierto_sin_cerrar": "tipo_rango_abierto",
    "video_faltante":           "tipo_video_faltante",
}


def _etiqueta_tipo_corta(tipo_valor: str) -> str:
    return i18n.t(CLAVES_I18N_TIPO.get(tipo_valor, tipo_valor))


# Traduce el status tal como lo reporta MAL/Jikan ("Finished Airing",
# "Currently Airing", etc.) para mostrarlo en la UI/CSV en el idioma
# activo. Solo para mostrar — la lógica de comparación en
# comparador.py/orquestador.py siempre compara contra el string crudo en
# inglés, nunca contra esta traducción.
CLAVES_I18N_STATUS = {
    "finished airing":  "status_finished_airing",
    "currently airing": "status_currently_airing",
    "not yet aired":    "status_not_yet_aired",
}


def _traducir_status(status: "str | None") -> str:
    if not status:
        return "?"
    clave = CLAVES_I18N_STATUS.get(status.strip().lower())
    if clave is None:
        return status  # status no reconocido (ej. uno nuevo de MAL): mostrar tal cual, no tronar
    return i18n.t(clave)


class DelegateTipo(QStyledItemDelegate):
    """
    Delegate personalizado para la columna 'Tipo' de la tabla de
    discrepancias. Pinta franjas de color verticales, una por cada tipo
    de discrepancia que tiene el anime de esa fila — permite que un anime
    con 'tema_faltante' Y 'rango_abierto' muestre ambos colores en la
    misma celda, sin mezclarlos en un único color de fila.

    Los datos se leen desde el UserRole de la celda, que guarda la lista
    de tipos como [(tipo_valor, etiqueta_corta), ...].
    """

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex):
        tipos = index.data(Qt.ItemDataRole.UserRole)
        if not tipos:
            super().paint(painter, option, index)
            return

        painter.save()
        rect = option.rect
        n = len(tipos)
        alto_franja = rect.height() // n
        fm = QFontMetrics(option.font)

        for i, (tipo_valor, etiqueta) in enumerate(tipos):
            y = rect.top() + i * alto_franja
            # la última franja toma el espacio restante para evitar gaps de redondeo
            h = alto_franja if i < n - 1 else rect.height() - i * alto_franja
            franja = QRect(rect.left(), y, rect.width(), h)

            color = COLORES_TIPO.get(tipo_valor, QColor(35, 35, 35))
            painter.fillRect(franja, color)

            painter.setPen(QColor(220, 220, 220))
            texto_rect = franja.adjusted(4, 0, -4, 0)
            painter.drawText(texto_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, etiqueta)

        painter.restore()

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex):
        tipos = index.data(Qt.ItemDataRole.UserRole)
        if not tipos:
            return super().sizeHint(option, index)
        # altura mínima de 22px por tipo para que cada franja sea legible
        return super().sizeHint(option, index).__class__(
            option.rect.width(), max(22 * len(tipos), 30)
        )


class _WorkerEscaneo(QThread):
    """
    Corre una función del orquestador (orq.escanear_temporada u
    orq.detectar_animes_faltantes_en_at) en un hilo separado para no
    congelar la UI. Usa señales de Qt (el patrón nativo, en vez de
    queue.Queue + root.after() que usábamos en Tkinter) para reportar
    progreso y resultado final de forma segura hacia el hilo principal.

    Antes existían dos clases QThread casi idénticas
    (WorkerEscaneoDiscrepancias / WorkerEscaneoFaltantes), una por cada
    función del orquestador que corren — la única diferencia real era
    esa función y el tipo del resultado que emiten (terminado emite
    pyqtSignal(object), así que no hace falta un signal distinto por
    tipo). Se unifican recibiendo la función a ejecutar como parámetro
    del constructor.
    """

    progreso = pyqtSignal(int, int, str)         # indice, total, nombre_anime
    terminado = pyqtSignal(object)                # orq.ResultadoEscaneo u orq.ResultadoFaltantes
    error_fatal = pyqtSignal(str)                  # mensaje de error

    def __init__(self, funcion_escaneo, year: int, season: str):
        super().__init__()
        self.funcion_escaneo = funcion_escaneo
        self.year = year
        self.season = season

    def run(self):
        def callback_progreso(indice, total, nombre_anime):
            self.progreso.emit(indice, total, nombre_anime)

        try:
            resultado = self.funcion_escaneo(
                self.year, self.season, progreso_callback=callback_progreso
            )
            self.terminado.emit(resultado)
        except Exception as e:  # noqa: BLE001 — cualquier fallo se reporta, no debe tronar el hilo
            self.error_fatal.emit(str(e))


SEASONS = ["Winter", "Spring", "Summer", "Fall"]

# Mes en que arranca cada temporada (aprox.), para filtrar qué temporadas
# del año ACTUAL ya empezaron o están en curso. Confirmado con el usuario:
# Winter=enero, Spring=abril, Summer=julio, Fall=octubre.
MES_INICIO_TEMPORADA = {"Winter": 1, "Spring": 4, "Summer": 7, "Fall": 10}


def _temporadas_disponibles(year: int) -> list[str]:
    """
    Devuelve las temporadas que tiene sentido escanear para un año dado.
    Si el año es ANTERIOR al actual, las 4 siempre aplican (ya pasaron
    todas). Si es el año ACTUAL, solo las que ya empezaron o están en
    curso (no tiene sentido escanear una temporada que aún no llega,
    nunca habría nada que encontrar). Si es un año FUTURO, ninguna aplica
    todavía — se devuelve la lista vacía en ese caso límite.
    """
    hoy = datetime.date.today()

    if year < hoy.year:
        return list(SEASONS)
    if year > hoy.year:
        return []

    return [s for s in SEASONS if MES_INICIO_TEMPORADA[s] <= hoy.month]


# El valor interno de temporada SIEMPRE es en inglés (Winter/Spring/...),
# porque es lo que espera la API de AnimeThemes (animethemes_client.py) y
# lo que se usa como clave en _resultados_por_temporada. Solo el TEXTO que
# ve el usuario en el combo debe traducirse — por eso el combo guarda el
# valor en inglés como userData (igual patrón que combo_idioma) y muestra
# el texto traducido como label visible.
SEASON_A_CLAVE_I18N = {
    "Winter": "season_winter",
    "Spring": "season_spring",
    "Summer": "season_summer",
    "Fall":   "season_fall",
}


def _texto_temporada(season_en: str) -> str:
    return i18n.t(SEASON_A_CLAVE_I18N.get(season_en, season_en))


def _poblar_combo_temporadas(combo: QComboBox, temporadas: list[str], seleccion_previa_en: "str | None", year: int) -> bool:
    """
    Repuebla un combo de temporadas mostrando el texto traducido según el
    idioma activo, pero guardando el valor interno en inglés como
    userData. Intenta conservar la selección previa (por su valor en
    inglés, no por el texto mostrado, que puede cambiar de idioma entre
    llamadas). Si no había selección previa y el año es el actual,
    preselecciona la temporada en curso. Devuelve True si el combo quedó
    con al menos una temporada disponible.
    """
    combo.blockSignals(True)
    combo.clear()
    if temporadas:
        for season_en in temporadas:
            combo.addItem(_texto_temporada(season_en), userData=season_en)

        if seleccion_previa_en in temporadas:
            idx = combo.findData(seleccion_previa_en)
            if idx >= 0:
                combo.setCurrentIndex(idx)
        elif not seleccion_previa_en and year == datetime.date.today().year:
            actual = _temporada_actual()
            if actual in temporadas:
                idx = combo.findData(actual)
                if idx >= 0:
                    combo.setCurrentIndex(idx)
    combo.blockSignals(False)
    return bool(temporadas)


def _formatear_antiguedad(segundos: float) -> str:
    segundos = int(segundos)
    if segundos < 60:
        return i18n.t("age_seconds")
    minutos = segundos // 60
    if minutos < 60:
        return i18n.t("age_minutes_1") if minutos == 1 else i18n.t("age_minutes_n", n=minutos)
    horas = minutos // 60
    return i18n.t("age_hours_1") if horas == 1 else i18n.t("age_hours_n", n=horas)



def _temporada_actual() -> str:
    """
    Devuelve la temporada en curso AHORA MISMO (según el mes de hoy), para
    preseleccionarla por defecto al abrir el programa — más útil que
    siempre arrancar en Winter, ya que lo más probable es que el usuario
    quiera revisar la temporada más reciente.
    """
    hoy = datetime.date.today()
    # Recorremos de la más tardía a la más temprana y devolvemos la
    # primera cuyo mes de inicio ya llegó.
    for season in reversed(SEASONS):
        if MES_INICIO_TEMPORADA[season] <= hoy.month:
            return season
    return SEASONS[0]  # caso límite teórico, no debería alcanzarse


TEMA_OSCURO_QSS = """
QWidget {
    background-color: #1e1e1e;
    color: #e0e0e0;
    font-size: 13px;
}
QMainWindow {
    background-color: #1e1e1e;
}
QTabWidget::pane {
    border: 1px solid #3a3a3a;
    background-color: #232323;
}
QTabBar::tab {
    background-color: #2b2b2b;
    color: #c0c0c0;
    padding: 8px 16px;
    border: 1px solid #3a3a3a;
    border-bottom: none;
}
QTabBar::tab:selected {
    background-color: #3a7bd5;
    color: #ffffff;
}
QTabBar::tab:hover {
    background-color: #353535;
}
QPushButton {
    background-color: #3a7bd5;
    color: #ffffff;
    border: none;
    padding: 6px 14px;
    border-radius: 4px;
}
QPushButton:hover {
    background-color: #4a8be5;
}
QPushButton:disabled {
    background-color: #3a3a3a;
    color: #707070;
}
QLineEdit, QComboBox {
    background-color: #2b2b2b;
    color: #e0e0e0;
    border: 1px solid #3a3a3a;
    padding: 4px;
    border-radius: 3px;
}
QSpinBox {
    background-color: #2b2b2b;
    color: #e0e0e0;
    border: 1px solid #3a3a3a;
    border-radius: 3px;
    padding: 2px 2px 2px 4px;
}
QSpinBox::up-button, QSpinBox::down-button {
    background-color: #353535;
    width: 16px;
    border: none;
    border-left: 1px solid #3a3a3a;
}
QSpinBox::up-button {
    subcontrol-position: top right;
    subcontrol-origin: border;
    border-bottom: 1px solid #3a3a3a;
    border-top-right-radius: 3px;
}
QSpinBox::down-button {
    subcontrol-position: bottom right;
    subcontrol-origin: border;
    border-bottom-right-radius: 3px;
}
QSpinBox::up-button:hover, QSpinBox::down-button:hover {
    background-color: #454545;
}
QSpinBox::up-button:pressed, QSpinBox::down-button:pressed {
    background-color: #3a7bd5;
}
QSpinBox::up-arrow {
    image: none;
    border-left: 3px solid transparent;
    border-right: 3px solid transparent;
    border-bottom: 4px solid #c0c0c0;
    width: 0;
    height: 0;
}
QSpinBox::down-arrow {
    image: none;
    border-left: 3px solid transparent;
    border-right: 3px solid transparent;
    border-top: 4px solid #c0c0c0;
    width: 0;
    height: 0;
}
QComboBox QAbstractItemView {
    background-color: #2b2b2b;
    color: #e0e0e0;
    selection-background-color: #3a7bd5;
}
QTableWidget, QTreeWidget {
    background-color: #232323;
    color: #e0e0e0;
    gridline-color: #3a3a3a;
    border: 1px solid #3a3a3a;
}
QHeaderView::section {
    background-color: #2b2b2b;
    color: #e0e0e0;
    padding: 4px;
    border: 1px solid #3a3a3a;
}
QProgressBar {
    background-color: #2b2b2b;
    border: 1px solid #3a3a3a;
    border-radius: 3px;
    text-align: center;
    color: #e0e0e0;
}
QProgressBar::chunk {
    background-color: #3a7bd5;
    border-radius: 3px;
}
QCheckBox {
    color: #e0e0e0;
}
QScrollBar:vertical {
    background-color: #2b2b2b;
    width: 12px;
}
QScrollBar::handle:vertical {
    background-color: #4a4a4a;
    border-radius: 4px;
    min-height: 20px;
}
QScrollBar::handle:vertical:hover {
    background-color: #5a5a5a;
}
"""


class PestanaDiscrepancias(QWidget):
    """
    Pestaña de Discrepancias: panel de controles (año, temporada dinámica,
    filtro por tipo en combobox, botones), barra de progreso, y un
    QTabWidget interno con 2 sub-pestañas (Discrepancias / Omitidos-Errores).
    """

    def __init__(self):
        super().__init__()
        # Etiquetas amigables para el filtro (combobox en vez de
        # checkboxes, a pedido del usuario). "Todos" no filtra nada.
        # Vive en __init__ (atributo de instancia), no como atributo de
        # clase: antes se calculaba una sola vez al cargar el módulo,
        # con el idioma que estuviera activo en ESE momento — funcionaba
        # porque solo existe una instancia real de esta clase, pero era
        # confuso de mantener (¿por qué hay un OPCIONES_FILTRO de clase
        # Y uno de instancia que lo tapa en _actualizar_textos?).
        self.OPCIONES_FILTRO = {
            i18n.t("filter_all"): None,
            i18n.t("filter_tema_faltante"): "tema_faltante",
            i18n.t("filter_rango_abierto"): "rango_abierto_sin_cerrar",
            i18n.t("filter_video_faltante"): "video_faltante",
        }
        self._resultado_actual: orq.ResultadoEscaneo | None = None
        self._filas_discrepancias: list[tuple[str, list[tuple[str, str]], str]] = []  # (nombre, [(tipo, desc)], url_at)
        self._worker: _WorkerEscaneo | None = None
        # Caché en memoria de resultados ya escaneados en esta sesión, por
        # (year, season) -> (datetime_en_que_se_guardo, ResultadoEscaneo).
        # Permite volver a ver una temporada ya escaneada sin tener que
        # re-escanear, simplemente cambiando el selector — el botón
        # "Escanear" sigue disponible para forzar una actualización fresca.
        self._resultados_por_temporada: dict[tuple[int, str], tuple[datetime.datetime, orq.ResultadoEscaneo]] = {}

        layout = QVBoxLayout(self)
        layout.addLayout(self._construir_panel_controles())
        layout.addWidget(self._construir_barra_progreso())
        layout.addWidget(self._construir_subpestanas())

    # ---------- construcción de la UI ----------

    def _construir_panel_controles(self) -> QHBoxLayout:
        fila = QHBoxLayout()

        self.label_anio = QLabel(i18n.t("year_label"))
        fila.addWidget(self.label_anio)
        self.spin_year = QSpinBox()
        self.spin_year.setRange(1960, datetime.date.today().year)
        self.spin_year.setValue(datetime.date.today().year)
        self.spin_year.valueChanged.connect(self._actualizar_temporadas_disponibles)
        fila.addWidget(self.spin_year)

        self.label_temporada = QLabel(i18n.t("season_label"))
        fila.addWidget(self.label_temporada)
        self.combo_season = QComboBox()
        # Conectado por índice, no por texto: el texto mostrado cambia de
        # idioma, pero el valor real que nos interesa (userData, en
        # inglés) no. Ver _poblar_combo_temporadas.
        self.combo_season.currentIndexChanged.connect(
            lambda _i: self._on_cambio_temporada_seleccionada(self.combo_season.currentData())
        )
        fila.addWidget(self.combo_season)

        self.btn_escanear = QPushButton(i18n.t("scan_button"))
        self.btn_escanear.clicked.connect(self._iniciar_escaneo)
        fila.addWidget(self.btn_escanear)

        fila.addStretch()

        self.btn_exportar = QPushButton(i18n.t("export_button"))
        self.btn_exportar.setEnabled(False)
        self.btn_exportar.clicked.connect(self._exportar_csv)
        fila.addWidget(self.btn_exportar)

        self._actualizar_temporadas_disponibles()  # llenar el combo inicial

        return fila

    def _construir_barra_progreso(self) -> QWidget:
        contenedor = QWidget()
        layout = QVBoxLayout(contenedor)
        layout.setContentsMargins(0, 0, 0, 0)

        self.barra_progreso = QProgressBar()
        self.barra_progreso.setRange(0, 1)
        self.barra_progreso.setValue(0)
        layout.addWidget(self.barra_progreso)

        # "Filtrar por:" vive en esta misma fila, a la derecha del label
        # de estado (a pedido del usuario — antes vivía en la esquina de
        # las sub-pestañas, pero quedaba muy abajo y lejos del resto de
        # los controles).
        fila_estado = QHBoxLayout()
        self.label_estado = QLabel(i18n.t("status_ready"))
        fila_estado.addWidget(self.label_estado)
        fila_estado.addStretch()

        self.label_filtro = QLabel(i18n.t("filter_label"))
        fila_estado.addWidget(self.label_filtro)
        self.combo_filtro = QComboBox()
        self.combo_filtro.addItems(self.OPCIONES_FILTRO.keys())
        self.combo_filtro.currentTextChanged.connect(self._aplicar_filtro)
        fila_estado.addWidget(self.combo_filtro)

        layout.addLayout(fila_estado)

        return contenedor

    def _construir_subpestanas(self) -> QTabWidget:
        sub = QTabWidget()
        # Pestañas abajo, estilo hojas de Excel (a pedido del usuario).
        sub.setTabPosition(QTabWidget.TabPosition.South)

        # --- subpestaña: discrepancias ---
        self.tabla_discrepancias = QTableWidget(0, 3)
        self.tabla_discrepancias.setHorizontalHeaderLabels([i18n.t("col_anime"), i18n.t("col_tipo"), i18n.t("col_descripcion")])
        self.tabla_discrepancias.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tabla_discrepancias.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tabla_discrepancias.setWordWrap(True)
        header = self.tabla_discrepancias.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.tabla_discrepancias.setColumnWidth(0, 260)
        self.tabla_discrepancias.setColumnWidth(1, 150)
        self.tabla_discrepancias.cellClicked.connect(self._on_click_celda_discrepancias)
        sub.addTab(self.tabla_discrepancias, i18n.t("subtab_discrepancias"))

        # --- subpestaña: omitidos / errores ---
        self.tabla_omitidos = QTableWidget(0, 2)
        self.tabla_omitidos.setHorizontalHeaderLabels([i18n.t("col_anime"), i18n.t("col_motivo")])
        self.tabla_omitidos.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tabla_omitidos.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        header2 = self.tabla_omitidos.horizontalHeader()
        header2.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        header2.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.tabla_omitidos.setColumnWidth(0, 300)
        sub.addTab(self.tabla_omitidos, i18n.t("subtab_omitidos"))

        self.sub_tabs = sub  # guardado para poder retraducir sus pestañas en _actualizar_textos
        return sub

    def _actualizar_temporadas_disponibles(self):
        """
        Se llama al inicio y cada vez que cambia el año: recalcula qué
        temporadas tiene sentido mostrar (ver _temporadas_disponibles) y
        repuebla el combo, intentando conservar la selección actual si
        sigue siendo válida. Si no hay selección previa (primera vez que
        se construye) y el año es el actual, preselecciona la temporada
        EN CURSO en vez de la primera de la lista — más útil al abrir el
        programa, ya que lo más probable es que se quiera revisar la
        temporada más reciente.
        """
        seleccion_previa = self.combo_season.currentData()
        temporadas = _temporadas_disponibles(self.spin_year.value())

        hay_temporadas = _poblar_combo_temporadas(
            self.combo_season, temporadas, seleccion_previa, self.spin_year.value()
        )

        self.combo_season.setEnabled(hay_temporadas)
        if hasattr(self, "btn_escanear"):
            self.btn_escanear.setEnabled(hay_temporadas)
        if hasattr(self, "label_estado"):
            if not hay_temporadas:
                self.label_estado.setText(
                    i18n.t("status_no_seasons")
                )
            else:
                self.label_estado.setText(i18n.t("status_ready"))

    def _on_cambio_temporada_seleccionada(self, season: str):
        """
        Se dispara al cambiar la temporada seleccionada (incluye los
        repoblados internos de _actualizar_temporadas_disponibles, que
        también disparan esta señal — por eso se ignora silenciosamente
        si season viene vacío, que es el estado transitorio mientras se
        repuebla el combo).

        Si ya existe un resultado guardado en memoria para (año, season),
        lo muestra de inmediato sin tocar la red, junto con un aviso de
        cuándo se obtuvo. El botón Escanear sigue disponible para forzar
        una actualización fresca en cualquier momento.

        Si NO hay nada guardado para la nueva combinación, se limpia la
        tabla explícitamente — de lo contrario quedarían visibles los
        resultados de la selección anterior, dando la falsa impresión de
        que pertenecen a la nueva.
        """
        if not season or (self._worker is not None and self._worker.isRunning()):
            return

        year = self.spin_year.value()
        clave = (year, season)
        entrada = self._resultados_por_temporada.get(clave)
        if entrada is None:
            self._limpiar_resultados()
            self._resultado_actual = None
            self.btn_exportar.setEnabled(False)
            self.label_estado.setText(i18n.t("status_ready"))
            return

        momento_guardado, resultado = entrada
        self._resultado_actual = resultado
        self._mostrar_resultado(resultado)

        segundos = (datetime.datetime.now() - momento_guardado).total_seconds()
        antiguedad = _formatear_antiguedad(segundos)
        self.label_estado.setText(
            i18n.t("status_cached", age=antiguedad)
        )

    # ---------- lógica de escaneo ----------

    def _iniciar_escaneo(self):
        if self._worker is not None and self._worker.isRunning():
            return

        year = self.spin_year.value()
        season = self.combo_season.currentData()  # valor interno en inglés, para la API
        if not season:
            return

        self._limpiar_resultados()
        self.spin_year.setEnabled(False)
        self.combo_season.setEnabled(False)
        self.btn_escanear.setEnabled(False)
        self.btn_exportar.setEnabled(False)
        self.barra_progreso.setMaximum(1)
        self.barra_progreso.setValue(0)
        self.label_estado.setText(i18n.t("status_starting", season=_texto_temporada(season), year=year))

        self._worker = _WorkerEscaneo(orq.escanear_temporada, year, season)
        self._worker.progreso.connect(self._on_progreso)
        self._worker.terminado.connect(self._on_terminado)
        self._worker.error_fatal.connect(self._on_error_fatal)
        self._worker.start()

    def _on_progreso(self, indice: int, total: int, nombre_anime: str):
        self.barra_progreso.setMaximum(max(total, 1))
        self.barra_progreso.setValue(indice)
        self.label_estado.setText(i18n.t("status_scanning", i=indice, total=total, name=nombre_anime))

    def _on_terminado(self, resultado: orq.ResultadoEscaneo):
        if self._worker is not None:
            clave = (self._worker.year, self._worker.season)
            self._resultados_por_temporada[clave] = (datetime.datetime.now(), resultado)

        self._resultado_actual = resultado
        self._mostrar_resultado(resultado)
        self._rehabilitar_controles()

        # Canario de posible cambio de HTML en MAL (issue #3): se muestra
        # aparte de _on_error_fatal (QMessageBox.warning, no critical) y
        # después de pintar los resultados — no es un error de red ni
        # bloquea nada, es una sospecha sobre la validez de lo que ya se
        # mostró. Ver orq.ResultadoEscaneo.alerta_posible_cambio_html_mal.
        if resultado.alerta_posible_cambio_html_mal:
            QMessageBox.warning(
                self,
                i18n.t("dlg_canario_title"),
                i18n.t(
                    "dlg_canario_msg",
                    vacios=resultado.total_finished_airing_con_temas_mal_vacios,
                    evaluados=resultado.total_finished_airing_evaluados,
                ),
            )

    def _on_error_fatal(self, mensaje: str):
        self._rehabilitar_controles()
        self.label_estado.setText(i18n.t("status_error"))
        QMessageBox.critical(self, i18n.t("dlg_scan_error_title"), mensaje)

    def _rehabilitar_controles(self):
        self.spin_year.setEnabled(True)
        self.combo_season.setEnabled(True)
        self.btn_escanear.setEnabled(True)
        self.btn_exportar.setEnabled(True)

    def _actualizar_textos(self):
        """Re-renderiza todas las etiquetas traducibles cuando cambia el idioma."""
        self.btn_escanear.setText(i18n.t("scan_button"))
        self.btn_exportar.setText(i18n.t("export_button"))
        self.label_anio.setText(i18n.t("year_label"))
        self.label_temporada.setText(i18n.t("season_label"))
        self.label_filtro.setText(i18n.t("filter_label"))
        # Repoblar el combo de temporada con el texto traducido, sin
        # perder la selección (se conserva por el valor en inglés, ver
        # _poblar_combo_temporadas).
        self._actualizar_temporadas_disponibles()
        tipo_actual_valor = self.OPCIONES_FILTRO.get(self.combo_filtro.currentText())
        self.OPCIONES_FILTRO = {
            i18n.t("filter_all"): None,
            i18n.t("filter_tema_faltante"): "tema_faltante",
            i18n.t("filter_rango_abierto"): "rango_abierto_sin_cerrar",
            i18n.t("filter_video_faltante"): "video_faltante",
        }
        self.combo_filtro.blockSignals(True)
        self.combo_filtro.clear()
        self.combo_filtro.addItems(self.OPCIONES_FILTRO.keys())
        for etiqueta, valor in self.OPCIONES_FILTRO.items():
            if valor == tipo_actual_valor:
                self.combo_filtro.setCurrentText(etiqueta)
                break
        self.combo_filtro.blockSignals(False)
        self.tabla_discrepancias.setHorizontalHeaderLabels(
            [i18n.t("col_anime"), i18n.t("col_tipo"), i18n.t("col_descripcion")]
        )
        self.tabla_omitidos.setHorizontalHeaderLabels(
            [i18n.t("col_anime"), i18n.t("col_motivo")]
        )
        self.sub_tabs.setTabText(0, i18n.t("subtab_discrepancias"))
        self.sub_tabs.setTabText(1, i18n.t("subtab_omitidos"))
        # Repintar las filas ya visibles: _aplicar_filtro vuelve a leer
        # self._filas_discrepancias y llama a _etiqueta_tipo_corta al
        # dibujar, así que las etiquetas de Tipo quedan en el idioma
        # nuevo sin necesidad de volver a escanear.
        if self._filas_discrepancias:
            self._aplicar_filtro()

    # ---------- presentación de resultados ----------

    def _limpiar_resultados(self):
        self.tabla_discrepancias.setRowCount(0)
        self.tabla_omitidos.setRowCount(0)
        self._filas_discrepancias = []

    def _mostrar_resultado(self, resultado: orq.ResultadoEscaneo):
        self._limpiar_resultados()
        self._filas_discrepancias = self._construir_filas_agrupadas(resultado.con_problemas)
        self._aplicar_filtro()

        self.tabla_omitidos.setRowCount(0)
        for nombre in resultado.omitidos_sin_mal_id:
            self._agregar_fila_omitidos(nombre, i18n.t("omit_no_mal_id"))
        for nombre in resultado.omitidos_no_terminados:
            self._agregar_fila_omitidos(nombre, i18n.t("omit_not_finished"))
        for nombre, mensaje_error in resultado.errores:
            self._agregar_fila_omitidos(nombre, i18n.t("omit_error", msg=mensaje_error))

        total_con_problemas = len(resultado.con_problemas)
        total_revisados = len(resultado.resultados)
        self.label_estado.setText(
            i18n.t("status_done_disc", n=total_con_problemas, total=total_revisados)
        )

    @staticmethod
    def _construir_filas_agrupadas(animes_con_problemas) -> list[tuple[str, list[tuple[str, str]], str]]:
        """
        Devuelve una entrada por anime:
        (nombre, [(tipo_valor, descripcion_compactada), ...], url_at)

        Al pintar la tabla, el nombre se fusiona verticalmente (setSpan)
        cubriendo todas las filas de sus tipos — mismo efecto que
        'Combinar celdas' en Excel. Tipo y Descripción quedan en filas
        separadas, perfectamente alineados entre sí.
        """
        filas = []
        for anime_resultado in animes_con_problemas:
            por_tipo: dict[str, list[str]] = {}
            orden_tipos: list[str] = []
            for d in anime_resultado.discrepancias:
                if d.tipo.value not in por_tipo:
                    por_tipo[d.tipo.value] = []
                    orden_tipos.append(d.tipo.value)
                por_tipo[d.tipo.value].append(d.descripcion)

            tipos_con_desc = [
                (tipo_valor, "\n".join(por_tipo[tipo_valor]))
                for tipo_valor in orden_tipos
            ]
            url_at = f"https://animethemes.moe/anime/{anime_resultado.slug}"
            filas.append((anime_resultado.nombre, tipos_con_desc, url_at))

        return filas

    def _aplicar_filtro(self):
        """Repinta la tabla filtrando por tipo si el usuario eligió uno concreto."""
        tipo_filtro = self.OPCIONES_FILTRO.get(self.combo_filtro.currentText())

        self.tabla_discrepancias.setRowCount(0)
        for nombre, tipos_con_desc, url_at in self._filas_discrepancias:
            if tipo_filtro is not None:
                tipos_filtrados = [(t, d) for t, d in tipos_con_desc if t == tipo_filtro]
                if not tipos_filtrados:
                    continue
            else:
                tipos_filtrados = tipos_con_desc

            self._agregar_fila_discrepancia(nombre, tipos_filtrados, url_at)

        self.tabla_discrepancias.resizeRowsToContents()

    def _agregar_fila_discrepancia(self, anime: str, tipos_con_desc: list[tuple[str, str]], url_at: str):
        """
        Inserta una fila por tipo de discrepancia, y fusiona la columna
        Anime verticalmente sobre todas ellas (setSpan), igual que
        'Combinar celdas' en Excel. Tipo y Descripción quedan perfectamente
        alineados en la misma fila, cada una con su color de fondo propio.
        """
        fila_inicio = self.tabla_discrepancias.rowCount()
        n = len(tipos_con_desc)

        for i, (tipo_valor, descripcion) in enumerate(tipos_con_desc):
            self.tabla_discrepancias.insertRow(self.tabla_discrepancias.rowCount())
            fila = self.tabla_discrepancias.rowCount() - 1
            color = QBrush(COLORES_TIPO.get(tipo_valor, QColor(35, 35, 35)))
            etiqueta = _etiqueta_tipo_corta(tipo_valor)

            # Columna Anime: solo en la primera fila del grupo
            if i == 0:
                item_anime = QTableWidgetItem(anime)
                item_anime.setForeground(Qt.GlobalColor.cyan)
                item_anime.setData(Qt.ItemDataRole.UserRole, url_at)
                item_anime.setTextAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
                self.tabla_discrepancias.setItem(fila, 0, item_anime)
            else:
                self.tabla_discrepancias.setItem(fila, 0, QTableWidgetItem(""))

            # Columna Tipo con color de fondo
            item_tipo = QTableWidgetItem(etiqueta)
            item_tipo.setBackground(color)
            self.tabla_discrepancias.setItem(fila, 1, item_tipo)

            # Columna Descripción con color de fondo
            item_desc = QTableWidgetItem(descripcion)
            item_desc.setBackground(color)
            self.tabla_discrepancias.setItem(fila, 2, item_desc)

        # Fusionar la columna Anime sobre todas las filas del grupo
        if n > 1:
            self.tabla_discrepancias.setSpan(fila_inicio, 0, n, 1)

    def _on_click_celda_discrepancias(self, fila: int, columna: int):
        """Si se hace clic en la columna del nombre del anime, abre su página en AnimeThemes."""
        if columna != 0:
            return
        # Con setSpan, el item vive en la primera fila del grupo.
        # Buscamos hacia arriba hasta encontrar el item con URL.
        for f in range(fila, -1, -1):
            item = self.tabla_discrepancias.item(f, 0)
            if item is None:
                continue
            url = item.data(Qt.ItemDataRole.UserRole)
            if url:
                QDesktopServices.openUrl(QUrl(url))
                return

    def _agregar_fila_omitidos(self, anime: str, motivo: str):
        fila = self.tabla_omitidos.rowCount()
        self.tabla_omitidos.insertRow(fila)
        self.tabla_omitidos.setItem(fila, 0, QTableWidgetItem(anime))
        self.tabla_omitidos.setItem(fila, 1, QTableWidgetItem(motivo))

    # ---------- exportar ----------

    def _exportar_csv(self):
        if self._resultado_actual is None:
            return

        ruta, _ = QFileDialog.getSaveFileName(
            self, i18n.t("export_button"), "animethemes_discrepancias.csv", "CSV (*.csv)"
        )
        if not ruta:
            return

        with open(ruta, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow([
                i18n.t("col_anime"), i18n.t("col_tipo"), i18n.t("col_descripcion"),
                i18n.t("col_mal_id"), i18n.t("col_status_mal"),
            ])
            for anime_resultado in self._resultado_actual.con_problemas:
                for d in anime_resultado.discrepancias:
                    writer.writerow([
                        anime_resultado.nombre,
                        _etiqueta_tipo_corta(d.tipo.value),
                        d.descripcion,
                        anime_resultado.mal_id,
                        _traducir_status(anime_resultado.status_mal),
                    ])

        QMessageBox.information(self, i18n.t("dlg_exported_title"), i18n.t("dlg_exported_msg", path=ruta))


class PestanaAnimesFaltantes(QWidget):
    """
    Pestaña de Animes Faltantes: detecta animes que MAL reporta para la
    temporada y que NO existen en absoluto en AnimeThemes. Mismo patrón
    que PestanaDiscrepancias pero pasando
    orq.detectar_animes_faltantes_en_at a _WorkerEscaneo y usando
    orq.ResultadoFaltantes (que tiene .faltantes y .errores).
    """

    def __init__(self):
        super().__init__()
        self._resultado_actual: orq.ResultadoFaltantes | None = None
        self._worker: _WorkerEscaneo | None = None
        self._resultados_por_temporada: dict[tuple[int, str], tuple[datetime.datetime, orq.ResultadoFaltantes]] = {}

        layout = QVBoxLayout(self)
        layout.addLayout(self._construir_panel_controles())
        layout.addWidget(self._construir_barra_progreso())
        layout.addWidget(self._construir_subpestanas())

    # ---------- construcción de la UI ----------

    def _construir_panel_controles(self) -> QHBoxLayout:
        fila = QHBoxLayout()

        self.label_anio = QLabel(i18n.t("year_label"))
        fila.addWidget(self.label_anio)
        self.spin_year = QSpinBox()
        self.spin_year.setRange(1960, datetime.date.today().year)
        self.spin_year.setValue(datetime.date.today().year)
        self.spin_year.valueChanged.connect(self._actualizar_temporadas_disponibles)
        fila.addWidget(self.spin_year)

        self.label_temporada = QLabel(i18n.t("season_label"))
        fila.addWidget(self.label_temporada)
        self.combo_season = QComboBox()
        # Conectado por índice, no por texto: el texto mostrado cambia de
        # idioma, pero el valor real que nos interesa (userData, en
        # inglés) no. Ver _poblar_combo_temporadas.
        self.combo_season.currentIndexChanged.connect(
            lambda _i: self._on_cambio_temporada_seleccionada(self.combo_season.currentData())
        )
        fila.addWidget(self.combo_season)

        self.btn_escanear = QPushButton(i18n.t("scan_button"))
        self.btn_escanear.clicked.connect(self._iniciar_escaneo)
        fila.addWidget(self.btn_escanear)

        fila.addStretch()

        self.btn_exportar = QPushButton(i18n.t("export_button"))
        self.btn_exportar.setEnabled(False)
        self.btn_exportar.clicked.connect(self._exportar_csv)
        fila.addWidget(self.btn_exportar)

        self._actualizar_temporadas_disponibles()

        return fila

    def _construir_barra_progreso(self) -> QWidget:
        contenedor = QWidget()
        layout = QVBoxLayout(contenedor)
        layout.setContentsMargins(0, 0, 0, 0)

        self.barra_progreso = QProgressBar()
        self.barra_progreso.setRange(0, 1)
        self.barra_progreso.setValue(0)
        layout.addWidget(self.barra_progreso)

        self.label_estado = QLabel(
            i18n.t("status_ready_note")
        )
        layout.addWidget(self.label_estado)

        return contenedor

    def _construir_subpestanas(self) -> QTabWidget:
        sub = QTabWidget()
        # Pestañas abajo, estilo hojas de Excel (a pedido del usuario) —
        # el corner widget ("Filtrar por:"), si lo hay, se mueve junto
        # con la barra de pestañas automáticamente.
        sub.setTabPosition(QTabWidget.TabPosition.South)

        # --- subpestaña: animes faltantes ---
        self.tabla_faltantes = QTableWidget(0, 4)
        self.tabla_faltantes.setHorizontalHeaderLabels(
            [i18n.t("col_tipo_af"), i18n.t("col_titulo"), i18n.t("col_mal_id"), i18n.t("col_status")]
        )
        self.tabla_faltantes.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tabla_faltantes.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        header = self.tabla_faltantes.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        self.tabla_faltantes.setColumnWidth(0, 90)
        self.tabla_faltantes.setColumnWidth(2, 80)
        self.tabla_faltantes.setColumnWidth(3, 140)
        self.tabla_faltantes.cellClicked.connect(self._on_click_celda_faltantes)
        sub.addTab(self.tabla_faltantes, i18n.t("subtab_faltantes"))

        # --- subpestaña: errores ---
        self.tabla_errores = QTableWidget(0, 2)
        self.tabla_errores.setHorizontalHeaderLabels([i18n.t("col_anime"), i18n.t("col_error")])
        self.tabla_errores.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tabla_errores.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        header_err = self.tabla_errores.horizontalHeader()
        header_err.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        header_err.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.tabla_errores.setColumnWidth(0, 300)
        sub.addTab(self.tabla_errores, i18n.t("subtab_errores"))

        self.sub_tabs = sub  # guardado para poder retraducir sus pestañas en _actualizar_textos
        return sub

    def _actualizar_temporadas_disponibles(self):
        seleccion_previa = self.combo_season.currentData()
        temporadas = _temporadas_disponibles(self.spin_year.value())

        hay_temporadas = _poblar_combo_temporadas(
            self.combo_season, temporadas, seleccion_previa, self.spin_year.value()
        )

        self.combo_season.setEnabled(hay_temporadas)
        if hasattr(self, "btn_escanear"):
            self.btn_escanear.setEnabled(hay_temporadas)
        if hasattr(self, "label_estado"):
            if not hay_temporadas:
                self.label_estado.setText(
                    i18n.t("status_no_seasons")
                )
            else:
                self.label_estado.setText(i18n.t("status_ready"))

    def _on_cambio_temporada_seleccionada(self, season: str):
        """Ver docstring de la misma función en PestanaDiscrepancias: comportamiento idéntico."""
        if not season or (self._worker is not None and self._worker.isRunning()):
            return

        year = self.spin_year.value()
        clave = (year, season)
        entrada = self._resultados_por_temporada.get(clave)
        if entrada is None:
            self._limpiar_resultados()
            self._resultado_actual = None
            self.btn_exportar.setEnabled(False)
            self.label_estado.setText(i18n.t("status_ready"))
            return

        momento_guardado, resultado = entrada
        self._resultado_actual = resultado
        self._mostrar_resultado(resultado)

        segundos = (datetime.datetime.now() - momento_guardado).total_seconds()
        antiguedad = _formatear_antiguedad(segundos)
        self.label_estado.setText(
            i18n.t("status_cached", age=antiguedad)
        )

    # ---------- lógica de escaneo ----------

    def _iniciar_escaneo(self):
        if self._worker is not None and self._worker.isRunning():
            return

        year = self.spin_year.value()
        season = self.combo_season.currentData()  # valor interno en inglés, para la API
        if not season:
            return

        self._limpiar_resultados()
        self.spin_year.setEnabled(False)
        self.combo_season.setEnabled(False)
        self.btn_escanear.setEnabled(False)
        self.btn_exportar.setEnabled(False)
        self.barra_progreso.setMaximum(1)
        self.barra_progreso.setValue(0)
        self.label_estado.setText(i18n.t("status_starting", season=_texto_temporada(season), year=year))

        self._worker = _WorkerEscaneo(orq.detectar_animes_faltantes_en_at, year, season)
        self._worker.progreso.connect(self._on_progreso)
        self._worker.terminado.connect(self._on_terminado)
        self._worker.error_fatal.connect(self._on_error_fatal)
        self._worker.start()

    def _on_progreso(self, indice: int, total: int, nombre_anime: str):
        self.barra_progreso.setMaximum(max(total, 1))
        self.barra_progreso.setValue(indice)
        self.label_estado.setText(i18n.t("status_verifying", i=indice, total=total, name=nombre_anime))

    def _on_terminado(self, resultado: orq.ResultadoFaltantes):
        if self._worker is not None:
            clave = (self._worker.year, self._worker.season)
            self._resultados_por_temporada[clave] = (datetime.datetime.now(), resultado)

        self._resultado_actual = resultado
        self._mostrar_resultado(resultado)
        self._rehabilitar_controles()

    def _on_error_fatal(self, mensaje: str):
        self._rehabilitar_controles()
        self.label_estado.setText(i18n.t("status_error"))
        QMessageBox.critical(self, i18n.t("dlg_scan_error_title"), mensaje)

    def _rehabilitar_controles(self):
        self.spin_year.setEnabled(True)
        self.combo_season.setEnabled(True)
        self.btn_escanear.setEnabled(True)
        self.btn_exportar.setEnabled(True)

    def _actualizar_textos(self):
        """Re-renderiza todas las etiquetas traducibles cuando cambia el idioma."""
        self.btn_escanear.setText(i18n.t("scan_button"))
        self.btn_exportar.setText(i18n.t("export_button"))
        self.label_anio.setText(i18n.t("year_label"))
        self.label_temporada.setText(i18n.t("season_label"))
        self._actualizar_temporadas_disponibles()
        self.tabla_faltantes.setHorizontalHeaderLabels([
            i18n.t("col_tipo_af"), i18n.t("col_titulo"),
            i18n.t("col_mal_id"), i18n.t("col_status")
        ])
        self.tabla_errores.setHorizontalHeaderLabels(
            [i18n.t("col_anime"), i18n.t("col_error")]
        )
        self.sub_tabs.setTabText(0, i18n.t("subtab_faltantes"))
        self.sub_tabs.setTabText(1, i18n.t("subtab_errores"))
        # Repintar las filas ya visibles: si ya hay un resultado en
        # pantalla, _mostrar_resultado lo vuelve a dibujar desde cero,
        # así el status ya mostrado también queda traducido al idioma
        # nuevo sin necesidad de volver a escanear.
        if self._resultado_actual is not None:
            self._mostrar_resultado(self._resultado_actual)

    # ---------- presentación de resultados ----------

    def _limpiar_resultados(self):
        self.tabla_faltantes.setRowCount(0)
        self.tabla_errores.setRowCount(0)

    def _mostrar_resultado(self, resultado: orq.ResultadoFaltantes):
        self._limpiar_resultados()

        for anime in resultado.faltantes:
            self._agregar_fila_faltante(anime)

        for titulo, mensaje_error in resultado.errores:
            fila = self.tabla_errores.rowCount()
            self.tabla_errores.insertRow(fila)
            self.tabla_errores.setItem(fila, 0, QTableWidgetItem(titulo))
            self.tabla_errores.setItem(fila, 1, QTableWidgetItem(mensaje_error))

        total_faltantes = len(resultado.faltantes)
        total_errores = len(resultado.errores)
        texto_estado = i18n.t("status_done_falt", n=total_faltantes)
        if total_errores:
            texto_estado += i18n.t("status_done_falt_errors", n_err=total_errores)
        self.label_estado.setText(texto_estado)

    def _agregar_fila_faltante(self, anime: orq.AnimeFaltanteEnAT):
        link = f"https://myanimelist.net/anime/{anime.mal_id}"
        fila = self.tabla_faltantes.rowCount()
        self.tabla_faltantes.insertRow(fila)
        self.tabla_faltantes.setItem(fila, 0, QTableWidgetItem(anime.tipo or "?"))

        item_titulo = QTableWidgetItem(anime.titulo)
        item_titulo.setForeground(Qt.GlobalColor.cyan)
        item_titulo.setData(Qt.ItemDataRole.UserRole, link)
        self.tabla_faltantes.setItem(fila, 1, item_titulo)

        self.tabla_faltantes.setItem(fila, 2, QTableWidgetItem(str(anime.mal_id)))
        self.tabla_faltantes.setItem(fila, 3, QTableWidgetItem(_traducir_status(anime.status)))

    def _on_click_celda_faltantes(self, fila: int, columna: int):
        """Si se hace clic en la columna del título, abre la página del anime en MAL."""
        if columna != 1:
            return
        item = self.tabla_faltantes.item(fila, columna)
        if item is not None:
            url = item.data(Qt.ItemDataRole.UserRole)
            if url:
                QDesktopServices.openUrl(QUrl(url))

    # ---------- exportar ----------

    def _exportar_csv(self):
        if self._resultado_actual is None:
            return

        ruta, _ = QFileDialog.getSaveFileName(
            self, i18n.t("export_button"), "animethemes_animes_faltantes.csv", "CSV (*.csv)"
        )
        if not ruta:
            return

        with open(ruta, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow([
                i18n.t("col_tipo_af"), i18n.t("col_titulo"),
                i18n.t("col_mal_id"), i18n.t("col_status"), i18n.t("col_link"),
            ])
            for anime in self._resultado_actual.faltantes:
                writer.writerow([
                    anime.tipo or "?",
                    anime.titulo,
                    anime.mal_id,
                    _traducir_status(anime.status),
                    f"https://myanimelist.net/anime/{anime.mal_id}",
                ])

        QMessageBox.information(self, i18n.t("dlg_exported_title"), i18n.t("dlg_exported_msg", path=ruta))


class VentanaPrincipal(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(i18n.t("window_title"))
        self.resize(1150, 700)

        # Barra superior: selector de idioma a la derecha
        barra_superior = QWidget()
        layout_barra = QHBoxLayout(barra_superior)
        layout_barra.setContentsMargins(4, 2, 4, 2)
        layout_barra.addStretch()
        self.label_idioma = QLabel(i18n.t("language_label"))
        layout_barra.addWidget(self.label_idioma)
        self.combo_idioma = QComboBox()
        for codigo, nombre in i18n.IDIOMAS.items():
            self.combo_idioma.addItem(nombre, userData=codigo)
        # Seleccionar el idioma activo
        idx = self.combo_idioma.findData(i18n.idioma_actual())
        if idx >= 0:
            self.combo_idioma.setCurrentIndex(idx)
        self.combo_idioma.currentIndexChanged.connect(self._on_cambio_idioma)
        layout_barra.addWidget(self.combo_idioma)

        self.tabs = QTabWidget()
        self.pestana_disc = PestanaDiscrepancias()
        self.pestana_falt = PestanaAnimesFaltantes()
        self.tabs.addTab(self.pestana_disc, i18n.t("tab_discrepancias"))
        self.tabs.addTab(self.pestana_falt, i18n.t("tab_faltantes"))

        contenedor = QWidget()
        layout_contenedor = QVBoxLayout(contenedor)
        layout_contenedor.setContentsMargins(0, 0, 0, 0)
        layout_contenedor.setSpacing(0)
        layout_contenedor.addWidget(barra_superior)
        layout_contenedor.addWidget(self.tabs)
        self.setCentralWidget(contenedor)

        # Registrar el callback de actualización de textos
        i18n.registrar_callback(self._actualizar_textos)

    def _on_cambio_idioma(self, index: int):
        codigo = self.combo_idioma.itemData(index)
        if codigo:
            i18n.cambiar_idioma(codigo)

    def _actualizar_textos(self):
        """Actualiza todas las etiquetas de la ventana cuando cambia el idioma."""
        self.setWindowTitle(i18n.t("window_title"))
        self.label_idioma.setText(i18n.t("language_label"))
        self.tabs.setTabText(0, i18n.t("tab_discrepancias"))
        self.tabs.setTabText(1, i18n.t("tab_faltantes"))
        self.pestana_disc._actualizar_textos()
        self.pestana_falt._actualizar_textos()


def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(TEMA_OSCURO_QSS)

    # Limpieza de mantenimiento del caché: se hace una sola vez por sesión,
    # al arrancar, para no dejar crecer cache_jikan.json indefinidamente
    # con entradas vencidas (de temporadas/animes que ya pasaron sus 15
    # días de validez y no se han vuelto a consultar). No bloquea nada
    # visible — es rápido, solo recorre y filtra el archivo JSON.
    cache_jikan.limpiar_expirados()

    ventana = VentanaPrincipal()
    ventana.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
