# -*- coding: utf-8 -*-
"""
Extracción de reglas causa→efecto desde los manuales de alarmas Huawei (etiqueta «plata»).

Flujo (semi-automático, con revisión humana obligatoria):
  1. extraer_manual(pdf)        -> catálogo de alarmas + pares CANDIDATOS (alarma mencionada dentro de la ficha de
                                   otra), con la frase del manual y la página como evidencia, y una propuesta.
  2. planilla_revision(...)     -> Excel para que el tesista/NOC marque cada par: aceptar / corregir / descartar.
  3. revision_a_reglas(...)     -> pasa SOLO los pares aceptados a docs/reglas_causales_huawei.csv, con versión.

Probado con la «OptiX RTN 320&320F Maintenance Guide» (anexo A, «Alarms and Handling Procedures»). Otros manuales
(ATN 910C, NE40E) pueden tener otro formato de ficha: revisar `PATRON_FICHA` y las secciones antes de usarlos.
Requiere pypdf.
"""
import os
import re
from datetime import date

import pandas as pd

PATRON_FICHA = re.compile(r"^A\.3\.\d+\s+([A-Z][A-Z0-9_]+)\s*$")      # «A.3.132 MW_LOF»
SECCIONES = ("Description", "Attribute", "Parameters", "Impact on the System", "Possible Causes", "Procedure",
             "Related Information")
CLAVES_CAUSA = re.compile(r"(other alarms?|following alarms?|clear (?:them|the alarms?|it) first|"
                          r"equipment-related alarms|handle the .*alarms|check whether .*alarm|alarms? may be reported|"
                          r"check for the|is triggered by)", re.I)
CONDICIONAL = re.compile(r"\bif no\b|\bif the .* alarm is not\b|\bwithout\b", re.I)
REMOTO = re.compile(r"\b(peer|opposite|remote|far[- ]end)\b", re.I)
LOCAL = re.compile(r"\blocal\b", re.I)


def _lineas_pdf(ruta_pdf):
    """Lista de (página impresa, línea) del PDF."""
    from pypdf import PdfReader
    r = PdfReader(ruta_pdf)
    etiquetas = list(r.page_labels) if getattr(r, "page_labels", None) else [str(i + 1) for i in range(len(r.pages))]
    out = []
    for i, p in enumerate(r.pages):
        for l in (p.extract_text() or "").split("\n"):
            l = l.strip()
            if l and "Copyright" not in l:
                out.append((etiquetas[i], l))
    return out


