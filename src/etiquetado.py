# -*- coding: utf-8 -*-
"""
Verdad de referencia en dos niveles (Sprint 2 · Protocolo de validación).

- INCIDENTE (unidad de evaluación): equipos con alarmas en la MISMA ventana que están conectados en el grafo
  físico a <= `max_saltos` saltos (router–IDU–enlace–IDU–router = 4). Agrupa la falla completa, no un solo equipo.
- Etiqueta ORO: el NOC etiqueta la causa raíz de una muestra de incidentes (planilla Excel generada aquí).
- Etiqueta PLATA: reglas causa→efecto de los manuales Huawei (docs/reglas_causales_huawei.csv) aplicadas
  automáticamente a todos los incidentes. El oro valida a la plata.

Uso:
    from src.etiquetado import construir_incidentes, seleccionar_muestra_oro, planilla_oro, cargar_reglas, etiqueta_plata
"""
import os
from datetime import datetime

import numpy as np
import pandas as pd
import networkx as nx

try:
    from .grafo import subgrafo_fisico, ubicar_alarmas, grafo_sitios, jerarquia_sitios
except ImportError:
    from grafo import subgrafo_fisico, ubicar_alarmas, grafo_sitios, jerarquia_sitios

SEED = 42
COL_EQUIPO = "NOMBRE DE EQUIPO EN RED"
COL_SITIO = "CODIGO DE NODO"


def col_tiempo(df, preferir=("t_evento", "occurUtc", "firstOccurUtc", "arriveUtc")):
    """Columna de tiempo para ordenar alarmas DENTRO de un incidente. Se prefiere `t_evento` (ver
    `asignar_tiempo_evento`: la marca de tiempo que cae dentro de la ventana); si no existe, la hora de ocurrencia."""
    for c in preferir:
        if c in df.columns and df[c].notna().any():
            return c
    return None


CANDIDATAS_TIEMPO = ("occurUtc", "latestOccurUtc", "arriveUtc", "firstOccurUtc")


def _utc_naive(s):
    s = pd.to_datetime(s, errors="coerce")
    try:
        return s.dt.tz_convert(None) if s.dt.tz is not None else s
    except AttributeError:
        return s


def detectar_desfase_horas(df, col="arriveUtc"):
    """Diferencia (horas) entre la hora del nombre de la carpeta (`ventana_ts`, hora local del scraping) y UTC.
    Se estima con las alarmas nuevas: su llegada al NCE cae justo antes de la descarga, así que
    floor(ventana_ts − llegada) se concentra en el desfase (p. ej. −5 para Lima). Devuelve el valor más frecuente."""
    col = col if col in df.columns and df[col].notna().any() else next(
        (c for c in CANDIDATAS_TIEMPO if c in df.columns and df[c].notna().any()), None)
    if col is None:
        return 0
    d = (pd.to_datetime(df["ventana_ts"]) - _utc_naive(df[col])).dt.total_seconds() / 3600
    d = np.floor(d.dropna())
    d = d[(d >= -14) & (d <= 14)]
    return int(d.mode().iloc[0]) if len(d) else 0


