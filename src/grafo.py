# -*- coding: utf-8 -*-
"""
Grafo de conocimiento v1 de la red (Sprint 2 · F3).

Instancia la ontología mínima (docs/03_ontologia.md, docs/ontologia_v1.ttl) a partir de red.xlsx:
  - hoja 'nodos'         -> Sitio y Equipo                    (relación contiene)
  - hoja 'topologia_ax'  -> Enlace de acceso (microondas/fibra), extremoDe y conexionLocal router-IDU
  - hoja 'topologia_tx'  -> Enlace de transporte (fibra, anillos/bus) y extremoDe
  - reglas del NOC       -> engancha: router del nodo distrital (AD) -> router de transporte (TT) del
                            mismo sitio (excepciones en data/raw/reglas_red.json) y NOC Acceso -> NOC Transporte

Granularidad: sitio -> equipo -> extremo de enlace. El puerto NO es un nodo (migra por averías):
queda como atributo dinámico de la alarma (columna moi_puerto de la ingesta).

Uso:
    from src.grafo import construir_grafo, grafo_sitios, jerarquia_sitios, ubicar_alarmas
    G = construir_grafo("data/raw/red.xlsx")
"""
import os
import json
import pandas as pd
import networkx as nx

try:
    from .ingesta import rol_equipo
except ImportError:  # ejecución como script
    from ingesta import rol_equipo

# Reglas de conexión confirmadas con el NOC. Son DATOS DEL OPERADOR (topología): no se escriben en el código.
# Se leen de data/raw/reglas_red.json (no versionado), junto a red.xlsx. Formato: config/reglas_red.ejemplo.json
ENGANCHE_EXCEPCIONES = {}     # {"codigo_router_acceso": "codigo_router_transporte"}
ENLACES_OPTICOS = []          # [["NOMBRE EQUIPO A", "NOMBRE EQUIPO B"], ...]


def cargar_reglas_red(ruta_xlsx):
    """Carga las excepciones de enganche y los enlaces ópticos desde reglas_red.json (misma carpeta que red.xlsx)."""
    global ENGANCHE_EXCEPCIONES, ENLACES_OPTICOS
    ruta = os.path.join(os.path.dirname(os.path.abspath(ruta_xlsx)), "reglas_red.json")
    if os.path.exists(ruta):
        with open(ruta, encoding="utf-8") as f:
            r = json.load(f)
        ENGANCHE_EXCEPCIONES = dict(r.get("enganche_excepciones", {}))
        ENLACES_OPTICOS = [tuple(x) for x in r.get("enlaces_opticos", [])]
    else:
        print(f"[aviso] No se encontró {ruta}: el grafo se construye sin excepciones de enganche ni enlaces ópticos.")
# Los sitios externos (RDNFO) son rutas de RESPALDO o de agregación de tráfico hacia el NOC:
# la ruta principal de cada sitio se calcula por la red propia (evitar_externos=True).

CLASE_POR_ROL = {
    "router_acceso": "RouterAcceso",
    "router_transporte": "RouterTransporte",
    "router_agregacion_acceso": "RouterAgregacionAcceso",
    "microondas": "EquipoMicroondas",
    "optico_transporte": "EquipoOpticoTransporte",
    "sectorial": "Sectorial",
}
REL_FISICAS = {"conexionLocal", "extremoDe", "engancha"}


def sid(codigo):
    return f"S:{codigo}"


def eid(nombre):
    return f"E:{str(nombre).strip()}"


def lid(codigo):
    return f"L:{codigo}"


def _base(codigo):
    """'XX-0000-AD' -> 'XX-0000' (identifica el sitio físico compartido por acceso y transporte)."""
    return str(codigo).rsplit("-", 1)[0]


def _leer(ruta, hoja):
    d = pd.read_excel(ruta, sheet_name=hoja)
    d.columns = [str(c).strip() for c in d.columns]
    return d


