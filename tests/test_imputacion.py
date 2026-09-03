"""Imputacion de quotes ausentes.

Lo que se testea acá son las dos propiedades que justifican el modulo:

1. Imputar con la variacion de la categoria **no mueve el indice del periodo**.
   Si lo moviera, la imputacion estaria inventando señal.
2. Sirve para el periodo **siguiente**: un quote que falta y vuelve aporta su
   variacion real en vez de perderse dos veces.

Y la comparacion contra lo que no hay que hacer: arrastrar el ultimo precio.
"""

from __future__ import annotations

import pytest

from reporte.elemental import indice_elemental, jevons, emparejar
from reporte.imputacion import (
    MOTIVO_BAJA_POR_AUSENCIA,
    MOTIVO_SIN_INDICE,
    imputar,
)

CAT = "almacen.fideos_secos_500g"


def q(n: int) -> tuple[str, str, str]:
    """Un quote sintetico del comercio 1, sucursal 1."""
    return ("1", "1", f"p{n}")


def categorias(*quotes) -> dict:
    return {quote: CAT for quote in quotes}


# --------------------------------------------------------------------------- #
# La propiedad que hace que la imputacion sea honesta
# --------------------------------------------------------------------------- #


def test_imputar_no_mueve_el_indice_del_periodo():
    """El precio imputado lleva justo la variacion media: el promedio no cambia.

    Es la razon por la que el imputado NO entra al calculo del periodo y se
    devuelve aparte. Si moviera el indice, estaria agregando informacion que
    nadie observo.
    """
    base = {q(1): 100.0, q(2): 200.0, q(3): 50.0}
    actual = {q(1): 110.0, q(2): 220.0}  # q3 falta

    original = indice_elemental(CAT, base, actual)
    assert original.n_quotes == 2

    res = imputar(base, actual, categorias(q(1), q(2), q(3)),
                  {CAT: original.indice})

    # El indice recalculado incluyendo el imputado da exactamente lo mismo.
    con_imputado = indice_elemental(CAT, base, res.base_proximo_periodo)
    assert con_imputado.indice == pytest.approx(original.indice)
    assert con_imputado.n_quotes == 3


def test_el_precio_imputado_es_la_base_por_el_indice():
    base = {q(1): 100.0, q(2): 50.0}
    actual = {q(1): 110.0}

    res = imputar(base, actual, categorias(q(1), q(2)), {CAT: 1.10})

    assert res.base_proximo_periodo[q(2)] == pytest.approx(55.0)
    assert res.n_imputados == 1
    imp = res.imputados[0]
    assert imp.quote == q(2)
    assert imp.precio_base == 50.0
    assert imp.indice_usado == 1.10
    assert imp.periodos_ausente == 1
    assert not imp.es_reimputacion


# --------------------------------------------------------------------------- #
# Para lo que sirve de verdad: el periodo siguiente
# --------------------------------------------------------------------------- #


def test_un_quote_que_falta_y_vuelve_aporta_su_variacion_real():
    """Sin imputar se pierde dos veces: sin actual, y despues sin base."""
    agosto = {q(1): 100.0, q(9): 1000.0}
    septiembre = {q(1): 110.0}                      # q9 no reporto
    octubre = {q(1): 121.0, q(9): 1300.0}           # q9 volvio

    # Sin imputacion: en octubre q9 no tiene base y se cae.
    relativos_sin, _ = emparejar(septiembre, octubre)
    assert [r.quote for r in relativos_sin] == [q(1)]

    # Con imputacion: septiembre le deja un precio sintetico de 1.100.
    res = imputar(agosto, septiembre, categorias(q(1), q(9)), {CAT: 1.10})
    assert res.base_proximo_periodo[q(9)] == pytest.approx(1100.0)

    relativos_con, _ = emparejar(res.base_proximo_periodo, octubre)
    por_quote = {r.quote: r.ratio for r in relativos_con}
    assert q(9) in por_quote
    assert por_quote[q(9)] == pytest.approx(1300.0 / 1100.0)


def test_arrastrar_el_ultimo_precio_daria_otro_numero():
    """El metodo que NO se usa, y por que.

    Arrastrar asume 0% en el periodo que falta y despues descarga los dos
    periodos de suba juntos en el siguiente.
    """
    agosto = {q(9): 1000.0}
    octubre = {q(9): 1300.0}

    imputado = 1000.0 * 1.10   # lo que hace este modulo
    arrastrado = 1000.0        # lo que haria arrastrar el ultimo precio

    ratio_imputado = 1300.0 / imputado
    ratio_arrastrado = 1300.0 / arrastrado

    assert ratio_imputado == pytest.approx(1.1818, abs=1e-4)
    assert ratio_arrastrado == pytest.approx(1.30)
    # Arrastrar mete en octubre una suba que en parte fue de septiembre.
    assert ratio_arrastrado > ratio_imputado
    assert agosto[q(9)] < octubre[q(9)]


# --------------------------------------------------------------------------- #
# Estado entre periodos
# --------------------------------------------------------------------------- #


