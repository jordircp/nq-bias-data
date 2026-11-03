"""Dibuja el plan del día: velas reales de 15 min + camino hipotético de los escenarios A y B.

Uso:  python draw_plan.py plan.json salida.png

plan.json (horas en hora de ALEMANIA, "HH:MM" de hoy; precios en puntos de NQ):
{
  "titulo": "Bias bajista, pero con cuidado",
  "fecha": "2026-11-04",                       # opcional, por defecto hoy (Europe/Berlin)
  "zonas": [{"nombre": "FVG 1h", "de": 28148, "a": 28214, "tipo": "bajista"},
            {"nombre": "OTE", "de": 28579, "a": 28719, "tipo": "ote"}],
  "niveles": [{"nombre": "PDL", "precio": 28101.75}, {"nombre": "Stop A", "precio": 28270, "tipo": "stop"}],
  "escenario_a": {"nombre": "A: rebote al FVG y venta", "puntos": [["09:00", 28150], ["15:45", 28205], ["21:00", 27980]]},
  "escenario_b": {"nombre": "B: el barrido fue la trampa", "puntos": [["09:00", 28150], ["16:30", 28250]]},
  "marcas": [{"hora": "15:45", "precio": 28205, "texto": "CISD/MSS 5 min → entrada"}]
}
El primer punto de cada escenario se une al último precio real automáticamente.
"""
import json
import sys

import numpy as np
import pandas as pd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

BER = "Europe/Berlin"
BG, FG, GRID, UP, DN = "#131722", "#d1d4dc", "#2a2e39", "#26a69a", "#ef5350"
ZCOL = {"bajista": DN, "alcista": UP, "ote": "#2962ff", "neutra": "#9e9e9e"}


