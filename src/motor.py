# -*- coding: utf-8 -*-
"""
Motor de inferencia v1 (Sprint 2): proceso de Hawkes TOPOLÓGICO para ordenar la causa raíz de cada incidente.

Idea. Cada alarma de un incidente o bien aparece «espontáneamente» (tasa de fondo μ de su tipo) o bien fue
«disparada» por una alarma anterior de un equipo cercano. La influencia de i sobre j es
        α[tipo_i, tipo_j] · g(t_j − t_i) · γ^saltos(i, j)
  α : cuánto excita un tipo de alarma a otro (se APRENDE de los datos, sin etiquetas)
  g : núcleo exponencial con tolerancia al jitter de colección: i puede ser padre de j aunque llegue hasta
      τ segundos DESPUÉS (g(Δ) = β·exp(−β·(Δ+τ)) para Δ ≥ −τ)
  γ : atenuación por cada salto físico en el grafo (máscara topológica: más de `max_saltos` → sin influencia)
El entrenamiento es EM (Lewis & Mohler): el paso E reparte cada alarma entre «fondo» y sus posibles padres; el paso
M reestima μ, α y β. La causa raíz de un incidente es la alarma con alta probabilidad de ser espontánea Y que
explica a muchas otras (descendencia esperada):  score = P(fondo) · (1 + descendencia esperada).

No usa etiquetas ni las reglas de los manuales (que son la verdad «plata»): es comparable con plata y oro sin
circularidad. Simplificaciones de la v1 (documentadas en docs/06_motor_v1.md): cada incidente es una realización
independiente; el integral del núcleo se aproxima por el nº de hijos posibles dentro del incidente.

Uso:
    m = HawkesTopologico(G).fit(df_inc_train)
    ranking = m.rankear(df_inc_test)              # incidente_id, equipo, alarma, rank, score, metodo, seg
    m.explicar(df_inc_test, "07_10_2026_11_00_00#1")
"""
import time
from collections import defaultdict

import numpy as np
import pandas as pd
import networkx as nx

try:
    from .grafo import subgrafo_fisico, eid, grafo_sitios, jerarquia_sitios
    from .etiquetado import col_tiempo, COL_EQUIPO
except ImportError:
    from grafo import subgrafo_fisico, eid, grafo_sitios, jerarquia_sitios
    from etiquetado import col_tiempo, COL_EQUIPO


