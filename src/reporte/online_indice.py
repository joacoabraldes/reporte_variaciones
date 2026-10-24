"""Lo online dentro del indice: de las fotos diarias a quotes por articulo ENGHo.

El calculo del indice es un Jevons por ARTICULO de la ENGHo: los quotes de SEPA
se agrupan por articulo (`agrupar(..., articulo_de)` en correr_indice), cada
articulo da un indice, y la operacion 5 los sube a clase con los pesos de la
ENGHo. Lo online entra por el mismo lugar: sus quotes, ya con el precio del mes,
se suman al grupo de su articulo. De ahi en adelante no hay un camino aparte:
la misma muestra emparejada, el mismo filtro, el mismo Jevons, los mismos pesos.

Las dos tablas que se leen las publica el repo de captura:

    staged/relevamiento_online/sitio=X/fecha=Y/datos.json   las fotos
    staged/clasificacion_online/clasificacion_online.parquet  (sitio, item_id)
                                                            -> categoria,
                                                               clase, articulo

**Las claves no chocan.** Un quote de SEPA es `(comercio, sucursal, producto)`
y uno online `(sitio, item_id)`: largos distintos, asi que juntarlos en el
mismo grupo no puede pisar ninguno.

**Mes o semana.** El precio de un articulo online es la media de sus fotos de
la ventana (formula 6 del INDEC, `fuentes_online.precio_del_periodo`), con el
mismo minimo de dias que SEPA: 20 en el mes, 5 en la semana. Las fotos
arrancaron el 14/10/2026: por mes octubre no alcanza y el primero que entra es
noviembre contra diciembre; por semana, la del 19 al 25/10 contra la del 12 al
18 (que tiene 5 dias, del 14 al 18).
"""

from __future__ import annotations

import csv
import json
from datetime import date, datetime
from pathlib import Path

from .fuentes_online import ClaveOnline, precio_del_periodo, quotes_de_foto

# Combustibles: el `idproducto` del dataset de Energia -> categoria del indice
# (el articulo ENGHo lo pone config/mapeo_categorias_engho.yaml). Son los cinco
# productos que publica el dataset; uno que no este aca no entra.
PRODUCTOS_SURTIDOR = {
    "2": "combustibles.nafta_super",       # Nafta (super) entre 92 y 95 Ron
    "3": "combustibles.nafta_premium",     # Nafta (premium) de mas de 95 Ron
    "19": "combustibles.gasoil",           # Gas Oil Grado 2
    "21": "combustibles.gasoil_premium",   # Gas Oil Grado 3
    "6": "combustibles.gnc",               # GNC
}

# Un precio vigente con mas de 90 dias es de una estacion que dejo de informar:
# aportaria ratio 1 y diluiria el indice. Medido sobre la foto del 14/10/2026,
# la antiguedad tiene dos jorobas: 18% de los precios con menos de 60 dias, 70%
# con mas de un año y 3,7% en el medio. Cortar en 60, 90 o 180 da casi lo mismo.
# Y deja afuera a casi todo YPF: el 97,9% de sus precios tiene mas de un año, asi
# que combustibles mide las otras banderas (Shell, Axion, Puma, Gulf...).
ANTIGUEDAD_MAXIMA_DIAS = 90


def cargar_fotos(raiz: Path) -> dict[date, dict[ClaveOnline, float]]:
    """Todas las fotos de `raiz/sitio=X/fecha=Y/datos.json`, juntas por dia.

    Se pueden juntar los sitios porque la clave ya lleva el sitio:
    `(sitio, item_id)`. Es el arbol que deja `LectorBucket.bajar_fotos_online`
    y el mismo que tiene el repo de captura en disco.
    """
    fotos: dict[date, dict[ClaveOnline, float]] = {}
    for f in sorted(raiz.glob("sitio=*/fecha=*/datos.json")):
        dia = date.fromisoformat(f.parent.name.split("=", 1)[1])
        fotos.setdefault(dia, {}).update(
            quotes_de_foto(json.loads(f.read_text(encoding="utf-8")))
        )
    return fotos


