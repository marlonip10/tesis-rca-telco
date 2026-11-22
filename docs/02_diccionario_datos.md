# 02 · Diccionario de datos y reporte de calidad (Sprint 1)

> Entregable de la tarjeta *"Comprensión de los datos: inventario de alarmas y KPIs"*.
> Fase CRISP-DM: **Comprensión de los datos**.

## 1. Fuentes

Los datos se descargan de los **gestores Huawei NCE** cada 15 min. Cada descarga es una **ventana**
(carpeta `DD_MM_AAAA_HH_MM_SS`) y constituye la **unidad de análisis**. Dentro de cada ventana:

| Archivo | Contenido | Clave de unión |
|---|---|---|
| `Tx_Pasco.json` | Estado de routers de **transporte** | `NE IP Address` |
| `routers_ATN910_pasco.json` | Estado de routers de **acceso** (ATN910) | `NE IP Address` |
| `RTN_pasco.json` | Estado de **radios** de acceso (RTN) | `address` |
| `alarmas_tx_pasco.json` | Alarmas de **transporte** | `address` |
| `alarmas_ax_pasco.json` | Alarmas de **acceso (router)** | `address` |
| `alarmas_RTN_pasco.json` | Alarmas de **acceso (radio)** | `address` |

Fuera de las ventanas, en `data/raw/`: **`red.xlsx`**, con tres hojas: `nodos` (inventario de equipos),
`topologia_ax` (enlaces de la red de acceso) y `topologia_tx` (anillos/bus de la red de transporte). Las dos hojas de
topología son estáticas y se usan para construir el grafo (Sprint 2); la ingesta de alarmas solo usa `nodos`.

## 2. Esquema de las alarmas (campos)

| Campo | Tipo | Descripción | Naturaleza |
|---|---|---|---|
| `csn` | entero | Identificador de la alarma | estructurado |
| `cleared` | entero | 0 = activa, 1 = despejada | estructurado |
| `count` | entero | Nº de ocurrencias | estructurado |
| `arriveUtc` / `firstOccurUtc` / `occurUtc` / `latestOccurUtc` | fecha-hora | Marcas de tiempo | estructurado |
| `meName` | texto | Nombre del elemento de red | no estructurado |
| `meDn` / `meType` | texto | Identificadores internos | estructurado |
| `moi` | texto | **Objeto gestionado**: codifica subrack/placa/puerto (p. ej. `0-subrack-6-B1LDX-1(IN1/OUT1)-OCH:1`) | **semiestructurado** |
| `eventType` | texto | Tipo de evento (Equipment…) | estructurado |
| `alarmName` | texto | Nombre de la alarma | **no estructurado** |
| `alarmId` / `reasonId` | entero | Código de alarma / causa | estructurado |
| `probableCause` | texto | Causa probable (lenguaje natural) | **no estructurado** |
| `severity` | entero | 1 Crítica, 2 Mayor, 3 Menor, 4 Advertencia | estructurado |
| `additionalInformation` | texto | Información adicional | no estructurado |
| `productName` | texto | Modelo del equipo | estructurado |
| `address` | IP | **Identificador del equipo** (clave de unión) | estructurado |

## 3. Esquema de los estados de equipo

- `Tx_Pasco.json` / `routers_ATN910_pasco.json`: `NE IP Address`, `Communication Status`.
- `RTN_pasco.json`: `NE Name`, `Subrack Type`, `Communication Status`, `address`.

## 4. Inventario y topología (`red.xlsx`)

### 4.1 Hoja `nodos` — 9 columnas (una fila por equipo)

`CODIGO DE NODO`, `NOMBRE DE NODO`, `TIPO DE NODO`, **`Red`** (`tx` transporte / `ax` acceso), `CODIGO_Enlace`,
`EQUIPO`, `TIPO`, `NOMBRE DE EQUIPO EN RED`, `IP_Equipo`. Clave de unión con las alarmas: **`IP_Equipo = address`**.

A partir de `TIPO`, la ingesta deriva **`rol_equipo`**:

| `TIPO` | `rol_equipo` | Comentario |
|---|---|---|
| `ATN910C` | `router_acceso` | router de cada nodo de acceso |
| `1+0`, `1+0 XPIC`, `2+0 XPIC` | `microondas` | IDU RTN 320; un equipo por extremo de enlace (nombre = código del enlace) |
| `RTN510_AP` | `sectorial` | radio multipunto hacia clientes finales; **no afecta la conectividad del nodo** |
| `NE40E-M2K`, `NE40E-X3(V8)` | `router_transporte` | routers de los anillos de transporte |
| `NE40E-X8A(V8)` | `router_agregacion_acceso` | NOC Acceso (Pasco) |
| `OptiX OSN 1800 II TP` | `optico_transporte` | equipo óptico del NOC Transporte |

### 4.2 Hoja `topologia_ax` — enlaces de la red de acceso (topología en *clusters*)