class HawkesTopologico:
    def __init__(self, G, max_saltos=8, gamma=0.7, tau=30.0, beta0=1 / 120, iteraciones=50, alpha0=0.05,
                 alpha_inicial=0.5, suavizado=1.0, ventana_s=900.0, direccional=True, holgura_saltos=0, J=None,
                 tol=1e-4, semilla=42):
        """max_saltos: saltos en el grafo físico (equipo–enlace–equipo cuenta 2); gamma: atenuación por salto;
        tau: tolerancia de jitter (s); beta0: tasa inicial del núcleo (1/s); alpha0 y suavizado: prior de α para
        pares de tipos poco vistos; alpha_inicial: arranque del EM; ventana_s: duración de observación de cada
        incidente (una ventana de 15 min), para que μ quede en alarmas por segundo como el núcleo;
        direccional: una alarma solo puede ser «padre» de otra si su sitio no está más lejos del núcleo que el del
        hijo (+ holgura_saltos): las fallas se propagan aguas abajo (conocimiento topológico, no de los manuales)."""
        self.G = G
        self.F = subgrafo_fisico(G)
        self.max_saltos, self.gamma, self.tau = max_saltos, gamma, tau
        self.beta, self.iteraciones, self.alpha0, self.suavizado, self.tol = beta0, iteraciones, alpha0, suavizado, tol
        self.alpha_inicial, self.T, self.semilla = alpha_inicial, ventana_s, semilla
        self.direccional, self.holgura = direccional, holgura_saltos
        J = J if J is not None else jerarquia_sitios(G, grafo_sitios(G))
        nivel = dict(zip(J["sitio"], J["saltos_al_nucleo"]))
        self.nivel_eq = {n: nivel.get(d.get("sitio"), np.nan) for n, d in G.nodes(data=True) if d.get("clase") == "Equipo"}
        self._dist_cache = {}
        self.historial = []

    def params(self):
        return {"direccional": self.direccional, "holgura_saltos": self.holgura,
                "max_saltos": self.max_saltos, "gamma": self.gamma, "tau": self.tau, "iteraciones": self.iteraciones,
                "alpha0": self.alpha0, "alpha_inicial": self.alpha_inicial, "suavizado": self.suavizado,
                "ventana_s": self.T}

    # ----------------------------------------------------------------------------------------- utilidades
    def _dist(self, u):
        if u not in self._dist_cache:
            self._dist_cache[u] = (nx.single_source_shortest_path_length(self.F, u, cutoff=self.max_saltos)
                                   if u in self.F else {u: 0})
        return self._dist_cache[u]

    def _eventos(self, df_inc):
        """Por incidente: arrays de tiempos (s), tipos, equipos y los pares candidatos padre→hijo (i→j).

        Los eventos se ordenan por (hora, nivel topológico). Un evento puede ser padre de otro posterior en ese
        orden (respetando la dirección aguas abajo). Por el jitter, un evento que llegó hasta τ s DESPUÉS también
        puede ser padre, pero solo si está estrictamente aguas arriba: así nunca hay ciclos (el grafo de
        parentesco es acíclico y la descendencia esperada queda acotada)."""
        t = col_tiempo(df_inc)
        out = []
        for iid, g in df_inc.groupby("incidente_id", sort=False):
            nodos_g = [eid(str(e).strip()) for e in g[COL_EQUIPO]]
            g = g.assign(_nivel=[self.nivel_eq.get(n, np.nan) for n in nodos_g], _n=nodos_g)
            g = g.sort_values([t, "_nivel"], kind="mergesort", na_position="last")
            ts = (g[t] - g[t].min()).dt.total_seconds().to_numpy(float)
            tipos = g["alarmName"].astype(str).to_numpy()
            eqs = g[COL_EQUIPO].astype(str).to_numpy()
            nodos = g["_n"].tolist()
            nivel = g["_nivel"].to_numpy(float)
            n = len(ts)
            pares = []                                   # (i, j, Δ+τ, saltos)
            for j in range(n):
                dj = self._dist(nodos[j])
                for i in range(n):
                    if i == j:
                        continue
                    h = dj.get(nodos[i])
                    if h is None:
                        continue
                    conocido = nivel[i] == nivel[i] and nivel[j] == nivel[j]
                    if i < j:                                         # anterior en el orden
                        if self.direccional and conocido and nivel[i] > nivel[j] + self.holgura:
                            continue
                    else:                                             # posterior: solo por jitter
                        if not (self.direccional and conocido and nivel[i] < nivel[j] - self.holgura
                                and ts[i] - ts[j] <= self.tau):
                            continue
                    pares.append((i, j, ts[j] - ts[i] + self.tau, h))
            out.append({"incidente_id": iid, "t": ts, "tipo": tipos, "equipo": eqs,
                        "pares": np.array(pares, dtype=float).reshape(-1, 4)})
        return out

    def _estep(self, ev):
        """Probabilidades: fondo p0[j] y padre P[i, j]."""
        n = len(ev["t"])
        P = np.zeros((n, n))
        mu = np.array([self.mu.get(a, self.mu_def) for a in ev["tipo"]])
        if len(ev["pares"]):
            i, j, d, h = ev["pares"].T
            i, j = i.astype(int), j.astype(int)
            a = np.array([self.alpha.get((ev["tipo"][x], ev["tipo"][y]), self._alpha_def) for x, y in zip(i, j)])
            P[i, j] = a * self.beta * np.exp(-self.beta * d) * self.gamma ** h
        tot = mu + P.sum(axis=0)
        return mu / tot, P / tot

    # ----------------------------------------------------------------------------------------- entrenamiento
    def fit(self, df_inc):
        eventos = self._eventos(df_inc)
        n_tipo = defaultdict(int)
        for ev in eventos:
            for a in ev["tipo"]:
                n_tipo[a] += 1
        n_inc = max(len(eventos), 1)
        escala = n_inc * self.T                                    # μ en alarmas por segundo y por incidente
        self.mu = {a: 0.5 * c / escala for a, c in n_tipo.items()}   # inicial: la mitad de las alarmas es fondo
        self.mu_def = 1.0 / escala
        self.alpha, self._alpha_def = {}, self.alpha_inicial
        prev = None
        for it in range(self.iteraciones):
            s_fondo, s_alpha, s_d, s_p = defaultdict(float), defaultdict(float), 0.0, 0.0
            hijos_pos = defaultdict(float)                         # oportunidades de cada tipo como padre
            loglik = 0.0
            for ev in eventos:
                p0, P = self._estep(ev)
                for a, p in zip(ev["tipo"], p0):
                    s_fondo[a] += p
                if len(ev["pares"]):
                    i, j, d, _ = ev["pares"].T
                    i, j = i.astype(int), j.astype(int)
                    pij = P[i, j]
                    for x, y, p in zip(i, j, pij):
                        s_alpha[(ev["tipo"][x], ev["tipo"][y])] += p
                    s_d += float((pij * d).sum()); s_p += float(pij.sum())
                    for x in set(i.tolist()):
                        hijos_pos[ev["tipo"][x]] += 1
                mu = np.array([self.mu.get(a, self.mu_def) for a in ev["tipo"]])
                loglik += float(np.log(np.maximum(mu / np.maximum(p0, 1e-12), 1e-300)).sum())
            # paso M
            self.mu = {a: max(s_fondo[a], 1e-3) / escala for a in n_tipo}
            self._alpha_def = self.alpha0
            self.alpha = {k: (v + self.suavizado * self.alpha0) / (n_tipo[k[0]] + self.suavizado)
                          for k, v in s_alpha.items()}
            if s_p > 0 and s_d > 0:
                self.beta = s_p / s_d
            self.historial.append({"iteracion": it + 1, "loglik": loglik, "beta": self.beta})
            if prev is not None and abs(loglik - prev) < self.tol * max(abs(prev), 1):
                break
            prev = loglik
        self.n_incidentes_train = len(eventos)
        return self

    # ----------------------------------------------------------------------------------------- inferencia
    def _scores(self, ev):
        p0, P = self._estep(ev)
        n = len(p0)
        # descendencia esperada: D = P + P² + … = (I − P)⁻¹ − I   (P es estrictamente «hacia adelante», converge)
        try:
            D = np.linalg.solve(np.eye(n) - P, np.eye(n)) - np.eye(n)
        except np.linalg.LinAlgError:
            D = P.copy()
        desc = D.sum(axis=1)
        return p0, desc, p0 * (1 + desc), P

    def rankear(self, df_inc):
        """Ranking de equipos por incidente (mismo formato que evaluacion.rankear)."""
        salida = []
        for ev in self._eventos(df_inc):
            t0 = time.perf_counter()
            p0, desc, sc, _ = self._scores(ev)
            d = pd.DataFrame({"equipo": ev["equipo"], "alarma": ev["tipo"], "score": sc, "p_fondo": p0,
                              "explica": desc, "t": ev["t"]})
            d = d.sort_values(["score", "t"], ascending=[False, True]).drop_duplicates("equipo")
            d["rank"] = np.arange(1, len(d) + 1)
            d["incidente_id"] = ev["incidente_id"]
            d["metodo"] = "hawkes_topologico"
            d["seg"] = time.perf_counter() - t0
            salida.append(d.drop(columns="t"))
        cols = ["incidente_id", "equipo", "alarma", "rank", "score", "p_fondo", "explica", "metodo", "seg"]
        return pd.concat(salida, ignore_index=True)[cols] if salida else pd.DataFrame(columns=cols)

    def explicar(self, df_inc, incidente_id, top=3, umbral=0.2):
        """Explicación legible: para las `top` causas propuestas, qué alarmas explican y con qué probabilidad."""
        sub = df_inc[df_inc["incidente_id"] == incidente_id]
        ev = self._eventos(sub)[0]
        p0, desc, sc, P = self._scores(ev)
        orden = np.argsort(-sc)
        vistos, filas = set(), []
        for i in orden:
            if ev["equipo"][i] in vistos:
                continue
            vistos.add(ev["equipo"][i])
            hijos = [f"{ev['tipo'][j]} @ {ev['equipo'][j]} ({P[i, j]:.0%})" for j in np.argsort(-P[i]) if P[i, j] >= umbral]
            filas.append({"rank": len(vistos), "equipo": ev["equipo"][i], "alarma": ev["tipo"][i],
                          "score": round(float(sc[i]), 3), "p_espontanea": round(float(p0[i]), 2),
                          "alarmas_que_explica": round(float(desc[i]), 1),
                          "explica_directamente": "; ".join(hijos) or "—"})
            if len(filas) >= top:
                break
        return pd.DataFrame(filas)

    def matriz_alpha(self, min_alpha=0.05):
        """Pares de tipos aprendidos (quién excita a quién), ordenados: material para comparar con los manuales."""
        a = pd.DataFrame([(k[0], k[1], v) for k, v in self.alpha.items()], columns=["tipo_causa", "tipo_efecto", "alpha"])
        return a[a["alpha"] >= min_alpha].sort_values("alpha", ascending=False).reset_index(drop=True)