def cargar_combustibles(
    raiz: Path, antiguedad_dias: int = ANTIGUEDAD_MAXIMA_DIAS
) -> tuple[dict[date, dict[tuple, float]], dict[tuple, str]]:
    """Las fotos de `raiz/fecha=Y/vigentes.csv` y la categoria de cada quote.

    El quote es `("surtidor", idempresa, idproducto)`: una estacion y un
    producto. No choca con SEPA ni con lo online (y si chocara, `sumar_grupos`
    corta). Del dataset se toma el horario Diurno —cada precio viene dos veces—
    y solo los precios con vigencia de hasta `antiguedad_dias` al dia de la foto.
    """
    fotos: dict[date, dict[tuple, float]] = {}
    categoria: dict[tuple, str] = {}
    for f in sorted(raiz.glob("fecha=*/vigentes.csv")):
        dia = date.fromisoformat(f.parent.name.split("=", 1)[1])
        with f.open(encoding="utf-8-sig", newline="") as fh:
            for fila in csv.DictReader(fh):
                cat = PRODUCTOS_SURTIDOR.get(fila["idproducto"])
                if cat is None or fila["tipohorario"] != "Diurno":
                    continue
                vigencia = datetime.fromisoformat(fila["fecha_vigencia"][:19]).date()
                if (dia - vigencia).days > antiguedad_dias:
                    continue
                clave = ("surtidor", fila["idempresa"], fila["idproducto"])
                fotos.setdefault(dia, {})[clave] = float(fila["precio"])
                categoria[clave] = cat
    return fotos, categoria


def clasificacion_por_item(filas: list[tuple]) -> dict[ClaveOnline, tuple[str, str, str]]:
    """`(sitio, item_id) -> (categoria, clase, articulo_engho)` desde la tabla publicada."""
    return {(str(s), str(i)): (str(c), str(cl), str(a)) for s, i, c, cl, a in filas}


def quotes_por_articulo(
    fotos: dict[date, dict[ClaveOnline, float]],
    clasificacion: dict[ClaveOnline, tuple[str, str, str]],
    inicio: date,
    fin: date,
    minimo_dias: int,
) -> dict[str, dict[ClaveOnline, float]]:
    """`articulo ENGHo -> {quote online: precio de la ventana}`, un mes o una semana.

    El precio es la media de las fotos de la ventana (`precio_del_periodo`),
    con el minimo de dias de la ventana: 20 en el mes, 5 en la semana. Un
    articulo sin clasificar no entra: sin articulo no tiene peso, y meterlo en
    algun lado seria inventarle uno.
    """
    del_periodo = precio_del_periodo(fotos, inicio, fin, minimo_dias)
    salida: dict[str, dict[ClaveOnline, float]] = {}
    for clave, precio in del_periodo.items():
        c = clasificacion.get(clave)
        if c is None:
            continue
        salida.setdefault(c[2], {})[clave] = precio
    return salida


def por_categoria(
    por_articulo: dict[str, dict[ClaveOnline, float]],
    clasificacion: dict[ClaveOnline, tuple[str, str, str]],
) -> dict[str, dict[ClaveOnline, float]]:
    """Los mismos quotes, agrupados por categoria en vez de por articulo.

    Es para el reporte de diagnostico por categoria, no para el calculo: el
    nivel elemental del indice es el articulo.
    """
    salida: dict[str, dict[ClaveOnline, float]] = {}
    for quotes in por_articulo.values():
        for clave, precio in quotes.items():
            salida.setdefault(clasificacion[clave][0], {})[clave] = precio
    return salida


def sumar_grupos(
    sepa: dict[str, dict[tuple, float]], online: dict[str, dict[tuple, float]]
) -> dict[str, dict[tuple, float]]:
    """Suma los quotes online a los grupos por articulo de SEPA, sin pisar nada.

    Si una clave apareciera en los dos lados seria un error de diseño, no un
    dato: corta en vez de quedarse con uno en silencio.
    """
    salida = {art: dict(q) for art, q in sepa.items()}
    for art, quotes in online.items():
        destino = salida.setdefault(art, {})
        repetidas = set(destino) & set(quotes)
        if repetidas:
            raise ValueError(
                f"el articulo {art} tiene {len(repetidas)} quotes en SEPA y online "
                f"con la misma clave: las claves no deberian poder chocar"
            )
        destino.update(quotes)
    return salida


def clases_online(clasificacion: dict[ClaveOnline, tuple[str, str, str]]) -> set[str]:
    """Las clases COICOP que agrega lo online, para el universo de Laspeyres."""
    return {c[1] for c in clasificacion.values()}