def construir_grafo(ruta_xlsx, incluir_sectoriales=False, verbose=True):
    """Construye el grafo de conocimiento v1 (networkx.Graph) a partir de red.xlsx.

    Nodos: clase ∈ {Sitio, Equipo, Enlace} (+ subclase de la ontología).
    Aristas: rel ∈ {contiene, conexionLocal, extremoDe, engancha}.
    Los avisos de calidad quedan en G.graph['avisos'].
    """
    cargar_reglas_red(ruta_xlsx)
    G = nx.Graph(nombre="Grafo de conocimiento v1 - red Pasco")
    avisos = []
    nodos = _leer(ruta_xlsx, "nodos")
    ax = _leer(ruta_xlsx, "topologia_ax")
    tx = _leer(ruta_xlsx, "topologia_tx")

    # --- sitios (un equipo sin CODIGO DE NODO se agrupa en un sitio provisional)
    def cod_sitio(r):
        c = r["CODIGO DE NODO"]
        if pd.isna(c):
            return f"SIN_CODIGO:{r['NOMBRE DE NODO']}:{r['TIPO DE NODO']}"
        return str(c).strip()
    nodos["_sitio"] = nodos.apply(cod_sitio, axis=1)
    n_sin = int(nodos["CODIGO DE NODO"].isna().sum())
    if n_sin:
        avisos.append(f"{n_sin} equipo(s) sin CODIGO DE NODO: agrupados en un sitio provisional 'SIN_CODIGO:...'")

    for c, g in nodos.groupby("_sitio"):
        r = g.iloc[0]
        red = str(r["Red"]).strip().lower() if "Red" in g.columns and pd.notna(r["Red"]) else ""
        sub = {"tx": "SitioTransporte", "ax": "SitioAcceso"}.get(red, "Sitio")
        G.add_node(sid(c), clase="Sitio", subclase=sub, codigo=c, nombre=str(r["NOMBRE DE NODO"]).strip(),
                   tipo_nodo=str(r["TIPO DE NODO"]).strip(), red=red, cluster="", anillos=[])

    # --- equipos + contiene
    for _, r in nodos.iterrows():
        rol = rol_equipo(r["TIPO"])
        if rol == "sectorial" and not incluir_sectoriales:
            continue
        nom = str(r["NOMBRE DE EQUIPO EN RED"]).strip()
        red = str(r["Red"]).strip().lower() if "Red" in nodos.columns and pd.notna(r["Red"]) else ""
        G.add_node(eid(nom), clase="Equipo", subclase=CLASE_POR_ROL.get(rol, "Equipo"), nombre=nom,
                   tipo=str(r["TIPO"]).strip(), rol=rol, red=red, ip=str(r["IP_Equipo"]).strip(),
                   sitio=r["_sitio"])
        G.add_edge(sid(r["_sitio"]), eid(nom), rel="contiene")

    def equipo(nombre):
        n = eid(nombre)
        if n not in G:
            G.add_node(n, clase="Equipo", subclase="Equipo", nombre=str(nombre).strip(), rol="desconocido",
                       red="", ip="", sitio="", fuera_inventario=True)
            avisos.append(f"Equipo de la topología que no está en 'nodos': {nombre}")
        return n

    # --- enlaces de acceso (árboles por cluster)
    for _, r in ax.iterrows():
        cod = str(r["CODIGO_Enlace"]).strip()
        cluster = str(r["CLUSTER"]).strip()
        medio = "microondas" if "MICRO" in str(r["TIPO DE ENLACE"]).upper() else "fibra"
        L = lid(cod)
        if L not in G:
            G.add_node(L, clase="Enlace", subclase="EnlaceMicroondas" if medio == "microondas" else "EnlaceFibra",
                       codigo=cod, medio=medio, red="ax", cluster=cluster, anillos=[], respaldo=False)
        for k in ("1", "2"):
            s = str(r[f"CODIGO DE NODO_{k}"]).strip()
            if sid(s) in G:
                G.nodes[sid(s)]["cluster"] = cluster
            router = equipo(r[f"NOMBRE DE EQUIPO ROUTER EN NODO_{k}"])
            idu_nom = r.get(f"NOMBRE DE EQUIPO MICROONDAS EN NODO_{k}")
            if medio == "microondas" and pd.notna(idu_nom):
                idu = equipo(idu_nom)
                G.add_edge(idu, L, rel="extremoDe")
                G.add_edge(router, idu, rel="conexionLocal")      # Router N -- IDU N
            else:
                G.add_edge(router, L, rel="extremoDe")             # fibra: el router es el extremo

    # --- enlaces de transporte (anillos / bus); un tramo compartido por 2 anillos es un solo enlace
    for _, r in tx.iterrows():
        a, b = str(r["CODIGO DE NODO1"]).strip(), str(r["CODIGO DE NODO2"]).strip()
        anillo = str(r["Anillo_Logico"]).strip()
        cod = "TX:" + "|".join(sorted([a, b]))
        L = lid(cod)
        if L not in G:
            G.add_node(L, clase="Enlace", subclase="EnlaceFibra", codigo=cod, medio="fibra", red="tx",
                       cluster="", anillos=[], respaldo=False)
        if anillo not in G.nodes[L]["anillos"]:
            G.nodes[L]["anillos"].append(anillo)
        for s, col_eq, col_nom in ((a, "NOMBRE DE EQUIPO EN RED1", "NOMBRE DE NODO1"),
                                   (b, "NOMBRE DE EQUIPO EN RED2", "NOMBRE DE NODO2")):
            if sid(s) not in G:                                    # p. ej. RDNFO (Red Dorsal Nacional)
                G.add_node(sid(s), clase="Sitio", subclase="SitioExterno", codigo=s,
                           nombre=str(r[col_nom]).strip(), tipo_nodo="EXTERNO", red="externo",
                           cluster="", anillos=[])
            if G.nodes[sid(s)].get("subclase") == "SitioExterno":
                G.nodes[L]["respaldo"] = True                      # tramo hacia la RDNFO = ruta de respaldo
            if anillo not in G.nodes[sid(s)]["anillos"]:
                G.nodes[sid(s)]["anillos"].append(anillo)
            if pd.notna(r[col_eq]):
                G.add_edge(equipo(r[col_eq]), L, rel="extremoDe")
            else:
                G.add_edge(sid(s), L, rel="extremoDe")

    def equipos_de(codigo_sitio, roles):
        s = sid(codigo_sitio)
        if s not in G:
            return []
        return [e for e in G.neighbors(s)
                if G.edges[s, e]["rel"] == "contiene" and G.nodes[e].get("rol") in roles]

    # --- enganche acceso -> transporte (regla confirmada con el NOC)
    sitios_tx = [d["codigo"] for _, d in G.nodes(data=True)
                 if d.get("clase") == "Sitio" and d.get("subclase") == "SitioTransporte"]
    por_base = {}
    for c in sitios_tx:
        por_base.setdefault(_base(c), []).append(c)
    distritales = [d["codigo"] for _, d in G.nodes(data=True)
                   if d.get("clase") == "Sitio" and d.get("red") == "ax" and d.get("tipo_nodo") == "DISTRITAL"]
    for c in distritales:
        destino = ENGANCHE_EXCEPCIONES.get(c) or (por_base.get(_base(c)) or [None])[0]
        if not destino:
            avisos.append(f"{c}: sin sitio de transporte para el enganche")
            continue
        ra = equipos_de(c, {"router_acceso"})
        rt = equipos_de(destino, {"router_transporte"})
        if not ra or not rt:
            avisos.append(f"{c} -> {destino}: falta router de acceso o de transporte para el enganche")
        for x in ra:
            for y in rt:
                G.add_edge(x, y, rel="engancha", tipo="acceso-transporte",
                           mismo_sitio=c not in ENGANCHE_EXCEPCIONES)

    # --- NOC Acceso -> NOC Transporte
    noc_ax = [n for n, d in G.nodes(data=True) if d.get("rol") == "router_agregacion_acceso"]
    noc_tx = [d["codigo"] for _, d in G.nodes(data=True)
              if d.get("clase") == "Sitio" and d.get("tipo_nodo") == "NOC Transporte"]
    rt_noc = [e for c in noc_tx for e in equipos_de(c, {"router_transporte"})]
    for x in noc_ax:
        for y in rt_noc:
            G.add_edge(x, y, rel="engancha", tipo="noc", mismo_sitio=False)

    # --- enlaces ópticos OptiX OSN (confirmados con el NOC). Supuesto: cada OptiX está cableado
    #     localmente a los routers de transporte de su sitio (marcado con supuesto=True)
    for a_nom, b_nom in ENLACES_OPTICOS:
        ea, eb = eid(a_nom), eid(b_nom)
        if ea not in G or eb not in G:
            avisos.append(f"Enlace óptico no creado: falta el equipo {a_nom if ea not in G else b_nom}")
            continue
        sa, sb = G.nodes[ea]["sitio"], G.nodes[eb]["sitio"]
        cod = "OPT:" + "|".join(sorted([sa, sb]))
        G.add_node(lid(cod), clase="Enlace", subclase="EnlaceFibra", codigo=cod, medio="optico", red="tx",
                   cluster="", anillos=[], respaldo=False)
        G.add_edge(ea, lid(cod), rel="extremoDe")
        G.add_edge(eb, lid(cod), rel="extremoDe")
    for n, d in list(G.nodes(data=True)):
        if d.get("rol") == "optico_transporte":
            for r_ in equipos_de(d["sitio"], {"router_transporte"}):
                G.add_edge(n, r_, rel="conexionLocal", supuesto=True)

    # --- sectoriales (opcional): cuelgan del router de acceso de su sitio
    if incluir_sectoriales:
        for n, d in list(G.nodes(data=True)):
            if d.get("rol") == "sectorial":
                for r_ in equipos_de(d["sitio"], {"router_acceso"}):
                    G.add_edge(r_, n, rel="conexionLocal")

    # --- equipos sin conexión física (solo 'contiene')
    aislados = [d["nombre"] for n, d in G.nodes(data=True)
                if d.get("clase") == "Equipo" and not any(G.edges[n, m]["rel"] in REL_FISICAS
                                                          for m in G.neighbors(n))]
    if aislados:
        avisos.append(f"{len(aislados)} equipo(s) sin conexión física en la topología: {aislados[:6]}")

    G.graph["avisos"] = avisos
    if verbose:
        print(f"Grafo v1: {G.number_of_nodes():,} nodos | {G.number_of_edges():,} aristas")
        for a in avisos:
            print("  aviso:", a)
    return G


