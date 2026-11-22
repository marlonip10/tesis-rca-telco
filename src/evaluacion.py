# -*- coding: utf-8 -*-
"""
Protocolo de evaluación (Sprint 2 · OE6): folds temporales, enmascaramiento, métodos de referencia y métricas.

Todo método de causa raíz se evalúa igual: recibe las alarmas de un incidente y devuelve un RANKING de equipos
candidatos (rank 1 = causa más probable). Las métricas comparan ese ranking con la verdad de referencia (oro o
plata) del incidente.

Uso:
    from src.evaluacion import folds_temporales, enmascarar, rankear, evaluar, comparar
"""
import re
import time

import numpy as np
import pandas as pd

try:
    from .etiquetado import col_tiempo, COL_EQUIPO, COL_SITIO
    from .grafo import grafo_sitios, jerarquia_sitios
except ImportError:
    from etiquetado import col_tiempo, COL_EQUIPO, COL_SITIO
    from grafo import grafo_sitios, jerarquia_sitios

SEED = 42
KS = (1, 3, 5)

# Columnas que delatarían la respuesta o que no existen al momento del diagnóstico.
PATRON_FUGA = re.compile(r"(^ack|acknowledg|comment|^root|rootalarm|^rca|correlat|clear(ed)?(utc|time)|"
                         r"^cleared_by|operator|handled)", re.I)


# --------------------------------------------------------------------------------------------- particiones
def folds_temporales(ventanas, n_folds=4, min_entrenamiento=0.4, separacion=1, pesos=None):
    """Validación con origen móvil (rolling origin) sobre ventanas ordenadas en el tiempo.

    El primer `min_entrenamiento` de las ventanas solo entrena; el resto se divide en `n_folds` bloques
    consecutivos de prueba. En cada fold se entrena con TODO lo anterior al bloque (ventana creciente) y se
    deja `separacion` ventanas de margen para que una falla larga no quede a ambos lados del corte.
    `ventanas`: DataFrame con ventana_id y ventana_ts (o la serie de ventana_ts indexada por ventana_id).
    `pesos` (opcional): dict/Series ventana_id -> nº de incidentes. Si se da, los cortes se hacen para que cada
    bloque de prueba tenga aproximadamente la MISMA cantidad de incidentes (y el entrenamiento inicial, la fracción
    `min_entrenamiento` de los incidentes), en vez de la misma cantidad de ventanas. Sigue siendo temporal.
    Devuelve lista de dicts {fold, train, test, desde, hasta} con listas de ventana_id.
    """
    v = (ventanas[["ventana_id", "ventana_ts"]].drop_duplicates().sort_values("ventana_ts")
         if isinstance(ventanas, pd.DataFrame) else ventanas.sort_values().reset_index())
    v.columns = ["ventana_id", "ventana_ts"]
    ids = v["ventana_id"].tolist()
    if pesos is not None:
        w = np.array([float(pd.Series(pesos).get(i, 0)) for i in ids])
        acum = np.cumsum(w) / max(w.sum(), 1e-9)
        n0 = int(np.searchsorted(acum, min_entrenamiento, side="right"))
        resto = acum[n0:] - (acum[n0 - 1] if n0 else 0)
        resto = resto / max(resto[-1], 1e-9) if len(resto) else resto
        etiqueta = np.minimum((resto * n_folds - 1e-9).astype(int), n_folds - 1) if len(resto) else []
        bloques = [np.arange(n0, len(ids))[np.asarray(etiqueta) == k] for k in range(n_folds)]
    else:
        n0 = int(len(ids) * min_entrenamiento)
        bloques = np.array_split(np.arange(n0, len(ids)), n_folds)
    folds = []
    for k, b in enumerate(bloques, 1):
        if not len(b):
            continue
        fin_train = max(b[0] - separacion, 0)
        folds.append({"fold": k, "train": ids[:fin_train], "test": [ids[i] for i in b],
                      "desde": v["ventana_ts"].iloc[b[0]], "hasta": v["ventana_ts"].iloc[b[-1]]})
    return folds


def tabla_folds(folds):
    return pd.DataFrame([{"fold": f["fold"], "ventanas_train": len(f["train"]), "ventanas_test": len(f["test"]),
                          "test_desde": f["desde"], "test_hasta": f["hasta"]} for f in folds])


