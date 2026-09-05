#!/usr/bin/env python3
"""
Extrae de los productos del TFT (data/raw/, no versionados) los parámetros persistidos
que necesita la previsión diaria, y los deja en data/static/ (versionados y pequeños).

    python src/prepare_static.py

Se ejecuta solo cuando se regeneran los productos del TFT. La previsión diaria
(src/build_forecast.py) consume únicamente data/static/, nunca data/raw/, para que el
flujo automático no dependa del paquete completo de datos ni pueda tocar el histórico.
"""
from __future__ import annotations

import json
import os
import shutil
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "data", "raw")
STATIC = os.path.join(ROOT, "data", "static")


def mm(x, lo, hi):
    """Normalización min-max acotada con los límites persistidos del TFT."""
    return np.clip((np.asarray(x, dtype=float) - lo) / (hi - lo + 1e-9), 0, 1)


def main() -> None:
    os.makedirs(STATIC, exist_ok=True)

    defs = json.load(open(os.path.join(RAW, "31_isia_definicion.json"), encoding="utf-8"))

    # --- Nodos meteorológicos -------------------------------------------------
    asig = pd.read_csv(os.path.join(RAW, "meteo_asignacion_nodos.csv"))
    nodos = (asig.groupby("node_id")
                 .agg(node_lat=("node_lat", "first"), node_lon=("node_lon", "first"))
                 .reset_index())
    nodos.to_csv(os.path.join(STATIC, "nodos.csv"), index=False)

    # --- Semilla FFMC/DMC/DC: último estado archivado por nodo ----------------
    hist = pd.read_parquet(os.path.join(RAW, "fwi_nodos_2017_2026.parquet"))
    hist["date"] = pd.to_datetime(hist["date"])
    huecos = {n: int((g.sort_values("date").date.diff().dt.days.fillna(1) != 1).sum())
              for n, g in hist.groupby("node_id")}
    if any(huecos.values()):
        print("AVISO: la serie FWI archivada tiene días ausentes por nodo:", huecos)
    semilla = (hist.sort_values("date").groupby("node_id").tail(1)
                   [["node_id", "date", "ffmc", "dmc", "dc"]]
                   .rename(columns={"date": "fecha"}))
    semilla.to_csv(os.path.join(STATIC, "fwi_semilla.csv"), index=False)

    # --- Factores ISIA por celda: última observación de vegetación disponible --
    f3 = pd.read_parquet(os.path.join(RAW, "dataset_analitico_andorra_2017_2026_f3.parquet"))
    f3["date"] = pd.to_datetime(f3["date"])
    last = (f3.sort_values("date").groupby("cell_id").tail(1)
              [["cell_id", "date", "NDMI", "slope_deg", "northness", "node_id"]])

    celdas = pd.DataFrame({
        "cell_id": last.cell_id.values,
        "node_id": last.node_id.values,
        # f_veg y f_topo tal como los define 31_isia_definicion.json
        "f_veg": (0.8 + 0.4 * (1 - mm(last.NDMI, *defs["ndmi_p2_p98"]))).round(6),
        "f_topo": (0.9 + 0.2 * (0.5 * (1 - last.northness) / 2
                                + 0.5 * mm(last.slope_deg, *defs["slope_p2_p98"]))).round(6),
        "ndmi_last": last.NDMI.round(4).values,
        "ndmi_fecha": last.date.dt.strftime("%Y-%m-%d").values,
    })
    celdas.to_csv(os.path.join(STATIC, "celdas_modelo.csv"), index=False)

    # --- Tasa base: cuántos días de la climatología alcanzan cada nivel -------
    # Sirve para que el visor pueda contrastar la previsión con lo que es normal
    # en la serie 2017-2026, en lugar de dejar el nivel sin referencia.
    thr = pd.read_csv(os.path.join(RAW, "34_niveles_climatologicos.csv"))
    umbrales = thr[(thr.indice == "isia") & (thr.escala == "diaria")][
        ["p25", "p50", "p75", "p90", "p97.5"]].values[0]
    f3["nivel"] = np.digitize(f3.isia, umbrales) + 1
    md = f3.date.dt.strftime("%m-%d")
    temporada = f3[(md >= "06-10") & (md <= "09-18")]
    por_dia = temporada.groupby("date").nivel.apply(lambda g: (g >= 5).mean())
    tasa = {
        "temporada": {"desde": "06-10", "hasta": "09-18"},
        "pct_celdas_ge5_temporada": round(float((temporada.nivel >= 5).mean()) * 100, 1),
        "pct_celdas_ge5_septiembre": round(
            float((temporada[temporada.date.dt.month == 9].nivel >= 5).mean()) * 100, 1),
        "dias_temporada": int(len(por_dia)),
        "dias_con_mas_75pct_ge5": int((por_dia > 0.75).sum()),
        "reparto_temporada_pct": [round(float((temporada.nivel == n).mean()) * 100, 1)
                                  for n in range(1, 7)],
    }
    json.dump(tasa, open(os.path.join(STATIC, "tasa_base_niveles.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)

    # --- Parámetros climatológicos, copiados sin tocar ------------------------
    shutil.copy(os.path.join(RAW, "31_isia_definicion.json"),
                os.path.join(STATIC, "isia_definicion.json"))
    shutil.copy(os.path.join(RAW, "34_niveles_climatologicos.csv"),
                os.path.join(STATIC, "niveles_climatologicos.csv"))

    if celdas.cell_id.nunique() != 515:
        sys.exit(f"Se esperaban 515 celdas y hay {celdas.cell_id.nunique()}")

    print(f"data/static/ actualizado: {len(nodos)} nodos, {len(celdas)} celdas")
    print(f"  semilla FWI: {semilla.fecha.dt.date.min()} .. {semilla.fecha.dt.date.max()}")
    print(f"  último NDMI: {celdas.ndmi_fecha.min()} .. {celdas.ndmi_fecha.max()}")
    print(f"  tasa base climatológica: {tasa['pct_celdas_ge5_temporada']}% de los días-celda de "
          f"temporada en nivel >=5 ({tasa['pct_celdas_ge5_septiembre']}% en septiembre); "
          f"{tasa['dias_con_mas_75pct_ge5']} de {tasa['dias_temporada']} días superan el 75%")


if __name__ == "__main__":
    main()
