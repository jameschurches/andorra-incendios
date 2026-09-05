"""
Sistema FWI canadiense (Van Wagner 1987), portado sin cambios desde el apéndice 7 del
notebook del TFT y de la variante sembrada usada para la previsión.

Entradas diarias por nodo meteorológico: tmax (°C), rhum (%), wind (km/h), precip 24h (mm).
Se usa tmax como temperatura de mediodía (aproximación documentada en el TFT).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# Factores de duración del día (Le para DMC, Lf para DC) por mes, latitud 40-45 N.
LE_MONTH = {1: 6.5, 2: 7.5, 3: 9.0, 4: 12.8, 5: 13.9, 6: 13.9,
            7: 12.4, 8: 10.9, 9: 9.4, 10: 8.0, 11: 7.0, 12: 6.0}
LF_MONTH = {1: -1.6, 2: -1.6, 3: -1.6, 4: 0.9, 5: 3.8, 6: 5.8,
            7: 6.4, 8: 5.0, 9: 2.4, 10: 0.4, 11: -1.6, 12: -1.6}

# Valores de arranque estándar del sistema, usados solo cuando no hay semilla.
ARRANQUE = (85.0, 6.0, 15.0)


def fwi_series(df: pd.DataFrame, ffmc0: float = ARRANQUE[0],
               dmc0: float = ARRANQUE[1], dc0: float = ARRANQUE[2]) -> pd.DataFrame:
    """Serie FWI diaria para UN nodo.

    `df` debe venir ordenado por fecha y sin días ausentes, con columnas
    tmax, rhum, wind, precip y month. `ffmc0`/`dmc0`/`dc0` son el estado del día
    anterior al primero de `df`: con ellos se siembra la propagación en lugar de
    reiniciar los códigos de humedad.
    """
    T = df.tmax.values
    H = np.clip(df.rhum.values, 0, 100)
    W = df.wind.values
    P = df.precip.values
    M = df.month.values
    n = len(T)
    out = np.zeros((n, 6))

    for i in range(n):
        t, h, w, ro, mo = T[i], H[i], W[i], P[i], M[i]

        # ---- FFMC (humedad del combustible fino) ----
        mo_ = 147.2 * (101 - ffmc0) / (59.5 + ffmc0)
        if ro > 0.5:
            rf = ro - 0.5
            mr = mo_ + 42.5 * rf * np.exp(-100 / (251 - mo_)) * (1 - np.exp(-6.93 / rf))
            if mo_ > 150:
                mr += 0.0015 * (mo_ - 150) ** 2 * rf ** 0.5
            mo_ = min(mr, 250)
        Ed = 0.942 * h ** 0.679 + 11 * np.exp((h - 100) / 10) + 0.18 * (21.1 - t) * (1 - np.exp(-0.115 * h))
        if mo_ > Ed:
            ko = 0.424 * (1 - (h / 100) ** 1.7) + 0.0694 * w ** 0.5 * (1 - (h / 100) ** 8)
            m = Ed + (mo_ - Ed) * 10 ** (-ko * 0.581 * np.exp(0.0365 * t))
        else:
            Ew = 0.618 * h ** 0.753 + 10 * np.exp((h - 100) / 10) + 0.18 * (21.1 - t) * (1 - np.exp(-0.115 * h))
            if mo_ < Ew:
                kl = 0.424 * (1 - ((100 - h) / 100) ** 1.7) + 0.0694 * w ** 0.5 * (1 - ((100 - h) / 100) ** 8)
                m = Ew - (Ew - mo_) * 10 ** (-kl * 0.581 * np.exp(0.0365 * t))
            else:
                m = mo_
        ff = float(np.clip(59.5 * (250 - m) / (147.2 + m), 0, 101))
        ffmc0 = ff

        # ---- DMC (humedad del mantillo) ----
        te = max(t, -1.1)
        K = 1.894 * (te + 1.1) * (100 - h) * LE_MONTH[int(mo)] * 1e-6
        if ro > 1.5:
            re = 0.92 * ro - 1.27
            mo_d = 20 + np.exp(5.6348 - dmc0 / 43.43)
            if dmc0 <= 33:
                b = 100 / (0.5 + 0.3 * dmc0)
            elif dmc0 <= 65:
                b = 14 - 1.3 * np.log(dmc0)
            else:
                b = 6.2 * np.log(dmc0) - 17.2
            pr = max(244.72 - 43.43 * np.log(mo_d + 1000 * re / (48.77 + b * re) - 20), 0)
        else:
            pr = dmc0
        dmc0 = max(pr + 100 * K, 0)

        # ---- DC (sequía profunda) ----
        V = max(0.36 * (te + 2.8) + LF_MONTH[int(mo)], 0)
        if ro > 2.8:
            rd = 0.83 * ro - 1.27
            Qr = 800 * np.exp(-dc0 / 400) + 3.937 * rd
            dr = max(400 * np.log(800 / Qr), 0)
        else:
            dr = dc0
        dc0 = dr + 0.5 * V

        # ---- ISI, BUI y FWI ----
        mm_ = 147.2 * (101 - ff) / (59.5 + ff)
        isi = 0.208 * np.exp(0.05039 * w) * 91.9 * np.exp(-0.1386 * mm_) * (1 + mm_ ** 5.31 / 4.93e7)
        if dmc0 <= 0.4 * dc0:
            bu = 0.8 * dmc0 * dc0 / (dmc0 + 0.4 * dc0 + 1e-9)
        else:
            bu = dmc0 - (1 - 0.8 * dc0 / (dmc0 + 0.4 * dc0 + 1e-9)) * (0.92 + (0.0114 * dmc0) ** 1.7)
        bu = max(bu, 0)
        fD = 0.626 * bu ** 0.809 + 2 if bu <= 80 else 1000 / (25 + 108.64 * np.exp(-0.023 * bu))
        B = 0.1 * isi * fD
        out[i] = [ff, dmc0, dc0, isi, bu, np.exp(2.72 * (0.434 * np.log(B)) ** 0.647) if B > 1 else B]

    return pd.DataFrame(out, columns=["ffmc", "dmc", "dc", "isi", "bui", "fwi"], index=df.index)
