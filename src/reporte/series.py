"""Las series que se llevan mes a mes: global, solo SEPA y un rubro por division.

Juan (22/10/2026): llevar por separado el numero con lo online y el que no lo
tiene, y los rubros. Cada serie se encadena por su lado (operacion 9: el nivel
multiplica las variaciones del mes). Una serie que arranca despues —lo online,
desde noviembre— parte de 100 en su primer mes con dato; un hueco en el medio
corta la cadena, porque seguir por arriba seria inventar ese mes.
"""

from __future__ import annotations

import re

from .agregacion import encadenar

COLUMNAS_CSV = ("base", "mes", "serie", "variacion_pct", "nivel", "cobertura_ipc_pct")
NIVEL_INICIAL = 100.0

# Los nombres de las series son parte del contrato con el dashboard
# (`reporte.dashboard`): se escriben y se leen desde aca, nunca a mano.
SERIE_GLOBAL = "global (con online)"
SERIE_SIN_ONLINE = "sin online (solo SEPA)"
SERIE_POR_REGION = "por region (formula 15)"
_RUBRO = re.compile(r"^(\d\d) ")


def serie_de_rubro(division: str, nombre: str) -> str:
    """El nombre de la serie de un rubro: `"06 Salud"`."""
    return f"{division} {nombre}"


def division_de_serie(serie: str) -> str | None:
    """La division de una serie de rubro, o None si no es un rubro."""
    m = _RUBRO.match(serie)
    return m.group(1) if m else None


def filas_de_series(
    meses: list[tuple[str, str]],
    series: dict[str, list[tuple[float | None, float]]],
) -> list[dict]:
    """Una fila por (mes, serie).

    `meses` son los pares (base, mes) en orden, y cada serie trae, alineado con
    ellos, `(indice del mes o None, cobertura del IPC)`: 1,0230 y 0,2697 son
    +2,30% midiendo el 26,97% del IPC.
    """
    filas: list[dict] = []
    for nombre, valores in series.items():
        if len(valores) != len(meses):
            raise ValueError(f"la serie {nombre} tiene {len(valores)} meses, no {len(meses)}")
        nivel: float | None = None
        empezo = cortada = False
        for (base, mes), (indice, cobertura) in zip(meses, valores):
            if indice is None:
                cortada = cortada or empezo
                nivel = None
            elif not cortada:
                nivel = encadenar(nivel if empezo else NIVEL_INICIAL, indice)
                empezo = True
            filas.append({
                "base": base, "mes": mes, "serie": nombre,
                "variacion_pct": None if indice is None else (indice - 1) * 100,
                "nivel": nivel,
                "cobertura_ipc_pct": cobertura * 100,
            })
    return filas


COLUMNAS_DETALLE = ("base", "mes", "nivel", "codigo", "indice", "peso")


def filas_de_detalle(
    base: str,
    mes: str,
    clases: dict[str, float],
    pesos_clase: dict[str, float],
    articulos: dict[str, float],
    pesos_articulo: dict[str, float],
) -> list[dict]:
    """Indice y peso de cada clase y de cada articulo en un mes.

    Es el contrato con la comparacion contra el INDEC (`scripts/contra_indec.py`):
    lee esto y no el texto impreso del reporte. El peso de una clase es el que
    uso Laspeyres; el de un articulo, su peso ENGHo dentro de su clase.
    """
    filas = [{"base": base, "mes": mes, "nivel": "clase", "codigo": c,
              "indice": clases[c], "peso": pesos_clase.get(c)} for c in sorted(clases)]
    filas += [{"base": base, "mes": mes, "nivel": "articulo", "codigo": a,
               "indice": articulos[a], "peso": pesos_articulo.get(a)}
              for a in sorted(articulos)]
    return filas