def asignar_tiempo_evento(df, desfase_h=None, margen_min=5):
    """Hora del EVENTO de cada alarma dentro de su ventana.

    El NCE guarda varias marcas (occurUtc, latestOccurUtc, arriveUtc, firstOccurUtc). En una alarma crónica o que
    reaparece, occur/firstOccur pueden ser de hace semanas. Aquí se toma, en ese orden, la primera marca que cae entre
    la descarga anterior y la actual (± margen). Si ninguna cae ahí, la alarma es `reaparecida` (no inicia una falla
    en esta ventana). Las horas de ventana_ts (hora local del nombre de la carpeta) se pasan a UTC con `desfase_h`
    (si es None se detecta). Añade: t_evento (UTC sin zona), fuente_t_evento, reaparecida."""
    out = df.copy()
    if desfase_h is None:
        desfase_h = detectar_desfase_horas(out)
    v = (out[["ventana_id", "ventana_ts"]].drop_duplicates().sort_values("ventana_ts").reset_index(drop=True))
    v["ts_utc"] = pd.to_datetime(v["ventana_ts"]) - pd.Timedelta(hours=desfase_h)
    v["ini"] = v["ts_utc"].shift(1).fillna(v["ts_utc"] - pd.Timedelta(minutes=15))
    lim = v.set_index("ventana_id")
    ini = out["ventana_id"].map(lim["ini"]) - pd.Timedelta(minutes=margen_min)
    fin = out["ventana_id"].map(lim["ts_utc"]) + pd.Timedelta(minutes=margen_min)
    t = pd.Series(pd.NaT, index=out.index, dtype="datetime64[ns]")
    fuente = pd.Series(None, index=out.index, dtype=object)
    for c in CANDIDATAS_TIEMPO:
        if c not in out.columns:
            continue
        x = _utc_naive(out[c]).astype("datetime64[ns]")
        ok = t.isna() & x.notna() & (x >= ini) & (x <= fin)
        t[ok], fuente[ok] = x[ok], c
    out["t_evento"], out["fuente_t_evento"] = t, fuente
    out["reaparecida"] = t.isna()
    out.attrs["desfase_h"] = desfase_h
    return out


# ----------------------------------------------------------------------------------------------- incidentes
COLS_INCIDENTE = ["incidente_id", "ventana_id", "ventana_ts", "n_alarmas", "n_equipos", "n_sitios", "sitios",
                  "equipos", "red", "primeras_alarmas", "tipos_distintos"]


def marcar_nuevas(df, clave=(COL_EQUIPO, "alarmName", "moi")):
    """Añade `nueva`: True si la alarma (equipo, tipo, moi) NO estaba activa en la ventana anterior.

    El NCE entrega una foto de las alarmas ACTIVAS cada 15 min, así que una alarma crónica reaparece en muchas
    ventanas. Para formar incidentes solo cuentan las alarmas que aparecen por primera vez (inicio de la falla);
    así la misma avalancha no se cuenta decenas de veces ni se filtra entre entrenamiento y prueba."""
    clave = [c for c in clave if c in df.columns]
    k = df[clave].astype(str).agg("|".join, axis=1)
    orden = sorted(df["ventana_id"].unique(), key=lambda v: df.loc[df["ventana_id"] == v, "ventana_ts"].iloc[0])
    previa, vistas = {}, set()
    for v in orden:
        previa[v] = vistas
        vistas = set(k[df["ventana_id"] == v])
    out = df.copy()
    out["nueva"] = [kk not in previa[v] for kk, v in zip(k, out["ventana_id"])]
    return out


