# -*- coding: utf-8 -*-
"""
Baseline mínimo de diagnóstico de causa raíz (Sprint 1 · fase Modelado).

Punto de comparación TRIVIAL (no aprende): para cada "caso" (una avalancha = un equipo con
>= `umbral` alarmas en una ventana) propone una causa raíz Top-1 con una regla simple:

  - "temprana"  : la alarma que ocurrió primero         (causa precede al efecto)
  - "severa"    : la alarma más severa (severity menor)  (lo más grave manda)
  - "frecuente" : el tipo de alarma más frecuente del caso

Sirve de piso contra el cual se comparará el motor híbrido (Hawkes / GNN).

Evaluación provisional: como todavía no hay etiquetas del NOC, se usa un PROXY de dominio: en los
casos donde co-ocurren RADIO_RSL_LOW (señal baja) y MW_LOF (pérdida de trama), la causa física es
RADIO_RSL_LOW. NO es verdad de referencia validada; es un control de cordura hasta etiquetar con el NOC.

Uso:
    from src.baseline import ejecutar
    metrics = ejecutar(df, out_dir="results/baseline")
"""
import os
import json
import pandas as pd

CAUSA_PROXY = "RADIO_RSL_LOW"
EFECTO_PROXY = "MW_LOF"
CLAVES = ("ventana_id", "address")


def _col_tiempo(df):
    for c in ["firstOccurUtc", "occurUtc", "arriveUtc", "latestOccurUtc"]:
        if c in df.columns and df[c].notna().any():
            return c
    return None


def construir_casos(df, claves=CLAVES, umbral=3):
    """Devuelve los casos (grupos con >= umbral alarmas) = avalanchas a diagnosticar."""
    claves = list(claves)
    tam = df.groupby(claves).size().rename("n_alarmas")
    return tam[tam >= umbral].reset_index()


def _ordenar_grupo(g, regla, tcol):
    if regla == "temprana":
        cols = [tcol] + (["csn"] if "csn" in g.columns else [])
        return g.sort_values(cols)
    if regla == "severa":
        sev = g["severity"] if "severity" in g.columns else pd.Series(9, index=g.index)
        return g.assign(_sev=sev).sort_values(["_sev", tcol])
    if regla == "frecuente":
        frec = g["alarmName"].map(g["alarmName"].value_counts())
        return g.assign(_frec=frec).sort_values(["_frec", tcol], ascending=[False, True])
    raise ValueError(f"regla desconocida: {regla}")


def baseline_top1(df, regla="temprana", claves=CLAVES, umbral=3, topk=3):
    """Para cada caso devuelve la causa raíz Top-1 (y un ranking Top-k) según la regla."""
    claves = list(claves)
    tcol = _col_tiempo(df)
    casos = construir_casos(df, claves, umbral)
    sub = df.merge(casos[claves], on=claves)
    filas = []
    for kv, g in sub.groupby(claves):
        gr = _ordenar_grupo(g, regla, tcol)
        top = gr.iloc[0]
        kv = kv if isinstance(kv, tuple) else (kv,)
        rec = dict(zip(claves, kv))
        rec.update({
            "n_alarmas": len(g),
            "top1_alarmName": top.get("alarmName"),
            "top1_csn": top.get("csn"),
            "top1_tiempo": top.get(tcol) if tcol else None,
            "topk_alarmas": list(gr["alarmName"].head(topk)),
            "regla": regla,
        })
        filas.append(rec)
    cols = claves + ["n_alarmas", "top1_alarmName", "top1_csn", "top1_tiempo", "topk_alarmas", "regla"]
    return pd.DataFrame(filas, columns=cols)


def evaluar_proxy(top1, df, claves=CLAVES, causa=CAUSA_PROXY, efecto=EFECTO_PROXY):
    """Top-1 provisional contra el proxy de dominio (causa<-efecto). No validado por NOC."""
    claves = list(claves)
    sets = df.groupby(claves)["alarmName"].agg(lambda s: set(s))
    casos_proxy = {k for k, s in sets.items() if (causa in s and efecto in s)}
    t = top1.copy()
    t["_key"] = list(map(tuple, t[claves].values))
    sub = t[t["_key"].isin(casos_proxy)]
    if len(sub) == 0:
        return {"casos_proxy": 0, "top1_accuracy_proxy": None}
    return {"casos_proxy": int(len(sub)),
            "top1_accuracy_proxy": round(float((sub["top1_alarmName"] == causa).mean()), 3)}


def ejecutar(df, out_dir="results/baseline", reglas=("temprana", "severa", "frecuente"), umbral=3):
    """Corre las reglas, guarda Top-1 por caso (CSV), métricas (JSON) y deja logs. Reproducible."""
    try:
        from .ingesta import configurar
    except ImportError:
        from ingesta import configurar
    log = configurar()
    os.makedirs(out_dir, exist_ok=True)
    metrics = {}
    for r in reglas:
        t = baseline_top1(df, regla=r, umbral=umbral)
        t.to_csv(os.path.join(out_dir, f"baseline_top1_{r}.csv"), index=False, encoding="utf-8-sig")
        m = evaluar_proxy(t, df)
        metrics[r] = {"n_casos": int(len(t)), **m}
        log.info("Baseline '%s': casos=%d | Top-1 proxy=%s (n=%s)",
                 r, len(t), m["top1_accuracy_proxy"], m["casos_proxy"])
    with open(os.path.join(out_dir, "baseline_metrics.json"), "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2, ensure_ascii=False)
    log.info("Resultados y métricas guardados en %s/", out_dir)
    return metrics


if __name__ == "__main__":
    import sys
    try:
        from .ingesta import construir_dataset, parsear_moi, marcar_particion_temporal
    except ImportError:
        from ingesta import construir_dataset, parsear_moi, marcar_particion_temporal
    base = sys.argv[1] if len(sys.argv) > 1 else "data/raw"
    proc = "data/processed/dataset_consolidado.parquet"
    df = pd.read_parquet(proc) if os.path.exists(proc) else \
        marcar_particion_temporal(parsear_moi(construir_dataset(base)))
    print(json.dumps(ejecutar(df), indent=2, ensure_ascii=False))
