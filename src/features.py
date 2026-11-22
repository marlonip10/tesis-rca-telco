# -*- coding: utf-8 -*-
"""
Ingeniería de atributos (Sprint 2): una fila por CANDIDATO = (incidente, equipo con alarmas).

Familias (ver docs/05_features.md):
  temporales      : cuándo y cuánto alarmó el equipo dentro del incidente.
  topológicas     : dónde está el equipo en la red y respecto de los demás equipos del incidente.
  co-ocurrencia   : estadísticas aprendidas SOLO del entrenamiento (qué tipo de alarma suele llegar primero, qué
                    tipo suele preceder a cuál). Se ajustan por fold con `fit` para no filtrar el futuro.
  semánticas      : rol del equipo y tipo de alarma según el catálogo de los manuales.
Las reglas causa→efecto de los manuales NO entran como feature (son la etiqueta plata; usarlas sería circular).

Uso:
    fx = ExtractorFeatures(G).fit(df_inc_train)
    X = fx.transform(df_inc_test)
"""
import numpy as np
import pandas as pd
import networkx as nx

try:
    from .grafo import subgrafo_fisico, grafo_sitios, jerarquia_sitios, eid
    from .etiquetado import col_tiempo, COL_EQUIPO, COL_SITIO
except ImportError:
    from grafo import subgrafo_fisico, grafo_sitios, jerarquia_sitios, eid
    from etiquetado import col_tiempo, COL_EQUIPO, COL_SITIO

ROLES = ["router_transporte", "router_agregacion_acceso", "router_acceso", "microondas", "optico_transporte"]
SUAVIZADO = 1.0          # suavizado de Laplace para las tasas de co-ocurrencia


class ExtractorFeatures:
    """Calcula la tabla de candidatos. `fit` aprende las estadísticas de co-ocurrencia del entrenamiento."""

    def __init__(self, G, catalogo=None, max_saltos=4, J=None):
        self.G = G
        self.F = subgrafo_fisico(G)
        J = J if J is not None else jerarquia_sitios(G, grafo_sitios(G))
        self.saltos_sitio = dict(zip(J["sitio"], J["saltos_al_nucleo"]))
        self.dep_sitio = dict(zip(J["sitio"], J["dependientes"]))
        self.max_saltos = max_saltos
        self.cat = {}
        if catalogo is not None and len(catalogo):
            k = catalogo["nombre_nce"] if "nombre_nce" in catalogo else catalogo["alarma"]
            self.cat = dict(zip(k.astype(str), catalogo["tipo_alarma"].astype(str)))
        self.ajustado = False

    # --------------------------------------------------------------------------------- co-ocurrencia (train)
    def fit(self, df_inc):
        """Aprende, solo con incidentes de entrenamiento:
           p_primero[tipo]   = P(el tipo es la primera alarma del incidente | aparece en el incidente)
           p_precede[a][b]   = P(a llega antes que b | a y b aparecen juntos)
           log_frec[tipo]    = log(1 + nº de apariciones)"""
        t = col_tiempo(df_inc)
        aparece, primero, frec = {}, {}, {}
        antes, juntos = {}, {}
        for _, g in df_inc.sort_values(t).groupby("incidente_id"):
            tipos = g["alarmName"].astype(str).tolist()
            primera_vez = {}
            for k, a in enumerate(tipos):
                primera_vez.setdefault(a, k)
                frec[a] = frec.get(a, 0) + 1
            for a in primera_vez:
                aparece[a] = aparece.get(a, 0) + 1
            primero[tipos[0]] = primero.get(tipos[0], 0) + 1
            u = list(primera_vez)
            for i, a in enumerate(u):
                for b in u[i + 1:]:
                    juntos[(a, b)] = juntos.get((a, b), 0) + 1
                    juntos[(b, a)] = juntos.get((b, a), 0) + 1
                    antes[(a, b)] = antes.get((a, b), 0) + 1      # a llegó primero (orden de primera_vez)
        self.p_primero = {a: (primero.get(a, 0) + SUAVIZADO * 0.1) / (n + SUAVIZADO) for a, n in aparece.items()}
        self.p_precede = {k: (antes.get(k, 0) + SUAVIZADO * 0.5) / (n + SUAVIZADO) for k, n in juntos.items()}
        self.log_frec = {a: float(np.log1p(n)) for a, n in frec.items()}
        self.p_primero_global = sum(primero.values()) / max(sum(aparece.values()), 1)
        self.ajustado = True
        return self

    # --------------------------------------------------------------------------------- tabla de candidatos
    def transform(self, df_inc):
        if not self.ajustado:
            raise RuntimeError("Llamar primero a fit() con los incidentes de entrenamiento.")
        t = col_tiempo(df_inc)
        filas = []
        for iid, g in df_inc.sort_values(t).groupby("incidente_id", sort=False):
            g = g.assign(_eq=g[COL_EQUIPO].astype(str), _nodo=g[COL_EQUIPO].astype(str).str.strip().map(eid))
            t0 = g[t].min()
            equipos = list(dict.fromkeys(g["_eq"]))
            nodos = dict(zip(g["_eq"], g["_nodo"]))
            sitios = dict(zip(g["_eq"], g[COL_SITIO].astype(str)))
            dist = {e: nx.single_source_shortest_path_length(self.F, nodos[e], cutoff=self.max_saltos * 2)
                    if nodos[e] in self.F else {} for e in equipos}
            saltos = {e: self.saltos_sitio.get(sitios[e], np.nan) for e in equipos}
            smin = np.nanmin(list(saltos.values())) if len(equipos) else np.nan
            tipos_inc = list(dict.fromkeys(g["alarmName"].astype(str)))
            for orden, e in enumerate(equipos, 1):
                ge = g[g["_eq"] == e]
                tipo1 = str(ge["alarmName"].iloc[0])
                otros = [o for o in equipos if o != e]
                d_otros = [dist[e].get(nodos[o]) for o in otros]
                d_ok = [d for d in d_otros if d is not None]
                abajo = sum(1 for o, d in zip(otros, d_otros)
                            if d is not None and d <= self.max_saltos and saltos[o] >= saltos[e])
                otros_tipos = [b for b in tipos_inc if b != tipo1]
                prec = [self.p_precede.get((tipo1, b), 0.5) for b in otros_tipos]
                rol = self.G.nodes[nodos[e]].get("rol") if nodos[e] in self.G else None
                fila = {
                    "incidente_id": iid, "equipo": e, "alarma": tipo1, "sitio": sitios[e],
                    # temporales
                    "t_rel_s": (ge[t].min() - t0).total_seconds() if pd.notna(t0) else np.nan,
                    "orden_llegada": orden, "pct_orden": orden / len(equipos),
                    "n_alarmas_eq": len(ge), "n_tipos_eq": ge["alarmName"].nunique(),
                    "sev_max": 4 - ge["severity"].min() if "severity" in ge and ge["severity"].notna().any() else np.nan,
                    # topológicas
                    "saltos_nucleo": saltos[e], "saltos_rel": saltos[e] - smin if pd.notna(saltos[e]) else np.nan,
                    "grado_fisico": self.F.degree(nodos[e]) if nodos[e] in self.F else 0,
                    "dependientes_sitio": self.dep_sitio.get(sitios[e], 0),
                    "n_alarmados_abajo": abajo, "pct_alarmados_abajo": abajo / max(len(otros), 1),
                    "dist_media_alarmados": float(np.mean(d_ok)) if d_ok else np.nan,
                    "n_equipos_incidente": len(equipos),
                    # co-ocurrencia (aprendidas en train)
                    "p_primero_tipo": self.p_primero.get(tipo1, self.p_primero_global),
                    "p_precede_media": float(np.mean(prec)) if prec else 0.5,
                    "log_frec_tipo": self.log_frec.get(tipo1, 0.0),
                    "tipo_visto_en_train": tipo1 in self.log_frec,
                    # semánticas
                    "rol": rol, "tipo_alarma_manual": self.cat.get(tipo1, "sin_catalogo"),
                }
                for r in ROLES:
                    fila[f"rol_{r}"] = int(rol == r)
                filas.append(fila)
        return pd.DataFrame(filas)