def construir_incidentes(df, G, max_saltos=4, min_alarmas=3, solo_nuevas=True, excluir_reaparecidas=True,
                         desfase_h=None, verbose=False):
    """Agrupa las alarmas en incidentes.

    solo_nuevas=True: usa solo alarmas que aparecen por primera vez (ver `marcar_nuevas`).
    excluir_reaparecidas=True: descarta las alarmas nuevas cuya hora no cae en la ventana (ver
    `asignar_tiempo_evento`); el orden dentro del incidente usa `t_evento`.
    Devuelve (df_inc, incidentes):
      df_inc     : alarmas que pertenecen a un incidente con >= min_alarmas, con la columna `incidente_id`.
      incidentes : una fila por incidente (ventana, nº de alarmas y equipos, sitios, red, primeras alarmas).
    """
    F = subgrafo_fisico(G)
    if solo_nuevas:
        df = marcar_nuevas(df)
        df = df[df["nueva"]]
    if len(df):
        df = asignar_tiempo_evento(df, desfase_h=desfase_h)
        if verbose:
            print(f"Desfase hora de carpeta vs UTC: {df.attrs['desfase_h']} h · alarmas nuevas: {len(df):,} · "
                  f"reaparecidas (hora fuera de la ventana): {int(df['reaparecida'].sum()):,}")
        if excluir_reaparecidas:
            df = df[~df["reaparecida"]]
    d = ubicar_alarmas(df, G)
    d = d[d["en_grafo"]].copy()
    tcol = col_tiempo(d)
    asignacion = {}
    for vid, g in d.groupby("ventana_id"):
        eqs = list(g["nodo_grafo"].unique())
        padre = {e: e for e in eqs}

        def raiz(x):
            while padre[x] != x:
                padre[x] = padre[padre[x]]
                x = padre[x]
            return x
        presentes = set(eqs)
        for e in eqs:
            if e not in F:
                continue
            for v in nx.single_source_shortest_path_length(F, e, cutoff=max_saltos):
                if v in presentes:
                    a, b = raiz(e), raiz(v)
                    if a != b:
                        padre[a] = b
        grupos = {}
        for e in eqs:
            grupos.setdefault(raiz(e), []).append(e)
        for k, miembros in enumerate(sorted(grupos.values(), key=lambda m: sorted(m)[0])):
            for e in miembros:
                asignacion[(vid, e)] = f"{vid}#{k + 1}"
    d["incidente_id"] = [asignacion[(v, e)] for v, e in zip(d["ventana_id"], d["nodo_grafo"])]
    tam = d.groupby("incidente_id").size()
    d = d[d["incidente_id"].map(tam) >= min_alarmas].copy()
    if tcol:
        d = d.sort_values(["incidente_id", tcol])

    def resumen(g):
        primeras = g["alarmName"].astype(str).head(3).tolist()
        redes = sorted(g["Red"].dropna().astype(str).unique()) if "Red" in g else []
        return pd.Series({
            "ventana_id": g["ventana_id"].iloc[0], "ventana_ts": g["ventana_ts"].iloc[0],
            "n_alarmas": len(g), "n_equipos": g["nodo_grafo"].nunique(), "n_sitios": g[COL_SITIO].nunique(),
            "sitios": ", ".join(sorted(g[COL_SITIO].dropna().astype(str).unique())),
            "equipos": ", ".join(sorted(g[COL_EQUIPO].dropna().astype(str).unique())),
            "red": "+".join(redes) or "—", "primeras_alarmas": " → ".join(primeras),
            "tipos_distintos": g["alarmName"].nunique()})
    if not len(d):
        return d.assign(incidente_id=pd.Series(dtype=str)), pd.DataFrame(columns=COLS_INCIDENTE)
    incidentes = d.groupby("incidente_id").apply(resumen, include_groups=False).reset_index()
    return d, incidentes


# ----------------------------------------------------------------------------------------------- etiqueta ORO
def seleccionar_muestra_oro(incidentes, n_grandes=30, n_aleatorios=30, semilla=SEED, estrato="red",
                            todos_si_menos_de=None):
    """Muestra para el NOC: los `n_grandes` incidentes con más alarmas + `n_aleatorios` al azar, estratificados
    por red (proporcional), sin repetir. Reproducible con `semilla`. Si hay menos de `todos_si_menos_de`
    incidentes, se devuelven TODOS (motivo = «todos»): con pocos incidentes conviene etiquetarlos completos."""
    if todos_si_menos_de and len(incidentes) <= todos_si_menos_de:
        return incidentes.assign(motivo="todos").sort_values("ventana_ts").reset_index(drop=True)
    inc = incidentes.sort_values("n_alarmas", ascending=False)
    grandes = inc.head(n_grandes)
    resto = inc.iloc[n_grandes:]
    rng = np.random.default_rng(semilla)
    if len(resto) and n_aleatorios:
        cuotas = (resto[estrato].value_counts(normalize=True) * n_aleatorios).round().astype(int)
        partes = []
        for valor, n in cuotas.items():
            pool = resto[resto[estrato] == valor]
            if n > 0 and len(pool):
                partes.append(pool.iloc[rng.choice(len(pool), size=min(n, len(pool)), replace=False)])
        aleatorios = pd.concat(partes) if partes else resto.head(0)
    else:
        aleatorios = resto.head(0)
    muestra = pd.concat([grandes.assign(motivo="grande"), aleatorios.assign(motivo="aleatorio")])
    return muestra.sort_values("ventana_ts").reset_index(drop=True)


