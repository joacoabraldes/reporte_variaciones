"""Escribe las tablas que lee el dashboard, desde toda la historia de corridas.

    python scripts/datos_dashboard.py
    python scripts/datos_dashboard.py --historial salida/historial --salida salida/dashboard

Deja `periodos.csv`, `clases.csv` y `contra_indec.csv`. Cada periodo toma su
calculo mas reciente (`reporte.dashboard`); la historia no se toca. Despues se
suben al dashboard de claude.ai, siempre al mismo.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))

from reporte.dashboard import tabla_clases, tabla_contra_indec, tabla_periodos  # noqa: E402
from reporte.historial import RAIZ_HISTORIAL, cargar_historial  # noqa: E402
from reporte.ponderadores import REGION_NACIONAL, ponderadores_de_clase  # noqa: E402


def escribir(filas: list[dict], destino: Path) -> None:
    destino.parent.mkdir(parents=True, exist_ok=True)
    with destino.open("w", encoding="utf-8", newline="") as fh:
        if filas:
            escritor = csv.DictWriter(fh, fieldnames=list(filas[0]))
            escritor.writeheader()
            escritor.writerows(filas)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--historial", default=str(RAIZ_HISTORIAL))
    p.add_argument("--salida", default=str(RAIZ / "salida" / "dashboard"))
    args = p.parse_args(argv)

    historial = cargar_historial(Path(args.historial))
    if not historial:
        print(f"no hay ninguna corrida en {args.historial}")
        return 1
    pond = ponderadores_de_clase(REGION_NACIONAL)
    nombres = {c: n for c, (n, _) in pond.items()}
    pesos_division = {c: p * 100 for c, (_, p) in pond.items() if len(c) == 2}
    salida = Path(args.salida)
    tablas = {
        "periodos": tabla_periodos(historial, pesos_division),
        "clases": tabla_clases(historial, nombres),
        "contra_indec": tabla_contra_indec(historial),
    }
    for nombre, filas in tablas.items():
        escribir(filas, salida / f"{nombre}.csv")
        print(f"{nombre:<14} {len(filas):>6} filas -> {salida / (nombre + '.csv')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