def subgrafo_fisico(G):
    """Solo equipos/enlaces y las relaciones físicas (sin 'contiene')."""
    H = nx.Graph()
    for u, v, d in G.edges(data=True):
        if d["rel"] in REL_FISICAS:
            H.add_node(u, **G.nodes[u]); H.add_node(v, **G.nodes[v]); H.add_edge(u, v, **d)
    return H


def _sitio_de(G, n):
    d = G.nodes[n]
    return d["codigo"] if d.get("clase") == "Sitio" else (d.get("sitio") or None)


def grafo_sitios(G):
    """Proyección a nivel de sitio: dos sitios se unen si un enlace o un enganche conecta sus equipos.

    Si hay varios enlaces entre el mismo par de sitios (p. ej. fibra + óptico) se guardan todos en `enlaces`.
    `respaldo=True` marca las conexiones que solo existen por la RDNFO."""
    H = nx.Graph()
    for _, d in G.nodes(data=True):
        if d.get("clase") == "Sitio":
            H.add_node(d["codigo"], nombre=d.get("nombre"), red=d.get("red"), tipo_nodo=d.get("tipo_nodo"),
                       cluster=d.get("cluster"), subclase=d.get("subclase"))

    def unir(a, b, enlace, medio, red, respaldo):
        if H.has_edge(a, b):
            e = H.edges[a, b]
            e["enlaces"].append(enlace); e["medios"].append(medio)
            e["respaldo"] = e["respaldo"] and respaldo
        else:
            H.add_edge(a, b, enlaces=[enlace], medios=[medio], red=red, respaldo=respaldo)

    for n, d in G.nodes(data=True):
        if d.get("clase") == "Enlace":
            ext = {_sitio_de(G, m) for m in G.neighbors(n) if G.edges[n, m]["rel"] == "extremoDe"}
            ext = sorted(e for e in ext if e)
            if len(ext) >= 2:
                unir(ext[0], ext[1], d["codigo"], d["medio"], d["red"], bool(d.get("respaldo")))
    for u, v, e in G.edges(data=True):
        if e["rel"] == "engancha":
            a, b = _sitio_de(G, u), _sitio_de(G, v)
            if a and b and a != b:
                unir(a, b, f"enganche:{a}->{b}", "enganche", "ax-tx", False)
    return H