def _excel(v):
    """Valor apto para Excel: fechas sin zona horaria (se mantienen en UTC) y NaN como celda vacía."""
    if isinstance(v, pd.Timestamp):
        return (v.tz_convert(None) if v.tzinfo else v).floor("s").to_pydatetime()
    try:
        return None if pd.isna(v) else v
    except (TypeError, ValueError):
        return v


def planilla_oro(df_inc, muestra, ruta_xlsx):
    """Genera la planilla Excel para que el NOC etiquete la causa raíz de cada incidente de la muestra."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.worksheet.datavalidation import DataValidation

    F = "Arial"
    cab = PatternFill("solid", fgColor="0B5D8F")
    llenar = PatternFill("solid", fgColor="FFF2CC")
    fino = Side(style="thin", color="D9E2EA")
    borde = Border(left=fino, right=fino, top=fino, bottom=fino)
    wb = Workbook()

    # --- Instrucciones
    ws = wb.active; ws.title = "Instrucciones"
    lineas = [
        ("Etiquetado de causa raíz — muestra de incidentes (verdad de referencia «oro»)", True),
        ("", False),
        ("Qué es un incidente: alarmas de una misma ventana de 15 min en equipos conectados entre sí en la red.", False),
        ("Qué hacer: en la hoja «Incidentes», completar SOLO las columnas en amarillo de cada fila.", False),
        ("Para decidir, usar la hoja «Alarmas» (filtrar por incidente_id): están ordenadas por hora de llegada.", False),
        ("", False),
        ("Columnas a completar:", True),
        ("causa_raiz_equipo: nombre del equipo donde se originó la falla (tal como aparece en la hoja Alarmas).", False),
        ("causa_raiz_alarma: la alarma que mejor representa la causa (alarmName).", False),
        ("confianza: Alta / Media / Baja.", False),
        ("es_falla_real: Sí / No (No = ruido, mantenimiento programado o alarmas sin impacto).", False),
        ("comentario: opcional (p. ej. «corte de fibra», «falla de energía», «causa externa RDNFO»).", False),
        ("etiquetado_por: iniciales de quien etiqueta.", False),
        ("", False),
        ("Ejemplo de fila completada (formato esperado):", True),
        ("causa_raiz_equipo = <NOMBRE DE EQUIPO EN RED> · causa_raiz_alarma = <alarmName de la IDU> · confianza = Alta · "
         "es_falla_real = Sí · comentario = enlace MW caído por lluvia · etiquetado_por = JP", False),
    ]
    for i, (t, negrita) in enumerate(lineas, 1):
        c = ws.cell(row=i, column=1, value=t)
        c.font = Font(name=F, bold=negrita, size=12 if i == 1 else 10)
    ws.column_dimensions["A"].width = 130

    # --- Incidentes
    ws = wb.create_sheet("Incidentes")
    cols_info = ["incidente_id", "motivo", "ventana_ts", "red", "n_alarmas", "n_equipos", "sitios", "equipos",
                 "primeras_alarmas"]
    cols_llenar = ["causa_raiz_equipo", "causa_raiz_alarma", "confianza", "es_falla_real", "comentario",
                   "etiquetado_por"]
    ws.append(cols_info + cols_llenar)
    for _, r in muestra.iterrows():
        fila = [r.get(c) for c in cols_info]
        fila[2] = _excel(pd.Timestamp(fila[2])) if pd.notna(fila[2]) else None
        ws.append(fila + [None] * len(cols_llenar))
    n = ws.max_row
    for j, nombre in enumerate(cols_info + cols_llenar, 1):
        h = ws.cell(row=1, column=j)
        h.font = Font(name=F, bold=True, color="FFFFFF"); h.fill = cab
        h.alignment = Alignment(wrap_text=True, vertical="center")
        for i in range(2, n + 1):
            c = ws.cell(row=i, column=j)
            c.font = Font(name=F, size=10); c.border = borde
            c.alignment = Alignment(wrap_text=True, vertical="top")
            if nombre in cols_llenar:
                c.fill = llenar
            if nombre == "ventana_ts":
                c.number_format = "yyyy-mm-dd hh:mm"
    anchos = {"incidente_id": 26, "motivo": 10, "ventana_ts": 17, "red": 8, "n_alarmas": 9, "n_equipos": 9,
              "sitios": 28, "equipos": 40, "primeras_alarmas": 45, "causa_raiz_equipo": 30, "causa_raiz_alarma": 30,
              "confianza": 11, "es_falla_real": 12, "comentario": 35, "etiquetado_por": 12}
    for j, nombre in enumerate(cols_info + cols_llenar, 1):
        ws.column_dimensions[ws.cell(row=1, column=j).column_letter].width = anchos.get(nombre, 14)
    ws.freeze_panes = "B2"
    letra = {nombre: ws.cell(row=1, column=j).column_letter for j, nombre in enumerate(cols_info + cols_llenar, 1)}
    for campo, opciones in (("confianza", '"Alta,Media,Baja"'), ("es_falla_real", '"Sí,No"')):
        dv = DataValidation(type="list", formula1=opciones, allow_blank=True)
        ws.add_data_validation(dv)
        dv.add(f"{letra[campo]}2:{letra[campo]}{max(n, 2)}")

    # --- Alarmas de los incidentes de la muestra
    ws = wb.create_sheet("Alarmas")
    tcol = col_tiempo(df_inc)
    cols = ["incidente_id", tcol, COL_SITIO, COL_EQUIPO, "rol_equipo", "alarmName", "severidad_txt",
            "probableCause", "moi_puerto"]
    cols = [c for c in cols if c and c in df_inc.columns]
    sub = df_inc[df_inc["incidente_id"].isin(set(muestra["incidente_id"]))][cols]
    ws.append([f"{c} (UTC)" if c == tcol else c for c in cols])
    for fila in sub.itertuples(index=False):
        ws.append([_excel(v) for v in fila])
    for j in range(1, len(cols) + 1):
        h = ws.cell(row=1, column=j)
        h.font = Font(name=F, bold=True, color="FFFFFF"); h.fill = cab
        ws.column_dimensions[h.column_letter].width = 22 if j > 1 else 26
        if cols[j - 1] == tcol:
            for i in range(2, ws.max_row + 1):
                ws.cell(row=i, column=j).number_format = "yyyy-mm-dd hh:mm:ss"
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.font = Font(name=F, size=10)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions

    os.makedirs(os.path.dirname(os.path.abspath(ruta_xlsx)), exist_ok=True)
    wb.save(ruta_xlsx)
    return ruta_xlsx


def cargar_oro(ruta_xlsx, solo_fallas_reales=True):
    """Lee la planilla completada por el NOC. Devuelve las filas con causa raíz informada."""
    oro = pd.read_excel(ruta_xlsx, sheet_name="Incidentes")
    oro = oro[oro["causa_raiz_equipo"].notna() | oro["causa_raiz_alarma"].notna()].copy()
    if solo_fallas_reales and "es_falla_real" in oro:
        oro = oro[oro["es_falla_real"].fillna("Sí").astype(str).str.lower().str.startswith("s")]
    for c in ("causa_raiz_equipo", "causa_raiz_alarma"):
        oro[c] = oro[c].astype(str).str.strip()
    return oro.reset_index(drop=True)


# ----------------------------------------------------------------------------------------------- etiqueta PLATA
def cargar_reglas(ruta_csv):
    """Reglas de los manuales Huawei. Columnas: alarma_causa, alarma_efecto, relacion (puedeCausar | esSinonimoDe),
    alcance, fuente, confianza. Las líneas que empiezan con # son comentarios."""
    r = pd.read_csv(ruta_csv, comment="#", skip_blank_lines=True)
    r.columns = [c.strip() for c in r.columns]
    for c in ("alarma_causa", "alarma_efecto", "relacion"):
        r[c] = r[c].astype(str).str.strip()
    return r


