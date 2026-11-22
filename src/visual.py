# -*- coding: utf-8 -*-
"""
Visualizaciones INTERACTIVAS del grafo de conocimiento (Sprint 2).

Genera páginas HTML autocontenidas con vis-network: se pueden arrastrar los nodos, acercar con la rueda,
ver los datos de cada sitio/equipo/enlace al pasar el cursor, buscar, filtrar por tipo desde la leyenda y
fijar el detalle de un nodo con un clic. La librería JS va incrustada en el HTML (funciona sin internet).

Uso en un notebook:
    from src.visual import vista_sitios, vista_cluster, vista_incidente, mostrar, guardar_html
    html = vista_sitios(G)
    mostrar(html)                                   # se ve dentro del notebook
    guardar_html(html, "reports/sprint2/grafo_red_sitios.html")
"""
import os
import json
import math
from html import escape

import networkx as nx

try:
    from .grafo import grafo_sitios, jerarquia_sitios, sid, eid, subgrafo_fisico
except ImportError:  # ejecución como script
    from grafo import grafo_sitios, jerarquia_sitios, sid, eid, subgrafo_fisico

# Paleta (contrastes legibles en claro y oscuro)
COLOR = {
    "tx": "#0B5D8F", "ax": "#2BA88A", "noc": "#D1495B", "externo": "#F2A541",
    "fibra": "#5B6B7A", "microondas": "#2BA88A", "enganche": "#21295C", "optico": "#7B5EA7",
    "respaldo": "#F2A541", "ruta": "#D1495B",
    "router_acceso": "#0B5D8F", "router_transporte": "#21295C", "microondas_eq": "#F2A541",
    "enlace_mw": "#E4572E", "enlace_fo": "#7B5EA7", "sin_alarma": "#C9D3DD",
}
VIS_VERSION = "9.1.2"


# ----------------------------------------------------------------------------------------------- utilidades
def _visjs(offline=True):
    """Código de vis-network: incrustado desde el paquete pyvis (offline) o, si no está, desde un CDN."""
    if offline:
        try:
            import pyvis
            ruta = os.path.join(os.path.dirname(pyvis.__file__), "templates", "lib",
                                f"vis-{VIS_VERSION}", "vis-network.min.js")
            with open(ruta, encoding="utf-8") as f:
                return "<script>" + f.read().replace("</script", "<\\/script") + "</script>"
        except Exception:
            pass
    return (f'<script src="https://unpkg.com/vis-network@{VIS_VERSION}/standalone/umd/'
            f'vis-network.min.js"></script>')


