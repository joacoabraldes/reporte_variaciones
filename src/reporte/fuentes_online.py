"""Precios de las fuentes que no son SEPA: de las fotos diarias a un precio del mes.

La captura (repo `relevamiento_precios`, etapa 5) guarda una foto por dia y por
sitio y la publica en el bucket. Este modulo las lee **por el dato**, no
importando codigo del otro repo: el contrato son los campos del archivo
(`sitio`, `item_id`, `precio`).

**Por que un precio del mes y no dos dias sueltos.** Primera comparacion real,
14 al 15/10/2026: Rapsodia dio +8,079% en un dia porque se termino un 25% de
descuento en 55 de sus 137 articulos. Y Farmacity, calibrada contra SEPA, tiene
un tercio de sus productos online 35-40% abajo por promociones que no declara.
Comparar dos fotos mide el calendario de promociones.

La respuesta es la misma que para SEPA, la formula 6 del INDEC: el precio del
mes de cada articulo es la **media aritmetica** de sus precios observados en el
mes. Una promo de diez dias pesa un tercio del mes, no el mes entero. Es la
operacion 1 de la ruta critica (`periodo.quotes_del_periodo`) aplicada a las
fotos online; cuando lo online entre al indice, esto es esa operacion.

El minimo de dias es el mismo parametro que usa SEPA
(`ventanas.mensual.minimo_dias_quote` en config/parametros.yaml): un articulo
visto tres dias no tiene un precio del mes, tiene tres precios.
"""

from __future__ import annotations

import statistics
from datetime import date

ClaveOnline = tuple[str, str]  # (sitio, item_id)


def quotes_de_foto(registros: list[dict]) -> dict[ClaveOnline, float]:
    """`{(sitio, item_id): precio}` de una foto.

    Se saltea el registro sin `item_id` o sin precio positivo. Si un item aparece
    dos veces —dos terminos de busqueda lo devolvieron— se queda el primero: es
    el mismo articulo, no dos.
    """
    salida: dict[ClaveOnline, float] = {}
    for r in registros:
        item_id = str(r.get("item_id") or "").strip()
        precio = r.get("precio")
        if not item_id or not isinstance(precio, (int, float)) or precio <= 0:
            continue
        salida.setdefault((str(r.get("sitio") or ""), item_id), float(precio))
    return salida


def mes_de(dia: date) -> str:
    return f"{dia.year:04d}-{dia.month:02d}"


def precio_del_mes(
    fotos: dict[date, dict[ClaveOnline, float]], minimo_dias: int
) -> dict[str, dict[ClaveOnline, float]]:
    """`mes -> {clave: media de sus precios en el mes}`.

    Solo entra el articulo observado en al menos `minimo_dias` dias de ese mes.
    Con menos, el promedio no representa el mes y mezcla mas la suerte de que
    dias se relevo que el precio.
    """
    por_mes: dict[str, dict[ClaveOnline, list[float]]] = {}
    for dia, quotes in fotos.items():
        acum = por_mes.setdefault(mes_de(dia), {})
        for clave, precio in quotes.items():
            acum.setdefault(clave, []).append(precio)
    return {
        mes: {k: statistics.fmean(v) for k, v in acum.items() if len(v) >= minimo_dias}
        for mes, acum in por_mes.items()
    }


def precio_del_periodo(
    fotos: dict[date, dict[ClaveOnline, float]],
    inicio: date,
    fin: date,
    minimo_dias: int,
) -> dict[ClaveOnline, float]:
    """`{clave: media de sus precios}` en la ventana cerrada `inicio..fin`.

    La misma cuenta que `precio_del_mes`, para cualquier ventana: un mes o una
    semana ISO. Solo entra el articulo observado en al menos `minimo_dias` dias
    de la ventana (20 en el mes, 5 en la semana: config/parametros.yaml).
    """
    acum: dict[ClaveOnline, list[float]] = {}
    for dia, quotes in fotos.items():
        if inicio <= dia <= fin:
            for clave, precio in quotes.items():
                acum.setdefault(clave, []).append(precio)
    return {k: statistics.fmean(v) for k, v in acum.items() if len(v) >= minimo_dias}


def dias_por_mes(fotos: dict[date, dict]) -> dict[str, int]:
    """Cuantos dias relevados tiene cada mes, para explicar un mes que no alcanza."""
    salida: dict[str, int] = {}
    for dia in fotos:
        salida[mes_de(dia)] = salida.get(mes_de(dia), 0) + 1
    return dict(sorted(salida.items()))