def extraer_manual(ruta_pdf, nombre_manual, modelos):
    """Lee el anexo de alarmas del manual.

    Devuelve (catalogo, candidatos):
      catalogo   : alarma, descripcion, severidad, tipo_alarma, modelos, manual, pagina
      candidatos : alarma_causa, alarma_efecto, alcance (mismo_equipo | extremo_remoto | sin_dato), seccion,
                   paso (texto de la causa del manual), evidencia (frase), pagina, propuesta, confianza_sugerida, nota
    """
    lineas = _lineas_pdf(ruta_pdf)
    inicios = [(k, PATRON_FICHA.match(l).group(1)) for k, (_, l) in enumerate(lineas) if PATRON_FICHA.match(l)]
    # el índice del manual repite los títulos: nos quedamos con la ÚLTIMA aparición de cada ficha (el cuerpo)
    ultimo = {}
    for k, nombre in inicios:
        ultimo[nombre] = k
    fichas = sorted((k, n) for n, k in ultimo.items())
    nombres = {n for _, n in fichas}
    patron_alarmas = re.compile(r"\b(" + "|".join(sorted(nombres, key=len, reverse=True)) + r")\b")

    catalogo, candidatos = [], []
    for idx, (k, efecto) in enumerate(fichas):
        fin = fichas[idx + 1][0] if idx + 1 < len(fichas) else len(lineas)
        bloque = lineas[k + 1:fin]
        seccion, paso, alcance_ctx, ctx_causa = None, "", None, False
        desc, sev, tipo = "", None, None
        for j, (pag, l) in enumerate(bloque):
            if l in SECCIONES or any(l.startswith(s + " ") and len(l) < len(s) + 3 for s in SECCIONES):
                seccion = l.strip()
                continue
            if seccion == "Description" and not desc:
                desc = l
            if seccion == "Attribute" and sev is None:
                m = re.match(r"(Critical|Major|Minor|Warning)\s+(.*)", l)
                if m:
                    sev, tipo = m.group(1), m.group(2).strip()
            if seccion not in ("Possible Causes", "Procedure", "Related Information"):
                continue
            m = re.match(r"^Step \d+\s+(.*)", l)
            if m:
                paso, alcance_ctx, ctx_causa = m.group(1), None, bool(CLAVES_CAUSA.search(m.group(1)))
            if REMOTO.search(l):
                alcance_ctx = "extremo_remoto"
            elif LOCAL.search(l) and alcance_ctx is None:
                alcance_ctx = "mismo_equipo"
            if CLAVES_CAUSA.search(l):
                ctx_causa = True
            for causa in patron_alarmas.findall(l):
                if causa == efecto:
                    continue
                if seccion == "Related Information":
                    prop, conf, nota = "revisar", "", "Mencionada en «Related Information»"
                elif CONDICIONAL.search(l):
                    prop, conf, nota = "revisar", "", "Mención condicional («if no …»): puede ser relación inversa"
                elif ctx_causa:
                    prop, conf, nota = "puedeCausar", "alta", "El manual pide revisar/despejar esta alarma primero"
                else:
                    prop, conf, nota = "revisar", "", "Mencionada en el procedimiento sin indicar causalidad"
                candidatos.append({"alarma_causa": causa, "alarma_efecto": efecto,
                                   "alcance": alcance_ctx or "sin_dato", "seccion": seccion, "paso": paso[:160],
                                   "evidencia": l[:220], "pagina": pag, "propuesta": prop,
                                   "confianza_sugerida": conf, "nota": nota})
        catalogo.append({"alarma": efecto, "descripcion": desc, "severidad": sev, "tipo_alarma": tipo,
                         "modelos": modelos, "manual": nombre_manual, "pagina": lineas[k][0]})
    cat = pd.DataFrame(catalogo)
    cand = pd.DataFrame(candidatos)
    if len(cand):
        # un par puede aparecer varias veces (local y remoto, o en varios pasos): se consolida
        orden = {"puedeCausar": 0, "revisar": 1}
        cand = (cand.assign(_o=cand["propuesta"].map(orden))
                    .sort_values(["alarma_efecto", "alarma_causa", "_o"])
                    .groupby(["alarma_causa", "alarma_efecto"], as_index=False)
                    .agg(alcance=("alcance", lambda s: "|".join(sorted(set(s)))),
                         propuesta=("propuesta", "first"), confianza_sugerida=("confianza_sugerida", "first"),
                         nota=("nota", "first"), paso=("paso", "first"), evidencia=("evidencia", "first"),
                         pagina=("pagina", "first"), menciones=("evidencia", "size")))
        cand["fuente"] = [f"{nombre_manual} · A.3 {e} · p. {p}" for e, p in zip(cand["alarma_efecto"], cand["pagina"])]
    return cat, cand