| Campo | Descripción |
|---|---|
| `CLUSTER` | cluster al que pertenecen el enlace y sus nodos |
| `CODIGO_Enlace` | código del enlace entre Nodo_1 y Nodo_2 |
| `CODIGO DE NODO_1` / `_2` | extremos del enlace |
| `EQUIPO ROUTER EN NODO_1` / `_2` y `NOMBRE DE EQUIPO ROUTER EN NODO_1` / `_2` | router (ATN910C) de cada extremo |
| `EQUIPO MICROONDAS EN NODO_1` / `_2` y `NOMBRE DE EQUIPO MICROONDAS EN NODO_1` / `_2` | IDU de microondas de cada extremo (vacío si el enlace es por fibra) |
| `TIPO DE ENLACE` | `POR MICROONDAS` o `POR FIBRA OPTICA` (en fibra solo intervienen los routers) |

Los nodos distritales que son el único nodo de su cluster no aparecen en esta hoja, pero sí en `nodos`.

### 4.3 Hoja `topologia_tx` — anillos / bus de la red de transporte (fibra óptica)

`Anillo_Logico` (nº de anillo o nombre del bus), `CODIGO DE NODO1/2`, `NOMBRE DE NODO1/2`, `TIPO DE NODO1/2`,
`NOMBRE DE EQUIPO EN RED1/2` (router de cada extremo) y `Enlace` (siempre `Por FO`).

### 4.4 Observaciones de calidad del inventario

- Los nombres de equipo de las hojas de topología **cruzan al 100 %** con `NOMBRE DE EQUIPO EN RED` de `nodos`
  (routers de acceso, IDU de microondas y routers de transporte), así que cada alarma puede ubicarse en el grafo.
- 2 equipos con `IP_Equipo = PENDIENTE` (un enlace 2+0, IDU *SLAVE*): se excluyen del cruce.
- 2 equipos del **NOC Acceso** (NE40E-X8A) sin `CODIGO DE NODO`: sus alarmas entran, pero no pueden ubicarse en el
  grafo hasta asignarles un código.
- `topologia_tx` incluye el nodo externo **`RDNFO`** (Red Dorsal Nacional), que no está en `nodos`: es un nodo frontera.
- Un enlace de transporte figura en dos anillos lógicos: es un mismo tramo compartido por dos anillos lógicos.
- `Anillo_Logico` mezcla números y texto (`BUS 1 OXAPAMAPA`): se lee como texto.

## 5. Reglas de integración (resumen)

1. A cada archivo de alarmas se le añade el **estado del equipo** uniendo por IP.
2. Se **apilan** las alarmas de las 3 redes con una etiqueta `segmento_red`.
3. Se anexan al inicio las **columnas de nodo** de `red.xlsx` (incluida `Red`) y el **`rol_equipo`**, por
   `IP_Equipo = address`, y se marca `en_inventario` (si el equipo figura o no en `red.xlsx`).
4. Se añaden `ventana_id` y `ventana_ts` (identificación de la ventana).
5. **Filtro de alcance (`solo_inventario`, por defecto activo):** se conservan únicamente las alarmas cuyo
   equipo está en `red.xlsx`. Esto descarta las alarmas de **equipos de usuario final** (antenas CPE que cuelgan
   de la torre, que el NCE de RTN reporta pero que **no afectan la conectividad del nodo**) y las alarmas de
   **gestión (OSS)**. Se puede desactivar (`solo_inventario=False`) para auditar qué quedaría fuera.
6. **Separación de sectoriales (`excluir_sectoriales`, por defecto activo):** las alarmas de los equipos con
   `rol_equipo = sectorial` (RTN510_AP) no entran al dataset de causa raíz, porque no afectan la conectividad del
   nodo. Se guardan aparte (`alarmas_sectoriales.csv`) y se resumen en un **indicador de salud de clientes** por
   ventana (`sectoriales_por_ventana.csv`: sectoriales con alarma, nº de alarmas, nodos afectados).
7. Los equipos del inventario con IP no válida (p. ej. `PENDIENTE`) se excluyen del cruce y se informan.

(El pipeline reproducible está en `src/ingesta.py` y en el notebook del Sprint 1.)

## 6. Reporte de calidad (a completar al ejecutar)

> Estos valores se generan al correr el notebook sobre los datos reales. Plantilla:

- Nº de ventanas procesadas: `___`
- Nº total de alarmas consolidadas: `___`
- Alarmas por segmento (transporte / acceso-router / acceso-radio): `___ / ___ / ___`
- Alarmas por severidad (Crítica/Mayor/Menor/Advertencia): `___`
- **% de alarmas con IP sin nodo en `red.xlsx`** (a revisar): `___ %`
- Campos con más nulos: `___`
- Duplicados detectados: `___`

> **Nota de alcance:** este diccionario describe la **estructura de los datos que ingresan** (estable). El
> reporte de calidad con cifras reales se completa en la tarea de **EDA**, y la ingesta de la topología
> completa / anclaje a puerto corresponde a fases posteriores (F2 avanzada / F3).