DICCIONARIO = [
    ("t_rel_s", "temporal", "Segundos entre la primera alarma del incidente y la primera alarma del equipo"),
    ("orden_llegada", "temporal", "Posición del equipo por hora de su primera alarma (1 = el primero)"),
    ("pct_orden", "temporal", "orden_llegada / nº de equipos del incidente"),
    ("n_alarmas_eq", "temporal", "Alarmas del equipo en el incidente"),
    ("n_tipos_eq", "temporal", "Tipos de alarma distintos del equipo"),
    ("sev_max", "temporal", "Severidad máxima del equipo (3 = crítica, 1 = menor)"),
    ("saltos_nucleo", "topológica", "Saltos del sitio del equipo al núcleo (ruta principal, sin RDNFO)"),
    ("saltos_rel", "topológica", "saltos_nucleo menos el mínimo del incidente (0 = el más aguas arriba)"),
    ("grado_fisico", "topológica", "Conexiones físicas del equipo en el grafo"),
    ("dependientes_sitio", "topológica", "Sitios que pierden conexión si cae el sitio del equipo"),
    ("n_alarmados_abajo", "topológica", "Otros equipos alarmados del incidente a <= 4 saltos e igual o más lejos del núcleo"),
    ("pct_alarmados_abajo", "topológica", "n_alarmados_abajo / (equipos del incidente − 1)"),
    ("dist_media_alarmados", "topológica", "Distancia media (saltos físicos) a los demás equipos alarmados"),
    ("n_equipos_incidente", "topológica", "Tamaño del incidente en equipos"),
    ("p_primero_tipo", "co-ocurrencia", "En train: P(este tipo de alarma es la primera del incidente | aparece)"),
    ("p_precede_media", "co-ocurrencia", "En train: P(este tipo llega antes que cada otro tipo del incidente), promedio"),
    ("log_frec_tipo", "co-ocurrencia", "En train: log(1 + apariciones del tipo)"),
    ("tipo_visto_en_train", "co-ocurrencia", "El tipo de alarma apareció en el entrenamiento"),
    ("rol / rol_*", "semántica", "Rol del equipo (router de transporte, de acceso, IDU de microondas, óptico)"),
    ("tipo_alarma_manual", "semántica", "Tipo según el catálogo del manual (Communication / Equipment / …)"),
]


def diccionario_features():
    return pd.DataFrame(DICCIONARIO, columns=["feature", "familia", "descripcion"])