def _canonicos(reglas):
    """Mapa alarma -> representante de su grupo de sinónimos (esSinonimoDe)."""
    S = nx.Graph()
    for _, x in reglas[reglas["relacion"] == "esSinonimoDe"].iterrows():
        S.add_edge(x["alarma_causa"], x["alarma_efecto"])
    return {a: sorted(comp)[0] for comp in nx.connected_components(S) for a in comp}


def etiqueta_plata(df_inc, reglas, G=None):
    """Causa raíz «plata» de cada incidente según las reglas de los manuales.

    En cada incidente: se agrupan los sinónimos, se arma el grafo causa→efecto entre los tipos presentes y la causa
    raíz es el tipo que explica a otros y no es explicado por ninguno. Si hay varios, se desempata por topología
    (sitio más cercano al NOC = más aguas arriba) y luego por tiempo de llegada.
    estado ∈ {unica, desempate, sin_regla}.
    """
    canon = _canonicos(reglas)
    causa = reglas[reglas["relacion"] == "puedeCausar"]
    aristas = {(canon.get(a, a), canon.get(b, b)) for a, b in zip(causa["alarma_causa"], causa["alarma_efecto"])}
    saltos = {}
    if G is not None:
        J = jerarquia_sitios(G, grafo_sitios(G))
        saltos = dict(zip(J["sitio"], J["saltos_al_nucleo"]))
    tcol = col_tiempo(df_inc)
    filas = []
    for iid, g in df_inc.groupby("incidente_id"):
        g = g.assign(_tipo=g["alarmName"].astype(str).map(lambda a: canon.get(a, a)))
        tipos = set(g["_tipo"])
        D = nx.DiGraph(); D.add_nodes_from(tipos)
        D.add_edges_from((a, b) for a, b in aristas if a in tipos and b in tipos and a != b)
        raices = [t for t in D if D.in_degree(t) == 0 and D.out_degree(t) > 0]
        if not raices:
            filas.append({"incidente_id": iid, "plata_estado": "sin_regla", "plata_alarma": None,
                          "plata_equipo": None, "plata_explica": 0})
            continue
        cand = g[g["_tipo"].isin(raices)].assign(
            _saltos=lambda x: x[COL_SITIO].map(saltos).fillna(999) if saltos else 0)
        orden = ["_saltos"] + ([tcol] if tcol else [])
        top = cand.sort_values(orden).iloc[0]
        filas.append({"incidente_id": iid, "plata_estado": "unica" if len(raices) == 1 else "desempate",
                      "plata_alarma": top["alarmName"], "plata_equipo": top[COL_EQUIPO],
                      "plata_explica": len(nx.descendants(D, top["_tipo"]))})
    return pd.DataFrame(filas)


