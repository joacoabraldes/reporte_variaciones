"""Tarifas y precios fijados por norma: de la tabla publicada a quotes por articulo.

Captura transcribe cada norma (el salario minimo del servicio domestico, un
boleto, un cuadro tarifario) y publica `staged/tarifas/fuente=X/fecha=Y/valores.csv`:
una fila por item y mes. Este modulo decide que articulo de la ENGHo es cada item
y lo convierte en quotes, que se suman al grupo de su articulo igual que los de
SEPA y los online. De ahi en adelante es el mismo calculo.

La clasificacion vive en `config/tarifas.yaml` (item -> categoria) y el articulo
lo pone `config/mapeo_categorias_engho.yaml`, como para todo lo demas.

**El precio del periodo es el del mes de su ultimo dia.** Una norma fija un
valor por mes: en la ventana mensual es el del mes; una semana que cruza de mes
toma el que rige al cerrar.

**La clave es `("tarifa", fuente, item_id)`.** No choca con SEPA, cuyo primer
campo es un id de comercio numerico, ni con lo online, que tiene dos.
"""

from __future__ import annotations

from pathlib import Path

import yaml

RAIZ_REPO = Path(__file__).resolve().parents[2]
PATH_CLASIFICACION = RAIZ_REPO / "config" / "tarifas.yaml"

ClaveTarifa = tuple[str, str, str]


def cargar_clasificacion(path: Path | None = None) -> dict[tuple[str, str], str]:
    """`(fuente, item_id) -> categoria` de `config/tarifas.yaml`.

    El item `"*"` clasifica a todos los de su fuente (los planes de las prepagas
    son miles y van a la misma categoria).
    """
    datos = yaml.safe_load((path or PATH_CLASIFICACION).read_text(encoding="utf-8"))
    return {(fuente, str(item)): cat
            for fuente, items in (datos.get("fuentes") or {}).items()
            for item, cat in items.items()}


def fuentes_solo_con_afiliados(path: Path | None = None) -> set[str]:
    """Las fuentes cuyos items cuentan solo si tienen afiliados (columna `capitas`)."""
    datos = yaml.safe_load((path or PATH_CLASIFICACION).read_text(encoding="utf-8"))
    return set(datos.get("solo_con_afiliados") or [])


def quotes_por_articulo(
    filas: list[dict],
    clasificacion: dict[tuple[str, str], str],
    articulo_de_categoria: dict[str, str],
    mes: str,
    solo_con_afiliados: set[str] | frozenset[str] = frozenset(),
) -> dict[str, dict[ClaveTarifa, float]]:
    """`articulo -> {("tarifa", fuente, item): precio}` del mes AAAA-MM.

    Un item sin clasificar no entra. Una categoria sin articulo corta: si no,
    el item se perderia sin que nada avise. En las fuentes de `solo_con_afiliados`
    un item sin afiliados ese mes no entra: un plan ofrecido donde nadie lo tiene
    no es un precio que alguien pague.
    """
    sin_articulo = sorted(set(clasificacion.values()) - set(articulo_de_categoria))
    if sin_articulo:
        raise ValueError("estas categorias de tarifas no tienen articulo en "
                         "config/mapeo_categorias_engho.yaml: " + ", ".join(sin_articulo))
    grupos: dict[str, dict[ClaveTarifa, float]] = {}
    for f in filas:
        cat = (clasificacion.get((f["fuente"], f["item_id"]))
               or clasificacion.get((f["fuente"], "*")))
        if cat is None or f["mes"] != mes:
            continue
        if f["fuente"] in solo_con_afiliados and int(f.get("capitas") or 0) <= 0:
            continue
        art = articulo_de_categoria[cat]
        grupos.setdefault(art, {})[("tarifa", f["fuente"], f["item_id"])] = f["precio"]
    return grupos
