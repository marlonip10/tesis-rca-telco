# -*- coding: utf-8 -*-
"""
Registro de experimentos (Sprint 3 · MLOps): cada corrida guarda parámetros, métricas por fold, huellas de los datos,
versión de las reglas y el modelo entrenado.

Usa MLflow si está instalado (base local `mlruns/mlflow.db`, se ve con `mlflow ui`); si no, deja todo en
`results/experimentos.jsonl`. En ambos casos el resumen también queda en `results/<experimento>/`.

Uso:
    from src.tracking import Corrida, huella_archivo, huella_df
    with Corrida("motor_v1", params={...}, tags={...}, raiz=ROOT) as run:
        run.metricas({"top_1": 0.61}, paso=1)
        run.modelo(motor, "motor_v1")
        run.artefacto("results/motor_v1/por_fold.csv")
Ver la interfaz:  mlflow ui --backend-store-uri sqlite:///mlruns/mlflow.db   (desde la carpeta del repo)
"""
import hashlib
import json
import os
import pickle
import platform
import time
from datetime import datetime
from pathlib import Path

import pandas as pd


def huella_archivo(ruta, n=12):
    """SHA-256 abreviado de un archivo, o de una carpeta (nombres + tamaños + fechas de sus archivos: rápido)."""
    p = Path(ruta)
    if not p.exists():
        return "no_existe"
    h = hashlib.sha256()
    if p.is_file():
        with open(p, "rb") as f:
            for bloque in iter(lambda: f.read(1 << 20), b""):
                h.update(bloque)
    else:
        for q in sorted(p.rglob("*")):
            if q.is_file():
                st = q.stat()
                h.update(f"{q.relative_to(p)}|{st.st_size}|{int(st.st_mtime)}".encode())
    return h.hexdigest()[:n]


def huella_df(df, n=12):
    """Huella del contenido de un DataFrame (independiente de cómo se guardó)."""
    if df is None or len(df) == 0:
        return "vacio"
    v = pd.util.hash_pandas_object(df.reset_index(drop=True).astype(str), index=False).values
    return hashlib.sha256(v.tobytes()).hexdigest()[:n]


def _mlflow():
    try:
        os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")
        import logging
        for nombre in ("mlflow", "alembic"):
            logging.getLogger(nombre).setLevel(logging.WARNING)
        import mlflow
        return mlflow
    except Exception:
        return None


class Corrida:
    """Una corrida de un experimento. Se usa con `with`."""

    def __init__(self, experimento, params=None, tags=None, raiz=".", nombre=None, usar_mlflow=True):
        self.raiz = Path(raiz)
        self.experimento = experimento
        self.params = {k: (v if isinstance(v, (int, float, str, bool)) else str(v)) for k, v in (params or {}).items()}
        self.tags = {k: str(v) for k, v in (tags or {}).items()}
        self.nombre = nombre or f"{experimento}_{datetime.now():%Y%m%d_%H%M%S}"
        self.mlf = _mlflow() if usar_mlflow else None
        self.registro = {"experimento": experimento, "corrida": self.nombre, "params": self.params,
                         "tags": self.tags, "metricas": [], "artefactos": []}
        self.dir_res = self.raiz / "results" / experimento
        self.run_id = None

    def __enter__(self):
        self.t0 = time.time()
        self.tags.setdefault("python", platform.python_version())
        if self.mlf:
            carpeta = (self.raiz / "mlruns").resolve()
            carpeta.mkdir(parents=True, exist_ok=True)
            self.mlf.set_tracking_uri("sqlite:///" + (carpeta / "mlflow.db").as_posix())
            if self.mlf.get_experiment_by_name(self.experimento) is None:
                self.mlf.create_experiment(self.experimento, artifact_location=(carpeta / "artefactos" /
                                                                                 self.experimento).as_uri())
            self.mlf.set_experiment(self.experimento)
            r = self.mlf.start_run(run_name=self.nombre)
            self.run_id = r.info.run_id
            self.mlf.log_params(self.params)
            self.mlf.set_tags(self.tags)
        return self

    def metricas(self, valores, paso=None):
        """Registra métricas numéricas (paso = nº de fold, o None para el resumen)."""
        num = {k: float(v) for k, v in valores.items() if isinstance(v, (int, float)) and v == v}
        self.registro["metricas"].append({"paso": paso, **num})
        if self.mlf:
            self.mlf.log_metrics(num, step=paso if paso is not None else 0)

    def artefacto(self, ruta):
        self.registro["artefactos"].append(str(ruta))
        if self.mlf and Path(ruta).exists():
            self.mlf.log_artifact(str(ruta))

    def modelo(self, objeto, nombre="modelo"):
        """Guarda el modelo entrenado (pickle) como artefacto de la corrida."""
        self.dir_res.mkdir(parents=True, exist_ok=True)
        ruta = self.dir_res / f"{nombre}.pkl"
        with open(ruta, "wb") as f:
            pickle.dump(objeto, f)
        self.artefacto(ruta)
        return ruta

    def __exit__(self, tipo, valor, tb):
        self.registro["duracion_s"] = round(time.time() - self.t0, 2)
        self.registro["estado"] = "ok" if tipo is None else f"error: {valor}"
        self.registro["fecha"] = datetime.now().isoformat(timespec="seconds")
        self.registro["mlflow_run_id"] = self.run_id
        self.dir_res.mkdir(parents=True, exist_ok=True)
        with open(self.dir_res / "ultima_corrida.json", "w", encoding="utf-8") as f:
            json.dump(self.registro, f, ensure_ascii=False, indent=2, default=str)
        (self.raiz / "results").mkdir(exist_ok=True)
        with open(self.raiz / "results" / "experimentos.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(self.registro, ensure_ascii=False, default=str) + "\n")
        if self.mlf:
            self.mlf.end_run(status="FINISHED" if tipo is None else "FAILED")
        return False
