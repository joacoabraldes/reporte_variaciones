"""La historia de resultados: cada corrida se suma y ninguna se pisa.

Es lo que lee el dashboard: todas las semanas y todos los meses calculados,
para poder ir a cualquier fecha. La regla es la misma que la de las fotos
online: **nunca se reescribe una corrida que ya existe**. Si un periodo se
recalcula (otro filtro, un dato que llego tarde), la corrida nueva se suma, el
dashboard muestra lo ultimo con su fecha de calculo, y lo anterior queda.

    salida/historial/ventana=<semanal|mensual|contra_indec>/corrida=<fecha y hora>/<tabla>.csv

**Hoy vive en esta maquina.** Donde se guarda en forma permanente (el bucket o
git) se decide despues, y esta es la unica puerta: ese dia cambia este modulo y
nada mas. Hasta entonces no esta respaldado.
"""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

RAIZ_HISTORIAL = Path(__file__).resolve().parents[2] / "salida" / "historial"
_FORMATO_CORRIDA = "%Y-%m-%dT%H-%M-%S"


def guardar_corrida(
    ventana: str,
    calculado_el: datetime,
    tablas: dict[str, list[dict]],
    raiz: Path = RAIZ_HISTORIAL,
) -> Path:
    """Guarda las tablas de una corrida. Corta si esa corrida ya existe."""
    carpeta = raiz / f"ventana={ventana}" / f"corrida={calculado_el.strftime(_FORMATO_CORRIDA)}"
    if carpeta.exists():
        raise FileExistsError(f"la corrida {carpeta} ya existe: la historia no se pisa")
    carpeta.mkdir(parents=True)
    for nombre, filas in tablas.items():
        if not filas:
            continue
        with (carpeta / f"{nombre}.csv").open("w", encoding="utf-8", newline="") as fh:
            escritor = csv.DictWriter(fh, fieldnames=list(filas[0]))
            escritor.writeheader()
            escritor.writerows(filas)
    return carpeta


def cargar_historial(raiz: Path = RAIZ_HISTORIAL) -> dict[str, list[dict]]:
    """`tabla -> filas de todas las corridas`, cada una con su `ventana` y `calculado_el`."""
    salida: dict[str, list[dict]] = {}
    for archivo in sorted(raiz.glob("ventana=*/corrida=*/*.csv")):
        ventana = archivo.parent.parent.name.split("=", 1)[1]
        cuando = datetime.strptime(archivo.parent.name.split("=", 1)[1], _FORMATO_CORRIDA)
        with archivo.open(encoding="utf-8", newline="") as fh:
            for fila in csv.DictReader(fh):
                fila.update(ventana=ventana, calculado_el=cuando.isoformat())
                salida.setdefault(archivo.stem, []).append(fila)
    return salida


def lo_ultimo_por_periodo(filas: list[dict], clave: tuple[str, ...]) -> list[dict]:
    """Para cada `clave` (ventana, periodo, serie...), la fila del calculo mas reciente."""
    vigente: dict[tuple, dict] = {}
    for f in filas:
        k = tuple(f[c] for c in clave)
        if k not in vigente or f["calculado_el"] > vigente[k]["calculado_el"]:
            vigente[k] = f
    return list(vigente.values())
