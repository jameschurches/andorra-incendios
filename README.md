# Visor de peligro de incendio forestal · Andorra

Dos productos sobre la misma malla de **515 celdas de 1 km²**, derivados del Trabajo Final
(Sentinel-2 + Open-Meteo/ERA5-Land + FWI canadiense + ISIA):

👉 **https://jameschurches.github.io/andorra-incendios/**

| Producto | Qué es |
|---|---|
| **ANÁLISIS 2025** | Caso de estudio retrospectivo y reproducible de la temporada 10/06/2025–18/09/2025 (20 fechas Sentinel-2 válidas). Resultados científicos del TFT, congelados. |
| **PREVISIÓN D+0–D+7** | Demostrador experimental que se regenera a diario con meteorología prevista: Hoy y siete días más. |

El visor abre en el análisis de 2025; el selector de la cabecera cambia de producto.

## Qué aporta el ISIA

La referencia internacional para Andorra es el FWI canadiense que Copernicus/EFFIS calcula
para toda Europa sobre meteorología a unos 9 km: a esa malla el país entero cabe en unas seis
celdas, y el fondo del valle y la solana de enfrente reciben el mismo valor en un territorio
con 1 800 m de desnivel. El ISIA baja a 1 km —515 celdas, unas 81 por cada celda europea— y
añade lo que el FWI no mira: el estado real de la vegetación por satélite (NDMI de Sentinel-2)
y el relieve (pendiente y orientación). El FWI sigue siendo el núcleo meteorológico público y
auditable sobre el que se construye; el ISIA no lo sustituye, lo territorializa.

El IPIF que AEMET puso en operación en 2026 sí trabaja a 1 km y comparte esa arquitectura,
pero cubre España y se detiene en la frontera. Y ninguno de los dos publica cuánta confianza
merece cada celda: aquí la incertidumbre es una capa más del mapa.

## Análisis retrospectivo 2025

Tres capas sobre la media estacional del indicador `danger_met`:

- **Peligro** — media estacional por celda, clasificada en seis niveles (Muy bajo → Extremo)
  con los percentiles climatológicos **estacionales** de la serie 2017–2026
  (p25, p50, p75, p90, p97,5). Escala cromática armonizada con AEMET/IPIF.
- **Incertidumbre relativa del modelo** — predicción conforme:
  `0,7 × anchura del intervalo al 90 % + 0,3 × residuo absoluto out-of-fold`, con
  normalización robusta (p2–p98) y promedio por celda. Se representa en los terciles
  Baja / Media / Alta ya calculados en el producto operativo; el valor continuo queda en el
  popup como índice relativo. **No es una probabilidad de incendio ni un porcentaje de error**:
  señala dónde el modelo generalizó peor sobre territorio no usado en su entrenamiento.
- **Acción recomendada** — cruce de peligro × incertidumbre.

Los umbrales no se recalculan sobre 2025: se leen de la climatología 2017–2026 persistida.

## Previsión experimental D+0–D+7

```
Open-Meteo diario por nodo  →  FWI previsto (Van Wagner, sembrado con el último
(tmax, HR, precipitación,       FFMC/DMC/DC archivado y propagado sin saltos)
 viento máx; Europe/Madrid)  →  ISIA por celda (último NDMI + topografía estática)
                             →  umbrales climatológicos diarios 2017-2026  →  nivel 1-6
```

- **FWI**: no se reinicia en D+0. Se parte del último estado FFMC/DMC/DC de cada nodo y se
  propaga día a día. La serie meteorológica desde la semilla hasta D+7 debe ser continua; el
  tramo ya transcurrido se pide al archivo `era5_seamless` —la misma fuente con la que se
  construyó la semilla— y el resto a la API de previsión. Si queda algún día ausente, el
  proceso aborta y no se publica previsión.
- **ISIA**: definición persistida en `data/static/isia_definicion.json`,
  `clip(mm(FWI) × f_veg × f_topo, 0, 1)`, con las normalizaciones p2–p98 multianuales fijadas.
  Los límites no se recalculan con los ocho días previstos.
- **Niveles**: umbrales **diarios** del ISIA. Cada nivel indica la posición respecto a la
  climatología 2017–2026, no la posición relativa dentro de la propia semana.
