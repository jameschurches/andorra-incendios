#!/usr/bin/env python3
"""
Construye el visor: docs/index.html y el artefacto del caso de estudio 2025.

    python src/build_map.py

Presentación común a los dos modos del visor. Los datos van aparte y separados por
producto, para que la actualización diaria de la previsión no pueda tocar el histórico:

    docs/data/historico_2025.geojson   caso de estudio retrospectivo (este script)
    docs/data/forecast.json            previsión D+0-D+7 (src/build_forecast.py)

El histórico cruza la malla de `mapa_peligro_andorra_2025.gpkg` (capa `peligro_medio`)
con el producto operativo de tres capas `28_producto_operativo_2025.csv`, ambos en
data/raw/ (paquete del TFT, no versionado).
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date

import geopandas as gpd
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(ROOT, "data", "raw")
DOCS = os.path.join(ROOT, "docs")

GPKG = os.path.join(RAW, "mapa_peligro_andorra_2025.gpkg")
LAYER = "peligro_medio"
PROD = os.path.join(RAW, "28_producto_operativo_2025.csv")
GEOJSON = os.path.join(DOCS, "data", "historico_2025.geojson")
OUT = os.path.join(DOCS, "index.html")

NIVELES = ["Muy bajo", "Bajo", "Moderado", "Alto", "Muy alto", "Extremo"]
INCERT = ["Baja", "Media", "Alta"]

# Lectura de cada nivel. El percentil y la columna "uso" son los de la tabla 6 de la
# memoria; el texto traduce esa escala a lenguaje llano sin prometer nada que el índice
# no mida: describe susceptibilidad del territorio, no probabilidad de ignición.
NIVEL_PCT = ["por debajo del p25", "p25–p50", "p50–p75", "p75–p90", "p90–p97,5", "por encima del p97,5"]
NIVEL_USO = ["Vigilancia mínima", "Ordinaria", "Atención", "Refuerzo", "Prealerta", "Alerta máxima"]
NIVEL_TXT = [
    "El combustible conserva humedad: es difícil que un fuego prenda y avance.",
    "Un fuego podría prender, pero avanzaría despacio y sería fácil de controlar.",
    "Situación intermedia de la temporada: un fuego prendería sin dificultad y avanzaría a ritmo medio.",
    "El combustible está seco: un fuego prendería con facilidad, avanzaría rápido y exigiría más medios.",
    "Entre el 10 % de días más desfavorables de la serie 2017–2026: propagación rápida y extinción difícil.",
    "Entre el 2,5 % de días más desfavorables de la serie: una ignición puede escapar al control inicial.",
]

# Paletas del proyecto (paletas_carlemany.py); si no hay matplotlib se usan las mismas
# constantes embebidas.
try:  # pragma: no cover - depende del entorno
    sys.path.insert(0, RAW)
    import paletas_carlemany as PC

    COLS6 = list(PC.NIVELES_PELIGRO_AEMET)
    UNC_STOPS = [PC.SEQ_GREY[0], "#F7AB1C", PC.ACCENT]
except Exception:
    # NIVELES_PELIGRO_AEMET / SEQ_GREY[0] / ACCENT de paletas_carlemany.py
    COLS6 = ["#4A78C8", "#6EC8F0", "#6FCE44", "#F5F23B", "#DE8631", "#D92B20"]
    UNC_STOPS = ["#FCFCFC", "#F7AB1C", "#c0392b"]

ACC_COLORS = {
    "Sin acción especial": "#1a9850",
    "Monitorización rutinaria": "#a6d96a",
    "Alerta preventiva": "#fee08b",
    "Alerta + más datos": "#fdae61",
    "Vigilancia + verificación campo": "#d73027",
    "Vigilancia activa": "#8B0000",
}


def _hex2rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def rampa(stops: list[str], t: float) -> str:
    """Interpola la rampa de color del proyecto en t in [0,1]."""
    t = min(max(t, 0.0), 1.0)
    n = len(stops) - 1
    i = min(int(t * n), n - 1)
    f = t * n - i
    a, b = _hex2rgb(stops[i]), _hex2rgb(stops[i + 1])
    return "#%02X%02X%02X" % tuple(int(round(a[k] + (b[k] - a[k]) * f)) for k in range(3))


# Tres categorías de incertidumbre (terciles ya calculados en el producto operativo),
# muestreadas de la misma rampa que usaba el gradiente continuo.
UNC_COLORS = {n: rampa(UNC_STOPS, t) for n, t in zip(INCERT, (0.22, 0.62, 1.0))}


def construir_historico() -> dict:
    g = (gpd.read_file(GPKG, layer=LAYER)[["cell_id", "geometry"]]
           .merge(pd.read_csv(PROD), on="cell_id")
           .to_crs(4326))
    if g.empty:
        raise SystemExit("El cruce malla x producto operativo no ha devuelto celdas.")

    feats = []
    for _, r in g.iterrows():
        nivel = int(r.nivel_peligro)
        geom = json.loads(gpd.GeoSeries([r.geometry], crs=4326).to_json())["features"][0]["geometry"]
        geom["coordinates"] = [[[round(x, 5), round(y, 5)] for x, y in anillo]
                               for anillo in geom["coordinates"]]
        feats.append({"type": "Feature", "geometry": geom, "properties": {
            "id": r.cell_id, "np": nivel, "nombre": NIVELES[nivel - 1],
            "dm": round(float(r.danger_met), 4), "fwi": round(float(r.fwi), 2),
            "ndmi": round(float(r.NDMI), 4), "dry": round(float(r.dry_days), 2),
            "inc": round(float(r.incertidumbre), 4), "ni": r.nivel_incert, "acc": r.accion,
            # colores precalculados por capa
            "cp": COLS6[nivel - 1],
            "cu": UNC_COLORS.get(r.nivel_incert, "#ccc"),
            "ca": ACC_COLORS.get(r.accion, "#ccc"),
        }})

    gj = {"type": "FeatureCollection", "features": feats}
    os.makedirs(os.path.dirname(GEOJSON), exist_ok=True)
    with open(GEOJSON, "w", encoding="utf-8") as fh:
        json.dump(gj, fh, ensure_ascii=False, separators=(",", ":"))

    cent = g.to_crs(32631).geometry.centroid.to_crs(4326)
    return {
        "centro": [round(float(cent.y.mean()), 6), round(float(cent.x.mean()), 6)],
        "celdas": len(g),
        "reparto": {NIVELES[k - 1]: int(v) for k, v in g.nivel_peligro.value_counts().sort_index().items()},
        "reparto_inc": {k: int(v) for k, v in g.nivel_incert.value_counts().items()},
        "peso": os.path.getsize(GEOJSON) / 1024,
    }


def render(meta: dict) -> str:
    plantilla = open(os.path.join(SRC, "plantilla.html"), encoding="utf-8").read()
    reemplazos = {
        "__CENTRO__": json.dumps(meta["centro"]),
        "__COLS6__": json.dumps(COLS6),
        "__N_CELDAS__": str(meta["celdas"]),
        "__LEYENDA_PELIGRO__": "".join(
            f'<li><i style="background:{COLS6[i]}"></i>{i + 1} · {NIVELES[i]}'
            f'<span class="n">{meta["reparto"].get(NIVELES[i], 0)}</span></li>' for i in range(6)),
        "__LEYENDA_INCERT__": "".join(
            f'<li><i style="background:{UNC_COLORS[k]}"></i>{k}'
            f'<span class="n">{meta["reparto_inc"].get(k, 0)}</span></li>' for k in INCERT),
        "__NIVEL_INFO__": json.dumps([{"uso": u, "pct": p, "txt": t}
                                      for u, p, t in zip(NIVEL_USO, NIVEL_PCT, NIVEL_TXT)],
                                     ensure_ascii=False),
        "__NIVELES_SIGNIFICADO__": "".join(
            f'<li><i style="background:{COLS6[i]}"></i><div><b>{i + 1} · {NIVELES[i]}</b> '
            f'<span class="uso">{NIVEL_USO[i]}</span><br>{NIVEL_TXT[i]}'
            f'<span class="pct">Posición en la climatología 2017–2026: {NIVEL_PCT[i]}.</span></div></li>'
            for i in range(6)),
        "__TABLA_NIVELES__": "".join(
            f'<tr><td class="n"><i style="background:{COLS6[i]}"></i>{i + 1} · {NIVELES[i]}</td>'
            f"<td>{NIVEL_PCT[i]}</td><td>{NIVEL_USO[i]}</td><td>{NIVEL_TXT[i]}</td></tr>"
            for i in range(6)),
        "__LEYENDA_ACCION__": "".join(
            f'<li><i style="background:{c}"></i>{k}</li>' for k, c in ACC_COLORS.items()),
        "__FECHA_BUILD__": date.today().strftime("%d/%m/%Y"),
    }
    for k, v in reemplazos.items():
        plantilla = plantilla.replace(k, v)
    import re
    sobran = set(re.findall(r"__[A-Z0-9_]+__", plantilla))
    if sobran:
        raise SystemExit(f"Marcadores sin sustituir en la plantilla: {sorted(sobran)}")
    return plantilla


def main() -> None:
    meta = construir_historico()
    html = render(meta)
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(html)
    print(f"{os.path.relpath(GEOJSON, ROOT)} · {meta['celdas']} celdas · {meta['peso']:.0f} KB")
    print(f"{os.path.relpath(OUT, ROOT)} · {len(html) / 1024:.0f} KB")
    print(f"  reparto de peligro: {meta['reparto']}")
    print(f"  reparto de incertidumbre: {meta['reparto_inc']}")


if __name__ == "__main__":
    main()