def main(plan_path, out_path, csv="data/latest/candles_15m.csv", bars=56):
    plan = json.load(open(plan_path, encoding="utf-8"))
    df = pd.read_csv(csv, index_col=0)
    df.index = pd.to_datetime(df.index, utc=True).tz_convert(BER)
    df = df.iloc[-bars:]
    fecha = plan.get("fecha") or pd.Timestamp.now(tz=BER).strftime("%Y-%m-%d")
    t_end = pd.Timestamp(f"{fecha} 22:30", tz=BER)
    step = pd.Timedelta("15min")
    last_t = df.index[-1]
    # eje x continuo en pasos de 15 min (incluye el futuro hasta las 22:30)
    n_future = max(0, int((t_end - last_t) / step))
    times = list(df.index) + [last_t + step * (i + 1) for i in range(n_future)]

    def xpos(t):
        # futuro medido desde la última vela real (el mercado cierra 23:00–00:00 y rompe la continuidad)
        return (len(df) - 1) + (pd.Timestamp(t) - last_t) / step

    def tpoint(hhmm):
        return pd.Timestamp(f"{fecha} {hhmm}", tz=BER)

    fig, ax = plt.subplots(figsize=(13, 7.4), dpi=110)
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)
    o, h, l, c = (df[k].values for k in ("Open", "High", "Low", "Close"))
    for i in range(len(df)):
        col = UP if c[i] >= o[i] else DN
        ax.vlines(i, l[i], h[i], color=col, lw=0.9)
        ax.add_patch(Rectangle((i - .35, min(o[i], c[i])), .7, max(abs(c[i] - o[i]), .25), color=col))
    x_now = len(df) - 1
    x_max = len(times) - 1
    ax.axvspan(x_now + 0.5, x_max + 6, color="#ffffff", alpha=0.035)
    ax.axvline(x_now + 0.5, color="#ffd54f", lw=1, ls="--")

    prices = list(l) + list(h)
    right = []  # (precio, texto, color)
    for z in plan.get("zonas", []):
        lo_, hi_ = sorted((z["de"], z["a"]))
        col = ZCOL.get(z.get("tipo", "neutra"), "#9e9e9e")
        ax.add_patch(Rectangle((0, lo_), x_max + 6, hi_ - lo_, color=col, alpha=0.16))
        right.append(((lo_ + hi_) / 2, f"{z['nombre']} {lo_:,.0f}–{hi_:,.0f}".replace(",", "."), col))
        prices += [lo_, hi_]
    for nv in plan.get("niveles", []):
        col = "#ff8a80" if nv.get("tipo") == "stop" else ("#26a69a" if nv.get("tipo") == "objetivo" else "#ffb74d")
        ax.axhline(nv["precio"], color=col, lw=0.9, ls="--", alpha=.9)
        right.append((nv["precio"], f"{nv['nombre']} {nv['precio']:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."), col))
        prices.append(nv["precio"])

    for key, col, lw, alpha in (("escenario_a", "#ffd54f", 2.4, 0.95), ("escenario_b", "#b0bec5", 1.6, 0.75)):
        esc = plan.get(key)
        if not esc or not esc.get("puntos"):
            continue
        xs = [x_now] + [xpos(tpoint(t)) for t, _ in esc["puntos"]]
        ys = [float(c[-1])] + [p for _, p in esc["puntos"]]
        ax.plot(xs, ys, color=col, lw=lw, ls="--", alpha=alpha, marker="o", ms=3.5)
        ax.plot([], [], color=col, lw=lw, ls="--", label=esc.get("nombre", key))
        prices += ys
    for m in plan.get("marcas", []):
        xm = xpos(tpoint(m["hora"]))
        ax.annotate(m["texto"], (xm, m["precio"]), xytext=(xm - 14, m["precio"]), color=FG, fontsize=8.5,
                    arrowprops=dict(arrowstyle="->", color=FG, lw=0.8), va="center", ha="right")

    lo_v, hi_v = min(prices), max(prices)
    pad = (hi_v - lo_v) * 0.08
    ax.set_ylim(lo_v - pad, hi_v + pad)
    gap = (hi_v - lo_v + 2 * pad) * 0.032
    placed = []
    for pr, txt, col in sorted(right, key=lambda r: r[0]):
        y = pr if not placed or pr - placed[-1] >= gap else placed[-1] + gap
        placed.append(y)
        ax.text(x_max + 6.5, y, txt, color=col, fontsize=8.5, va="center")
        if abs(y - pr) > 1e-6:
            ax.plot([x_max + 5.6, x_max + 6.3], [pr, y], color=col, lw=0.6, clip_on=False)
    leg = ax.legend(loc="upper left", facecolor=BG, edgecolor=GRID, labelcolor=FG, fontsize=9)
    if leg:
        leg.get_frame().set_alpha(0.9)
    ax.set_xlim(-1, x_max + 6)
    ticks = []
    for i, t in enumerate(times):
        if t.minute == 0 and t.hour % 2 == 0 and (not ticks or i - ticks[-1] >= 5):
            ticks.append(i)
    ax.set_xticks(ticks)
    ax.set_xticklabels([times[i].strftime("%H:%M") for i in ticks], color=FG, fontsize=8.5)
    for hh, lab in (("08:00", "Londres"), ("14:30", "Datos"), ("15:30", "NY abre")):
        xv = xpos(tpoint(hh))
        if xv > x_now:
            ax.axvline(xv, color=FG, lw=0.5, ls=":", alpha=.6)
            ax.text(xv + .3, lo_v - pad * 0.7, lab, color=FG, fontsize=8, alpha=.8)
    ax.yaxis.tick_right()
    ax.tick_params(colors=FG, labelsize=8.5)
    ax.grid(color=GRID, lw=.5)
    for s in ax.spines.values():
        s.set_color(GRID)
    ax.set_title(f"NQ 15 min · plan {pd.Timestamp(fecha).strftime('%d/%m')} · {plan.get('titulo', '')} (hora Alemania)",
                 color=FG, fontsize=11, loc="left")
    ax.text(0.99, 0.97, "EJEMPLO ILUSTRATIVO · NO ES CONSEJO FINANCIERO", transform=ax.transAxes,
            color=FG, alpha=0.5, fontsize=8, ha="right", va="top")
    ax.text(x_now + 0.8, hi_v + pad * 0.55, "ahora", color="#ffd54f", fontsize=8.5)
    fig.tight_layout()
    fig.savefig(out_path, facecolor=BG)
    print("ok", out_path)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
