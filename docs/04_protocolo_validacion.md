# 04 · Protocolo de validación y verdad de referencia (Sprint 2)

> Entregable de la tarjeta *"Protocolo de validación"*. Fase CRISP-DM: **Evaluación** (diseño, antes de modelar).
> Código: `src/etiquetado.py`, `src/evaluacion.py`, `src/sintetico.py` · Notebook: `notebooks/05_etiquetado_validacion.ipynb`.

El protocolo se fija **antes** de construir el motor, para que ningún resultado del Sprint 3–4 pueda ajustar las reglas
de evaluación a posteriori.

## 1. Unidad de evaluación: el incidente

Una ventana de 15 min puede contener varias fallas independientes, y una falla afecta a varios equipos. Por eso no se
evalúa por ventana ni por alarma, sino por **incidente**:

> **Incidente** = conjunto de alarmas **nuevas** de una misma ventana cuyos equipos están conectados en el **grafo
> físico** a ≤ 4 saltos (router – IDU – enlace – IDU – router), con al menos 3 alarmas.

| Decisión | Motivo |
|---|---|
| Solo alarmas **nuevas** (no activas en la ventana anterior) | El NCE entrega una foto de las alarmas *activas*; una alarma crónica reaparece en decenas de ventanas. Contarla siempre inflaría los incidentes y filtraría la misma falla entre entrenamiento y prueba. |
| Conectividad en el grafo **físico** (no por sitio ni por anillo lógico) | Una avalancha se propaga por enlaces reales. Es la relación `conexionLocal`/`extremoDe`/`engancha` de la ontología (OE3). |
| Radio de 4 saltos | Cubre un salto de microondas completo y su router vecino; se revisará con la distribución real de tamaños de incidente. |
| Mínimo 3 alarmas | Con 1–2 alarmas no hay avalancha que diagnosticar (la causa es trivial). |
| Hora de cada alarma = `t_evento`: la primera marca (`occurUtc` → `latestOccurUtc` → `arriveUtc` → `firstOccurUtc`) que cae dentro de la ventana | En alarmas crónicas la ocurrencia puede ser de hace semanas. Si ninguna marca cae en la ventana, la alarma es **reaparecida** y no entra al incidente. La hora de la carpeta (local) se pasa a UTC con el desfase detectado (−5 h en Lima). |

## 2. Verdad de referencia en dos niveles

| Nivel | Quién la produce | Cobertura | Uso |
|---|---|---|---|
| **Plata** | Reglas causa→efecto de los **manuales de alarmas Huawei** (`docs/reglas_causales_huawei.csv`), aplicadas automáticamente | Todos los incidentes que tengan al menos una regla aplicable | Métricas con muchos casos; detectar dónde falla el motor |
| **Oro** | Especialista del **NOC** | Todos los incidentes si son ≤ 100; si no, 60 (30 más grandes + 30 al azar estratificados por red, semilla 42) | Métrica principal de la tesis; **valida a la plata** |

**Etiqueta plata (algoritmo).** En cada incidente: (1) se agrupan los sinónimos (`esSinonimoDe`); (2) se arma el grafo
causa→efecto (`puedeCausar`) entre los tipos de alarma presentes; (3) la causa raíz es el tipo que explica a otros y no es
explicado por ninguno; (4) si hay varios candidatos, se desempata por **topología** (sitio más cercano al núcleo = aguas
arriba) y luego por **hora**. Estado: `unica`, `desempate` o `sin_regla` (no se etiqueta). Las reglas sin fuente del
manual no se aceptan; las de criterio del NOC van con `confianza = baja`.

**Etiqueta oro (procedimiento).**
1. `seleccionar_muestra_oro` elige la muestra (reproducible).
2. `planilla_oro` genera `reports/sprint2/planilla_oro_NOC.xlsx` con tres hojas: *Instrucciones*, *Incidentes* (una fila
   por incidente; columnas amarillas a completar: `causa_raiz_equipo`, `causa_raiz_alarma`, `confianza`,
   `es_falla_real`, `comentario`, `etiquetado_por`) y *Alarmas* (todas las alarmas de la muestra, ordenadas por hora).
3. El NOC devuelve la planilla; se guarda como `data/interim/oro_noc_completada.xlsx` (dato, no se versiona; respaldar).
4. `cargar_oro` la lee (descarta `es_falla_real = No`) y `acuerdo_plata_oro` mide el acuerdo plata–oro.
5. Si dos especialistas etiquetan, se reporta el acuerdo entre ellos (κ de Cohen) sobre los incidentes comunes.

**Criterio de aceptación de la plata:** acuerdo de equipo con el oro ≥ 0,80. Si no se alcanza, la plata se usa solo como
métrica secundaria y se revisan las reglas que fallan.