def planilla_revision(candidatos, catalogo, ruta_xlsx):
    """Excel de revisión: una fila por par candidato. Se completa la columna `decision` (aceptar / descartar /
    invertir / sinonimo) y, si hace falta, `alcance_final` y `comentario`."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.worksheet.datavalidation import DataValidation
    F, cab, amarillo = "Arial", PatternFill("solid", fgColor="0B5D8F"), PatternFill("solid", fgColor="FFF2CC")
    wb = Workbook()
    ws = wb.active; ws.title = "Instrucciones"
    texto = [
        ("Revisión de reglas causa→efecto extraídas del manual (etiqueta plata)", True),
        ("", False),
        ("Cada fila es un par «alarma_causa → alarma_efecto» encontrado en la ficha de la alarma efecto.", False),
        ("La columna «propuesta» es una sugerencia automática; la decisión es humana.", False),
        ("Completar «decision» en la hoja Candidatos:", True),
        ("  aceptar   = la causa provoca el efecto (se carga como puedeCausar).", False),
        ("  invertir  = la relación existe pero al revés (efecto → causa).", False),
        ("  sinonimo  = mismo evento con otro nombre (esSinonimoDe).", False),
        ("  descartar = no es una relación causal (solo se menciona).", False),
        ("«alcance_final»: mismo_equipo | extremo_remoto | aguas_abajo | vecindad (si se deja vacío se usa «alcance»).", False),
        ("«confianza»: alta (el manual lo afirma) | media (lo sugiere) | baja (criterio NOC).", False),
        ("Tip: filtrar primero propuesta = puedeCausar (revisión rápida) y luego propuesta = revisar.", False),
    ]
    for i, (t, b) in enumerate(texto, 1):
        ws.cell(row=i, column=1, value=t).font = Font(name=F, bold=b, size=12 if i == 1 else 10)
    ws.column_dimensions["A"].width = 120

    ws = wb.create_sheet("Candidatos")
    cols = ["alarma_causa", "alarma_efecto", "propuesta", "alcance", "evidencia", "paso", "nota", "pagina",
            "menciones", "fuente"]
    llenar = ["decision", "alcance_final", "confianza", "comentario"]
    ws.append(cols + llenar)
    desc = dict(zip(catalogo["alarma"], catalogo["descripcion"]))
    for _, r in candidatos.sort_values(["propuesta", "alarma_efecto", "alarma_causa"]).iterrows():
        ws.append([r[c] for c in cols] + [None, None, r.get("confianza_sugerida") or None, None])
    anchos = [20, 20, 13, 16, 60, 45, 34, 8, 10, 40, 12, 15, 11, 30]
    for j, w in enumerate(anchos, 1):
        h = ws.cell(row=1, column=j)
        h.font = Font(name=F, bold=True, color="FFFFFF"); h.fill = cab
        ws.column_dimensions[h.column_letter].width = w
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.font = Font(name=F, size=10); c.alignment = Alignment(wrap_text=True, vertical="top")
        for c in row[len(cols):]:
            c.fill = amarillo
    # descripción de las alarmas como comentario de ayuda en la celda
    from openpyxl.comments import Comment
    for row in ws.iter_rows(min_row=2, max_col=2):
        for c in row:
            if c.value in desc and desc[c.value]:
                c.comment = Comment(desc[c.value][:300], "manual")
    n = ws.max_row
    letra = {nombre: ws.cell(row=1, column=j).column_letter for j, nombre in enumerate(cols + llenar, 1)}
    for campo, ops in (("decision", '"aceptar,invertir,sinonimo,descartar"'),
                       ("alcance_final", '"mismo_equipo,extremo_remoto,aguas_abajo,vecindad"'),
                       ("confianza", '"alta,media,baja"')):
        dv = DataValidation(type="list", formula1=ops, allow_blank=True); ws.add_data_validation(dv)
        dv.add(f"{letra[campo]}2:{letra[campo]}{max(n, 2)}")
    ws.freeze_panes = "C2"; ws.auto_filter.ref = ws.dimensions

    ws = wb.create_sheet("Catalogo")
    ws.append(list(catalogo.columns))
    for r in catalogo.itertuples(index=False):
        ws.append(list(r))
    for j in range(1, len(catalogo.columns) + 1):
        h = ws.cell(row=1, column=j); h.font = Font(name=F, bold=True, color="FFFFFF"); h.fill = cab
        ws.column_dimensions[h.column_letter].width = 22 if j != 2 else 60
    ws.freeze_panes = "A2"; ws.auto_filter.ref = ws.dimensions
    os.makedirs(os.path.dirname(os.path.abspath(ruta_xlsx)), exist_ok=True)
    wb.save(ruta_xlsx)
    return ruta_xlsx


def revision_a_reglas(ruta_xlsx, ruta_csv, version, reemplazar_version=True):
    """Pasa los pares revisados (decision = aceptar / invertir / sinonimo) al CSV de reglas, con su `version`
    (p. ej. «v1-RTN320»). Si reemplazar_version, primero borra las filas de esa misma versión. Devuelve el
    DataFrame de reglas resultante."""
    rev = pd.read_excel(ruta_xlsx, sheet_name="Candidatos")
    rev["decision"] = rev["decision"].astype(str).str.strip().str.lower()
    ok = rev[rev["decision"].isin(["aceptar", "invertir", "sinonimo"])].copy()
    inv = ok["decision"] == "invertir"
    ok.loc[inv, ["alarma_causa", "alarma_efecto"]] = ok.loc[inv, ["alarma_efecto", "alarma_causa"]].values
    nuevas = pd.DataFrame({
        "alarma_causa": ok["alarma_causa"], "alarma_efecto": ok["alarma_efecto"],
        "relacion": ["esSinonimoDe" if d == "sinonimo" else "puedeCausar" for d in ok["decision"]],
        "alcance": ok["alcance_final"].fillna(ok["alcance"]),
        "fuente": ok["fuente"], "confianza": ok["confianza"].fillna("alta"),
        "nota": ok["comentario"].fillna(""), "version": version,
        "fecha_carga": date.today().isoformat()})
    with open(ruta_csv, encoding="utf-8") as f:
        comentarios = [l for l in f if l.startswith("#")]
    try:
        actuales = pd.read_csv(ruta_csv, comment="#")
    except pd.errors.EmptyDataError:
        actuales = pd.DataFrame(columns=nuevas.columns)
    for c in ("version", "fecha_carga"):
        if c not in actuales.columns:
            actuales[c] = ""
    if reemplazar_version:
        actuales = actuales[actuales["version"].astype(str) != version]
    reglas = pd.concat([actuales, nuevas], ignore_index=True)[list(nuevas.columns)]
    with open(ruta_csv, "w", encoding="utf-8", newline="") as f:
        f.writelines(comentarios)
        reglas.to_csv(f, index=False)
    return reglas


def cobertura_alarmas(df, catalogos=(), reglas=None, col="alarmName"):
    """Prioridad de revisión de manuales: tipos de alarma ordenados por volumen, con % acumulado, si están en algún
    catálogo de manual y si ya tienen regla (como causa o efecto)."""
    vc = df[col].astype(str).value_counts()
    t = vc.rename("alarmas").to_frame()
    t["pct"] = (t["alarmas"] / t["alarmas"].sum()).round(4)
    t["pct_acumulado"] = t["pct"].cumsum().round(4)
    en_cat = set()
    for c in catalogos:
        en_cat |= set(c["nombre_nce"] if "nombre_nce" in c else c["alarma"]) | set(c["alarma"])
    t["en_manual"] = t.index.isin(en_cat)
    if reglas is not None and len(reglas):
        con = set(reglas["alarma_causa"]) | set(reglas["alarma_efecto"])
        t["con_regla"] = t.index.isin(con)
    else:
        t["con_regla"] = False
    if "rol_equipo" in df.columns:
        rol = df.groupby(col)["rol_equipo"].agg(lambda s: ", ".join(sorted(s.dropna().astype(str).unique())))
        t["roles"] = t.index.map(rol)
    return t.reset_index().rename(columns={"index": col})


if __name__ == "__main__":
    import sys
    pdf = sys.argv[1]
    cat, cand = extraer_manual(pdf, "OptiX RTN 320&320F Maintenance Guide (Issue 03)", "RTN 320 / 320F")
    print(len(cat), "alarmas en el catálogo ·", len(cand), "pares candidatos")
    print(cand["propuesta"].value_counts())