def enmascarar(df, extra=()):
    """Quita columnas que filtran la respuesta (reconocimiento del operador, comentarios, banderas de alarma
    raíz/correlación del propio gestor, hora de cierre). Devuelve (df_sin_fuga, columnas_quitadas)."""
    quitar = [c for c in df.columns if PATRON_FUGA.search(str(c))] + [c for c in extra if c in df.columns]
    return df.drop(columns=quitar), quitar


# --------------------------------------------------------------------------------------------- métodos de referencia
def _ranking(g, claves, nombre):
    r = (g.sort_values(claves).drop_duplicates(COL_EQUIPO)[["incidente_id", COL_EQUIPO, "alarmName"]]
         .rename(columns={COL_EQUIPO: "equipo", "alarmName": "alarma"}))
    r["rank"] = np.arange(1, len(r) + 1)
    r["metodo"] = nombre
    return r


def rankear(df_inc, metodo="temporal", G=None):
    """Métodos de referencia (sin aprendizaje) que ordenan los equipos de cada incidente:
      temporal    : primero el equipo cuya alarma ocurrió antes (lo que hace un operador al ordenar por hora).
      severidad   : primero la alarma más severa; desempata por hora.
      topologico  : primero el equipo más cercano al núcleo (aguas arriba); desempata por hora. Requiere G.
    Devuelve: incidente_id, equipo, alarma, rank, metodo, seg (tiempo de cómputo del incidente)."""
    tcol = col_tiempo(df_inc)
    d = df_inc.copy()
    if metodo == "topologico":
        if G is None:
            raise ValueError("El método topológico necesita el grafo G.")
        J = jerarquia_sitios(G, grafo_sitios(G))
        d["_saltos"] = d[COL_SITIO].map(dict(zip(J["sitio"], J["saltos_al_nucleo"]))).fillna(999)
    claves = {"temporal": [tcol], "severidad": ["severity", tcol], "topologico": ["_saltos", tcol]}[metodo]
    salida = []
    for _, g in d.groupby("incidente_id"):
        t = time.perf_counter()
        r = _ranking(g, claves, metodo)
        r["seg"] = time.perf_counter() - t
        salida.append(r)
    return pd.concat(salida, ignore_index=True)


# --------------------------------------------------------------------------------------------- métricas
def evaluar(ranking, verdad, ks=KS, por="equipo"):
    """Compara un ranking con la verdad (incidente_id + causa_raiz_equipo [+ causa_raiz_alarma]).

    por="equipo": acierto si el equipo coincide; por="equipo_alarma": equipo y alarma deben coincidir.
    Devuelve (resumen dict, detalle por incidente). Métricas:
      top_k            : % de incidentes con la causa entre los k primeros.
      mrr              : media de 1/posición (0 si no aparece).
      precision/recall/f1 (sobre la 1ª propuesta): precision = aciertos/incidentes con propuesta;
                         recall = aciertos/incidentes con verdad.
      equipos_revisados: posición media de la causa = cuántos equipos revisa el operador hasta dar con ella
                         (proxy del tiempo de localización; sin causa en la lista = nº de equipos + 1).
      seg_por_incidente: tiempo de cómputo medio del método.
    """
    v = verdad[["incidente_id", "causa_raiz_equipo"] +
               (["causa_raiz_alarma"] if por == "equipo_alarma" else [])].copy()
    r = ranking
    m = r.merge(v, on="incidente_id", how="inner")
    ok = m["equipo"].astype(str).str.strip() == m["causa_raiz_equipo"].astype(str).str.strip()
    if por == "equipo_alarma":
        ok &= m["alarma"].astype(str).str.strip() == m["causa_raiz_alarma"].astype(str).str.strip()
    m["acierto"] = ok
    pos = m[m["acierto"]].groupby("incidente_id")["rank"].min()
    n_cand = r.groupby("incidente_id")["rank"].max()
    det = v[["incidente_id"]].copy()
    det["pos"] = det["incidente_id"].map(pos)
    det["n_candidatos"] = det["incidente_id"].map(n_cand)
    det["con_propuesta"] = det["n_candidatos"].notna()
    n = len(det)
    aciertos1 = int((det["pos"] == 1).sum())
    prec = aciertos1 / max(int(det["con_propuesta"].sum()), 1)
    rec = aciertos1 / max(n, 1)
    res = {"incidentes": n, **{f"top_{k}": round(float((det["pos"] <= k).mean()), 3) for k in ks},
           "mrr": round(float((1 / det["pos"]).fillna(0).mean()), 3),
           "precision": round(prec, 3), "recall": round(rec, 3),
           "f1": round(2 * prec * rec / (prec + rec), 3) if prec + rec else 0.0,
           "equipos_revisados": round(float(det["pos"].fillna(det["n_candidatos"].fillna(0) + 1).mean()), 2)}
    if "seg" in ranking:
        res["seg_por_incidente"] = round(float(ranking.groupby("incidente_id")["seg"].first().mean()), 5)
    return res, det


