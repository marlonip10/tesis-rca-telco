# 05 · Ingeniería de atributos (Sprint 2)

> Entregable de la tarjeta *"Ingeniería de atributos (features)"*. Fase CRISP-DM: **Preparación de los datos**.
> Código: `src/features.py` · Notebook: `notebooks/06_features.ipynb` · Salida: `data/processed/features_candidatos.parquet`.

## 1. Unidad: el candidato

El motor no elige entre alarmas sueltas sino entre **candidatos**: cada **equipo con alarmas dentro de un incidente**
(`docs/04_protocolo_validacion.md`). La tabla tiene una fila por candidato con identificadores (`incidente_id`, `equipo`,
`alarma` = primera alarma del equipo, `sitio`, `fold`) y los atributos de abajo.

## 2. Diccionario

| Atributo | Familia | Definición |
|---|---|---|
| `t_rel_s` | Temporal | Segundos entre la primera alarma del incidente y la primera del equipo (según `t_evento`) |
| `orden_llegada` | Temporal | Posición del equipo por hora de su primera alarma (1 = el primero) |
| `pct_orden` | Temporal | `orden_llegada` / nº de equipos del incidente |
| `n_alarmas_eq` | Temporal | Alarmas del equipo en el incidente |
| `n_tipos_eq` | Temporal | Tipos de alarma distintos del equipo |
| `sev_max` | Temporal | Severidad máxima del equipo (3 = crítica … 1 = menor) |
| `saltos_nucleo` | Topológica | Saltos del sitio al núcleo por la ruta principal (sin RDNFO) |
| `saltos_rel` | Topológica | `saltos_nucleo` − mínimo del incidente (0 = el más aguas arriba) |
| `grado_fisico` | Topológica | Conexiones físicas del equipo en el grafo |
| `dependientes_sitio` | Topológica | Sitios que pierden conexión si cae el sitio del equipo (criticidad, notebook 04) |
| `n_alarmados_abajo` | Topológica | Otros equipos alarmados del incidente a ≤ 4 saltos e igual o más lejos del núcleo |
| `pct_alarmados_abajo` | Topológica | `n_alarmados_abajo` / (equipos del incidente − 1) |
| `dist_media_alarmados` | Topológica | Distancia media (saltos físicos) a los demás equipos alarmados |
| `n_equipos_incidente` | Topológica | Tamaño del incidente en equipos |
| `p_primero_tipo` | Co-ocurrencia | *Entrenamiento:* P(el tipo es la primera alarma del incidente \| aparece) |
| `p_precede_media` | Co-ocurrencia | *Entrenamiento:* P(el tipo llega antes que cada otro tipo del incidente), promedio |
| `log_frec_tipo` | Co-ocurrencia | *Entrenamiento:* log(1 + apariciones del tipo) |
| `tipo_visto_en_train` | Co-ocurrencia | El tipo de alarma apareció en el entrenamiento |
| `rol`, `rol_*` | Semántica | Rol del equipo (router de transporte / agregación / acceso, IDU de microondas, óptico) |
| `tipo_alarma_manual` | Semántica | Tipo según `docs/catalogo_alarmas.csv` (Communication, Equipment…); `sin_catalogo` si falta el manual |

## 3. Reglas de construcción

1. **Sin fuga temporal:** los atributos de co-ocurrencia se ajustan con `ExtractorFeatures.fit` solo con las ventanas de
   entrenamiento de cada fold y se aplican con `transform` a las de prueba. Suavizado de Laplace para tipos poco vistos.
2. **Sin circularidad:** las reglas causa→efecto de los manuales **no** son atributos; son la etiqueta plata.
   El catálogo solo aporta el *tipo* de alarma (dato descriptivo del manual, no la respuesta).
3. **Puerto:** queda como dato de cada alarma (`moi_puerto`, OE3); en v1 no se usa como atributo porque los puertos
   cambian por averías y la topología se modela a nivel de equipo y enlace.
4. **Distancias** en el grafo físico (relaciones `conexionLocal`, `extremoDe`, `engancha`); un salto de microondas
   router–IDU–enlace–IDU–router = 4.

## 4. Verificación

Con avalanchas simuladas sobre la topología real (notebook 06, §5), la causa raíz llega antes (`pct_orden` 0,42 vs. 0,60),
está más aguas arriba (`saltos_rel` 0,02 vs. 0,65) y tiene casi todos los demás equipos aguas abajo
(`pct_alarmados_abajo` 0,99 vs. 0,46). El simulador propaga solo aguas abajo, por lo que la separación topológica real
será menor; la medición definitiva se hará con las etiquetas oro.