def _sin_externos(H):
    """Copia del grafo de sitios sin los sitios externos (RDNFO): solo la red propia."""
    H2 = H.copy()
    H2.remove_nodes_from([n for n, d in H.nodes(data=True) if d.get("subclase") == "SitioExterno"])
    return H2


def red_propia(H):
    """Grafo de sitios solo con la red del operador (sin RDNFO); útil para comparar con/sin respaldo."""
    return _sin_externos(H)


def _nucleo(H):
    return [n for n, d in H.nodes(data=True) if d.get("tipo_nodo") == "NOC Transporte"]


def dependientes_por_sitio(H):
    """Cuántos sitios se quedan sin camino al NOC de transporte si cada sitio falla.

    Usa la red completa, incluida la RDNFO como respaldo: un sitio con ruta alternativa no queda aislado.
    Solo los puntos de articulación del grafo pueden tener dependientes > 0."""
    nucleo = _nucleo(H)
    if not nucleo:
        return {}
    base = set().union(*(nx.node_connected_component(H, t) for t in nucleo))
    out = {}
    for v in nx.articulation_points(H):
        H2 = H.copy(); H2.remove_node(v)
        alcanzables = set().union(*(nx.node_connected_component(H2, t) for t in nucleo if t in H2))
        out[v] = len(base - alcanzables - {v})
    return out


