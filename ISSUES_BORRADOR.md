# Issues borrador

Copiar cada sección como un issue separado en GitHub. Ordenados por
prioridad sugerida.

---

## 1. Implementar Regla C: hueco de secuencia dentro de AT

**Contexto:** el docstring de `comparador.py` documenta explícitamente
una tercera regla:

> REGLA C — Hueco de SECUENCIA dentro de AT (ej. existe OP1 y OP3, no
> OP2). Esto es independiente de A/B y de MAL: solo mira la numeración
> interna de AT.

Incluso existe el encabezado de sección `# ---------- Regla C: hueco de
secuencia dentro de AT ----------` en el archivo, pero no hay ninguna
función implementada debajo, y `comparar()` solo invoca las reglas A, B
y D (`TEMA_FALTANTE`, `RANGO_ABIERTO_SIN_CERRAR`, `VIDEO_FALTANTE`).

**Qué falta:**
- Función `_detectar_huecos_de_secuencia(temas_at) -> list[Discrepancia]`
  que, para cada tipo (OP/ED), detecte si existe una secuencia > 1 sin
  que exista la secuencia anterior (ej. hay OP3 pero no OP1 ni OP2).
- Un nuevo valor en `TipoDiscrepancia` (ej. `HUECO_SECUENCIA`).
- Textos i18n (es/en) para el mensaje descriptivo.
- Llamarla desde `comparar()`.

**Ojo:** definir bien el criterio de "hueco" — ¿reportar cada número
faltante individual (falta OP2) o reportar el primer hueco encontrado?
Decidir esto antes de implementar, ya que cambia la forma del mensaje.

---

## 2. Agregar tests automatizados que no dependan de red

**Contexto:** el proyecto no tiene ninguna suite de tests. Las
validaciones se han hecho con corridas reales contra AnimeThemes/Jikan/MAL
(mencionado en varios docstrings, ej. `verificar_include_animeyear.py` en
`animethemes_client.py`), lo cual es lento y fragil para verificar
regresiones al iterar.

**Qué falta:**
- Tests unitarios para `comparador.py` (reglas A, B, D — puros, sin red).
- Tests para `_parsear_rango_episodios` y `_normalizar_titulo` en
  `comparador.py` (varios casos límite ya documentados en el código:
  rangos con coma, sufijo japonés, paréntesis con CV).
- Mockear `_get_json`/`urllib.request` en los clientes para no depender
  de que las APIs externas estén arriba durante CI.

---

## 3. Revisar el fallback `NECESITA_DETALLE_INDIVIDUAL` en `animethemes_client.py`

**Contexto:** el camino rápido (`_obtener_animes_completos_desde_listado`,
una sola llamada a `/animeyear` con include completo) es el default desde
que se confirmó que trae todo. El camino de respaldo
(`NECESITA_DETALLE_INDIVIDUAL=True`, uno por uno con hilos) sigue en el
código pero no se ejecuta en la práctica.

**Qué falta / a decidir:**
- Confirmar si el camino de respaldo sigue funcionando (podría haberse
  desactualizado sin que nadie lo note, al no usarse nunca).
- Si nunca se usa y no aporta valor real como respaldo activo, considerar
  si vale la pena mantenerlo o simplificarlo.

---

## 4. Mejorar manejo de errores/timeouts al scrapear MAL directamente

**Contexto:** con el fallback a `mal_scraper.py` para status y episodios
(en vez de Jikan), el programa ahora depende más de scrapear HTML de MAL
directamente, que es más frágil ante cambios de estructura de la página
que un endpoint de API versionado.

**Qué falta:**
- Confirmar qué pasa hoy si MAL cambia ligeramente su HTML (¿se cae
  silenciosamente, o lanza un error visible en la pestaña de
  Errores/Omitidos de la GUI?).
- Considerar agregar un test de "smoke" que detecte si el parser de
  `mal_scraper.py` dejó de encontrar los campos esperados, para notarlo
  rápido en vez de descubrirlo por un escaneo con resultados raros.