def _fmt(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "—"
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    if isinstance(v, (list, tuple, set)):
        v = ", ".join(map(str, v)) or "—"
    return escape(str(v))


def _info(pares):
    """Dict ordenado etiqueta -> valor (escapado) para el panel de detalle."""
    vacio = lambda v: v is None or (isinstance(v, float) and math.isnan(v)) or (isinstance(v, (str, list)) and not v)
    return {k: _fmt(v) for k, v in pares if not vacio(v)}


def _escala_calor(x, xmax):
    """Amarillo -> rojo según x/xmax (escala logarítmica)."""
    if not x:
        return COLOR["sin_alarma"]
    t = math.log1p(x) / math.log1p(max(xmax, 1))
    a, b = (0xFF, 0xD1, 0x66), (0xB2, 0x18, 0x2B)
    return "#%02X%02X%02X" % tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


PLANTILLA = r"""<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITULO__</title>
<style>
:root{--bg:#f4f6f9;--card:#fff;--ink:#1b2733;--mut:#5c6b7a;--line:#e2e7ee;--acc:#0B5D8F}
@media (prefers-color-scheme:dark){:root{--bg:#0f1720;--card:#16212c;--ink:#e6edf3;--mut:#9fb0c0;--line:#243241;--acc:#5fb3e6}}
*{box-sizing:border-box}html,body{height:100%}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
.wrap{display:grid;grid-template-columns:minmax(0,1fr) 290px;gap:12px;padding:12px;height:100%}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;display:flex;flex-direction:column;min-height:0;overflow:hidden}
header{padding:12px 16px 10px;border-bottom:1px solid var(--line)}
h1{font-size:15px;margin:0;font-weight:650}.sub{color:var(--mut);font-size:12.5px;margin-top:2px}
.bar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;padding:8px 12px;border-bottom:1px solid var(--line)}
input[type=search]{flex:1 1 180px;padding:6px 10px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--ink);font:inherit}
button{padding:6px 10px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--ink);cursor:pointer;font:inherit;font-size:12.5px}
button:hover{border-color:var(--acc)}
.chips{display:flex;flex-wrap:wrap;gap:6px;padding:8px 12px;border-bottom:1px solid var(--line)}
.chip{display:inline-flex;align-items:center;gap:6px;font-size:12px;color:var(--mut);padding:3px 9px;border:1px solid var(--line);border-radius:999px;user-select:none}
.chip.f{cursor:pointer}.chip.off{opacity:.35;text-decoration:line-through}
.dot{width:10px;height:10px;border-radius:50%;flex:none}.ln{width:16px;height:0;border-top:3px solid;flex:none}
#red{flex:1;min-height:360px}
.body{padding:12px 16px;overflow:auto}
h2{font-size:13.5px;margin:0 0 8px}
table{border-collapse:collapse;width:100%;font-size:12.5px}
td{padding:5px 0;border-bottom:1px solid var(--line);vertical-align:top}td:first-child{color:var(--mut);width:46%;padding-right:8px}
.hint{color:var(--mut);font-size:12.5px}
div.vis-tooltip{position:absolute;visibility:hidden;pointer-events:none;z-index:5;
 background:var(--card)!important;color:var(--ink)!important;border:1px solid var(--line)!important;border-radius:10px!important;
 padding:8px 10px!important;font:12.5px/1.45 system-ui,sans-serif!important;box-shadow:0 8px 24px rgba(0,0,0,.16)!important;white-space:normal!important;max-width:320px}
@media (max-width:860px){.wrap{grid-template-columns:1fr;height:auto}#red{height:65vh}}
</style>
__VISJS__
</head><body>
<div class="wrap">
 <section class="card">
  <header><h1>__TITULO__</h1><div class="sub">__SUBTITULO__</div></header>
  <div class="bar">
   <input id="buscar" type="search" placeholder="Buscar sitio o equipo (código o nombre)…" list="lista"><datalist id="lista"></datalist>
   <button id="ajustar">Ajustar vista</button><button id="fisica">Pausar física</button>
  </div>
  <div class="chips" id="leyenda"></div>
  <div id="red"></div>
 </section>
 <aside class="card"><header><h1>Detalle</h1><div class="sub">Clic en un nodo para fijar sus datos</div></header>
  <div class="body" id="panel"><p class="hint">Pasa el cursor sobre un nodo o un enlace para ver sus datos. Arrastra los nodos
  para reacomodarlos, usa la rueda para acercar y haz clic en un elemento de la leyenda para ocultarlo o mostrarlo.</p></div>
 </aside>
</div>
<script>
const NODOS=__NODOS__, ARISTAS=__ARISTAS__, LEYENDA=__LEYENDA__, OPC=__OPCIONES__;
const css=getComputedStyle(document.documentElement), INK=css.getPropertyValue('--ink').trim();
OPC.nodes=OPC.nodes||{}; OPC.nodes.font=Object.assign({color:INK,face:'system-ui',size:12},OPC.nodes.font||{});
OPC.edges=OPC.edges||{}; OPC.edges.font=Object.assign({color:INK,size:10,strokeWidth:0},OPC.edges.font||{});
const tip=h=>{const d=document.createElement('div');d.innerHTML=h;return d;};
const filas=o=>Object.entries(o||{}).map(([k,v])=>'<div><span style="opacity:.65">'+k+':</span> '+v+'</div>').join('');
const cab=t=>'<div style="font-weight:600;margin-bottom:4px">'+t+'</div>';
NODOS.forEach(n=>{n.title=tip(cab(n.nombre||n.label||n.id)+filas(n.info));});
ARISTAS.forEach(e=>{e.title=tip(cab(e.tipo||'')+filas(e.info));});
const nodes=new vis.DataSet(NODOS), edges=new vis.DataSet(ARISTAS);
const net=new vis.Network(document.getElementById('red'),{nodes,edges},OPC);
const dl=document.getElementById('lista');
NODOS.forEach(n=>{const o=document.createElement('option');o.value=n.buscar||n.label||n.id;dl.appendChild(o);});
function panel(id){const n=nodes.get(id);if(!n)return;
 let h='<h2>'+(n.nombre||n.label||id)+'</h2><table>';for(const[k,v]of Object.entries(n.info||{}))h+='<tr><td>'+k+'</td><td>'+v+'</td></tr>';
 const vec=net.getConnectedNodes(id).map(x=>nodes.get(x)).filter(Boolean);
 h+='</table><h2 style="margin-top:14px">Conectado con ('+vec.length+')</h2><div class="hint">'+vec.map(v=>v.nombre||v.label||v.id).slice(0,50).join(' · ')+'</div>';
 document.getElementById('panel').innerHTML=h;}
net.on('click',p=>{if(p.nodes.length)panel(p.nodes[0]);});
document.getElementById('buscar').addEventListener('change',e=>{const q=e.target.value.trim().toLowerCase();if(!q)return;
 const n=NODOS.find(x=>String(x.buscar||'').toLowerCase()===q)||NODOS.find(x=>String(x.buscar||x.id).toLowerCase().includes(q));
 if(n){net.selectNodes([n.id]);net.focus(n.id,{scale:1.5,animation:{duration:600}});panel(n.id);}});
document.getElementById('ajustar').onclick=()=>net.fit({animation:{duration:500}});
let fis=OPC.physics&&OPC.physics.enabled!==false;const bf=document.getElementById('fisica');
bf.textContent=fis?'Pausar física':'Activar física';
bf.onclick=()=>{fis=!fis;net.setOptions({physics:{enabled:fis}});bf.textContent=fis?'Pausar física':'Activar física';};
// al terminar de acomodarse, la red se queda quieta para leerla (la física se reactiva con el botón)
if(fis)net.once('stabilizationIterationsDone',()=>{fis=false;net.setOptions({physics:{enabled:false}});bf.textContent='Activar física';});
const ley=document.getElementById('leyenda'),ocultos=new Set();
LEYENDA.forEach(it=>{const c=document.createElement('span');c.className='chip'+(it.filtro?' f':'');
 c.innerHTML=(it.tipo==='linea'?'<span class="ln" style="border-color:'+it.color+';border-top-style:'+(it.estilo||'solid')+'"></span>':'<span class="dot" style="background:'+it.color+'"></span>')+it.texto;
 if(it.filtro)c.onclick=()=>{const off=!ocultos.has(it.filtro);off?ocultos.add(it.filtro):ocultos.delete(it.filtro);c.classList.toggle('off',off);
  nodes.update(NODOS.filter(n=>n.filtro===it.filtro).map(n=>({id:n.id,hidden:off})));};
 ley.appendChild(c);});
</script></body></html>"""


def _pagina(nodos, aristas, leyenda, opciones, titulo, subtitulo="", offline=True):
    js = lambda o: json.dumps(o, ensure_ascii=False).replace("</", "<\\/")
    return (PLANTILLA.replace("__VISJS__", "<!--VIS-->" + _visjs(offline) + "<!--/VIS-->")
            .replace("__TITULO__", escape(titulo)).replace("__SUBTITULO__", escape(subtitulo))
            .replace("__NODOS__", js(nodos)).replace("__ARISTAS__", js(aristas))
            .replace("__LEYENDA__", js(leyenda)).replace("__OPCIONES__", js(opciones)))


# ----------------------------------------------------------------------------------------------- vistas
def vista_sitios(G, H=None, J=None, alarmas_por_sitio=None, resaltar_ruta=None,
                 titulo="Red de Pasco a nivel de sitio", subtitulo=None, offline=True):
    """Red completa a nivel de sitio.

    - Color por red (transporte / acceso / NOC / RDNFO) o, si se pasa `alarmas_por_sitio` (dict código -> n),
      por volumen de alarmas (amarillo -> rojo).
    - Tamaño por criticidad (sitios que dependen de él) o por nº de alarmas.
    - Enlaces: fibra, microondas, enganche acceso→transporte, óptico y respaldo RDNFO (punteado).
    - `resaltar_ruta`: lista de códigos de sitio (p. ej. salida de ruta_al_nucleo) que se pinta encima.
    """
    H = H if H is not None else grafo_sitios(G)
    J = (J if J is not None else jerarquia_sitios(G, H)).set_index("sitio")
    alarmas = dict(alarmas_por_sitio or {})
    amax = max(alarmas.values()) if alarmas else 0
    ruta = list(resaltar_ruta or [])
    en_ruta = set(zip(ruta, ruta[1:])) | set(zip(ruta[1:], ruta))

    nodos = []
    for n, d in H.nodes(data=True):
        red, tipo = d.get("red", ""), str(d.get("tipo_nodo", ""))
        grupo = "noc" if tipo.startswith("NOC") else ("externo" if red == "externo" else (red or "ax"))
        fila = J.loc[n] if n in J.index else {}
        dep = int(fila.get("dependientes", 0) or 0) if len(fila) else 0
        nal = int(alarmas.get(n, 0))
        anillos = G.nodes[sid(n)].get("anillos") if sid(n) in G else None
        info = _info([("Código", n), ("Nombre", d.get("nombre")), ("Tipo de nodo", tipo),
                      ("Red", {"tx": "Transporte", "ax": "Acceso", "externo": "Externa (RDNFO)"}.get(red, red)),
                      ("Cluster", d.get("cluster")), ("Anillo(s)", anillos),
                      ("Nivel en el árbol de acceso", fila.get("nivel_arbol_acceso") if len(fila) else None),
                      ("Saltos al NOC (ruta principal)", fila.get("saltos_al_nucleo") if len(fila) else None),
                      ("Sitios que dependen de él", dep),
                      ("Alarmas en el período", nal if alarmas else None)])
        if alarmas:
            color, tam = _escala_calor(nal, amax), 8 + 5 * math.log1p(nal)
        else:
            color, tam = COLOR[grupo], 8 + 4 * math.log1p(dep)
        forma = {"noc": "star", "externo": "triangle"}.get(grupo, "diamond" if tipo == "DISTRITAL" else "dot")
        borde = COLOR["ruta"] if n in ruta else color
        nodos.append({"id": n, "label": n, "nombre": escape(f"{d.get('nombre', '')} ({n})"), "buscar": f"{n} {d.get('nombre', '')}",
                      "filtro": grupo, "shape": forma, "size": round(tam, 1),
                      "color": {"background": color, "border": borde, "highlight": {"background": color, "border": "#000"}},
                      "borderWidth": 4 if n in ruta else 1, "info": info})

    aristas = []
    for u, v, e in H.edges(data=True):
        medios = e.get("medios", [])
        if e.get("respaldo"):
            tipo, col, w, dash = "Respaldo (RDNFO)", COLOR["respaldo"], 2, [8, 6]
        elif "enganche" in medios:
            tipo, col, w, dash = "Enganche acceso→transporte", COLOR["enganche"], 1.5, [2, 4]
        elif "optico" in medios:
            tipo, col, w, dash = "Fibra + óptico (OptiX)", COLOR["optico"], 3.5, False
        elif "microondas" in medios and "fibra" not in medios:
            tipo, col, w, dash = "Microondas", COLOR["microondas"], 1.5, False
        else:
            tipo, col, w, dash = "Fibra óptica", COLOR["fibra"], 2.5 if e.get("red") == "tx" else 1.8, False
        if (u, v) in en_ruta:
            col, w = COLOR["ruta"], 5
        info = _info([("Tipo", tipo), ("Enlace(s)", e.get("enlaces")), ("Extremos", f"{u} – {v}")])
        aristas.append({"from": u, "to": v, "color": {"color": col, "highlight": "#000"}, "width": w,
                        "dashes": dash, "tipo": tipo, "info": info})

    leyenda = ([{"texto": "Transporte", "color": COLOR["tx"], "filtro": "tx"},
                {"texto": "Acceso (◆ = nodo distrital)", "color": COLOR["ax"], "filtro": "ax"},
                {"texto": "NOC", "color": COLOR["noc"], "filtro": "noc"},
                {"texto": "RDNFO", "color": COLOR["externo"], "filtro": "externo"}] if not alarmas else
               [{"texto": "Sin alarmas", "color": COLOR["sin_alarma"]},
                {"texto": "Pocas alarmas", "color": "#FFD166"}, {"texto": "Muchas alarmas", "color": "#B2182B"}])
    leyenda += [{"texto": "Fibra", "color": COLOR["fibra"], "tipo": "linea"},
                {"texto": "Microondas", "color": COLOR["microondas"], "tipo": "linea"},
                {"texto": "Enganche", "color": COLOR["enganche"], "tipo": "linea", "estilo": "dotted"},
                {"texto": "Respaldo RDNFO", "color": COLOR["respaldo"], "tipo": "linea", "estilo": "dashed"}]
    if ruta:
        leyenda.append({"texto": "Ruta resaltada", "color": COLOR["ruta"], "tipo": "linea"})

    opciones = {"physics": {"enabled": True, "solver": "forceAtlas2Based",
                            "forceAtlas2Based": {"gravitationalConstant": -45, "springLength": 70, "avoidOverlap": 0.4},
                            "stabilization": {"iterations": 400}},
                "interaction": {"hover": True, "tooltipDelay": 120, "navigationButtons": False, "multiselect": True},
                "nodes": {"scaling": {"label": {"enabled": True, "drawThreshold": 9}}, "font": {"size": 11}},
                "edges": {"smooth": {"type": "continuous"}}}
    if subtitulo is None:
        subtitulo = (f"{H.number_of_nodes()} sitios · {H.number_of_edges()} conexiones · "
                     + ("tamaño y color = alarmas del período" if alarmas else "tamaño = sitios que dependen de cada nodo"))
    return _pagina(nodos, aristas, leyenda, opciones, titulo, subtitulo, offline)


def vista_cluster(G, cluster, alarmas_por_equipo=None, titulo=None, offline=True):
    """Un cluster de acceso a nivel de equipo, en árbol de arriba hacia abajo desde el transporte:
    router TT → router del nodo distrital → IDU → enlace → IDU → router …
    `alarmas_por_equipo` (dict NOMBRE DE EQUIPO EN RED -> n) resalta en rojo los equipos con alarmas."""
    alarmas = dict(alarmas_por_equipo or {})
    sitios = {d["codigo"] for _, d in G.nodes(data=True) if d.get("clase") == "Sitio" and d.get("cluster") == cluster}
    if not sitios:
        raise ValueError(f"No hay sitios del cluster '{cluster}'")
    eq = {n for n, d in G.nodes(data=True) if d.get("clase") == "Equipo" and d.get("sitio") in sitios}
    enl = {n for n, d in G.nodes(data=True) if d.get("clase") == "Enlace" and d.get("cluster") == cluster}
    tt = {v for u in eq for v in G.neighbors(u) if G.edges[u, v]["rel"] == "engancha"}
    S = G.subgraph(eq | enl | tt)
    raices = list(tt) or [n for n in eq if G.nodes[n].get("rol") == "router_acceso"
                          and G.nodes[sid(G.nodes[n]["sitio"])].get("tipo_nodo") == "DISTRITAL"]
    nivel = nx.multi_source_dijkstra_path_length(S, raices) if raices else {}

    nodos = []
    for n, d in S.nodes(data=True):
        if d.get("clase") == "Enlace":
            mw = d.get("medio") == "microondas"
            col, forma, tam, lab = (COLOR["enlace_mw"] if mw else COLOR["enlace_fo"]), "square", 9, ("MW" if mw else "FO")
            info = _info([("Enlace", d.get("codigo")), ("Medio", d.get("medio")), ("Cluster", d.get("cluster"))])
            filtro, titulo_n = "enlace", f"Enlace {d.get('codigo')}"
        else:
            rol = d.get("rol")
            col = {"router_acceso": COLOR["router_acceso"], "router_transporte": COLOR["router_transporte"],
                   "microondas": COLOR["microondas_eq"]}.get(rol, "#888")
            forma = "triangle" if rol == "microondas" else "box"
            tam = 12 if rol == "microondas" else 16
            lab = "IDU" if rol == "microondas" else d.get("sitio", "")
            nal = int(alarmas.get(d.get("nombre"), 0))
            info = _info([("Equipo", d.get("nombre")), ("Rol", rol), ("Modelo", d.get("tipo")), ("IP", d.get("ip")),
                          ("Sitio", d.get("sitio")), ("Alarmas", nal if alarmas else None)])
            filtro, titulo_n = rol, d.get("nombre")
            if nal:
                col = "#D1495B"
        nodos.append({"id": n, "label": lab, "nombre": escape(str(titulo_n)), "buscar": f"{titulo_n} {d.get('sitio', '')}",
                      "filtro": filtro, "shape": forma, "size": tam, "level": int(nivel.get(n, 0)),
                      "color": {"background": col, "border": col, "highlight": {"background": col, "border": "#000"}},
                      "font": {"color": "#ffffff"} if forma == "box" else {},
                      "info": info})
    nombre_rel = {"conexionLocal": "Conexión local (cableado en el sitio)", "extremoDe": "Extremo del enlace",
                  "engancha": "Enganche acceso→transporte"}
    aristas = [{"from": u, "to": v, "width": 2, "dashes": [2, 4] if e["rel"] == "engancha" else False,
                "color": {"color": COLOR["enganche"] if e["rel"] == "engancha" else "#8A99A8"},
                "tipo": nombre_rel.get(e["rel"], e["rel"]), "info": {}}
               for u, v, e in S.edges(data=True)]
    leyenda = [{"texto": "Router de acceso", "color": COLOR["router_acceso"], "filtro": "router_acceso"},
               {"texto": "IDU microondas", "color": COLOR["microondas_eq"], "filtro": "microondas"},
               {"texto": "Enlace MW / FO", "color": COLOR["enlace_mw"], "filtro": "enlace"},
               {"texto": "Router de transporte", "color": COLOR["router_transporte"], "filtro": "router_transporte"}]
    if alarmas:
        leyenda.append({"texto": "Equipo con alarmas", "color": "#D1495B"})
    opciones = {"layout": {"hierarchical": {"enabled": True, "direction": "UD", "levelSeparation": 85,
                                            "nodeSpacing": 95, "treeSpacing": 120}},
                "physics": {"enabled": False},
                "interaction": {"hover": True, "tooltipDelay": 120},
                "edges": {"smooth": {"type": "cubicBezier", "forceDirection": "vertical", "roundness": 0.4}}}
    titulo = titulo or f"Cluster {cluster} a nivel de equipo"
    sub = f"{len(sitios)} sitios · {len(eq)} equipos · {len(enl)} enlaces · de arriba hacia abajo: del transporte a los nodos terminales"
    return _pagina(nodos, aristas, leyenda, opciones, titulo, sub, offline)


def vista_incidente(G, alarmas, ranking=None, causa_real=None, J=None, titulo=None, offline=True,
                    col_equipo="NOMBRE DE EQUIPO EN RED", col_tiempo=None):
    """Un incidente (avalancha) sobre el grafo físico, de arriba (más cerca del NOC) hacia abajo.

    alarmas    : filas del incidente (una por alarma).
    ranking    : opcional, ranking de un método (columnas equipo, rank): se muestra «#k» en cada candidato.
    causa_real : opcional, nombre del equipo causa raíz según la verdad de referencia (borde dorado).
    Muestra los equipos con alarmas, los enlaces y equipos intermedios que los unen y, al pasar el cursor, las
    alarmas de cada equipo en orden de llegada."""
    F = subgrafo_fisico(G)
    alarmados = [eid(str(x).strip()) for x in alarmas[col_equipo].dropna().unique()]
    alarmados = [n for n in alarmados if n in F]
    if not alarmados:
        raise ValueError("Ninguna alarma del incidente está en el grafo.")
    nodos_set = set(alarmados)
    for n in alarmados[1:]:
        try:
            nodos_set.update(nx.shortest_path(F, alarmados[0], n))
        except nx.NetworkXNoPath:
            pass
    S = F.subgraph(nodos_set)
    J = (J if J is not None else jerarquia_sitios(G, grafo_sitios(G))).set_index("sitio")
    nivel_sitio = J["saltos_al_nucleo"].to_dict()
    rk = dict(zip(ranking["equipo"].astype(str), ranking["rank"])) if ranking is not None else {}
    if col_tiempo is None:
        col_tiempo = next((c for c in ("t_evento", "occurUtc", "firstOccurUtc", "arriveUtc") if c in alarmas.columns), None)
    tcol = col_tiempo if col_tiempo in alarmas.columns else None
    porq = alarmas.sort_values(tcol) if tcol else alarmas

    def nivel(n):
        d = G.nodes[n]
        if d.get("clase") == "Enlace":
            vec = [nivel_sitio.get(G.nodes[v].get("sitio"), 0) for v in S.neighbors(n)] or [0]
            return int(min(vec)) * 2 + 1
        return int(nivel_sitio.get(d.get("sitio"), 0) or 0) * 2

    nodos = []
    for n, d in S.nodes(data=True):
        if d.get("clase") == "Enlace":
            mw = d.get("medio") == "microondas"
            col = COLOR["enlace_mw"] if mw else COLOR["enlace_fo"]
            nodos.append({"id": n, "label": "MW" if mw else "FO", "nombre": escape(f"Enlace {d.get('codigo')}"),
                          "buscar": str(d.get("codigo")), "filtro": "enlace", "shape": "square", "size": 8,
                          "level": nivel(n), "color": {"background": col, "border": col},
                          "info": _info([("Enlace", d.get("codigo")), ("Medio", d.get("medio"))])})
            continue
        nombre = d.get("nombre")
        mias = porq[porq[col_equipo].astype(str).str.strip() == str(nombre)]
        lista = [f"{(_hora(t) if tcol else '')} {a}" for t, a in
                 zip(mias[tcol] if tcol else [None] * len(mias), mias["alarmName"])][:8]
        pos = rk.get(str(nombre))
        es_causa = causa_real is not None and str(nombre) == str(causa_real)
        col = "#D1495B" if len(mias) else COLOR["sin_alarma"]
        etiqueta = (f"#{pos} " if pos else "") + (d.get("sitio") or "")
        nodos.append({"id": n, "label": etiqueta, "nombre": escape(str(nombre)), "buscar": f"{nombre} {d.get('sitio', '')}",
                      "filtro": "alarmado" if len(mias) else "intermedio",
                      "shape": "triangle" if d.get("rol") == "microondas" else "box",
                      "size": 14, "level": nivel(n), "borderWidth": 5 if es_causa else 1,
                      "color": {"background": col, "border": "#E0A100" if es_causa else col,
                                "highlight": {"background": col, "border": "#000"}},
                      "font": {"color": "#ffffff"} if d.get("rol") != "microondas" else {},
                      "info": _info([("Equipo", nombre), ("Rol", d.get("rol")), ("Sitio", d.get("sitio")),
                                     ("Saltos al NOC", nivel_sitio.get(d.get("sitio"))),
                                     ("Posición en el ranking", pos), ("Causa raíz (verdad)", "Sí" if es_causa else None),
                                     ("Alarmas (orden de llegada)", " · ".join(lista) if lista else None)])})
    aristas = [{"from": u, "to": v, "width": 2, "color": {"color": "#8A99A8"},
                "tipo": {"conexionLocal": "Conexión local", "extremoDe": "Extremo del enlace",
                         "engancha": "Enganche acceso→transporte"}.get(e.get("rel"), e.get("rel")), "info": {}}
               for u, v, e in S.edges(data=True)]
    leyenda = [{"texto": "Equipo con alarmas", "color": "#D1495B", "filtro": "alarmado"},
               {"texto": "Equipo intermedio sin alarmas", "color": COLOR["sin_alarma"], "filtro": "intermedio"},
               {"texto": "Enlace MW / FO", "color": COLOR["enlace_mw"], "filtro": "enlace"}]
    if causa_real is not None:
        leyenda.append({"texto": "Causa raíz (verdad)", "color": "#E0A100"})
    opciones = {"layout": {"hierarchical": {"enabled": True, "direction": "UD", "levelSeparation": 70,
                                            "nodeSpacing": 110}},
                "physics": {"enabled": False}, "interaction": {"hover": True, "tooltipDelay": 100},
                "edges": {"smooth": {"type": "cubicBezier", "forceDirection": "vertical", "roundness": 0.4}}}
    sub = (f"{len(alarmas)} alarmas en {len(alarmados)} equipos · arriba = más cerca del NOC"
           + (" · #k = posición en el ranking del método" if rk else ""))
    return _pagina(nodos, aristas, leyenda, opciones, titulo or "Incidente sobre el grafo físico", sub, offline)


def _hora(t):
    """Hora corta HH:MM:SS de un timestamp (o texto vacío)."""
    try:
        return t.strftime("%H:%M:%S")
    except Exception:
        return ""


# ----------------------------------------------------------------------------------------------- salida
def guardar_html(html, ruta):
    """Guarda la página (se abre con doble clic en cualquier navegador, sin internet)."""
    os.makedirs(os.path.dirname(os.path.abspath(ruta)), exist_ok=True)
    with open(ruta, "w", encoding="utf-8") as f:
        f.write(html)
    return ruta


def mostrar(html, alto=720, ligero=True):
    """Muestra la página dentro del notebook (Jupyter / VS Code) en un iframe aislado.

    ligero=True carga vis-network desde internet SOLO para la vista del notebook (el .ipynb no crece ~0,5 MB por
    gráfico). Los HTML guardados con guardar_html() siguen funcionando sin conexión. Sin internet: ligero=False."""
    import re
    import warnings
    from IPython.display import HTML, display
    if ligero:
        html = re.sub(r"<!--VIS-->.*?<!--/VIS-->", lambda m: _visjs(offline=False), html, count=1, flags=re.S)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        display(HTML(f'<iframe srcdoc="{escape(html, quote=True)}" style="width:100%;height:{alto}px;'
                 f'border:0;border-radius:12px" sandbox="allow-scripts allow-same-origin"></iframe>'))
