# -*- coding: utf-8 -*-
"""
Pipeline de ingesta y consolidación de alarmas (Sprint 1).

Lee las ventanas de descarga (carpetas DD_MM_AAAA_HH_MM_SS), une el estado de cada
equipo a sus alarmas, apila las 3 redes y anexa el inventario red.xlsx (hoja 'nodos').

Uso:
    from src.ingesta import construir_dataset
    df = construir_dataset("data/raw")
    df.to_parquet("data/processed/dataset_consolidado.parquet", index=False)
"""
import os
import re
import json
from datetime import datetime
import pandas as pd

ESTADO_COL = "Communication Status"
COLS_RED = ["CODIGO DE NODO", "NOMBRE DE NODO", "TIPO DE NODO", "CODIGO_Enlace",
            "EQUIPO", "TIPO", "NOMBRE DE EQUIPO EN RED", "IP_Equipo"]
COLS_RED_OPC = ["Red"]          # opcional: agregada en la versión de red.xlsx con topología
PATRON_VENTANA = re.compile(r"^\d{2}_\d{2}_\d{4}_\d{2}_\d{2}_\d{2}$")
PATRON_IP = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")
MAPA_SEV = {1: "Crítica", 2: "Mayor", 3: "Menor", 4: "Advertencia"}

# Rol funcional de cada equipo según la columna TIPO de red.xlsx (hoja 'nodos')
ROL_POR_TIPO = {
    "RTN510_AP": "sectorial",                       # radio multipunto hacia clientes finales
    "1+0": "microondas", "1+0 XPIC": "microondas", "2+0 XPIC": "microondas",
    "ATN910C": "router_acceso",
    "NE40E-X8A(V8)": "router_agregacion_acceso",    # NOC Acceso
    "NE40E-M2K": "router_transporte", "NE40E-X3(V8)": "router_transporte",
    "OptiX OSN 1800 II TP": "optico_transporte",
}


def rol_equipo(tipo):
    """Devuelve el rol funcional del equipo a partir de su TIPO (ver ROL_POR_TIPO)."""
    t = str(tipo).strip()
    if t in ROL_POR_TIPO:
        return ROL_POR_TIPO[t]
    if t.startswith("NE40E"):
        return "router_transporte"
    if t.startswith("OptiX"):
        return "optico_transporte"
    return "otro"


