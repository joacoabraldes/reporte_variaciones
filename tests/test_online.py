"""Lectura de catálogos online y comparación contra lo declarado.

No toca el cálculo del índice, pero decide si vale la pena scrapear, así que un
error acá lleva a una decisión equivocada y a semanas de trabajo en la dirección
errada. Los números de abajo están hechos a mano.
"""

from __future__ import annotations

import pytest

from reporte.online import (  # noqa: E402
    ProductoOnline,
    comparar,
    precio_de_item,
    productos_de_respuesta,
    resumir,
)


def item(precio, list_price=None, ean="7790001", nombre="Leche 1 L"):
    return {
        "ean": ean,
        "name": nombre,
        "sellers": [
            {"commertialOffer": {"Price": precio, "ListPrice": list_price}}
        ],
    }


# --------------------------------------------------------------------------- #
# El precio: cuál de los dos campos
# --------------------------------------------------------------------------- #


def test_toma_price_y_no_list_price():
    """La trampa medida en el catálogo real de Cencosud: ListPrice venía x100.

    2.890 de precio y 289.000 de lista para la misma leche de 1 litro, en los 288
    items que trajo Vea. Tomar ListPrice da un precio cien veces mayor y sigue
    siendo un número. En DIA, en cambio, los dos campos coinciden: la unidad del
    campo depende de la cadena, asi que no se puede confiar en ninguno de los dos
    sin mirar.
    """
    precio, crudo = precio_de_item(item(2890.0, 289000.0))
    assert precio == 2890.0
    assert crudo == 289000.0


def test_marca_el_item_cuya_lista_esta_en_otra_unidad():
    """Se cuenta, no se corrige: un factor 100 no es un descuento."""
    de_cencosud = ProductoOnline("1", "Leche", 2890.0, 289000.0)
    con_descuento = ProductoOnline("2", "Leche", 2890.0, 3200.0)
    sin_lista = ProductoOnline("3", "Leche", 2890.0, None)
    assert de_cencosud.list_price_sospechoso is True
    assert con_descuento.list_price_sospechoso is False
    assert sin_lista.list_price_sospechoso is False


def test_sin_vendedor_no_hay_precio():
    assert precio_de_item({"ean": "1", "sellers": []}) == (None, None)


def test_un_precio_en_cero_no_es_un_precio():
    precio, _ = precio_de_item(item(0.0, 1000.0))
    assert precio is None


# --------------------------------------------------------------------------- #
# Aplanar la respuesta
# --------------------------------------------------------------------------- #


def test_un_producto_con_varias_presentaciones_da_varios_ean():
    datos = [
        {
            "productName": "Leche",
            "items": [
                item(2915.0, ean="7790001", nombre="Leche 1 L"),
                item(5200.0, ean="7790002", nombre="Leche 2 L"),
            ],
        }
    ]
    salida = productos_de_respuesta(datos)
    assert [p.ean for p in salida] == ["7790001", "7790002"]
    assert [p.precio for p in salida] == [2915.0, 5200.0]


def test_un_item_sin_ean_se_saltea():
    """Sin EAN no hay con qué cruzarlo contra SEPA: no sirve para la sonda."""
    datos = [{"items": [item(100.0, ean=""), item(200.0, ean="7790003")]}]
    salida = productos_de_respuesta(datos)
    assert [p.ean for p in salida] == ["7790003"]


def test_una_respuesta_vacia_no_rompe():
    assert productos_de_respuesta([]) == []
    assert productos_de_respuesta([{"items": []}]) == []


# --------------------------------------------------------------------------- #
# El cruce y la pregunta bien hecha
# --------------------------------------------------------------------------- #


def test_el_precio_online_dentro_del_rango_de_las_sucursales():
    """La cadena tiene cientos de sucursales: la pregunta es si cae adentro."""
    online = [ProductoOnline("7790001", "Leche", 1000.0, None)]
    salen, sin_match = comparar(online, {"7790001": [900.0, 1000.0, 1200.0]})
    assert sin_match == []
    c = salen[0]
    assert (c.minimo, c.mediana, c.maximo) == (900.0, 1000.0, 1200.0)
    assert c.sucursales == 3
    assert c.donde_cae == "dentro del rango"
    assert c.dif_vs_mediana_pct == pytest.approx(0.0)


def test_arriba_de_todas_las_sucursales_es_senal():
    online = [ProductoOnline("7790001", "Leche", 1500.0, None)]
    c = comparar(online, {"7790001": [900.0, 1000.0, 1200.0]})[0][0]
    assert c.donde_cae == "arriba de todas"
    assert c.dif_vs_mediana_pct == pytest.approx(50.0)


def test_abajo_de_todas_las_sucursales_tambien():
    online = [ProductoOnline("7790001", "Leche", 450.0, None)]
    c = comparar(online, {"7790001": [900.0, 1000.0, 1200.0]})[0][0]
    assert c.donde_cae == "abajo de todas"
    assert c.dif_vs_mediana_pct == pytest.approx(-55.0)


def test_un_ean_que_sepa_no_tiene_queda_contado_aparte():
    """Es la mitad de lo que mide la sonda: qué agrega el catálogo online."""
    online = [
        ProductoOnline("7790001", "Leche", 1000.0, None),
        ProductoOnline("9999999", "Algo que SEPA no ve", 500.0, None),
    ]
    salen, sin_match = comparar(online, {"7790001": [1000.0]})
    assert [c.ean for c in salen] == ["7790001"]
    assert [p.ean for p in sin_match] == ["9999999"]


def test_la_mediana_de_una_sola_sucursal_es_su_precio():
    online = [ProductoOnline("7790001", "Leche", 1000.0, None)]
    c = comparar(online, {"7790001": [1234.0]})[0][0]
    assert c.mediana == 1234.0
    assert c.donde_cae == "abajo de todas"


# --------------------------------------------------------------------------- #
# El resumen
# --------------------------------------------------------------------------- #


def test_el_resumen_cuenta_cada_caso_una_sola_vez():
    online = [
        ProductoOnline("1", "dentro", 1000.0),
        ProductoOnline("2", "arriba", 2000.0),
        ProductoOnline("3", "abajo", 100.0),
        ProductoOnline("4", "no esta en sepa", 500.0),
    ]
    declarado = {
        "1": [900.0, 1000.0, 1100.0],
        "2": [900.0, 1000.0, 1100.0],
        "3": [900.0, 1000.0, 1100.0],
    }
    r = resumir(*comparar(online, declarado))
    assert r.ean_online == 4
    assert (r.en_sepa, r.sin_match) == (3, 1)
    assert (r.dentro_del_rango, r.arriba_de_todas, r.abajo_de_todas) == (1, 1, 1)
    assert r.iguales_a_la_mediana == 1
    assert r.pct(r.en_sepa) == pytest.approx(75.0)


def test_el_resumen_sin_nada_no_divide_por_cero():
    r = resumir([], [])
    assert r.pct(0) == 0.0
    assert r.percentil(0.5) == 0.0


# --------------------------------------------------------------------------- #
# Los tests del relevamiento se mudaron con el codigo.
#
# Seguir el mismo articulo en el tiempo es captura, y vive en
# `relevamiento_precios/tests/test_online.py`. Aca quedan los de la sonda, que
# es lo unico que hace este archivo ahora: comparar un precio online contra lo
# que esa cadena le declaro a SEPA.
# --------------------------------------------------------------------------- #