def intervalo_bootstrap(det, k=1, n=1000, semilla=SEED, nivel=0.95):
    """IC por bootstrap del Top-k (remuestrea incidentes)."""
    x = (det["pos"] <= k).astype(float).to_numpy()
    if not len(x):
        return (np.nan, np.nan)
    rng = np.random.default_rng(semilla)
    medias = rng.choice(x, size=(n, len(x)), replace=True).mean(axis=1)
    a = (1 - nivel) / 2
    return round(float(np.quantile(medias, a)), 3), round(float(np.quantile(medias, 1 - a)), 3)


def mcnemar(det_a, det_b, k=1):
    """Prueba de McNemar exacta (pareada por incidente) entre dos métodos: ¿difieren sus aciertos Top-k?
    Devuelve (b = solo acierta A, c = solo acierta B, p_valor bilateral)."""
    from math import comb
    m = det_a[["incidente_id", "pos"]].merge(det_b[["incidente_id", "pos"]], on="incidente_id", suffixes=("_a", "_b"))
    a, b_ = (m["pos_a"] <= k), (m["pos_b"] <= k)
    b, c = int((a & ~b_).sum()), int((~a & b_).sum())
    n = b + c
    if n == 0:
        return b, c, 1.0
    p = sum(comb(n, i) for i in range(0, min(b, c) + 1)) / 2 ** n * 2
    return b, c, round(min(p, 1.0), 4)


def comparar(rankings, verdad, ks=KS, por="equipo"):
    """Tabla comparativa: una fila por método, con IC 95 % del Top-1."""
    filas = []
    for nombre, rk in rankings.items():
        res, det = evaluar(rk, verdad, ks=ks, por=por)
        lo, hi = intervalo_bootstrap(det, k=1)
        filas.append({"metodo": nombre, **res, "top_1_ic95": f"[{lo:.2f}, {hi:.2f}]"})
    return pd.DataFrame(filas)


def evaluar_por_fold(df_inc, incidentes, verdad, folds, metodos, G=None, por="equipo"):
    """Aplica cada método a los incidentes de PRUEBA de cada fold (los métodos de referencia no entrenan;
    los que aprenden usarán `train`). Devuelve una fila por (fold, método)."""
    filas = []
    for f in folds:
        ids = set(incidentes.loc[incidentes["ventana_id"].isin(f["test"]), "incidente_id"])
        sub = df_inc[df_inc["incidente_id"].isin(ids)]
        vf = verdad[verdad["incidente_id"].isin(ids)]
        if sub.empty or vf.empty:
            continue
        for m in metodos:
            res, _ = evaluar(rankear(sub, m, G=G), vf, por=por)
            filas.append({"fold": f["fold"], "metodo": m, **res})
    return pd.DataFrame(filas)


def verdad_desde_simulacion(df_inc, verdad_sim):
    """Pasa la verdad por ventana del simulador a verdad por incidente: el incidente que contiene al equipo raíz."""
    m = df_inc.merge(verdad_sim, on="ventana_id")
    m = m[m[COL_EQUIPO] == m["causa_raiz_equipo"]]
    return m[["incidente_id", "causa_raiz_equipo", "causa_raiz_alarma"]].drop_duplicates("incidente_id")
