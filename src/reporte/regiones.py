"""La region del INDEC de cada sucursal.

El IPC se calcula por region (seis) y despues se suma con el peso de cada una
(formula 15 de la metodologia, `agregacion.nacional_desde_regiones`). Cinco
regiones salen de la provincia; Buenos Aires no:

    GBA      = CABA + los 24 partidos del Gran Buenos Aires
    Pampeana = el resto de Buenos Aires + Cordoba, Entre Rios, La Pampa, Santa Fe

El partido lo publica captura en `staged/sucursales/` (departamento de georef,
por las coordenadas de la sucursal). Que partido es GBA es metodologia y vive
aca, en `config/regiones.yaml`.

**No se inventa.** Una sucursal bonaerense sin partido, o declarada en Buenos
Aires con coordenadas en otra provincia, queda sin region (`None`): mandarla a
Pampeana "por default" moveria peso de una region a otra sin que se vea.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import yaml

RAIZ_REPO = Path(__file__).resolve().parents[2]
PATH_REGIONES = RAIZ_REPO / "config" / "regiones.yaml"

REQUIERE_LOCALIDAD = "REQUIERE_LOCALIDAD"
PROVINCIA_DESCONOCIDA = "DESCONOCIDA"
ID_CABA = "02"
ID_BUENOS_AIRES = "06"


@dataclasses.dataclass(frozen=True)
class Regiones:
    provincias: dict[str, str]          # nombre de SEPA -> region
    provincia_por_id: dict[str, str]    # codigo de georef -> nombre de SEPA
    partidos_gba: frozenset[str]        # codigos de departamento de georef


def cargar_regiones(path: Path | None = None) -> Regiones:
    datos = yaml.safe_load((path or PATH_REGIONES).read_text(encoding="utf-8"))
    return Regiones(
        provincias={str(k): str(v) for k, v in datos["provincias"].items()},
        provincia_por_id={str(k): str(v) for k, v in datos["provincia_por_id"].items()},
        partidos_gba=frozenset(str(d) for d in datos["partidos_gba_ids"]),
    )


def region_de_sucursal(
    provincia: str | None,
    provincia_id: str | None,
    departamento_id: str | None,
    reg: Regiones,
) -> str | None:
    """La region de una sucursal, o `None` si no hay datos para decidirla.

    `provincia` es la que declara SEPA; `provincia_id` y `departamento_id` los
    de georef. Manda SEPA, y georef completa: la provincia cuando SEPA no la
    sabe, y el partido para partir Buenos Aires.
    """
    if not provincia or provincia == PROVINCIA_DESCONOCIDA:
        provincia = reg.provincia_por_id.get(provincia_id or "")
        if provincia is None:
            return None
    region = reg.provincias.get(provincia)
    if region is None:
        raise ValueError(
            f"la provincia {provincia!r} no esta en config/regiones.yaml"
        )
    if region != REQUIERE_LOCALIDAD:
        return region

    # Buenos Aires: decide el partido. Una sucursal en el borde con la General
    # Paz puede caer en CABA, que tambien es GBA.
    if provincia_id == ID_CABA:
        return "GBA"
    if provincia_id != ID_BUENOS_AIRES or departamento_id is None:
        return None
    return "GBA" if departamento_id in reg.partidos_gba else "Pampeana"