- **Incertidumbre de previsión (heurística)**:
  `0,6 × horizonte/7 + 0,4 × mín(antigüedad NDMI/30, 1)`, en Baja (< 0,33), Media (0,33–0,66)
  y Alta (> 0,66). Crece con el horizonte y con la antigüedad de la última observación de
  vegetación. **No es un intervalo probabilístico calibrado** y es un concepto distinto de la
  incertidumbre conforme del análisis retrospectivo: no deben compararse entre sí.

### Interpretación de los niveles

Dos referencias acompañan siempre a la previsión en el panel, para que un nivel alto no se lea
sin contexto:

- **Tasa base climatológica.** En la serie 2017–2026 solo el **11,0 %** de los días-celda de
  temporada alcanzan niveles 5–6 (**3,2 %** en septiembre), y **1 de 112** días de temporada
  superó el 75 % de celdas en esos niveles.
- **Saltos entre días.** El FFMC responde a la lluvia en menos de 24 horas, así que una racha
  seca seguida de un frente húmedo hace caer el nivel varios escalones de un día para otro. Es
  el comportamiento previsto del índice, no un error: el popup muestra la meteorología que lo
  explica.

### Homogeneidad de fuentes

La semilla FWI y los umbrales climatológicos proceden del reanálisis ERA5; los días previstos,
del modelo de previsión. En los días que ambas fuentes cubren, el viento máximo del modelo casi
duplica el de ERA5 (≈17 frente a ≈10 km/h) con temperatura, humedad y precipitación
equivalentes. El visor **no aplica corrección de sesgo**: publica el valor tal cual y activa un
diagnóstico visible cuando más del 75 % de las celdas caen en niveles 5–6, para que pueda
distinguirse un episodio real de un artefacto de integración.

## Actualización automática

`.github/workflows/update_forecast.yml` se ejecuta a diario (05:20 UTC) y a demanda
(`workflow_dispatch`): descarga Open-Meteo, regenera la previsión, pasa los controles de
integridad y solo entonces publica. Open-Meteo no requiere clave, así que el flujo no guarda
ningún secreto. Si la descarga falla, no se publica nada: queda la previsión anterior y el
análisis de 2025 sigue funcionando. La página muestra siempre la hora de generación y marca
`PREVISIÓN NO ACTUALIZADA` si han pasado más de 24 horas. El flujo solo puede tocar
`docs/data/forecast.json`.

## Estructura

```
src/    build_map.py        caso de estudio 2025 + página (presentación común)
        build_forecast.py   descarga, FWI sembrado, ISIA y controles D+0-D+7
        prepare_static.py   extrae los parámetros persistidos del paquete del TFT
        fwi.py              FWI canadiense (Van Wagner 1987)
        checks.py           integridad de los artefactos publicados
        plantilla.html      plantilla Leaflet del visor
data/static/                parámetros persistidos (nodos, semilla FWI, factores ISIA
                            por celda, definición ISIA, umbrales climatológicos)
data/raw/                   (no versionado) paquete completo de datos del TFT
docs/index.html             página publicada
docs/data/                  historico_2025.geojson · forecast.json
docs/assets/                logo de la Universitat Carlemany
```

`data/raw/` no se distribuye: contiene `mapa_peligro_andorra_2025.gpkg`,
`28_producto_operativo_2025.csv`, `dataset_analitico_andorra_2017_2026_f3.parquet`,
`fwi_nodos_2017_2026.parquet`, `meteo_asignacion_nodos.csv` y `paletas_carlemany.py`.
Solo hace falta para regenerar el caso de estudio o para volver a extraer `data/static/`.

## Reproducir

```bash
pip install -r requirements.txt
python src/prepare_static.py    # solo si se renueva el paquete del TFT
python src/build_map.py         # caso de estudio 2025 + docs/index.html
python src/build_forecast.py    # previsión D+0-D+7
python src/checks.py            # integridad de lo publicado
python -m http.server --directory docs 8123   # http://localhost:8123
```

La página carga sus datos por `fetch`, así que hay que servirla por HTTP: abrirla con doble
clic desde el sistema de archivos no cargará las capas.

## Aviso

Producto **académico y demostrativo**. El análisis de 2025 es peligro estructural agregado por
temporada y no representa la situación actual. La previsión D+0–D+7 demuestra la viabilidad
técnica del flujo, pero **no ha sido validada prospectivamente** contra peligro observado ni
verdad-terreno institucional: no es un aviso oficial y no predice igniciones concretas. La
información oficial de emergencias en Andorra corresponde al Cos de Bombers d'Andorra i
Protecció Civil.

Datos y modelo: Carlos Iglesias Vicente.
