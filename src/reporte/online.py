"""Lectura de catálogos online y comparación contra lo declarado a SEPA.

Las funciones puras viven acá y no en el script, por la misma razón que la
operación 5 del índice se movió a la librería: lo que está en `scripts/` no se
testea, y lo que no se testea es donde aparecen los errores silenciosos.

Nada de este módulo entra al cálculo del índice. Sirve para decidir si vale la
pena scrapear, y una decisión equivocada ahí cuesta semanas de trabajo en la
dirección errada.

**La trampa del campo, medida en los catálogos reales.** VTEX devuelve dos
precios por producto, `Price` y `ListPrice`, y **en qué unidad viene la lista
depende de la cadena**: en Cencosud está multiplicada por 100 —2.890 de precio y
289.000 de lista para la misma leche— y en DIA los dos campos coinciden. Medido:
288 de 288 items de Vea con el factor, 0 de 287 de DIA.

Por eso se usa `Price`, que es el que paga el cliente, y `list_price_sospechoso`
marca el item cuya lista está en otra unidad en vez de tratarla como descuento.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

# Umbral para marcar la trampa: si la lista es 50 veces el precio, no es un
# descuento, es un campo en otra unidad.
FACTOR_SOSPECHOSO = 50


@dataclass(slots=True)
class ProductoOnline:
    ean: str
    nombre: str
    precio: float
    # Lo que vino en ListPrice, sin tocar. Se guarda para poder mostrar la
    # trampa, no para usarlo.
    list_price_crudo: float | None = None

    @property
    def list_price_sospechoso(self) -> bool:
        return bool(
            self.list_price_crudo
            and self.list_price_crudo > self.precio * FACTOR_SOSPECHOSO
        )


@dataclass(slots=True)
class Comparacion:
    """Un EAN online contra lo que la cadena declaró en todas sus sucursales.

    La pregunta bien hecha no es "¿coincide el precio?" sino "¿cae dentro del
    rango de sus sucursales?". Una cadena con cientos de sucursales tiene varios
    precios para el mismo producto, así que un número solo no alcanza.
    """

    ean: str
    nombre: str
    precio_online: float
    sucursales: int
    minimo: float
    mediana: float
    maximo: float

    @property
    def donde_cae(self) -> str:
        if self.precio_online < self.minimo:
            return "abajo de todas"
        if self.precio_online > self.maximo:
            return "arriba de todas"
        return "dentro del rango"

    @property
    def dif_vs_mediana_pct(self) -> float:
        return (self.precio_online / self.mediana - 1) * 100

    @property
    def igual_a_la_mediana(self) -> bool:
        return abs(self.dif_vs_mediana_pct) < 1e-9


def precio_de_item(item: dict) -> tuple[float | None, float | None]:
    """Devuelve (precio, list_price_crudo) de un item de VTEX.

    Un precio en cero o ausente devuelve None: no se asume nada, el item queda
    afuera del sondeo.
    """
    vendedores = item.get("sellers") or []
    if not vendedores:
        return None, None
    oferta = vendedores[0].get("commertialOffer") or {}
    precio = oferta.get("Price")
    crudo = oferta.get("ListPrice")
    if not precio or precio <= 0:
        return None, crudo
    return float(precio), float(crudo) if crudo else None


def productos_de_respuesta(datos: list) -> list[ProductoOnline]:
    """Aplana la respuesta de VTEX a una lista de (ean, nombre, precio).

    Un producto puede tener varios items —las presentaciones— y cada uno su
    EAN. El item sin EAN se saltea: sin EAN no hay con qué cruzarlo contra SEPA.
    """
    salida: list[ProductoOnline] = []
    for producto in datos:
        for item in producto.get("items") or []:
            ean = (item.get("ean") or "").strip()
            precio, crudo = precio_de_item(item)
            if not ean or precio is None:
                continue
            salida.append(
                ProductoOnline(
                    ean=ean,
                    nombre=item.get("name") or producto.get("productName") or "",
                    precio=precio,
                    list_price_crudo=crudo,
                )
            )
    return salida


def comparar(
    online: list[ProductoOnline], declarado: dict[str, list[float]]
) -> tuple[list[Comparacion], list[ProductoOnline]]:
    """Cruza por EAN. Devuelve (los que matchean, los que SEPA no tiene).

    Los que no matchean se devuelven, no se descartan: son la mitad de lo que
    mide el sondeo, porque son los productos que el catálogo online agrega.
    """
    salen: list[Comparacion] = []
    sin_match: list[ProductoOnline] = []
    for p in online:
        precios = declarado.get(p.ean)
        if not precios:
            sin_match.append(p)
            continue
        salen.append(
            Comparacion(
                ean=p.ean,
                nombre=p.nombre,
                precio_online=p.precio,
                sucursales=len(precios),
                minimo=min(precios),
                mediana=statistics.median(precios),
                maximo=max(precios),
            )
        )
    return salen, sin_match


@dataclass(slots=True)
class Resumen:
    """Los números del sondeo, ya contados."""

    ean_online: int
    en_sepa: int
    sin_match: int
    dentro_del_rango: int
    arriba_de_todas: int
    abajo_de_todas: int
    iguales_a_la_mediana: int
    difs_ordenadas: list[float]

    def pct(self, parte: int, sobre: int | None = None) -> float:
        total = sobre if sobre is not None else self.ean_online
        return parte / total * 100 if total else 0.0

    def percentil(self, p: float) -> float:
        if not self.difs_ordenadas:
            return 0.0
        i = min(int(len(self.difs_ordenadas) * p), len(self.difs_ordenadas) - 1)
        return self.difs_ordenadas[i]


def resumir(
    comparaciones: list[Comparacion], sin_match: list[ProductoOnline]
) -> Resumen:
    return Resumen(
        ean_online=len(comparaciones) + len(sin_match),
        en_sepa=len(comparaciones),
        sin_match=len(sin_match),
        dentro_del_rango=sum(1 for c in comparaciones if c.donde_cae == "dentro del rango"),
        arriba_de_todas=sum(1 for c in comparaciones if c.donde_cae == "arriba de todas"),
        abajo_de_todas=sum(1 for c in comparaciones if c.donde_cae == "abajo de todas"),
        iguales_a_la_mediana=sum(1 for c in comparaciones if c.igual_a_la_mediana),
        difs_ordenadas=sorted(c.dif_vs_mediana_pct for c in comparaciones),
    )


# --------------------------------------------------------------------------- #
# El relevamiento se mudo al repo de captura.
#
# Lo que estaba aca —leer los catalogos de VTEX y Magento y guardar una foto
# por dia— es CAPTURA, y vive en `relevamiento_precios`:
#
#     precios.online.catalogos    los dos aplanadores
#     precios.online.relevar      el pedido HTTP y la foto
#     precios.cli relevar-online  el comando
#
# Se mudo porque un catalogo online NO tiene historico: solo dice que vale algo
# hoy. Lo que no se guardo hoy no se puede recuperar manana, que es la misma
# razon por la que la bajada de SEPA esta en el otro repo.
#
# Lo que queda en este archivo es la SONDA: comparar un precio online contra lo
# que esa misma cadena le declaro a SEPA. Eso no captura nada, lee del bucket y
# contesta una pregunta. Es analisis, y por eso se queda.
#
# El consumo de las fotos relevadas se hace por el DATO, no importando codigo
# del otro repo: `scripts/variacion_online.py` lee el JSON y arma los quotes.
# La interfaz entre los dos repos es el dato, no el codigo.
# --------------------------------------------------------------------------- #
