"""Lectura de catálogos online y comparación contra lo declarado.

No toca el cálculo del índice, pero decide si vale la pena scrapear, así que un
error acá lleva a una decisión equivocada y a semanas de trabajo en la dirección
errada. Los números de abajo están hechos a mano.
"""

from __future__ import annotations

import pytest

from reporte.online import (  # noqa: E402
    ObservacionOnline,
    ProductoOnline,
    comparar,
    observaciones_de_respuesta,
    precio_de_item,
    productos_de_respuesta,
    quotes_de_observaciones,
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
# Relevamiento propio: seguir el MISMO artículo en el tiempo
# --------------------------------------------------------------------------- #


def producto_vtex(item_id, precio, ean="", nombre="Heladera", marca="Lacar"):
    return {
        "productName": nombre,
        "brand": marca,
        "categories": ["/Heladeras/"],
        "items": [
            {
                "itemId": item_id,
                "ean": ean,
                "name": nombre,
                "sellers": [
                    {"commertialOffer": {"Price": precio, "AvailableQuantity": 7}}
                ],
            }
        ],
    }


def test_la_clave_es_el_sku_y_no_el_ean():
    """Frávega no trae EAN: si la clave fuera el EAN no se podría seguir nada."""
    obs = observaciones_de_respuesta(
        "fravega", [producto_vtex("990007577", 379999.0, ean="")], "2026-10-14"
    )
    assert len(obs) == 1
    o = obs[0]
    assert o.clave == ("fravega", "990007577")
    assert o.ean == ""
    assert o.precio == 379999.0
    assert o.disponible == 7
    assert o.marca == "Lacar"


def test_un_item_sin_sku_no_se_puede_seguir():
    obs = observaciones_de_respuesta(
        "fravega", [producto_vtex("", 100.0)], "2026-10-14"
    )
    assert obs == []


def test_el_sitio_entra_en_la_clave():
    """Dos sitios pueden usar el mismo número de SKU y no son el mismo artículo."""
    a = observaciones_de_respuesta("fravega", [producto_vtex("1", 100.0)], "d")[0]
    b = observaciones_de_respuesta("easy", [producto_vtex("1", 200.0)], "d")[0]
    assert a.clave != b.clave
    quotes = quotes_de_observaciones([a, b])
    assert quotes == {("fravega", "1"): 100.0, ("easy", "1"): 200.0}


def test_un_articulo_que_aparece_en_dos_busquedas_cuenta_una_vez():
    """Dos términos pueden devolver el mismo artículo: es un quote, no dos."""
    o = ObservacionOnline("easy", "77", "Taladro", 50000.0, "2026-10-14")
    quotes = quotes_de_observaciones([o, o])
    assert quotes == {("easy", "77"): 50000.0}


def test_los_quotes_tienen_la_forma_que_consume_el_calculo_del_indice():
    """Es lo que permite reusar la muestra emparejada y Jevons sin reescribirlos."""
    from reporte.elemental import indice_elemental

    base = quotes_de_observaciones([
        ObservacionOnline("easy", "1", "a", 1000.0, "d1"),
        ObservacionOnline("easy", "2", "b", 2000.0, "d1"),
    ])
    actual = quotes_de_observaciones([
        ObservacionOnline("easy", "1", "a", 1100.0, "d2"),
        ObservacionOnline("easy", "2", "b", 2200.0, "d2"),
    ])
    r = indice_elemental("easy", base, actual)
    # Los dos subieron 10%: la geométrica de 1,1 y 1,1 es 1,1.
    assert r.variacion_pct == pytest.approx(10.0)
    assert r.n_quotes == 2
