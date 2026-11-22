# 01 · Comprensión del negocio (Sprint 1)

> Entregable de la tarjeta *"Comprensión del negocio y objetivos de diagnóstico"*.
> Fase CRISP-DM: **Comprensión del negocio**.

## 1. Contexto

Operador de telecomunicaciones (red del departamento de **Pasco**, Perú). La operación se gestiona
desde un **NOC** que supervisa dos segmentos:

- **Red de transporte** (backbone de fibra óptica): routers/OTN de transporte (p. ej. OptiX OSN, NE40E).
- **Red de acceso**: routers de acceso (**ATN910**) y **radios de microondas** (**RTN**).

El estado y las alarmas de los equipos se obtienen por **web scraping a los gestores Huawei NCE**,
descargando cada **15 minutos** una *ventana* con el estado de los equipos y sus alarmas activas.

## 2. Problema

Ante una falla, un único evento raíz desencadena una **avalancha de alarmas** en muchos equipos
conectados. El operador debe revisar manualmente decenas o cientos de alarmas correlacionadas para
localizar la causa, lo que **eleva el MTTR** y depende del conocimiento experto no formalizado. La
correlación simple no basta: se necesita **causalidad** (qué alarma/equipo **causó** el resto).

## 3. Pregunta de diagnóstico

> Dada una ventana de alarmas de la red (transporte + acceso), **¿qué equipo/alarma es la causa raíz**
> que explica la avalancha, y con qué orden de probabilidad (Top-k)?

## 4. Objetivos

### Objetivo general
Desarrollar un modelo de **diagnóstico de causa raíz de fallas** para la red de transporte y acceso del
operador, basado en **grafos e inferencia causal**, que proponga la causa raíz más probable (Top-k) de
forma **interpretable** a partir de las alarmas, el estado de los equipos y la topología.

### Objetivos específicos (alineados a las fases de la metodología)
- **OE1 (F1 · Ontología):** formalizar el dominio mediante una ontología.
- **OE2 (F2 · Adquisición e integración):** consolidar las fuentes en un repositorio unificado y
  etiquetado.
- **OE3 (F3 · Grafo de conocimiento):** instanciar la ontología en un grafo de conocimiento a nivel de
  puerto. *Precisión (Sprint 2):* como los puertos migran por averías, el grafo modela sitios, equipos y
  extremos de enlace, y el **puerto queda registrado como dato de cada alarma** (`moi_puerto`).
- **OE4 (F4 · Motor de inferencia):** construir el motor de inferencia híbrido.
- **OE5 (F5 · GraphRAG + LLM):** integrar GraphRAG con el modelo de lenguaje.
- **OE6 (F6 · Evaluación):** construir el banco de evaluación operacional.

## 5. Criterios de éxito (métricas)

- **Primaria:** exactitud **Top-1** (la causa raíz correcta es la primera propuesta).
- **Secundarias:** Top-k (k=3,5), Precision/Recall/F1, tiempo de localización, cobertura.
- **Comparación:** NOC manual (T0) vs. reglas (T1) vs. LLM sin contexto (T2) vs. híbrido propuesto (T3).

## 6. Alcance y supuestos

- **Dentro de alcance:** diagnóstico sobre alarmas + estado de equipo + topología (`red.xlsx`) de
  transporte y acceso en Pasco.
- **Fuera de alcance (por ahora):** registros de energía externos, datos de tráfico, otras regiones.
- **Supuestos:** la topología de `red.xlsx` es correcta y vincula por `IP_Equipo`; las alarmas traen
  timestamp fiable (con posible *jitter* de colección); la causa raíz suele estar entre las alarmas de
  la propia ventana.

## 7. Riesgos

| Riesgo | Mitigación |
|---|---|
| Pocas etiquetas de causa raíz | Motor **no supervisado** (Hawkes topológico); verdad de referencia construida con el NOC |
| *Jitter* / desorden temporal de alarmas | Usar un modelo que tolere retardos (TTHP) y ventanas |
| IPs de alarma que no están en el inventario | Reporte de calidad; completar `red.xlsx` |
| Diagnóstico a nivel de puerto vs. tipo de alarma | Anclar la alarma al puerto vía campo `moi` + topología |

## 8. Stakeholders

- **Operadores del NOC:** usuarios finales; validan la pertinencia de las hipótesis de causa raíz.
- **Gerencia de operaciones:** interesada en reducir el MTTR y en la escalabilidad del diagnóstico.
- **Asesor de tesis / jurado:** criterios metodológicos y de evaluación.