def acuerdo_plata_oro(plata, oro):
    """Valida la plata con el oro: % de incidentes etiquetados por el NOC donde coinciden equipo y alarma."""
    m = oro.merge(plata, on="incidente_id", how="left")
    con_plata = m["plata_estado"].isin(["unica", "desempate"])
    eq = (m["plata_equipo"].astype(str).str.strip() == m["causa_raiz_equipo"]) & con_plata
    al = (m["plata_alarma"].astype(str).str.strip() == m["causa_raiz_alarma"]) & con_plata
    return {"incidentes_oro": int(len(m)), "con_plata": int(con_plata.sum()),
            "acuerdo_equipo": round(float(eq[con_plata].mean()), 3) if con_plata.any() else None,
            "acuerdo_alarma": round(float(al[con_plata].mean()), 3) if con_plata.any() else None}


def kappa_cohen(a, b):
    """Acuerdo entre dos especialistas del NOC sobre los mismos incidentes (etiquetas nominales, p. ej. equipo)."""
    a, b = pd.Series(a).astype(str).reset_index(drop=True), pd.Series(b).astype(str).reset_index(drop=True)
    if not len(a):
        return None
    po = float((a == b).mean())
    cats = set(a) | set(b)
    pe = sum(float((a == c).mean()) * float((b == c).mean()) for c in cats)
    return round((po - pe) / (1 - pe), 3) if pe < 1 else 1.0