def test_a_los_tres_periodos_ausente_sale_de_la_muestra():
    base = {q(1): 100.0, q(2): 50.0}
    actual = {q(1): 110.0}
    cats = categorias(q(1), q(2))

    res = imputar(base, actual, cats, {CAT: 1.10})
    assert res.ausencias[q(2)] == 1

    res = imputar(res.base_proximo_periodo, actual, cats, {CAT: 1.10},
                  ausencias=res.ausencias)
    assert res.ausencias[q(2)] == 2
    assert res.imputados[0].es_reimputacion

    res = imputar(res.base_proximo_periodo, actual, cats, {CAT: 1.10},
                  ausencias=res.ausencias)
    assert q(2) not in res.base_proximo_periodo
    assert res.n_bajas == 1
    assert res.bajas[0].motivo == MOTIVO_BAJA_POR_AUSENCIA


def test_un_quote_que_vuelve_resetea_el_contador():
    base = {q(1): 100.0, q(2): 50.0}
    cats = categorias(q(1), q(2))

    res = imputar(base, {q(1): 110.0}, cats, {CAT: 1.10})
    assert res.ausencias[q(2)] == 1

    # Vuelve a reportar.
    res = imputar(res.base_proximo_periodo, {q(1): 121.0, q(2): 60.0}, cats,
                  {CAT: 1.10}, ausencias=res.ausencias)
    assert res.ausencias[q(2)] == 0
    assert res.n_imputados == 0
    assert res.reaparecidos == [q(2)]


def test_el_que_nunca_falto_no_figura_como_reaparecido():
    base = {q(1): 100.0}
    res = imputar(base, {q(1): 110.0}, categorias(q(1)), {CAT: 1.10})
    assert res.reaparecidos == []


def test_un_producto_nuevo_no_se_imputa_hacia_atras():
    """Aparece hoy y no estaba antes: no tiene historia que estimar."""
    base = {q(1): 100.0}
    actual = {q(1): 110.0, q(7): 500.0}

    res = imputar(base, actual, categorias(q(1), q(7)), {CAT: 1.10})

    assert res.n_imputados == 0
    assert res.base_proximo_periodo[q(7)] == 500.0
    assert res.ausencias[q(7)] == 0


# --------------------------------------------------------------------------- #
# Bordes
# --------------------------------------------------------------------------- #


def test_una_categoria_sin_indice_no_se_puede_imputar():
    """Si la categoria no dio indice no hay con que estimar. Se registra."""
    base = {q(1): 100.0, q(2): 50.0}
    actual = {q(1): 110.0}

    res = imputar(base, actual, categorias(q(1), q(2)), {CAT: None})

    assert res.n_imputados == 0
    assert q(2) not in res.base_proximo_periodo
    assert len(res.sin_indice) == 1
    assert res.sin_indice[0].motivo == MOTIVO_SIN_INDICE


def test_un_quote_sin_categoria_no_se_imputa():
    base = {q(1): 100.0, q(2): 50.0}
    actual = {q(1): 110.0}

    res = imputar(base, actual, {q(1): CAT}, {CAT: 1.10})

    assert res.n_imputados == 0
    assert len(res.sin_indice) == 1


def test_sin_ausentes_no_hay_imputacion():
    base = {q(1): 100.0}
    actual = {q(1): 110.0}
    res = imputar(base, actual, categorias(q(1)), {CAT: 1.10})
    assert res.n_imputados == 0
    assert res.base_proximo_periodo == actual


# --------------------------------------------------------------------------- #
# El supuesto fragil: un comercio entero que deja de reportar
# --------------------------------------------------------------------------- #


def test_las_imputaciones_se_pueden_contar_por_comercio():
    """Un comercio que concentra las imputaciones no perdio productos sueltos.

    Dejo de reportar, y se le esta asignando el movimiento de las cadenas que
    si reportaron. Tiene que poder verse.
    """
    caidos = [("20", "1", f"p{i}") for i in range(4)]
    base = {q(1): 100.0, **{c: 200.0 for c in caidos}}
    actual = {q(1): 110.0}
    cats = {q(1): CAT, **{c: CAT for c in caidos}}

    res = imputar(base, actual, cats, {CAT: 1.10})

    assert res.n_imputados == 4
    assert res.por_comercio() == {"20": 4}


def test_el_jevons_no_cambia_aunque_se_caiga_un_comercio_entero():
    """La imputacion no puede sostener sola un indice que perdio la muestra."""
    caidos = [("20", "1", f"p{i}") for i in range(5)]
    base = {q(1): 100.0, q(2): 100.0, **{c: 200.0 for c in caidos}}
    actual = {q(1): 110.0, q(2): 130.0}

    original = indice_elemental(CAT, base, actual)
    res = imputar(base, actual, {**categorias(q(1), q(2)),
                                 **{c: CAT for c in caidos}},
                  {CAT: original.indice})

    relativos, _ = emparejar(base, res.base_proximo_periodo)
    assert jevons(relativos) == pytest.approx(original.indice)
    assert res.n_imputados == 5
