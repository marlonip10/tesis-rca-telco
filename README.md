# Tesis MIA — Diagnóstico de causalidad de fallas en una red telco usando grafos

Repositorio de trabajo de la tesis. Metodología **CRISP-DM**, organizada por sprints
(ver tablero de Trello). Arquitectura híbrida/neuro-simbólica:
**ontología + grafo de conocimiento + motor de inferencia causal (Hawkes topológico ↔ GNN) + GraphRAG/LLM**.

## Estructura del repositorio

Leyenda: ✓ se publica en GitHub · X solo en la máquina autorizada (datos del operador, no se versiona)

```
tesis-rca-telco/
├── README.md            ✓ este archivo
├── RESULTADOS.md        ✓ resumen público de resultados del motor v1 (cifras agregadas y gráficos)
├── requirements.txt     ✓ librerías de Python
├── .gitignore           ✓ reglas de lo que NO se sube
├── config/
│   └── reglas_red.ejemplo.json   ✓ plantilla con valores ficticios de reglas_red.json
├── data/                X nada de esta carpeta se publica (solo los .gitkeep)
│   ├── raw/             X ventanas DD_MM_AAAA_HH_MM_SS/*.json + red.xlsx + reglas_red.json
│   ├── interim/         X intermedios + etiquetas oro devueltas por el NOC (oro_noc_completada.xlsx)
│   └── processed/       X salidas regenerables: dataset_consolidado.*, alarmas_sectoriales.csv, grafo_v1.*
├── notebooks/           ✓ sin salidas (nbstripout) · 01 ingesta · 02 EDA · 03 baseline · 04 grafo
│                        #   05 etiquetado y validación · 06 features · 07 motor v1 + resultados por fold
├── src/                 ✓ ingesta.py · baseline.py · grafo.py · visual.py (grafos interactivos)
│                        #   etiquetado.py (incidentes, plata/oro) · evaluacion.py (folds, métricas)
│                        #   sintetico.py (avalanchas simuladas) · manuales.py (reglas desde PDF Huawei)
│                        #   features.py · motor.py (Hawkes topológico) · tracking.py (MLflow)
├── docs/                ✓ entregables (Markdown) + ontología (.ttl) + catálogo y reglas de los manuales (.csv)
│   ├── figuras/         X figuras con la topología (grafo_*.png)
│   └── figuras_publicas/ ✓ figuras sin datos de la red (arquitectura, metodología, resultados simulados)
├── reports/             X vistas interactivas (.html), planillas (.xlsx) y demos por sprint
│   ├── sprint1/
│   └── sprint2/
├── results/
│   ├── baseline/        X rankings por ventana (listan equipos)
│   └── motor_v1/        ✓ métricas por partición de la simulación (resumen_*.csv, resumen_motor_v1.json)
└── mlruns/              X base local de MLflow y modelos .pkl: `mlflow ui --backend-store-uri sqlite:///mlruns/mlflow.db`
```

## Reglas de versionado (importante)

- **Repositorio público sin datos del operador.** No se suben las ventanas del NCE, `red.xlsx`, las reglas de
  conexión de la red (`data/raw/reglas_red.json`), las vistas HTML, las planillas, los modelos `.pkl` ni la base de
  MLflow: todos llevan topología, IPs o nombres de equipos. Las credenciales del scraping nunca entran al repositorio.
- Los notebooks se publican **sin salidas** (filtro `nbstripout`); los resultados agregados están en
  [`RESULTADOS.md`](RESULTADOS.md) y en `results/motor_v1/`.
- `data/raw/reglas_red.json` guarda las excepciones de enganche y los enlaces ópticos confirmados con el NOC;
  su formato está en `config/reglas_red.ejemplo.json` (valores ficticios).
- Lo que está en `data/processed/` se regenera corriendo los notebooks 01 y 04 (o `python src/ingesta.py` y
  `python src/grafo.py`).

## Puesta en marcha

```bash
python -m venv .venv            # en Windows: C:\Entornos_PY\venv_tesis_rca
pip install -r requirements.txt
# copiar las ventanas, red.xlsx y reglas_red.json en data/raw/ y correr los notebooks en orden: 01 → 02 → 03 → 04 → 05 → 06 → 07
# ver los experimentos registrados:  mlflow ui --backend-store-uri sqlite:///mlruns/mlflow.db
```

## Mapa tarjeta de Trello → entregable

### Sprint 1 (cerrado)

| Tarjeta | Entregable en el repo |
|---|---|
| Comprensión del negocio y objetivos | `docs/01_comprension_negocio.md` |
| Comprensión de los datos: inventario | `docs/02_diccionario_datos.md` (+ Word/PDF en `reports/sprint1/`) |
| Script reproducible de ingesta + preprocesado | `src/ingesta.py` + `notebooks/01_presentacion_dataset.ipynb` |
| Notebook de EDA | `notebooks/02_eda.ipynb` |
| Baseline mínimo + logs | `src/baseline.py` + `notebooks/03_baseline.ipynb` + `results/baseline/` |
| Demo interna Sprint 1 | `reports/sprint1/` (guion, PPT de operacionalización, PPT del problema) |

### Sprint 2 (en curso)

| Tarjeta | Entregable en el repo |
|---|---|
| Grafo de conocimiento v1 + ontología mínima | `docs/03_ontologia.md` + `docs/ontologia_v1.ttl` + `src/grafo.py` + `src/visual.py` + `notebooks/04_grafo.ipynb` + vistas `reports/sprint2/grafo_*.html` |
| Protocolo de validación | `docs/04_protocolo_validacion.md` + `docs/reglas_causales_huawei.csv` + `docs/catalogo_alarmas.csv` + `src/etiquetado.py` + `src/evaluacion.py` + `src/sintetico.py` + `src/manuales.py` + `notebooks/05_etiquetado_validacion.ipynb` + `reports/sprint2/planilla_oro_NOC.xlsx` + `reports/sprint2/revision_reglas_RTN320.xlsx` |
| Ingeniería de atributos (features) | `docs/05_features.md` + `src/features.py` + `notebooks/06_features.ipynb` |
| Motor de inferencia v1 | `docs/06_motor_v1.md` + `src/motor.py` + `notebooks/07_motor_v1.ipynb` |
| Resultados por fold (preliminares) | `notebooks/07_motor_v1.ipynb` §6–7 + `results/motor_v1/` (simulación; oro/plata cuando estén) |
| Demo + breve informe | *pendiente* → `reports/sprint2/` |

### Sprint 3 (adelantado)

| Tarjeta | Entregable en el repo |
|---|---|
| Primeros experimentos de MLOps | `src/tracking.py` (MLflow: parámetros, métricas por fold, huellas de datos, versión de reglas, modelo) · *pendiente:* versionado de datasets con DVC |

## Grafos interactivos

`src/visual.py` genera páginas HTML con vis-network (incrustada desde el paquete `pyvis`, funciona sin internet): se
arrastran los nodos, se acerca con la rueda, al pasar el cursor aparecen los datos, se busca por código y se filtra
desde la leyenda. En el notebook se ven con `mostrar(html)`; `guardar_html` las deja en `reports/sprint2/`.

## Convención de commits

`tipo(sprintX): descripción` — tipos: `feat`, `fix`, `docs`, `data`, `chore`, `exp`.
Ej.: `feat(sprint2): grafo de conocimiento v1 y ontología mínima`.
Se recomienda referenciar la tarjeta de Trello en el cuerpo del commit.