## 3. Particiones temporales (sin mirar el futuro)

Validación con **origen móvil** (`folds_temporales`): las ventanas se ordenan por tiempo y los cortes se hacen por
**incidentes** (`pesos`): el primer 40 % de los incidentes solo entrena y el resto se divide en **4 bloques** consecutivos
con una cantidad parecida de incidentes (con cortes por ventanas, la primera corrida real dejó un bloque con solo 4). En cada fold se entrena con todo lo anterior al bloque (ventana
creciente) y se deja **1 ventana de margen** antes del bloque para que una falla larga no quede a ambos lados del corte.
Nunca se mezclan ventanas al azar: el modelo no puede ver el futuro. Semilla global **42**.

Esto reemplaza al corte único 70/15/15 de `marcar_particion_temporal` (Sprint 1), que se mantiene solo para el EDA.

## 4. Enmascaramiento (anti-fuga)

`enmascarar` quita, antes de entregar los datos a cualquier método, las columnas que delatarían la respuesta o que no
existen al momento del diagnóstico: reconocimiento del operador (*Acknowledgement Status*, `ack*`), comentarios, banderas
de alarma raíz o correlación del propio gestor (`root*`, `rca*`, `correlat*`) y hora de cierre (`clear*Utc/Time`). En el
export actual del NCE estos campos no vienen, pero el filtro queda activo por si cambian las descargas.

## 5. Métricas

Todo método devuelve un **ranking de equipos** por incidente. Se mide a nivel **equipo** (principal) y **equipo + alarma**.

| Métrica | Definición | Indicador de la operacionalización |
|---|---|---|
| **Top-1** (principal) | % de incidentes con la causa correcta en 1.ª posición | Exactitud del diagnóstico |
| Top-3, Top-5 | Causa entre las k primeras | Utilidad como lista corta para el operador |
| MRR | Media de 1/posición de la causa | Calidad global del ranking |
| Precision / Recall / F1 | Sobre la 1.ª propuesta: P = aciertos/incidentes con propuesta; R = aciertos/incidentes con verdad | Cobertura vs. acierto |
| Equipos revisados | Posición media de la causa (si no aparece: nº de candidatos + 1) | **Tiempo de localización** (proxy de MTTR) |
| Segundos por incidente | Tiempo de cómputo del método | Viabilidad operativa |

Cada Top-1 se reporta con **IC 95 % por bootstrap** (1 000 remuestreos de incidentes) y por fold (media ± desviación).

## 6. Métodos de comparación

| Código | Método | Estado |
|---|---|---|
| T0 | Operador ordenando por hora (`rankear(..., "temporal")`) | Implementado |
| T0′ | Más severa primero (`"severidad"`) | Implementado |
| T1 | Reglas topológicas: más aguas arriba primero (`"topologico"`) | Implementado |
| T2 | LLM sin contexto de grafo | Sprint 5 |
| T3 | Motor propuesto (Hawkes topológico + grafo), `src/motor.py` | v1 implementado (Sprint 2); mejoras Sprint 3 |

T3 se considera mejor si su Top-1 supera al mejor de T0–T1 y los IC 95 % no se solapan (o prueba de McNemar pareada por
incidente, p < 0,05).

## 7. Prueba de humo con avalanchas sintéticas

Mientras llegan las etiquetas del NOC, `src/sintetico.py` inyecta fallas conocidas sobre la **topología real**: falla un
equipo, los equipos aguas abajo a ≤ 4 saltos emiten alarmas de efecto con retardo por salto y *jitter*, a veces la raíz
llega tarde (el equipo pierde gestión) y se agrega ruido. Sirve solo para comprobar que el protocolo funciona de punta a
punta y que las métricas distinguen métodos; **no** es evidencia del desempeño en la red real. Sus tipos de alarma llevan
prefijo `SIM_` y fuente `SIMULACION`, y nunca se mezclan con las reglas de los manuales.

## 8. Pendientes

- Reglas v1 (RTN 320): extraídas con `src/manuales.py` → revisar `reports/sprint2/revision_reglas_RTN320.xlsx`; al
  correr el notebook 05 se cargan las aceptadas como versión `v1-RTN320`. Reglas v2 (ATN 910C, NE40E): pendientes de
  los manuales; prioridad según `reports/sprint2/cobertura_alarmas.csv`.
- Con solo la v1, la plata cubre sobre todo incidentes de acceso (microondas): las métricas con plata se reportan por red.
- Enviar la planilla oro al NOC y recibirla completada.
- Ajustar el radio (4 saltos) y el mínimo de alarmas con la distribución real de incidentes.
