# AnimeThemes Mod Checker

Herramienta de escritorio (PyQt6) para detectar discrepancias y animes
faltantes entre [AnimeThemes](https://animethemes.moe) y
[MyAnimeList](https://myanimelist.net), pensada para ayudar en el trabajo
de moderación/contribución de AnimeThemes.

## ¿Qué hace?

Para una temporada/año dado:

- **Discrepancias** en animes que ya existen en AnimeThemes:
  - Temas (OP/ED) que MAL documenta y a AnimeThemes le faltan.
  - Rangos de episodios que quedaron abiertos sin cerrar (ej. `"7-"`).
  - Entradas sin video asociado (huérfanas).
- **Animes faltantes**: animes que MAL sí tiene en la temporada pero que
  no existen en absoluto en AnimeThemes.

## Estructura del proyecto

| Archivo                  | Responsabilidad                                                        |
|---------------------------|-------------------------------------------------------------------------|
| `gui_pyqt6.py`            | Interfaz gráfica (PyQt6), hilos de escaneo, exportación a CSV.          |
| `orquestador.py`          | Orquesta el flujo completo: junta AnimeThemes + MAL y arma resultados.  |
| `animethemes_client.py`   | Cliente de la API pública de AnimeThemes.                               |
| `jikan_client.py`         | Cliente de Jikan v4 (API no oficial de MAL) — listados por temporada.   |
| `mal_scraper.py`          | Scraper directo de la página HTML de MAL (fuente principal de status). |
| `comparador.py`           | Reglas de comparación AT vs MAL (temas faltantes, rangos, video).       |
| `modelos.py`              | Dataclasses compartidas (`TemaAT`, `TemaMAL`, `Discrepancia`, etc.).    |
| `cache_jikan.py`          | Caché en disco con expiración para datos de Jikan/MAL.                 |
| `i18n.py`                 | Textos de la UI en español/inglés.                                     |

## Por qué no se usa Jikan como fuente principal de status

El endpoint `/v4/anime/{id}` de Jikan tiene un bug conocido y reproducible
(animes puntuales fallan siempre, sin recuperarse) —
[jikan-rest#378](https://github.com/jikan-me/jikan-rest/issues/378). Por
eso el status se resuelve primero con el listado bulk de temporada y,
si no está disponible ahí, se saca directo de la página HTML de MAL
(`mal_scraper.py`). Ver el docstring de `jikan_client.obtener_info_mal`
para el detalle completo.

## Instalación

```bash
git clone <url-de-este-repo>
cd animethemes-checker
python -m venv venv
source venv/bin/activate  # en Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## Uso

```bash
python gui_pyqt6.py
```

Al ejecutarse por primera vez se generan localmente (y NO se versionan):

- `config.json` — preferencia de idioma.
- `cache_jikan.json` — caché de datos de Jikan/MAL (expira a los 15 días).

## Estado del proyecto

En desarrollo activo. Próximas mejoras se irán controlando por PRs
pequeños e incrementales.
