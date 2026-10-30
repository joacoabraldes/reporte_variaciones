"""Las carnicerias: el canal tradicional de la carne, desde el IPCVA.

SEPA son supermercados, y la carne fresca la informan casi solo DIA y
HiperChangomas. Pero la carne vacuna se compra en un 56% en carnicerias y solo en
un 24% en supermercados (ENGHo 2017/18). El INDEC releva los dos canales y los
combina (formula 8); sin el tradicional, el indice de la carne es el de DIA.

El IPCVA publica cada mes el precio por corte en tres plazas: AMBA, Rosario y
Cordoba. Lo baja captura (`relevar-ipcva`) y lo publica en `staged/ipcva/`;
este modulo arma, para cada articulo de la ENGHo, el relativo del canal
tradicional de una region. La combinacion con SEPA es la operacion 5 de la ruta
critica (`elemental.combinar_canales`).

Supuestos, todos escritos en `config/carnicerias.yaml`:

1. **El precio por corte del IPCVA es de carnicerias y supermercados juntos**
   (80 y 40 puntos de venta en AMBA). Es lo que publica por corte; el canal solo
   lo separa en el total. Dos tercios de la muestra son carnicerias.
2. Las regiones sin plaza (Noroeste, Noreste, Cuyo, Patagonia) toman GBA y
   Pampeana juntas, con los pesos regionales del INDEC. Es lo mas cercano que
   hay, como cuando una clase sin serie toma la de su grupo.
3. El relativo de un mes sale del informe de ese mes, que trae tambien el mes
   anterior: precio base y actual del mismo relevamiento.
"""

from __future__ import annotations

import dataclasses
import math
from pathlib import Path

import yaml

RAIZ_REPO = Path(__file__).resolve().parents[2]
PATH_CONFIG = RAIZ_REPO / "config" / "carnicerias.yaml"

# Los cortes de las tablas 3, 5 y 7 del informe (septiembre de 2026). Un par del
# config fuera de esta lista es un nombre mal escrito.
CORTES_DEL_IPCVA = frozenset({
    "Asado de tira", "Bife ancho", "Bife angosto", "Bola de lomo", "Carnaza común",
    "Colita de cuadril", "Cuadrada", "Cuadril", "Falda", "Lomo", "Matambre", "Nalga",
    "Osobuco", "Paleta", "Peceto", "Picada común", "Picada especial", "Roast beef",
    "Tapa de Asado", "Tapa de Nalga", "Tortuguita", "Vacío",
})


@dataclasses.dataclass(frozen=True)
class ConfigCarnicerias:
    pares: dict[str, str]     # articulo ENGHo -> corte del IPCVA
    plazas: dict[str, str]    # plaza del IPCVA -> region del INDEC


def cargar_config(path: Path | None = None) -> ConfigCarnicerias:
    datos = yaml.safe_load((path or PATH_CONFIG).read_text(encoding="utf-8"))
    return ConfigCarnicerias(pares=dict(datos["pares"]), plazas=dict(datos["plazas"]))


def relativos_por_plaza(
    filas: list[dict], base: str, actual: str
) -> dict[tuple[str, str], float]:
    """`(plaza, corte) -> precio actual / precio base`, del informe del mes actual.

    `base` y `actual` son meses AAAA-MM. Sin el informe de `actual` no hay
    relativos: el IPCVA publica a mediados del mes siguiente.
    """
    del_informe = [f for f in filas if f["mes_informe"] == actual]
    precio = {(f["plaza"], f["corte"], f["mes"]): f["precio"] for f in del_informe}
    return {
        (plaza, corte): precio[(plaza, corte, actual)] / precio[(plaza, corte, base)]
        for (plaza, corte, mes) in precio
        if mes == actual and (plaza, corte, base) in precio
    }


def _geometrica(valores: list[tuple[float, float]]) -> float:
    """Media geometrica ponderada de (relativo, peso)."""
    total = sum(p for _, p in valores)
    return math.exp(sum(p * math.log(v) for v, p in valores) / total)


def tradicional_por_articulo(
    relativos: dict[tuple[str, str], float],
    pares: dict[str, str],
    plazas: dict[str, str],
    region: str,
    pesos_regionales: dict[str, float],
) -> dict[str, float]:
    """`articulo -> relativo del canal tradicional` en la region.

    Una region con plaza toma la media geometrica de sus plazas; una sin plaza
    y el pais, la de las regiones que tienen, con los pesos regionales.
    """
    if not relativos:
        return {}
    con_plaza = sorted(set(plazas.values()))
    salida: dict[str, float] = {}
    for art, corte in pares.items():
        por_region: dict[str, float] = {}
        for r in con_plaza:
            rels = [relativos.get((p, corte)) for p, rp in plazas.items() if rp == r]
            if any(v is None for v in rels):
                raise ValueError(f"el informe del IPCVA no trae el corte {corte!r} en "
                                 f"todas las plazas de {r}: revisar config/carnicerias.yaml")
            por_region[r] = _geometrica([(v, 1.0) for v in rels])
        if region in por_region:
            salida[art] = por_region[region]
        else:
            salida[art] = _geometrica([(por_region[r], pesos_regionales[r]) for r in con_plaza])
    return salida
