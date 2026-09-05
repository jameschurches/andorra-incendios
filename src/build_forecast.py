#!/usr/bin/env python3
"""
Previsión de peligro D+0…D+7 para Andorra: descarga meteorología de Open-Meteo,
propaga el FWI canadiense sembrado con el último estado archivado, calcula el ISIA
por celda y lo clasifica con los umbrales climatológicos diarios 2017-2026.

    python src/build_forecast.py            # descarga, valida y escribe docs/data/forecast.json
    python src/build_forecast.py --check    # ejecuta todo sin escribir el artefacto

Cadena: Open-Meteo diario por nodo -> FWI previsto (Van Wagner, sembrado)
        -> ISIA previsto por celda -> umbrales climatológicos diarios -> nivel 1-6.

Ningún parámetro climatológico se recalcula aquí: las normalizaciones p2-p98 y los
umbrales p25/p50/p75/p90/p97.5 se leen tal cual de data/static/ y el script aborta si
no cuadran con lo persistido. El histórico de 2025 no se toca en ningún caso.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fwi import fwi_series  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "data", "static")
CACHE = os.path.join(ROOT, "data", "cache")
OUT = os.path.join(ROOT, "docs", "data", "forecast.json")

TZ = "Europe/Madrid"
HORIZONTE = 7                      # D+0 … D+7 -> 8 estados
NIVELES = ["Muy bajo", "Bajo", "Moderado", "Alto", "Muy alto", "Extremo"]
INCERT = ["Baja", "Media", "Alta"]
VARS = "temperature_2m_max,relative_humidity_2m_mean,precipitation_sum,wind_speed_10m_max"
COLS = {"temperature_2m_max": "tmax", "relative_humidity_2m_mean": "rhum",
        "precipitation_sum": "precip", "wind_speed_10m_max": "wind"}

log = lambda *a: print(*a, flush=True)


# --------------------------------------------------------------------------- datos
def _get(url: str, params: dict, intentos: int = 3) -> dict:
    ultimo = None
    for i in range(intentos):
        try:
            with urllib.request.urlopen(url + "?" + urllib.parse.urlencode(params), timeout=90) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:                     # límite de la API
            ultimo = e
            time.sleep(int(e.headers.get("Retry-After", 30)) if e.code == 429 else 5 * (i + 1))
        except Exception as e:                                  # red intermitente
            ultimo = e
            time.sleep(2 * (i + 1))
    raise RuntimeError(f"Open-Meteo no responde ({type(ultimo).__name__}: {ultimo})")


def _daily_a_df(daily: dict, node_id: str) -> pd.DataFrame:
    df = pd.DataFrame({"node_id": node_id, "date": pd.to_datetime(daily["time"]),
                       **{COLS[k]: daily[k] for k in COLS}})
    return df.dropna(subset=list(COLS.values()))


def meteo_nodo(node_id: str, lat: float, lon: float, desde: pd.Timestamp,
               hasta: pd.Timestamp, hoy: pd.Timestamp) -> tuple[pd.DataFrame, dict]:
    """Serie diaria continua [desde, hasta] para un nodo.

    El tramo ya transcurrido se toma del archivo `era5_seamless`, que es la fuente con
    la que el TFT construyó la meteorología histórica y, por tanto, la semilla
    FFMC/DMC/DC. El resto -los días que el archivo todavía no cubre y la previsión
    propiamente dicha- viene de la forecast-api. Las dos fuentes no son homogéneas
    (ver `contraste_fuentes` en los metadatos); no se aplica ninguna corrección.
    """
    origen = {"archive": None, "forecast": None}
    partes = []

    archivo = pd.DataFrame()
    if desde < hoy:
        archivo = _daily_a_df(_get("https://archive-api.open-meteo.com/v1/archive",
                                   {"latitude": lat, "longitude": lon, "daily": VARS,
                                    "start_date": str(desde.date()), "end_date": str(hoy.date()),
                                    "timezone": TZ, "models": "era5_seamless"})["daily"], node_id)
        archivo = archivo[archivo.date >= desde]
        if not archivo.empty:
            origen["archive"] = [str(archivo.date.min().date()), str(archivo.date.max().date())]
            partes.append(archivo)

    inicio_fc = (archivo.date.max() + timedelta(days=1)) if not archivo.empty else desde
    past = max(int((hoy - inicio_fc).days) + 1, 0)
    prev = _daily_a_df(_get("https://api.open-meteo.com/v1/forecast",
                            {"latitude": lat, "longitude": lon, "daily": VARS,
                             # con tramo de archivo se piden 92 días para que haya
                             # solape suficiente con el que medir el contraste de fuentes
                             "past_days": 92 if not archivo.empty else min(past, 92),
                             "forecast_days": HORIZONTE + 1,
                             "timezone": TZ})["daily"], node_id)
    # Contraste sobre los días que ambas fuentes cubren: documenta, no corrige.
    if not archivo.empty:
        com = archivo.merge(prev, on="date", suffixes=("_a", "_f"))
        if len(com) >= 10:
            origen["contraste"] = {"dias": int(len(com)), **{
                v: [round(float(com[f"{v}_a"].mean()), 2), round(float(com[f"{v}_f"].mean()), 2)]
                for v in ("tmax", "rhum", "wind", "precip")}}
    prev = prev[(prev.date >= inicio_fc) & (prev.date <= hasta)]
    if not prev.empty:
        origen["forecast"] = [str(prev.date.min().date()), str(prev.date.max().date())]
        partes.append(prev)

    serie = (pd.concat(partes, ignore_index=True)
               .drop_duplicates(subset="date", keep="first")
               .sort_values("date").reset_index(drop=True))
    serie["fuente"] = np.where(serie.date <= (archivo.date.max() if not archivo.empty else desde - timedelta(days=1)),
                               "archive", "forecast")
    return serie[(serie.date >= desde) & (serie.date <= hasta)], origen


def _resumen_contraste(origenes: dict) -> dict:
    """Promedia entre nodos el contraste ERA5 vs modelo de previsión."""
    datos = [o["contraste"] for o in origenes.values() if o.get("contraste")]
    if not datos:
        return {}
    res = {"dias": int(np.mean([d["dias"] for d in datos]))}
    for v in ("tmax", "rhum", "wind", "precip"):
        res[v] = [round(float(np.mean([d[v][0] for d in datos])), 2),
                  round(float(np.mean([d[v][1] for d in datos])), 2)]
    return res


def continuidad(serie: pd.DataFrame, desde: pd.Timestamp, hasta: pd.Timestamp) -> list[str]:
    """Fechas ausentes en [desde, hasta]. Vacío = serie continua."""
    esperadas = pd.date_range(desde, hasta, freq="D")
    return [str(d.date()) for d in esperadas.difference(pd.DatetimeIndex(serie.date))]


# ------------------------------------------------------------------- construcción
def construir(sin_cache: bool = False) -> dict:
    defs = json.load(open(os.path.join(STATIC, "isia_definicion.json"), encoding="utf-8"))
    thr = pd.read_csv(os.path.join(STATIC, "niveles_climatologicos.csv"))
    umbrales = thr[(thr.indice == "isia") & (thr.escala == "diaria")][["p25", "p50", "p75", "p90", "p97.5"]].values[0]
    nodos = pd.read_csv(os.path.join(STATIC, "nodos.csv"))
    celdas = pd.read_csv(os.path.join(STATIC, "celdas_modelo.csv"))
    semilla = pd.read_csv(os.path.join(STATIC, "fwi_semilla.csv")).set_index("node_id")
    tasa_base = json.load(open(os.path.join(STATIC, "tasa_base_niveles.json"), encoding="utf-8"))
    semilla["fecha"] = pd.to_datetime(semilla["fecha"])

    ahora = datetime.now(ZoneInfo(TZ))
    hoy = pd.Timestamp(ahora.date())
    fin = hoy + timedelta(days=HORIZONTE)
    seed_fecha = semilla.fecha.max()
    desde = semilla.fecha.min() + timedelta(days=1)

    log(f"Hoy {hoy.date()} ({TZ}) · previsión {hoy.date()} → {fin.date()}")
    log(f"Semilla FFMC/DMC/DC del {seed_fecha.date()} · serie meteo requerida {desde.date()} → {fin.date()}"
        f" ({(fin - desde).days + 1} días)")

    os.makedirs(CACHE, exist_ok=True)
    cache_f = os.path.join(CACHE, f"meteo_{hoy.date()}.parquet")
    cache_org = os.path.join(CACHE, f"meteo_{hoy.date()}_origen.json")
    origenes: dict[str, dict] = {}

    if os.path.exists(cache_f) and os.path.exists(cache_org) and not sin_cache:
        meteo = pd.read_parquet(cache_f)
        meteo["date"] = pd.to_datetime(meteo["date"])
        origenes = json.load(open(cache_org, encoding="utf-8"))
        log(f"Meteorología reutilizada de {os.path.relpath(cache_f, ROOT)}")
    else:
        series = []
        for _, nd in nodos.iterrows():
            s, org = meteo_nodo(nd.node_id, nd.node_lat, nd.node_lon,
                                semilla.loc[nd.node_id, "fecha"] + timedelta(days=1), fin, hoy)
            origenes[nd.node_id] = org
            series.append(s)
            time.sleep(0.3)
        meteo = pd.concat(series, ignore_index=True)
        meteo.to_parquet(cache_f, index=False)
        json.dump(origenes, open(cache_org, "w", encoding="utf-8"), ensure_ascii=False)
        log(f"Meteorología descargada para {len(nodos)} nodos")

    # --- continuidad obligatoria antes de propagar nada ---
    huecos = {}
    for node_id, g in meteo.groupby("node_id"):
        f = continuidad(g, semilla.loc[node_id, "fecha"] + timedelta(days=1), fin)
        if f:
            huecos[node_id] = f
    if huecos:
        raise SystemExit("Serie meteorológica con días ausentes; no se publica previsión.\n"
                         + json.dumps(huecos, indent=2, ensure_ascii=False))
    log(f"Serie meteorológica continua en los {meteo.node_id.nunique()} nodos, sin días ausentes.")
    if "fuente" in meteo.columns:
        rep_fuente = meteo.fuente.value_counts().to_dict()
        log(f"Origen de los días: {rep_fuente}")
    contraste = _resumen_contraste(origenes)
    if contraste:
        log("Contraste archivo ERA5 vs modelo de previsión en los días que ambos cubren "
            f"({contraste['dias']} días/nodo): " + ", ".join(
                f"{v} {contraste[v][0]} vs {contraste[v][1]}" for v in ("tmax", "rhum", "wind", "precip")))
        log("  Las dos fuentes no son homogéneas; la semilla FWI y los umbrales climatológicos "
            "proceden de ERA5. No se aplica corrección de sesgo.")

    # --- FWI propagado desde la semilla, sin reinicios ---
    meteo["month"] = meteo.date.dt.month
    partes = []
    for node_id, g in meteo.sort_values("date").groupby("node_id"):
        s = semilla.loc[node_id]
        r = fwi_series(g, float(s.ffmc), float(s.dmc), float(s.dc))
        r["node_id"] = node_id
        r["date"] = g.date.values
        partes.append(pd.concat([r.reset_index(drop=True),
                                 g[["tmax", "rhum", "wind", "precip"]].reset_index(drop=True)], axis=1))
    fwi_prev = pd.concat(partes, ignore_index=True)
    fwi_hoy = fwi_prev[fwi_prev.date >= hoy].copy()

    # --- ISIA por celda con los parámetros persistidos ---
    def mm(x, lo, hi):
        return np.clip((np.asarray(x, dtype=float) - lo) / (hi - lo + 1e-9), 0, 1)

    pred = celdas.merge(fwi_hoy, on="node_id")
    pred["isia"] = np.clip(mm(pred.fwi, *defs["fwi_p2_p98"]) * pred.f_veg * pred.f_topo, 0, 1).round(4)
    pred["nivel"] = np.digitize(pred.isia, umbrales) + 1
    pred["dia"] = (pred.date - hoy).dt.days
    pred["ndmi_dias"] = (hoy - pd.to_datetime(pred.ndmi_fecha)).dt.days
    # Heurística explícita del TFT: horizonte temporal + antigüedad de la vegetación.
    pred["incert"] = (0.6 * pred.dia / HORIZONTE + 0.4 * np.minimum(pred.ndmi_dias / 30, 1)).round(3)
    pred["incert_cls"] = np.digitize(pred.incert, [0.33, 0.66])

    return {"pred": pred, "fwi_prev": fwi_prev, "meteo": meteo, "hoy": hoy, "fin": fin,
            "seed_fecha": seed_fecha, "desde": desde, "semilla": semilla, "nodos": nodos,
            "celdas": celdas, "umbrales": umbrales, "defs": defs, "origenes": origenes,
            "ahora": ahora, "tasa_base": tasa_base}


# ------------------------------------------------------------------------- calidad
def controles(ctx: dict) -> list[str]:
    """Controles obligatorios previos a publicar. Devuelve la lista de avisos."""
    pred, avisos, errores = ctx["pred"], [], []
    dias = sorted(pred.dia.unique())

    n_celdas = pred.cell_id.nunique()
    log(f"[QC 1] celdas: {n_celdas}" + ("" if n_celdas == 515 else "  <-- ESPERADAS 515"))
    if n_celdas != 515:
        errores.append(f"se esperaban 515 celdas y hay {n_celdas}")

    log(f"[QC 2] horizontes: {dias}")
    if dias != list(range(HORIZONTE + 1)):
        errores.append(f"faltan horizontes: {dias}")
    incompletas = pred.groupby("cell_id").dia.nunique()
    if (incompletas != HORIZONTE + 1).any():
        errores.append(f"{int((incompletas != HORIZONTE + 1).sum())} celdas sin los 8 estados")

    log(f"[QC 3] nodos con serie continua: {ctx['meteo'].node_id.nunique()} "
        f"({ctx['desde'].date()} → {ctx['fin'].date()}, sin huecos)")

    no_finitos = int((~np.isfinite(pred[["fwi", "isia", "nivel"]].to_numpy(dtype=float))).sum())
    log(f"[QC 4] valores no finitos en fwi/isia/nivel: {no_finitos}")
    if no_finitos:
        errores.append(f"{no_finitos} valores no finitos")

    fuera = int(((pred.isia < 0) | (pred.isia > 1)).sum())
    log(f"[QC 5] ISIA fuera de [0,1]: {fuera}")
    if fuera:
        errores.append(f"{fuera} valores de ISIA fuera de [0,1]")

    fuera_n = int(((pred.nivel < 1) | (pred.nivel > 6)).sum())
    log(f"[QC 6] nivel fuera de [1,6]: {fuera_n}")
    if fuera_n:
        errores.append(f"{fuera_n} niveles fuera de [1,6]")

    # QC 7: los parámetros climatológicos deben ser exactamente los persistidos.
    defs_disco = json.load(open(os.path.join(STATIC, "isia_definicion.json"), encoding="utf-8"))
    thr_disco = pd.read_csv(os.path.join(STATIC, "niveles_climatologicos.csv"))
    u_disco = thr_disco[(thr_disco.indice == "isia") & (thr_disco.escala == "diaria")][
        ["p25", "p50", "p75", "p90", "p97.5"]].values[0]
    igual = defs_disco == ctx["defs"] and np.allclose(u_disco, ctx["umbrales"])
    h = hashlib.sha256(open(os.path.join(STATIC, "niveles_climatologicos.csv"), "rb").read()).hexdigest()[:12]
    log(f"[QC 7] parámetros climatológicos sin recalcular: {'sí' if igual else 'NO'} "
        f"(umbrales ISIA diarios {np.round(ctx['umbrales'], 4).tolist()}, sha256 {h})")
    if not igual:
        errores.append("los parámetros climatológicos no coinciden con los persistidos")

    log("[QC 8] celdas por nivel y día:")
    for d in dias:
        sub = pred[pred.dia == d]
        rep = sub.nivel.value_counts().reindex(range(1, 7), fill_value=0)
        fecha = (ctx["hoy"] + timedelta(days=int(d))).date()
        log(f"   D+{d} {fecha}  " + "  ".join(f"{NIVELES[i-1]}:{rep[i]:3d}" for i in range(1, 7)))

        # Diagnóstico: concentración anómala en niveles 5-6. No se corrige nada.
        alto = float((sub.nivel >= 5).mean())
        if alto > 0.75:
            f = sub.fwi
            tb = ctx["tasa_base"]
            aviso = (f"WARNING D+{d} ({fecha}): {alto:.1%} de las celdas en nivel 5-6, frente al "
                     f"{tb['pct_celdas_ge5_temporada']}% de días-celda de temporada en la "
                     f"climatología 2017-2026 ({tb['pct_celdas_ge5_septiembre']}% en septiembre; "
                     f"{tb['dias_con_mas_75pct_ge5']} de {tb['dias_temporada']} días de temporada "
                     f"superaron el 75%). "
                     f"FWI mediana {f.median():.2f}, máx {f.max():.2f}, p90 {f.quantile(.9):.2f}, "
                     f"p99 {f.quantile(.99):.2f}; ISIA mediana {sub.isia.median():.3f}, "
                     f"p90 {sub.isia.quantile(.9):.3f}, máx {sub.isia.max():.3f}; "
                     f"semilla FWI del {ctx['seed_fecha'].date()} "
                     f"(FFMC {ctx['semilla'].ffmc.min():.1f}-{ctx['semilla'].ffmc.max():.1f}, "
                     f"DMC {ctx['semilla'].dmc.min():.1f}-{ctx['semilla'].dmc.max():.1f}, "
                     f"DC {ctx['semilla'].dc.min():.1f}-{ctx['semilla'].dc.max():.1f}); "
                     f"nodos afectados: {sorted(sub[sub.nivel >= 5].node_id.unique())}")
            log("   " + aviso)
            avisos.append(aviso)

    if errores:
        raise SystemExit("Controles de calidad fallidos; no se publica previsión:\n - "
                         + "\n - ".join(errores))
    return avisos


# ------------------------------------------------------------------------ artefacto
def escribir(ctx: dict, avisos: list[str]) -> dict:
    pred, hoy = ctx["pred"], ctx["hoy"]
    dias = list(range(HORIZONTE + 1))
    fechas = [(hoy + timedelta(days=d)) for d in dias]

    nodos_out = {}
    for node_id, g in ctx["fwi_prev"][ctx["fwi_prev"].date >= hoy].sort_values("date").groupby("node_id"):
        nodos_out[node_id] = {
            "t": [round(float(v), 1) for v in g.tmax], "hr": [round(float(v)) for v in g.rhum],
            "p": [round(float(v), 1) for v in g.precip], "w": [round(float(v), 1) for v in g.wind],
            "fwi": [round(float(v), 2) for v in g.fwi],
        }

    celdas_out = {}
    for cell_id, g in pred.sort_values("dia").groupby("cell_id"):
        celdas_out[cell_id] = {
            "nodo": g.node_id.iloc[0], "ndmi": float(g.ndmi_last.iloc[0]),
            "isia": [round(float(v), 4) for v in g.isia],
            "n": [int(v) for v in g.nivel],
            "inc": [round(float(v), 3) for v in g.incert],
            "incc": [int(v) for v in g.incert_cls],
        }

    reparto = [[int((pred[(pred.dia == d)].nivel == n).sum()) for n in range(1, 7)] for d in dias]
    doc = {
        "meta": {
            "generado_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "generado_local": ctx["ahora"].strftime("%d/%m/%Y %H:%M"),
            "tz": TZ,
            "semilla_fwi_fecha": str(ctx["seed_fecha"].date()),
            "meteo_serie_inicio": str(ctx["desde"].date()),
            "forecast_inicio": str(fechas[0].date()),
            "forecast_fin": str(fechas[-1].date()),
            "nodos": int(ctx["nodos"].node_id.nunique()),
            "celdas": int(pred.cell_id.nunique()),
            "huecos": False,
            "fuentes_meteo": {k: {i: v[i] for i in ("archive", "forecast")}
                              for k, v in ctx["origenes"].items()},
            "contraste_fuentes": _resumen_contraste(ctx["origenes"]),
            "ndmi_fecha": str(pred.ndmi_fecha.iloc[0]),
            "ndmi_dias": int(pred.ndmi_dias.iloc[0]),
            "umbrales_isia_diarios": [float(x) for x in ctx["umbrales"]],
            "tasa_base": ctx["tasa_base"],
            "reparto": reparto,
            "avisos": avisos,
        },
        "dias": [{"d": d, "fecha": str(f.date()),
                  "etiqueta": "Hoy" if d == 0 else f"+{d}",
                  "corta": f.strftime("%d/%m")} for d, f in zip(dias, fechas)],
        "nodos": nodos_out,
        "celdas": celdas_out,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, separators=(",", ":"))
    log(f"\n{os.path.relpath(OUT, ROOT)} escrito · {os.path.getsize(OUT)/1024:.0f} KB")
    return doc


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="valida sin escribir el artefacto")
    ap.add_argument("--no-cache", action="store_true", help="fuerza la descarga aunque haya caché del día")
    a = ap.parse_args()

    ctx = construir(sin_cache=a.no_cache)
    avisos = controles(ctx)
    if a.check:
        log("\n--check: controles superados, no se escribe nada.")
        return
    escribir(ctx, avisos)


if __name__ == "__main__":
    main()
