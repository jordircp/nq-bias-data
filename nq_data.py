"""Descarga velas de NQ (Yahoo Finance), detecta FVG, liquidez, swings y Fibonacci,
y genera gráficos y un resumen para el bias diario.

Uso: python nq_data.py            (descarga real)
     python nq_data.py --synthetic (datos inventados, solo para probar)
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

TICKER = "NQ=F"
NY = "America/New_York"
OUT = os.path.join("data", "latest")
HIST = os.path.join("data", "history")
MIN_FVG = {"4h": 8.0, "1h": 5.0, "15m": 3.0, "5m": 2.0}  # tamaño mínimo en puntos


# ---------------------------------------------------------------- datos
def download():
    import yfinance as yf

    def get(interval, period):
        df = yf.download(TICKER, interval=interval, period=period, progress=False, auto_adjust=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
        df.index = pd.to_datetime(df.index)
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        return clean_spikes(df.tz_convert(NY))

    d = {
        "5m": get("5m", "5d"),
        "15m": get("15m", "10d"),
        "1h": get("60m", "60d"),
        "1d": get("1d", "6mo"),
    }
    d["4h"] = resample_4h(d["1h"])
    return d


def synthetic():
    rng = np.random.default_rng(7)
    idx = pd.date_range(end=pd.Timestamp.now(tz=NY).floor("5min"), periods=12 * 24 * 5, freq="5min")
    close = 21000 + np.cumsum(rng.normal(0, 6, len(idx)))
    o = np.r_[close[0], close[:-1]]
    hi = np.maximum(o, close) + rng.uniform(0, 4, len(idx))
    lo = np.minimum(o, close) - rng.uniform(0, 4, len(idx))
    m5 = pd.DataFrame({"Open": o, "High": hi, "Low": lo, "Close": close, "Volume": 100}, index=idx)
    agg = {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
    d = {"5m": m5, "15m": m5.resample("15min").agg(agg).dropna(), "1h": m5.resample("60min").agg(agg).dropna()}
    d["1d"] = m5.resample("1D").agg(agg).dropna()
    d["4h"] = resample_4h(d["1h"])
    return d


def clean_spikes(df, k=8.0):
    """Recorta mechas imposibles (errores de datos): mecha > k veces el rango mediano."""
    df = df.copy()
    rng = (df["High"] - df["Low"]).rolling(50, min_periods=10).median().bfill()
    # apertura imposible: salta lejísimos del cierre anterior fuera de la reapertura de las 18:00
    prev_c = df["Close"].shift(1)
    not_reopen = ~((df.index.hour == 18) & (df.index.minute == 0))
    bad_o = ((df["Open"] - prev_c).abs() > rng * k * 0.75) & not_reopen & prev_c.notna()
    df.loc[bad_o, "Open"] = prev_c[bad_o]
    body_hi = df[["Open", "Close"]].max(axis=1)
    body_lo = df[["Open", "Close"]].min(axis=1)
    lim = rng * k
    bad_h = (df["High"] - body_hi) > lim
    bad_l = (body_lo - df["Low"]) > lim
    df.loc[bad_h, "High"] = body_hi[bad_h] + rng[bad_h]
    df.loc[bad_l, "Low"] = body_lo[bad_l] - rng[bad_l]
    # cuerpos imposibles: vela que abre y cierra lejísimos de sus vecinas
    jump = (df["Close"] - df["Close"].shift(1)).abs() > lim * 1.5
    back = (df["Close"].shift(-1) - df["Close"].shift(1)).abs() < rng * 3
    bad_c = jump & back
    if bad_c.any():
        df = df[~bad_c]
    df["High"] = df[["High", "Open", "Close"]].max(axis=1)
    df["Low"] = df[["Low", "Open", "Close"]].min(axis=1)
    return df


def resample_4h(h1):
    agg = {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
    # velas de 4h ancladas a las 18:00 NY (apertura de los futuros)
    return h1.resample("4h", offset="2h").agg(agg).dropna()


def ff_calendar():
    """Calendario de Forex Factory (feed público de la semana): eventos USD de impacto alto/medio."""
    import urllib.request
    url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (nq-bias-data)"})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = json.loads(r.read().decode("utf-8"))
    out = []
    for e in data:
        if e.get("country") != "USD" or e.get("impact") not in ("High", "Medium"):
            continue
        t = pd.Timestamp(e["date"]).tz_convert("Europe/Berlin")
        out.append({
            "fecha": t.strftime("%Y-%m-%d"), "hora_alemania": t.strftime("%H:%M"),
            "evento": e.get("title"), "impacto": "ALTO" if e["impact"] == "High" else "medio",
            "prevision": e.get("forecast") or None, "anterior": e.get("previous") or None,
            "real": e.get("actual") or None,
        })
    return out


# ---------------------------------------------------------------- análisis
def find_fvgs(df, tf, max_items=8):
    """FVG de 3 velas. Devuelve los no rellenados (o parcialmente) más recientes."""
    out = []
    h, l, c = df["High"].values, df["Low"].values, df["Close"].values
    for i in range(1, len(df) - 1):
        kind = None
        if l[i + 1] > h[i - 1] and l[i + 1] - h[i - 1] >= MIN_FVG[tf]:
            kind, top, bot = "alcista", l[i + 1], h[i - 1]
        elif h[i + 1] < l[i - 1] and l[i - 1] - h[i + 1] >= MIN_FVG[tf]:
            kind, top, bot = "bajista", l[i - 1], h[i + 1]
        if not kind:
            continue
        later_l, later_h = l[i + 2:], h[i + 2:]
        if kind == "alcista":
            filled = len(later_l) and later_l.min() <= bot
            touched = len(later_l) and later_l.min() < top
        else:
            filled = len(later_h) and later_h.max() >= top
            touched = len(later_h) and later_h.max() > bot
        if filled:
            continue
        out.append({
            "tf": tf, "tipo": kind, "arriba": round(float(top), 2), "abajo": round(float(bot), 2),
            "ce": round(float((top + bot) / 2), 2), "tamano": round(float(top - bot), 2),
            "hora_ny": df.index[i].strftime("%Y-%m-%d %H:%M"),
            "estado": "tocado" if touched else "virgen", "_i": i,
        })
    last = float(c[-1])
    above = sorted([f for f in out if f["abajo"] >= last], key=lambda f: f["abajo"])[: max_items // 2]
    below = sorted([f for f in out if f["arriba"] <= last], key=lambda f: -f["arriba"])[: max_items // 2]
    inside = [f for f in out if f["abajo"] < last < f["arriba"]]
    return sorted(inside + above + below, key=lambda f: -f["arriba"])


def swings(df, order=3):
    h, l = df["High"].values, df["Low"].values
    sh, sl = [], []
    for i in range(order, len(df) - order):
        if h[i] == h[i - order:i + order + 1].max():
            sh.append((i, float(h[i])))
        if l[i] == l[i - order:i + order + 1].min():
            sl.append((i, float(l[i])))
    return sh, sl


def structure(df, order=3):
    """Estructura por rupturas: el último cierre que rompe un swing marca la dirección.
    Romper a favor = BOS; romper en contra = CHoCH/MSS."""
    sh, sl = swings(df, order)
    c = df["Close"].values
    state, last_break, broken = None, None, set()
    for j in range(len(df)):
        act_h = [p for p in sh if p[0] + order <= j and p[0] not in broken]
        act_l = [p for p in sl if p[0] + order <= j and p[0] not in broken]
        if act_h and c[j] > act_h[-1][1]:
            kind = "BOS" if state == "alcista" else "CHoCH/MSS"
            state = "alcista"
            broken.add(act_h[-1][0])
            last_break = f"{kind} alcista: cierre por encima de {act_h[-1][1]:.2f} ({df.index[j].strftime('%d/%m %H:%M')} NY)"
        elif act_l and c[j] < act_l[-1][1]:
            kind = "BOS" if state == "bajista" else "CHoCH/MSS"
            state = "bajista"
            broken.add(act_l[-1][0])
            last_break = f"{kind} bajista: cierre por debajo de {act_l[-1][1]:.2f} ({df.index[j].strftime('%d/%m %H:%M')} NY)"
    pattern = ""
    if len(sh) >= 2 and len(sl) >= 2:
        pattern = (" · " + ("HH" if sh[-1][1] > sh[-2][1] else "LH") + " y " + ("HL" if sl[-1][1] > sl[-2][1] else "LL"))
    return {
        "tendencia": (state or "sin ruptura clara") + pattern, "evento": last_break,
        "ultimos_swing_high": [round(v, 2) for _, v in sh[-3:]],
        "ultimos_swing_low": [round(v, 2) for _, v in sl[-3:]],
    }, sh, sl


def fib_leg(df, sh, sl):
    if not sh or not sl:
        return None
    h, l = df["High"].values, df["Low"].values
    ih0, vh0 = sh[-1]
    il_end = ih0 + int(np.argmin(l[ih0:]))  # mínimo real tras el último swing high
    down = (vh0 - l[il_end], ih0, vh0, il_end, float(l[il_end]))
    il0, vl0 = sl[-1]
    ih_end = il0 + int(np.argmax(h[il0:]))  # máximo real tras el último swing low
    up = (h[ih_end] - vl0, ih_end, float(h[ih_end]), il0, vl0)
    last0 = float(df["Close"].iloc[-1])
    up_ok, down_ok = last0 >= up[4], last0 <= down[2]  # un tramo cuyo origen ya se ha roto no vale
    if up_ok and (not down_ok or up[0] >= down[0]):
        _, ih, vh, il, vl = up
        d = "alcista (mínimo → máximo)"
    else:
        _, ih, vh, il, vl = down
        d = "bajista (máximo → mínimo)"
    rng = vh - vl
    if rng <= 0:
        return None
    lv = (lambda r: vh - rng * r) if d.startswith("alcista") else (lambda r: vl + rng * r)
    last = float(df["Close"].iloc[-1])
    eq = lv(0.5)
    zona = "premium" if last > eq else "discount"
    return {
        "direccion": d, "maximo": round(vh, 2), "minimo": round(vl, 2),
        "hora_max": df.index[ih].strftime("%Y-%m-%d %H:%M"), "hora_min": df.index[il].strftime("%Y-%m-%d %H:%M"),
        "50": round(eq, 2), "62": round(lv(0.62), 2), "70.5": round(lv(0.705), 2), "79": round(lv(0.79), 2),
        "precio_en": zona, "_ih": ih, "_il": il,
    }


def sessions(m5):
    now = m5.index[-1]
    day = now.normalize()
    if now.hour >= 18:  # tras las 18:00 NY empieza el día de futuros siguiente
        day = day + timedelta(days=1)

    def rng(start, end):
        s = m5[(m5.index >= start) & (m5.index < end)]
        if s.empty:
            return None
        return {"maximo": round(float(s["High"].max()), 2), "minimo": round(float(s["Low"].min()), 2)}

    prev = day - timedelta(days=1)
    return {
        "dia_trading": day.strftime("%Y-%m-%d"),
        "asia": rng(prev + timedelta(hours=20), day),
        "londres": rng(day + timedelta(hours=2), day + timedelta(hours=5)),
        "ny_am": rng(day + timedelta(hours=9, minutes=30), day + timedelta(hours=12)),
        "overnight": rng(prev + timedelta(hours=18), day + timedelta(hours=9, minutes=30)),
    }


def prev_day(daily, m15):
    """Día de futuros anterior (18:00–17:00 NY) calculado con velas de 15 min."""
    now = m15.index[-1]
    day = now.normalize() + (timedelta(days=1) if now.hour >= 18 else timedelta(0))
    start = day - timedelta(hours=6)  # 18:00 NY del día anterior = inicio del día actual
    prev = m15[(m15.index < start)]
    if prev.empty:
        return None
    pday_end = prev.index[-1]
    pday_start = (pday_end.normalize() - timedelta(hours=6)) if pday_end.hour < 18 else (pday_end.normalize() + timedelta(hours=18))
    p = prev[prev.index >= pday_start]
    week_start = day - timedelta(days=day.weekday()) - timedelta(hours=6)  # domingo 18:00 NY
    wk = m15[m15.index >= week_start]
    return {
        "fecha": pday_end.strftime("%Y-%m-%d"),
        "PDH": round(float(p["High"].max()), 2), "PDL": round(float(p["Low"].min()), 2),
        "cierre": round(float(p["Close"].iloc[-1]), 2),
        "max_semana": round(float(wk["High"].max()), 2), "min_semana": round(float(wk["Low"].min()), 2),
    }


def equal_levels(df, tol=3.0, lookback=60):
    """Máximos / mínimos iguales recientes (liquidez limpia)."""
    sh, sl = swings(df.iloc[-lookback:], 2)
    res = {"maximos_iguales": [], "minimos_iguales": []}
    for key, pts in (("maximos_iguales", sh), ("minimos_iguales", sl)):
        vals = [v for _, v in pts]
        for a in range(len(vals)):
            for b in range(a + 1, len(vals)):
                if abs(vals[a] - vals[b]) <= tol:
                    lvl = round((vals[a] + vals[b]) / 2, 2)
                    if all(abs(lvl - x) > tol for x in res[key]):
                        res[key].append(lvl)
    return res


def history_stats(h1):
    """Estadísticas de los últimos días de futuros (18:00–17:00 NY) con velas de 1h."""
    df = h1.copy()
    day = (df.index + pd.Timedelta(hours=6)).normalize()  # 18:00 NY pasa al día siguiente
    df["dia"] = day.tz_localize(None) if day.tz is not None else day
    hr = df.index.hour + df.index.minute / 60

    def ses(h):
        if h >= 18 or h < 2:
            return "Asia"
        if h < 8:
            return "Londres"
        if 9.5 <= h < 16:
            return "NY"
        return "otro"

    df["sesion"] = [ses(x) for x in hr]
    rows = []
    for d, g in df.groupby("dia"):
        if len(g) < 18:
            continue
        hi, lo = g["High"].max(), g["Low"].min()
        rows.append({
            "dia": d, "open": g["Open"].iloc[0], "close": g["Close"].iloc[-1], "high": hi, "low": lo,
            "rango": hi - lo,
            "ses_max": g.loc[g["High"].idxmax(), "sesion"], "ses_min": g.loc[g["Low"].idxmin(), "sesion"],
            "completo": g.index[-1].hour >= 15 and g.index[-1].hour < 18,
        })
    t = pd.DataFrame(rows).sort_values("dia").reset_index(drop=True)
    if len(t) < 10:
        return None
    t = t[t["completo"]].reset_index(drop=True)  # solo días cerrados
    t["toma_pdh"] = t["high"] > t["high"].shift(1)
    t["toma_pdl"] = t["low"] < t["low"].shift(1)
    t["pos_cierre"] = (t["close"] - t["low"]) / t["rango"]
    prev_bajista = t["pos_cierre"].shift(1) < 0.25
    prev_alcista = t["pos_cierre"].shift(1) > 0.75
    pct = lambda s: round(float(s.mean()) * 100) if len(s) else None  # noqa: E731
    n = len(t) - 1
    out = {
        "dias_analizados": int(n),
        "rango_medio_20d": round(float(t["rango"].tail(20).mean()), 1),
        "rango_medio_5d": round(float(t["rango"].tail(5).mean()), 1),
        "pct_dias_toman_pdh": pct(t["toma_pdh"].iloc[1:]),
        "pct_dias_toman_pdl": pct(t["toma_pdl"].iloc[1:]),
        "pct_dias_toman_ambos": pct((t["toma_pdh"] & t["toma_pdl"]).iloc[1:]),
        "sesion_del_maximo_pct": {k: round(v * 100) for k, v in t["ses_max"].value_counts(normalize=True).items()},
        "sesion_del_minimo_pct": {k: round(v * 100) for k, v in t["ses_min"].value_counts(normalize=True).items()},
    }
    tb = t[prev_bajista.fillna(False)]
    if len(tb) >= 3:
        out["tras_dia_bajista_fuerte"] = {
            "casos": int(len(tb)), "pct_toma_pdl": pct(tb["toma_pdl"]), "pct_toma_pdh": pct(tb["toma_pdh"]),
            "pct_cierra_arriba_de_apertura": pct(tb["close"] > tb["open"]),
            "rebote_medio_desde_apertura": round(float((tb["high"] - tb["open"]).mean()), 1),
        }
    ta = t[prev_alcista.fillna(False)]
    if len(ta) >= 3:
        out["tras_dia_alcista_fuerte"] = {
            "casos": int(len(ta)), "pct_toma_pdh": pct(ta["toma_pdh"]), "pct_toma_pdl": pct(ta["toma_pdl"]),
            "pct_cierra_abajo_de_apertura": pct(ta["close"] < ta["open"]),
            "retroceso_medio_desde_apertura": round(float((ta["open"] - ta["low"]).mean()), 1),
        }
    ult = t.iloc[-1]
    out["dia_anterior_tipo"] = "bajista fuerte" if ult["pos_cierre"] < 0.25 else ("alcista fuerte" if ult["pos_cierre"] > 0.75 else "neutro")
    return out


# ---------------------------------------------------------------- gráficos
BG, FG, GRID = "#131722", "#d1d4dc", "#2a2e39"
UP, DN = "#26a69a", "#ef5350"


def chart(df, tf, fvgs, levels, fib=None, bars=90, path=None, title=""):
    df = df.iloc[-bars:]
    off = len(df.index)  # posiciones 0..n-1
    fig, ax = plt.subplots(figsize=(13, 7), dpi=110)
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)
    x = np.arange(off)
    o, h, l, c = (df[k].values for k in ("Open", "High", "Low", "Close"))
    for i in x:
        col = UP if c[i] >= o[i] else DN
        ax.vlines(i, l[i], h[i], color=col, linewidth=0.9)
        ax.add_patch(Rectangle((i - 0.35, min(o[i], c[i])), 0.7, max(abs(c[i] - o[i]), 0.25), color=col))
    lo_v, hi_v = l.min(), h.max()
    pad = (hi_v - lo_v) * 0.08
    start_time = df.index[0]
    for f in fvgs:
        t = pd.Timestamp(f["hora_ny"]).tz_localize(NY)
        xs = max(0, int(np.searchsorted(df.index, t)))
        if f["arriba"] < lo_v - pad or f["abajo"] > hi_v + pad:
            continue
        col = UP if f["tipo"] == "alcista" else DN
        ax.add_patch(Rectangle((xs, f["abajo"]), off - xs + 3, f["arriba"] - f["abajo"], color=col, alpha=0.18))
        ax.text(off + 3.5, f["ce"], f"FVG {tf} {f['abajo']:.0f}-{f['arriba']:.0f}", color=col, fontsize=8, va="center")
    for name, val, col in levels:
        if val is None or not (lo_v - pad * 3 <= val <= hi_v + pad * 3):
            continue
        ax.axhline(val, color=col, linewidth=0.9, linestyle="--", alpha=0.85)
        ax.text(1, val, f" {name} {val:.2f}", color=col, fontsize=8, va="bottom")
    if fib:
        ax.axhspan(min(fib["62"], fib["79"]), max(fib["62"], fib["79"]), color="#2962ff", alpha=0.12)
        ax.axhline(fib["50"], color="#2962ff", linewidth=0.8, linestyle=":")
        ax.text(off - 10, fib["50"], "Fib 50 %", color="#5b8cff", fontsize=8, ha="right", va="bottom")
        ax.text(off - 10, fib["70.5"], "OTE 62-79 %", color="#5b8cff", fontsize=8, ha="right", va="center")
    ax.axhline(c[-1], color=FG, linewidth=0.6, alpha=0.6)
    ax.text(off + 0.5, c[-1], f" {c[-1]:.2f}", color=BG, fontsize=8, va="center",
            bbox=dict(facecolor=FG, edgecolor="none", pad=1.5))
    ax.set_xlim(-1, off + 22)
    ax.set_ylim(lo_v - pad, hi_v + pad)
    step = max(1, off // 8)
    ax.set_xticks(x[::step])
    fmt = "%d/%m %H:%M" if tf in ("5m", "15m", "1h") else "%d/%m"
    ax.set_xticklabels([t.strftime(fmt) for t in df.index[::step]], color=FG, fontsize=8)
    ax.tick_params(colors=FG, labelsize=8)
    ax.yaxis.tick_right()
    ax.grid(color=GRID, linewidth=0.5)
    for s in ax.spines.values():
        s.set_color(GRID)
    ax.set_title(f"NQ {tf} · {title} (hora NY)", color=FG, fontsize=11, loc="left")
    fig.tight_layout()
    fig.savefig(path, facecolor=BG)
    plt.close(fig)


# ---------------------------------------------------------------- main
def clean(o):
    if isinstance(o, dict):
        return {k: clean(v) for k, v in o.items() if not k.startswith("_")}
    if isinstance(o, list):
        return [clean(v) for v in o]
    return o


def main():
    data = synthetic() if "--synthetic" in sys.argv else download()
    m5 = data["5m"]
    os.makedirs(OUT, exist_ok=True)
    last = float(m5["Close"].iloc[-1])
    sess = sessions(m5)
    pdx = prev_day(data["1d"], data["15m"])

    summary = {
        "generado_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
        "ultima_vela_ny": m5.index[-1].strftime("%Y-%m-%d %H:%M"),
        "precio": round(last, 2),
        "dia_anterior": pdx, "sesiones": sess,
        "fuente": "Yahoo Finance (NQ=F, contrato continuo; puede tener algunos minutos de retraso y pequeñas diferencias con el contrato que operes)",
    }
    levels = []
    if pdx:
        levels += [("PDH", pdx["PDH"], "#ffb74d"), ("PDL", pdx["PDL"], "#ffb74d")]
    for key, name, col in (("asia", "Asia", "#ba68c8"), ("londres", "Londres", "#4fc3f7")):
        if sess.get(key):
            levels += [(f"{name} H", sess[key]["maximo"], col), (f"{name} L", sess[key]["minimo"], col)]

    per_tf = {}
    for tf, order, bars in (("4h", 2, 90), ("1h", 3, 120), ("15m", 3, 120), ("5m", 3, 130)):
        df = data[tf]
        st, sh, sl = structure(df, order)
        fv = find_fvgs(df, tf)
        fib = fib_leg(df, sh, sl) if tf in ("4h", "1h") else None
        eq = equal_levels(df)
        per_tf[tf] = {"estructura": st, "fvg_abiertos": fv, "fibonacci": fib, "liquidez_iguales": eq}
        chart(df, tf, fv, levels, fib=fib, bars=bars, path=os.path.join(OUT, f"chart_{tf}.png"),
              title=f"tendencia {st['tendencia']}")
        df.tail(400).round(2).to_csv(os.path.join(OUT, f"candles_{tf}.csv"))
    summary["temporalidades"] = per_tf
    try:
        summary["estadisticas"] = history_stats(data["1h"])
    except Exception as e:  # nunca romper el informe por las estadísticas
        summary["estadisticas"] = {"error": str(e)}
    try:
        cal = ff_calendar()
        hoy = pd.Timestamp.now(tz="Europe/Berlin").strftime("%Y-%m-%d")
        summary["calendario_hoy"] = [e for e in cal if e["fecha"] == hoy]
        summary["calendario_semana"] = [e for e in cal if e["fecha"] > hoy and e["impacto"] == "ALTO"]
    except Exception as e:  # el calendario es un extra: si falla, se sigue
        summary["calendario_hoy"] = {"error": str(e)}
    summary = clean(summary)

    with open(os.path.join(OUT, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    with open(os.path.join(OUT, "summary.md"), "w", encoding="utf-8") as f:
        f.write(to_md(summary))
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M")
    os.makedirs(HIST, exist_ok=True)
    with open(os.path.join(HIST, f"{stamp}.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False)
    print(to_md(summary))


def to_md(s):
    L = [f"# NQ datos · {s['ultima_vela_ny']} (NY)", "", f"Precio: **{s['precio']}**", ""]
    p = s.get("dia_anterior")
    if p:
        L.append(f"Día anterior ({p['fecha']}): PDH {p['PDH']} · PDL {p['PDL']} · cierre {p['cierre']} · semana {p['min_semana']}–{p['max_semana']}")
    for k, v in s["sesiones"].items():
        if isinstance(v, dict):
            L.append(f"- {k}: {v['minimo']} – {v['maximo']}")
    for tf, t in s["temporalidades"].items():
        st = t["estructura"]
        L += ["", f"## {tf}: {st['tendencia']}" + (f" · {st['evento']}" if st["evento"] else "")]
        L.append(f"Swing highs {st['ultimos_swing_high']} · swing lows {st['ultimos_swing_low']}")
        if t["fibonacci"]:
            fb = t["fibonacci"]
            L.append(f"Fib {fb['direccion']}: {fb['minimo']}–{fb['maximo']} · 50 % {fb['50']} · OTE {fb['62']}/{fb['70.5']}/{fb['79']} · precio en {fb['precio_en']}")
        for f in t["fvg_abiertos"]:
            L.append(f"- FVG {f['tipo']} {f['abajo']}–{f['arriba']} (CE {f['ce']}, {f['estado']}, {f['hora_ny']})")
        eq = t["liquidez_iguales"]
        if eq["maximos_iguales"] or eq["minimos_iguales"]:
            L.append(f"Máximos iguales {eq['maximos_iguales']} · mínimos iguales {eq['minimos_iguales']}")
    cal = s.get("calendario_hoy")
    if isinstance(cal, list):
        L += ["", "## Calendario USD de hoy (Forex Factory, hora de Alemania)"]
        if not cal:
            L.append("Sin eventos de impacto alto o medio.")
        for e in cal:
            L.append(f"- {e['hora_alemania']} · {e['evento']} · impacto {e['impacto']} · previsión {e['prevision']} · anterior {e['anterior']}" + (f" · REAL {e['real']}" if e.get('real') else ""))
        for e in s.get("calendario_semana") or []:
            L.append(f"- Próximo ALTO: {e['fecha']} {e['hora_alemania']} · {e['evento']} · previsión {e['prevision']}")
    elif isinstance(cal, dict):
        L += ["", f"Calendario no disponible: {cal.get('error')}"]
    st = s.get("estadisticas") or {}
    if st and "error" not in st:
        L += ["", f"## Estadísticas (últimos {st['dias_analizados']} días)"]
        L.append(f"Rango medio: {st['rango_medio_20d']} pts (20d) · {st['rango_medio_5d']} pts (5d)")
        L.append(f"Toma PDH {st['pct_dias_toman_pdh']} % · toma PDL {st['pct_dias_toman_pdl']} % · ambos {st['pct_dias_toman_ambos']} %")
        L.append(f"Sesión del máximo: {st['sesion_del_maximo_pct']} · del mínimo: {st['sesion_del_minimo_pct']}")
        L.append(f"Ayer fue un día: {st['dia_anterior_tipo']}")
        for k in ("tras_dia_bajista_fuerte", "tras_dia_alcista_fuerte"):
            if k in st:
                L.append(f"{k.replace('_', ' ')}: {st[k]}")
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    main()
