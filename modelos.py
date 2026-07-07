"""
Modelos de datos comunes para el AnimeThemes Checker.

Estas estructuras son el "vocabulario" que comparten todos los módulos:
- animethemes_client.py construye TemaAT a partir de la API de AnimeThemes.
- mal_scraper.py construye TemaMAL a partir del HTML de MAL.
- comparador.py recibe ambas listas y produce Discrepancia.
- gui_pyqt6.py solo lee Discrepancia para llenar la tabla.
"""

from dataclasses import dataclass, field
from enum import Enum


class TipoTema(str, Enum):
    OP = "OP"
    ED = "ED"


class TipoDiscrepancia(str, Enum):
    TEMA_FALTANTE = "tema_faltante"
    RANGO_ABIERTO_SIN_CERRAR = "rango_abierto_sin_cerrar"
    VIDEO_FALTANTE = "video_faltante"


@dataclass
class TemaAT:
    """Un tema (OP/ED) tal como existe en AnimeThemes."""
    tipo: TipoTema
    secuencia: int          # 1, 2, 3... (OP1 -> 1, ED2 -> 2)
    titulo_cancion: str
    artista: str
    episodios_texto: str    # el campo 'episodes' crudo de animethemeentries, ej. "1-11" o "" o "7-"
    version: int = 1        # versión de la entry (v1, v2, etc.)
    tiene_video: bool = True  # False si videos: [] en la entry — entrada huérfana sin video


@dataclass
class TemaMAL:
    """Un tema (OP/ED) tal como aparece en la caja de MAL."""
    tipo: TipoTema
    secuencia: int
    titulo_cancion: str
    artista: str
    episodios_texto: str    # ej. "eps 7-12"


@dataclass
class Discrepancia:
    """Un hallazgo a revisar para un anime en particular."""
    tipo: TipoDiscrepancia
    tipo_tema: TipoTema
    secuencia: int
    descripcion: str        # texto legible para mostrar en la GUI
    tema_mal: TemaMAL | None = None
    tema_at: TemaAT | None = None


@dataclass
class ResultadoAnime:
    """Resultado del análisis de un anime: sus discrepancias (si las hay)."""
    anime_id: int                 # id en AnimeThemes
    nombre: str
    slug: str                     # slug en AnimeThemes, para construir el link directo (ej. 'mato_seihei_no_slave_2')
    mal_id: int | None
    status_mal: str | None        # ej. "Finished Airing"
    discrepancias: list[Discrepancia] = field(default_factory=list)

    @property
    def tiene_problemas(self) -> bool:
        return len(self.discrepancias) > 0
