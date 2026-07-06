# Contribuir a AnimeThemes Mod Checker

Este proyecto se desarrolla con mejoras pequeñas y controladas, cada una
en su propia rama y PR, para poder revisar y probar cada cambio de forma
aislada.

## Flujo de trabajo

1. Crear una rama desde `main`, con nombre descriptivo:
   `feature/regla-c-hueco-secuencia`, `fix/mal-scraper-timeout`,
   `refactor/cache-jikan-...`.
2. Un PR = un cambio lógico. Evitar mezclar refactors con features nuevas
   en el mismo PR.
3. Antes de abrir el PR, correr manualmente el escaneo (`python
   gui_pyqt6.py`) sobre al menos una temporada real y confirmar que no
   se rompió nada visible.
4. La descripción del PR debe explicar el **porqué**, no solo el qué —
   este proyecto ya tiene la costumbre (ver docstrings existentes) de
   documentar el razonamiento detrás de cada decisión no obvia, para que
   nadie repita un intento ya descartado. Seguir esa convención.

## Convenciones del código existente

- El código está en español (nombres de funciones, variables, docstrings);
  mantener esa consistencia en el código nuevo.
- Los docstrings largos documentan explícitamente:
  - **qué bug o comportamiento raro se descubrió** (ej. el bug de Jikan en
    `jikan_client.py`, el sufijo japonés en títulos de `comparador.py`).
  - **qué alternativas se probaron y se descartaron**, y por qué.
  - Antes de "arreglar" algo que parezca raro, leer el docstring del
    módulo/función — es probable que ya se haya intentado la solución
    obvia y se haya documentado por qué no funcionó.
- Los textos visibles en la UI van en `i18n.py` (es/en), nunca hardcodeados
  en `gui_pyqt6.py`.
- `cache_jikan.py` solo cachea datos de MAL/Jikan, nunca de AnimeThemes
  (ver el docstring del módulo para el porqué).

## Reportar bugs

Al reportar un bug, seguir el formato que ya se usa en este proyecto:
- Qué se esperaba vs. qué pasó (expected vs. actual).
- Pasos para reproducir, con datos reales si es posible (año/temporada,
  slug de AnimeThemes, mal_id).
- Si aplica, screenshot de la GUI.

## Tests

Actualmente el proyecto no tiene suite de tests automatizados — las
verificaciones se han hecho con corridas reales contra las APIs (ver
menciones a `verificar_include_animeyear.py` en
`animethemes_client.py`, que no forma parte de este repo). Si se agregan
tests, deben poder correr sin red (mockeando `_get_json`/`urllib.request`)
para no depender de que las APIs externas estén arriba.