def leer_json(path):
    """Lee un .json con una lista de objetos. DataFrame vacío si no existe o está mal formado."""
    if not os.path.exists(path):
        return pd.DataFrame()
    with open(path, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError:
            return pd.DataFrame()
    if isinstance(data, dict):
        data = [data]
    return pd.DataFrame(data)


def _norm_ip(serie):
    return serie.astype(str).str.strip()


def parse_ventana_ts(nombre_carpeta):
    try:
        return datetime.strptime(nombre_carpeta, "%d_%m_%Y_%H_%M_%S")
    except ValueError:
        return pd.NaT


def unir_estado(df_alarmas, df_estado, key_estado):
    """Añade el estado del equipo (estado_equipo) a las alarmas, uniendo por IP."""
    if df_alarmas.empty:
        return df_alarmas
    if df_estado.empty:
        out = df_alarmas.copy()
        out["estado_equipo"] = pd.NA
        return out
    est = df_estado.copy()
    est[key_estado] = _norm_ip(est[key_estado])
    ren = {key_estado: "address", ESTADO_COL: "estado_equipo"}
    cols = ["address", "estado_equipo"]
    if "NE Name" in est.columns:
        ren["NE Name"] = "equipo_ne_name"; cols.append("equipo_ne_name")
    if "Subrack Type" in est.columns:
        ren["Subrack Type"] = "equipo_subrack"; cols.append("equipo_subrack")
    est = est.rename(columns=ren)[cols].drop_duplicates("address")
    out = df_alarmas.copy()
    out["address"] = _norm_ip(out["address"])
    return out.merge(est, on="address", how="left")


def cargar_inventario(ruta_xlsx, hoja="nodos", verbose=True):
    """Lee el inventario (hoja 'nodos') y añade `rol_equipo`.

    - La columna `Red` (tx/ax) es opcional: si no existe, queda vacía.
    - Excluye del cruce los equipos con IP no válida (p. ej. 'PENDIENTE') e informa cuáles.
    - Informa los equipos sin CODIGO DE NODO (no se descartan, pero no podrán ubicarse en el grafo).
    """
    red = pd.read_excel(ruta_xlsx, sheet_name=hoja)
    red.columns = [str(c).strip() for c in red.columns]
    faltan = [c for c in COLS_RED if c not in red.columns]
    if faltan:
        raise KeyError(f"Faltan columnas en red.xlsx: {faltan}. Disponibles: {list(red.columns)}")
    red = red[COLS_RED + [c for c in COLS_RED_OPC if c in red.columns]].copy()
    for c in COLS_RED_OPC:
        if c not in red.columns:
            red[c] = pd.NA
    red["IP_Equipo"] = _norm_ip(red["IP_Equipo"])
    ip_ok = red["IP_Equipo"].str.match(PATRON_IP)
    if verbose and (~ip_ok).any():
        print(f"Inventario: {int((~ip_ok).sum())} equipo(s) con IP no válida, excluidos del cruce: "
              f"{red.loc[~ip_ok, 'NOMBRE DE EQUIPO EN RED'].tolist()}")
    red = red[ip_ok].copy()
    sin_cod = red["CODIGO DE NODO"].isna()
    if verbose and sin_cod.any():
        print(f"Inventario: {int(sin_cod.sum())} equipo(s) sin CODIGO DE NODO: "
              f"{red.loc[sin_cod, 'NOMBRE DE EQUIPO EN RED'].tolist()}")
    red["rol_equipo"] = red["TIPO"].map(rol_equipo)
    return red.drop_duplicates("IP_Equipo")


def procesar_ventana(carpeta, red_inv):
    """Procesa una carpeta-ventana y devuelve sus alarmas consolidadas."""
    nombre = os.path.basename(carpeta.rstrip("/"))
    est_tx = leer_json(os.path.join(carpeta, "Tx_Pasco.json"))
    est_ax = leer_json(os.path.join(carpeta, "routers_ATN910_pasco.json"))
    est_rtn = leer_json(os.path.join(carpeta, "RTN_pasco.json"))
    al_tx = unir_estado(leer_json(os.path.join(carpeta, "alarmas_tx_pasco.json")), est_tx, "NE IP Address")
    al_ax = unir_estado(leer_json(os.path.join(carpeta, "alarmas_ax_pasco.json")), est_ax, "NE IP Address")
    al_rtn = unir_estado(leer_json(os.path.join(carpeta, "alarmas_RTN_pasco.json")), est_rtn, "address")
    for df, seg in [(al_tx, "Transporte"), (al_ax, "Acceso · Router"), (al_rtn, "Acceso · Radio")]:
        if not df.empty:
            df["segmento_red"] = seg
    alarmas = pd.concat([al_tx, al_ax, al_rtn], ignore_index=True)
    if alarmas.empty:
        return alarmas
    alarmas = alarmas.merge(red_inv, left_on="address", right_on="IP_Equipo", how="left")
    # marca si el equipo de la alarma figura en el inventario red.xlsx
    alarmas["en_inventario"] = alarmas["IP_Equipo"].notna()
    alarmas.insert(0, "ventana_id", nombre)
    alarmas.insert(1, "ventana_ts", parse_ventana_ts(nombre))
    frente = ["ventana_id", "ventana_ts"] + COLS_RED + COLS_RED_OPC + ["rol_equipo"]
    frente = [c for c in frente if c in alarmas.columns]
    resto = [c for c in alarmas.columns if c not in frente]
    return alarmas[frente + resto]


def construir_dataset(base_dir, hoja_red="nodos", tipar=True, solo_inventario=True,
                      excluir_sectoriales=True, devolver_sectoriales=False):
    """Procesa TODAS las ventanas de base_dir y devuelve el DataFrame consolidado.

    solo_inventario=True (por defecto) descarta las alarmas cuyo equipo NO está en red.xlsx
    (p. ej. antenas de usuario final del NCE de RTN, o alarmas de gestión OSS), que no afectan
    la conectividad del nodo. Usa solo_inventario=False para auditar qué se descartaría.

    excluir_sectoriales=True (por defecto) separa las alarmas de los equipos sectoriales
    (RTN510_AP): no afectan la conectividad del nodo, así que no entran al diagnóstico de causa
    raíz; se analizan aparte como indicador de salud de clientes (ver `indicador_sectoriales`).
    Con devolver_sectoriales=True se devuelve la tupla (df_causa_raiz, df_sectoriales).
    """
    red_inv = cargar_inventario(os.path.join(base_dir, "red.xlsx"), hoja=hoja_red)
    ventanas = sorted(os.path.join(base_dir, d) for d in os.listdir(base_dir)
                      if os.path.isdir(os.path.join(base_dir, d)) and PATRON_VENTANA.match(d))
    frames = []
    for v in ventanas:
        try:
            dfv = procesar_ventana(v, red_inv)
            if not dfv.empty:
                frames.append(dfv)
        except Exception as e:
            print("Error en ventana", os.path.basename(v), "->", e)
    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if tipar and not df.empty:
        for col in ["arriveUtc", "firstOccurUtc", "occurUtc", "latestOccurUtc"]:
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], errors="coerce")
        if "severity" in df.columns:
            df["severidad_txt"] = df["severity"].map(MAPA_SEV).fillna("Desconocida")
        if "cleared" in df.columns:
            df["activa"] = (df["cleared"] == 0)
    if solo_inventario and not df.empty and "en_inventario" in df.columns:
        fuera = int((~df["en_inventario"]).sum())
        print(f"Filtro inventario: se descartan {fuera:,} de {len(df):,} alarmas "
              f"cuyo equipo no está en red.xlsx ({fuera/max(len(df),1):.1%}).")
        df = df[df["en_inventario"]].copy()
    df_sect = pd.DataFrame()
    if excluir_sectoriales and not df.empty and "rol_equipo" in df.columns:
        es_sect = df["rol_equipo"].eq("sectorial")
        df_sect = df[es_sect].copy()
        df = df[~es_sect].copy()
        n_eq = df_sect["address"].nunique() if len(df_sect) else 0
        print(f"Sectoriales (RTN510_AP): {len(df_sect):,} alarmas de {n_eq} equipos separadas del "
              f"dataset de causa raíz (indicador de clientes, no afectan la conectividad del nodo).")
    return (df, df_sect) if devolver_sectoriales else df


