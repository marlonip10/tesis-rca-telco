# -*- coding: utf-8 -*-
"""
Avalanchas SINTÉTICAS sobre la topología real (prueba de humo del protocolo de validación).

Sirve para comprobar que incidentes, etiquetas, folds y métricas funcionan de punta a punta ANTES de tener
las etiquetas del NOC. Las causas raíz son conocidas porque se inyectan. Los tipos de alarma y la regla de
propagación son una SIMPLIFICACIÓN didáctica (no son las reglas de los manuales Huawei) y llevan
fuente="SIMULACION"; nunca se mezclan con docs/reglas_causales_huawei.csv.

Uso:
    from src.sintetico import simular_avalanchas
    df_sim, verdad, reglas_sim = simular_avalanchas(G, n_ventanas=200)
"""
import numpy as np
import pandas as pd
import networkx as nx

try:
    from .grafo import subgrafo_fisico, grafo_sitios, jerarquia_sitios
except ImportError:
    from grafo import subgrafo_fisico, grafo_sitios, jerarquia_sitios

SEED = 42

# alarma raíz por rol del equipo que falla y alarma que ven los equipos afectados (por rol del afectado)
RAIZ_POR_ROL = {"router_transporte": "SIM_PUERTO_CAIDO", "router_acceso": "SIM_PUERTO_CAIDO",
                "router_agregacion_acceso": "SIM_PUERTO_CAIDO", "microondas": "SIM_RADIO_SIN_SENAL",
                "optico_transporte": "SIM_FIBRA_SIN_LUZ"}
EFECTO_POR_ROL = {"router_transporte": "SIM_VECINO_CAIDO", "router_acceso": "SIM_GESTION_PERDIDA",
                  "router_agregacion_acceso": "SIM_VECINO_CAIDO", "microondas": "SIM_RADIO_REMOTO_FALLA",
                  "optico_transporte": "SIM_FIBRA_REMOTA"}
RUIDO = ["SIM_TEMPERATURA_ALTA", "SIM_VENTILADOR", "SIM_UMBRAL_TRAFICO"]


def reglas_simulacion():
    """Reglas causa→efecto que usa el simulador (formato de docs/reglas_causales_huawei.csv)."""
    filas = [{"alarma_causa": c, "alarma_efecto": e, "relacion": "puedeCausar", "alcance": "vecindad",
              "fuente": "SIMULACION", "confianza": "alta"}
             for c in sorted(set(RAIZ_POR_ROL.values())) for e in sorted(set(EFECTO_POR_ROL.values()))]
    return pd.DataFrame(filas)


def simular_avalanchas(G, n_ventanas=200, p_falla=0.6, max_saltos=4, p_propagar=0.85, ruido_por_ventana=3,
                       jitter_s=40, p_raiz_tardia=0.15, inicio="2026-01-01 00:00", semilla=SEED):
    """Genera alarmas con causa raíz conocida.

    En cada ventana de 15 min, con probabilidad p_falla falla un equipo: emite su alarma raíz y los equipos
    aguas abajo (igual o más lejos del núcleo) dentro de `max_saltos` saltos físicos emiten la alarma de efecto
    con probabilidad p_propagar, con retardo creciente por salto y jitter de colección (puede invertir el
    orden). Con probabilidad p_raiz_tardia la alarma raíz llega tarde (el equipo que falla pierde gestión),
    y la severidad de los efectos puede ser igual a la de la raíz: así ni «la primera» ni «la más severa»
    aciertan siempre. Se agregan alarmas de ruido aisladas.
    Devuelve (df_sim, verdad, reglas): verdad = ventana_id, causa_raiz_equipo, causa_raiz_alarma.
    """
    rng = np.random.default_rng(semilla)
    F = subgrafo_fisico(G)
    J = jerarquia_sitios(G, grafo_sitios(G))
    nivel = dict(zip(J["sitio"], J["saltos_al_nucleo"]))
    equipos = [n for n, d in G.nodes(data=True) if d.get("clase") == "Equipo" and d.get("rol") in RAIZ_POR_ROL
               and n in F]
    attr = {n: G.nodes[n] for n in equipos}
    filas, verdad = [], []
    t0 = pd.Timestamp(inicio, tz="UTC")
    for i in range(n_ventanas):
        ts = t0 + pd.Timedelta(minutes=15 * i)
        vid = ts.strftime("%d_%m_%Y_%H_%M_%S")

        def alarma(n, nombre, seg, sev):
            a = attr[n]
            filas.append({"ventana_id": vid, "ventana_ts": ts.tz_localize(None), "CODIGO DE NODO": a.get("sitio"),
                          "NOMBRE DE EQUIPO EN RED": a["nombre"], "Red": a.get("red"),
                          "rol_equipo": a.get("rol"), "alarmName": nombre, "severity": sev,
                          "severidad_txt": {1: "Crítica", 2: "Mayor", 3: "Menor"}[sev],
                          "moi": f"SIM={len(filas)}",
                          "firstOccurUtc": ts - pd.Timedelta(minutes=15) + pd.Timedelta(seconds=float(min(seg, 895)))})
        if rng.random() < p_falla:
            raiz = equipos[rng.integers(len(equipos))]
            n_raiz = nivel.get(attr[raiz].get("sitio"), 0)
            tardia = 120 if rng.random() < p_raiz_tardia else 0
            alarma(raiz, RAIZ_POR_ROL[attr[raiz]["rol"]], rng.uniform(0, 60) + tardia, 1)
            verdad.append({"ventana_id": vid, "causa_raiz_equipo": attr[raiz]["nombre"],
                           "causa_raiz_alarma": RAIZ_POR_ROL[attr[raiz]["rol"]]})
            for v, h in nx.single_source_shortest_path_length(F, raiz, cutoff=max_saltos).items():
                if v == raiz or v not in attr or nivel.get(attr[v].get("sitio"), 0) < n_raiz:
                    continue
                if rng.random() < p_propagar:
                    alarma(v, EFECTO_POR_ROL[attr[v]["rol"]], rng.uniform(0, 60) + 20 * h + rng.normal(0, jitter_s),
                           int(rng.choice([1, 2], p=[0.5, 0.5])))
        for _ in range(rng.poisson(ruido_por_ventana)):
            alarma(equipos[rng.integers(len(equipos))], RUIDO[rng.integers(len(RUIDO))], rng.uniform(0, 900), 3)
    df = pd.DataFrame(filas)
    return df, pd.DataFrame(verdad), reglas_simulacion()
