# 06 · Motor de inferencia v1: Hawkes topológico (Sprint 2)

> Entregable de las tarjetas *"Baseline sólido: motor de inferencia v1"* y *"Resultados por fold (preliminares)"*.
> Fase CRISP-DM: **Modelado / Evaluación**. Código: `src/motor.py` · Notebook: `notebooks/07_motor_v1.ipynb` ·
> Resultados: `results/motor_v1/` · Registro: MLflow (`src/tracking.py`).

## 1. Modelo

Cada alarma *j* de un incidente aparece espontáneamente (tasa de fondo μ de su tipo, en alarmas/s) o fue disparada por
una alarma anterior *i* de un equipo cercano, con intensidad

  α[tipo_i → tipo_j] · β·exp(−β·(t_j − t_i + τ)) · γ^saltos(i, j)

| Elemento | Valor v1 | Origen |
|---|---|---|
| α (excitación entre tipos) | aprendido | EM sin etiquetas (Lewis & Mohler) |
| β (decaimiento temporal) | aprendido | EM (estimador de máxima verosimilitud del exponencial) |
| τ (tolerancia de *jitter*) | 30 s | hiperparámetro (Sprint 3: búsqueda) |
| γ (atenuación por salto) | 0,7 | hiperparámetro |
| máscara topológica | ≤ 8 saltos en el grafo físico | grafo de conocimiento (OE3) |
| dirección aguas abajo | padre con sitio igual o más cerca del núcleo | topología (no manuales) |

**Score de causa raíz** de una alarma = P(espontánea) × (1 + descendencia esperada), con descendencia = suma de la
matriz (I − P)⁻¹ − I de probabilidades de parentesco. El ranking de equipos toma el mejor score de cada equipo.
`explicar()` devuelve, para las causas propuestas, qué alarmas explican y con qué probabilidad (insumo del LLM, F5).

**Simplificaciones v1:** cada incidente es una realización independiente observada durante una ventana de 15 min; el
integral del núcleo se aproxima por las oportunidades de ser padre dentro del incidente.

## 2. Por qué no usa los manuales

Las reglas de los manuales son la etiqueta plata. Si entraran al motor, evaluarlo con plata sería circular. Una versión
híbrida posterior podrá usarlas como *prior* de α; se evaluará **solo con oro**, y la diferencia v1 → híbrido medirá
el aporte de los manuales (ablación).

## 3. Resultados preliminares (simulación, 4 folds de origen móvil)

| Método | Top-1 | Top-3 | MRR | Equipos revisados |
|---|---|---|---|---|
| **T3 · Hawkes topológico** | **0,914 ± 0,040** | 0,986 ± 0,017 | 0,944 ± 0,027 | 1,17 ± 0,09 |
| T3 sin dirección (ablación) | 0,857 ± 0,041 | 0,943 ± 0,033 | 0,907 ± 0,026 | 1,34 ± 0,10 |
| T0′ · más severa primero | 0,514 ± 0,070 | 0,893 ± 0,043 | 0,709 ± 0,037 | 1,88 ± 0,10 |
| T1 · más aguas arriba primero | 0,457 ± 0,052 | 0,928 ± 0,028 | 0,684 ± 0,027 | 1,90 ± 0,07 |
| T0 · por hora | 0,357 ± 0,076 | 0,764 ± 0,049 | 0,587 ± 0,046 | 2,62 ± 0,25 |

Tiempo de cómputo: ~3–5 ms por incidente en todos los métodos. McNemar T3 vs. T0′: 56 incidentes acierta solo T3,
0 solo T0′ (p < 0,001).

**Lectura cautelosa.** La mayor parte de la ganancia viene de lo que el motor aprende (qué tipo de alarma dispara a
cuál) combinado con el orden temporal; la restricción aguas abajo suma ~6 puntos (ablación). La simulación es más
ordenada que la red real (tipos de alarma bien separados entre raíz y efecto, propagación solo aguas abajo), así que
estas cifras son un techo optimista. Los resultados de la tesis serán los medidos con **oro** (y con **plata por
segmento**).

**Historial.** Una primera versión (27/10, mañana) daba 0,772 en la simulación: permitía ciclos de parentesco entre
alarmas casi simultáneas (por la tolerancia de *jitter*) y usaba la hora de primera ocurrencia aun en alarmas crónicas.
Al correrla en la red real produjo descendencias absurdas (miles de alarmas explicadas). Se corrigió: (1) el parentesco
es acíclico (el jitter solo permite como padre a un equipo estrictamente aguas arriba) y (2) la hora de cada alarma es la
marca que cae dentro de la ventana (`t_evento`); las alarmas cuya hora no cae en la ventana (reaparecidas) no entran a
los incidentes.

## 4. Registro de experimentos

Cada corrida registra parámetros, huellas de datos (`huella_dataset`, `huella_red_xlsx`, `huella_reglas`), versión de
reglas, tipo de verdad, métricas por fold y el modelo entrenado. Ver con
`mlflow ui --backend-store-uri sqlite:///mlruns/mlflow.db` desde la carpeta del repositorio.
