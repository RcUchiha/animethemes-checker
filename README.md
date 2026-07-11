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
| `anilist_client.py`       | Cliente de AniList (GraphQL) — último recurso si Jikan y su caché fallan a la vez. |
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

## Por qué depende también de AniList (último recurso)

`detectar_animes_faltantes_en_at` (pestaña "Animes Faltantes") depende
por completo de tener un listado de la temporada según MAL — a diferencia
de "Discrepancias", ahí no hay forma de seguir sin ese dato. Jikan tiene
cortes intermitentes conocidos y documentados
([jikan-rest#607](https://github.com/jikan-me/jikan-rest/issues/607)), y
aunque hay un caché en disco que sirve como respaldo (incluso vencido,
ver `cache_jikan.py`), ese respaldo no ayuda la primera vez que se
escanea una temporada recién anunciada — justo el momento de mayor uso
real, y justo cuando no hay nada cacheado todavía que servir.

Por eso, como último recurso (solo si Jikan en vivo falla Y no hay ningún
caché previo), se consulta la API pública de AniList. Es una fuente
distinta a MAL, con su propio riesgo: el vínculo hacia MAL (`idMal`) es
dato crowd-sourced que a veces falta (se cuenta y excluye) o puede estar
mal cargado (riesgo real, no detectable automáticamente). Por eso nunca
reemplaza a Jikan ni a su caché — es estrictamente el último intento
antes de rendirse — y cuando se usa, la GUI se lo avisa explícitamente al
usuario en vez de mezclarlo en silencio. Ver el docstring de
`orquestador.detectar_animes_faltantes_en_at` para el detalle completo de
la cascada de 3 capas.

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