def indicador_sectoriales(df_sect):
    """Indicador de salud de clientes: por ventana, cuántos sectoriales (RTN510_AP) tienen alarmas,
    cuántas alarmas suman y en cuántos nodos están."""
    cols = ["ventana_id", "ventana_ts", "sectoriales_con_alarma", "alarmas", "nodos_afectados"]
    if df_sect is None or df_sect.empty:
        return pd.DataFrame(columns=cols)
    out = (df_sect.groupby(["ventana_id", "ventana_ts"])
                  .agg(sectoriales_con_alarma=("address", "nunique"),
                       alarmas=("address", "size"),
                       nodos_afectados=("CODIGO DE NODO", "nunique"))
                  .reset_index())
    return out.sort_values("ventana_ts").reset_index(drop=True)[cols]


def parsear_moi(df, col="moi"):
    """Extrae puerto/placa/subrack del campo semiestructurado `moi` (mejor esfuerzo).

    Formatos contemplados en los datos:
      - OTN:    '0-subrack-6-B1LDX-1(IN1/OUT1)-OCH:1'
      - Router: 'Physical Port Name=GigabitEthernet0/1/2 ...' / 'If Name=...' / 'PhysicalName=...'
      - RTN/OSS: 'NE=...', 'Node=...', 'User Name=...'  -> sin puerto (queda NaN)
    Añade columnas: moi_subrack, moi_placa, moi_puerto. No pierde filas.
    """
    s = df[col].fillna("").astype(str)
    out = df.copy()
    out["moi_subrack"] = s.str.extract(r"subrack-(\d+)", expand=False)
    out["moi_placa"] = s.str.extract(r"subrack-\d+-([A-Za-z0-9]+)", expand=False)
    # puerto: se intenta en orden de preferencia
    puerto = s.str.extract(r"(?:Port Name|If Name|PhysicalName)=([^\s,]+)", expand=False)
    otn = s.str.extract(r"(\d+\(IN\d+/OUT\d+\)[^\s]*)", expand=False)     # 1(IN1/OUT1)-OCH:1
    gig = s.str.extract(r"(GigabitEthernet[\d/]+)", expand=False)
    out["moi_puerto"] = puerto.fillna(otn).fillna(gig).replace("", pd.NA)
    return out


def marcar_particion_temporal(df, frac_train=0.8, col_ts="ventana_ts", col_id="ventana_id"):
    """Añade la columna `split` ('train'/'test') partiendo por tiempo a nivel de VENTANA.

    Las primeras `frac_train` ventanas (más antiguas) van a train y el resto a test,
    para respetar el orden temporal y evitar fuga de información.
    """
    orden = df[[col_id, col_ts]].drop_duplicates().sort_values(col_ts)
    k = int(len(orden) * frac_train)
    train_ids = set(orden[col_id].iloc[:k])
    out = df.copy()
    out["split"] = out[col_id].apply(lambda v: "train" if v in train_ids else "test")
    return out


SEED = 42  # semilla fija para reproducibilidad


def configurar(seed=SEED, nivel=None):
    """Fija la semilla y el logging del pipeline (reproducibilidad)."""
    import logging
    import random
    random.seed(seed)
    try:
        import numpy as np
        np.random.seed(seed)
    except Exception:
        pass
    logging.basicConfig(level=nivel or logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    return logging.getLogger("ingesta")


if __name__ == "__main__":
    import sys
    log = configurar()
    base = sys.argv[1] if len(sys.argv) > 1 else "data/raw"
    log.info("Procesando ventanas en %s (semilla=%d)", base, SEED)
    df, df_sect = construir_dataset(base, devolver_sectoriales=True)
    df = parsear_moi(df)
    df = marcar_particion_temporal(df, frac_train=0.8)
    log.info("Consolidado: %s | ventanas=%d", df.shape, df["ventana_id"].nunique())
    os.makedirs("data/processed", exist_ok=True)
    df.to_csv("data/processed/dataset_consolidado.csv", index=False, encoding="utf-8-sig")
    df.to_json("data/processed/dataset_consolidado.json", orient="records",
               force_ascii=False, date_format="iso", indent=2)
    try:
        df.to_parquet("data/processed/dataset_consolidado.parquet", index=False)
    except Exception as e:
        log.warning("Parquet omitido (instala pyarrow): %s", e)
    if len(df_sect):
        df_sect.to_csv("data/processed/alarmas_sectoriales.csv", index=False, encoding="utf-8-sig")
        indicador_sectoriales(df_sect).to_csv("data/processed/sectoriales_por_ventana.csv",
                                              index=False, encoding="utf-8-sig")
    print("Guardado en data/processed/")