def jerarquia_sitios(G, H=None, evitar_externos=True):
    """Tabla por sitio (insumo de las features topológicas):
    - saltos_al_nucleo: saltos hasta el NOC de transporte por la ruta PRINCIPAL (red propia, sin RDNFO)
    - nivel_arbol_acceso: profundidad en el árbol de su cluster (0 = nodo distrital)
    - dependientes: sitios que pierden conexión al núcleo si este sitio falla (considerando el respaldo RDNFO)
    - es_critico: dependientes > 0
    """
    H = H if H is not None else grafo_sitios(G)
    dep = dependientes_por_sitio(H)
    Hr = _sin_externos(H) if evitar_externos else H
    nucleo = _nucleo(Hr)
    sit_tx = [n for n, d in Hr.nodes(data=True) if d.get("red") == "tx"]
    d_nucleo = nx.multi_source_dijkstra_path_length(Hr, nucleo) if nucleo else {}
    d_tx = nx.multi_source_dijkstra_path_length(Hr, sit_tx) if sit_tx else {}
    filas = []
    for n, d in H.nodes(data=True):
        nivel = d_tx[n] - 1 if d.get("red") == "ax" and n in d_tx else None
        filas.append({"sitio": n, "nombre": d.get("nombre"), "red": d.get("red"), "tipo_nodo": d.get("tipo_nodo"),
                      "cluster": d.get("cluster") or None, "grado": H.degree(n),
                      "saltos_al_nucleo": d_nucleo.get(n), "nivel_arbol_acceso": nivel,
                      "dependientes": dep.get(n, 0), "es_critico": dep.get(n, 0) > 0})
    return pd.DataFrame(filas)


def ruta_al_nucleo(G, codigo_sitio, H=None, evitar_externos=True):
    """Camino de sitios desde `codigo_sitio` hasta el NOC de transporte más cercano (aguas arriba).

    Por defecto es la ruta PRINCIPAL por la red propia; con evitar_externos=False puede usar la RDNFO."""
    H = H if H is not None else grafo_sitios(G)
    if evitar_externos:
        H = _sin_externos(H)
    mejor = None
    for t in _nucleo(H):
        try:
            p = nx.shortest_path(H, codigo_sitio, t)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            continue
        if mejor is None or len(p) < len(mejor):
            mejor = p
    return mejor


def ubicar_alarmas(df, G, col="NOMBRE DE EQUIPO EN RED"):
    """Relación ocurreEn: añade `nodo_grafo` (id del equipo en el grafo) y `en_grafo`."""
    out = df.copy()
    ids = out[col].astype(str).str.strip().map(eid)
    out["nodo_grafo"] = ids.where(ids.isin(pd.Index(list(G.nodes))))
    out["en_grafo"] = out["nodo_grafo"].notna()
    return out


def resumen_grafo(G):
    """Conteos por clase/subclase de nodo y por tipo de relación."""
    nodos = pd.DataFrame([(d.get("clase"), d.get("subclase")) for _, d in G.nodes(data=True)],
                         columns=["clase", "subclase"]).value_counts().rename("n").reset_index()
    rels = pd.Series([d["rel"] for *_, d in G.edges(data=True)]).value_counts().rename("n")
    return nodos, rels.rename_axis("relacion").reset_index()


def exportar_grafo(G, carpeta):
    """Guarda el grafo en GraphML (Gephi / yEd) y JSON (node-link)."""
    os.makedirs(carpeta, exist_ok=True)
    H = G.copy()
    H.graph = {"nombre": G.graph.get("nombre", "")}
    for _, d in list(H.nodes(data=True)) + [(None, e) for *_, e in H.edges(data=True)]:
        for k, v in list(d.items()):
            if isinstance(v, (list, tuple, set)):
                d[k] = ";".join(map(str, v))
            elif v is None:
                d[k] = ""
    nx.write_graphml(H, os.path.join(carpeta, "grafo_v1.graphml"))
    from networkx.readwrite import json_graph
    try:
        data = json_graph.node_link_data(H, edges="edges")
    except TypeError:                                           # networkx < 3.4
        data = json_graph.node_link_data(H)
    with open(os.path.join(carpeta, "grafo_v1.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    return os.path.join(carpeta, "grafo_v1.graphml")


if __name__ == "__main__":
    import sys
    ruta = sys.argv[1] if len(sys.argv) > 1 else "data/raw/red.xlsx"
    G = construir_grafo(ruta)
    nodos, rels = resumen_grafo(G)
    print(nodos.to_string(index=False)); print(rels.to_string(index=False))
    print("Exportado:", exportar_grafo(G, "data/processed"))
