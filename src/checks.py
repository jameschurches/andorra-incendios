#!/usr/bin/env python3
"""
Comprobaciones de integridad de los artefactos publicados en docs/.

    python src/checks.py

Verifica el caso de estudio 2025, la previsión D+0-D+7, su coherencia mutua y que la
página no haya quedado con marcadores sin sustituir. Sale con código distinto de cero
si algo falla, para que el flujo automático no publique un visor roto.
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = os.path.join(ROOT, "docs")
STATIC = os.path.join(ROOT, "data", "static")

CELDAS = 515
DIAS = 8
BBOX = (1.40, 42.42, 1.79, 42.66)          # Andorra con margen
fallos: list[str] = []
avisos: list[str] = []


def check(cond: bool, msg: str, aviso: bool = False) -> None:
    estado = "ok  " if cond else ("AVISO" if aviso else "FALLO")
    print(f"  [{estado}] {msg}")
    if not cond:
        (avisos if aviso else fallos).append(msg)


def historico() -> dict:
    print("Caso de estudio 2025 · docs/data/historico_2025.geojson")
    gj = json.load(open(os.path.join(DOCS, "data", "historico_2025.geojson"), encoding="utf-8"))
    fs = gj["features"]
    check(len(fs) == CELDAS, f"{len(fs)} celdas (esperadas {CELDAS})")

    req = {"id", "np", "nombre", "dm", "fwi", "ndmi", "dry", "inc", "ni", "acc", "cp", "cu", "ca"}
    faltan = [f["properties"]["id"] for f in fs if not req <= set(f["properties"])]
    check(not faltan, f"todas las celdas traen los {len(req)} atributos del producto operativo")

    niveles = {f["properties"]["np"] for f in fs}
    check(niveles <= set(range(1, 7)), f"niveles de peligro dentro de 1-6: {sorted(niveles)}")
    check(all(f["properties"]["ni"] in ("Baja", "Media", "Alta") for f in fs),
          "incertidumbre histórica en terciles Baja/Media/Alta")
    hexes = [c for f in fs for c in (f["properties"]["cp"], f["properties"]["cu"], f["properties"]["ca"])]
    check(all(re.fullmatch(r"#[0-9A-Fa-f]{6}", c) for c in hexes), "colores precalculados válidos")

    xs = [p[0] for f in fs for anillo in f["geometry"]["coordinates"] for p in anillo]
    ys = [p[1] for f in fs for anillo in f["geometry"]["coordinates"] for p in anillo]
    check(BBOX[0] <= min(xs) and max(xs) <= BBOX[2] and BBOX[1] <= min(ys) and max(ys) <= BBOX[3],
          f"geometría dentro de Andorra ({min(xs):.3f},{min(ys):.3f})-({max(xs):.3f},{max(ys):.3f})")
    cerrados = all(anillo[0] == anillo[-1] for f in fs for anillo in f["geometry"]["coordinates"])
    check(cerrados, "polígonos cerrados")
    return {f["properties"]["id"] for f in fs}


def forecast() -> set:
    ruta = os.path.join(DOCS, "data", "forecast.json")
    print("\nPrevisión D+0–D+7 · docs/data/forecast.json")
    if not os.path.exists(ruta):
        check(False, "no existe el artefacto de previsión", aviso=True)
        return set()
    fc = json.load(open(ruta, encoding="utf-8"))
    m, celdas = fc["meta"], fc["celdas"]

    check(len(celdas) == CELDAS, f"{len(celdas)} celdas (esperadas {CELDAS})")
    check(len(fc["dias"]) == DIAS, f"{len(fc['dias'])} estados D+0…D+{DIAS - 1}")
    check([d["d"] for d in fc["dias"]] == list(range(DIAS)), "horizontes correlativos sin saltos")

    fechas = [d["fecha"] for d in fc["dias"]]
    consec = all((date.fromisoformat(b) - date.fromisoformat(a)).days == 1
                 for a, b in zip(fechas, fechas[1:]))
    check(consec, f"fechas consecutivas {fechas[0]} → {fechas[-1]}")
    check(m["forecast_inicio"] == fechas[0] and m["forecast_fin"] == fechas[-1],
          "metadatos de inicio y fin coherentes con los días publicados")

    largos = all(len(c[k]) == DIAS for c in celdas.values() for k in ("n", "isia", "inc", "incc"))
    check(largos, "todas las celdas tienen los 8 estados en todas las series")
    niveles = {n for c in celdas.values() for n in c["n"]}
    check(niveles <= set(range(1, 7)), f"nivel dentro de 1-6: {sorted(niveles)}")
    isias = [v for c in celdas.values() for v in c["isia"]]
    check(all(0 <= v <= 1 for v in isias), "ISIA dentro de [0,1]")
    check(all(v == v and abs(v) != float("inf") for v in isias), "ISIA sin valores no finitos")
    fwis = [v for n in fc["nodos"].values() for v in n["fwi"]]
    check(all(v == v and v >= 0 for v in fwis), f"FWI finito y no negativo (máx {max(fwis):.2f})")
    check(all(0 <= v <= 2 for c in celdas.values() for v in c["incc"]),
          "clase de incertidumbre de previsión en 0-2")

    nodos_ref = {c["nodo"] for c in celdas.values()}
    check(nodos_ref <= set(fc["nodos"]), f"cada celda apunta a un nodo con meteorología ({len(nodos_ref)} nodos)")
    check(all(len(v) == DIAS for n in fc["nodos"].values() for v in n.values()),
          "series meteorológicas de 8 días en todos los nodos")

    # Parámetros climatológicos: los publicados deben ser los persistidos.
    thr = open(os.path.join(STATIC, "niveles_climatologicos.csv"), encoding="utf-8").read().splitlines()
    fila = [l for l in thr if l.startswith("isia,diaria")][0].split(",")[2:]
    check([round(float(x), 6) for x in fila] == [round(float(x), 6) for x in m["umbrales_isia_diarios"]],
          f"umbrales ISIA diarios sin recalcular: {m['umbrales_isia_diarios']}")
    check(m["huecos"] is False, "sin huecos declarados en la serie meteorológica")
    tb = m.get("tasa_base") or {}
    check(bool(tb), f"tasa base climatológica presente ({tb.get('pct_celdas_ge5_temporada')} % de "
                    "días-celda de temporada en nivel 5-6)")
    check(bool(m.get("semilla_fwi_fecha")) and bool(m.get("ndmi_fecha")),
          f"trazabilidad: semilla FWI {m.get('semilla_fwi_fecha')} · NDMI {m.get('ndmi_fecha')}")

    serie = date.fromisoformat(m["meteo_serie_inicio"])
    semilla = date.fromisoformat(m["semilla_fwi_fecha"])
    check(serie == semilla + timedelta(days=1),
          f"la serie meteorológica arranca el día siguiente a la semilla ({serie})")

    edad = (datetime.now(timezone.utc) - datetime.strptime(m["generado_utc"], "%Y-%m-%dT%H:%M:%SZ")
            .replace(tzinfo=timezone.utc)).total_seconds() / 3600
    check(edad <= 24, f"previsión generada hace {edad:.1f} h", aviso=True)
    if m.get("avisos"):
        check(False, f"{len(m['avisos'])} día(s) con más del 75 % de celdas en nivel 5-6 "
                     "(diagnóstico, no se corrige)", aviso=True)
    return set(celdas)


def pagina(ids_hist: set, ids_fc: set) -> None:
    print("\nPágina · docs/index.html")
    html = open(os.path.join(DOCS, "index.html"), encoding="utf-8").read()
    check(not re.findall(r"__[A-Z0-9_]+__", html), "sin marcadores de plantilla sin sustituir")
    check("data/historico_2025.geojson" in html and "data/forecast.json" in html,
          "la página carga los dos productos por separado")
    check(html.count("integrity=\"sha256-") == 2, "Leaflet servido con integridad SRI")
    check("Visor de peligro de incendio forestal · Andorra" in html, "título del visor correcto")
    logo = os.path.join(DOCS, "assets", "universitat-carlemany.png")
    check(os.path.exists(logo) and "assets/universitat-carlemany.png" in html,
          "logo de la universidad presente y referenciado")
    check("ANÁLISIS 2025" in html and "PREVISIÓN D+0–D+7" in html, "selector de producto presente")

    if ids_fc:
        check(ids_fc <= ids_hist,
              f"las celdas de la previsión existen en la malla del histórico ({len(ids_fc & ids_hist)})")
        check(ids_hist <= ids_fc, "todas las celdas de la malla tienen previsión")


def main() -> None:
    ids_hist = historico()
    ids_fc = forecast()
    pagina(ids_hist, ids_fc)

    print()
    if avisos:
        print(f"{len(avisos)} aviso(s):")
        for a in avisos:
            print(f"  · {a}")
    if fallos:
        print(f"\n{len(fallos)} comprobación(es) fallidas:")
        for f in fallos:
            print(f"  · {f}")
        sys.exit(1)
    print("Todas las comprobaciones han pasado.")


if __name__ == "__main__":
    main()
