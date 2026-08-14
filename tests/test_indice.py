"""Calculo del indice sobre datos sinteticos.

Un bug aca es silencioso: el output sigue siendo un numero plausible. Por eso se
testea contra casos con resultado conocido a mano ANTES de correrlo sobre datos
reales.
"""

from __future__ import annotations

import math

import pytest

from reporte.agregacion import (
    MODO_CRUDO,
    agregar,
    encadenar,
    laspeyres,
    renormalizar,
    serie_encadenada,
    variacion_interanual,
)
from reporte.elemental import (
    MOTIVO_OUTLIER_TOPE,
    MOTIVO_SIN_ACTUAL,
    MOTIVO_SIN_BASE,
    Relativo,
    detectar_outliers,
    emparejar,
    indice_elemental,
    jevons,
    mad,
)


def q(n: int) -> tuple:
    """Un quote sintetico: (comercio, sucursal, producto)."""
    return ("10", "7", f"EAN{n}")


# --------------------------------------------------------------------------- #
# Los dos casos obligatorios
# --------------------------------------------------------------------------- #


def test_precios_que_suben_y_vuelven_dan_cero_acumulado():
    """Sube 50% y despues baja 33,33%: el acumulado tiene que ser 0%.

    Este es el caso que separa Jevons de una media aritmetica de relativos. Con
    la aritmetica el indice quedaria por encima de 100 aunque los precios hayan
    vuelto exactamente al valor original (drift de Carli).
    """
    p0 = {q(i): 100.0 for i in range(20)}
    p1 = {q(i): 150.0 for i in range(20)}
    p2 = {q(i): 100.0 for i in range(20)}

    r1 = indice_elemental("cat", p0, p1)
    r2 = indice_elemental("cat", p1, p2)

    assert r1.indice == pytest.approx(1.5)
    assert r2.indice == pytest.approx(1.0 / 1.5)

    serie = serie_encadenada([r1.indice, r2.indice], base=100.0)
    assert serie[-1] == pytest.approx(100.0), "el indice no volvio a la base"


def test_una_canasta_de_un_solo_producto_reproduce_su_variacion_exacta():
    p0 = {q(1): 1000.0}
    p1 = {q(1): 1234.0}
    r = indice_elemental("cat", p0, p1)
    assert r.indice == pytest.approx(1.234)
    assert r.variacion_pct == pytest.approx(23.4)
    assert r.n_quotes == 1


# --------------------------------------------------------------------------- #
# Jevons
# --------------------------------------------------------------------------- #


def test_jevons_es_media_geometrica():
    """Con ratios 2 y 0,5 la media geometrica es 1; la aritmetica seria 1,25."""
    rel = [Relativo(q(1), 100, 200), Relativo(q(2), 100, 50)]
    assert jevons(rel) == pytest.approx(1.0)


def test_jevons_es_estable_con_muchos_quotes():
    """Se computa en logaritmos: multiplicar 5.000 ratios desbordaria."""
    rel = [Relativo(q(i), 100.0, 105.0) for i in range(5000)]
    assert jevons(rel) == pytest.approx(1.05)


def test_jevons_sin_quotes_devuelve_none():
    assert jevons([]) is None
    assert indice_elemental("cat", {}, {}).indice is None


# --------------------------------------------------------------------------- #
# Muestra emparejada
# --------------------------------------------------------------------------- #


def test_solo_entran_los_quotes_presentes_en_los_dos_meses():
    base = {q(1): 100.0, q(2): 200.0, q(3): 300.0}
    actual = {q(2): 220.0, q(3): 330.0, q(4): 400.0}
    rel, desc = emparejar(base, actual)
    assert {r.quote for r in rel} == {q(2), q(3)}
    motivos = {d.quote: d.motivo for d in desc}
    assert motivos[q(4)] == MOTIVO_SIN_BASE, "producto nuevo, no tiene con que compararse"
    assert motivos[q(1)] == MOTIVO_SIN_ACTUAL, "producto que desaparecio"


def test_un_producto_nuevo_no_altera_la_variacion():
    """Aunque el producto nuevo tenga un precio muy distinto."""
    base = {q(i): 100.0 for i in range(20)}
    actual = {q(i): 110.0 for i in range(20)}
    r_sin = indice_elemental("cat", base, actual)
    actual[q(999)] = 100000.0
    r_con = indice_elemental("cat", base, actual)
    assert r_con.indice == pytest.approx(r_sin.indice)


# --------------------------------------------------------------------------- #
# Outliers
# --------------------------------------------------------------------------- #


def test_el_tope_absoluto_caza_un_error_de_tipeo():
    """Un digito de mas multiplica por 10."""
    base = {q(i): 1000.0 for i in range(30)}
    actual = {q(i): 1050.0 for i in range(30)}
    actual[q(0)] = 10000.0                      # se tipeo un cero de mas
    r = indice_elemental("cat", base, actual)
    assert r.indice == pytest.approx(1.05)
    assert any(d.motivo == MOTIVO_OUTLIER_TOPE and d.quote == q(0) for d in r.descartes)


def test_el_mad_cero_no_manda_todo_a_cuarentena():
    """EL caso que rompe la deteccion ingenua.

    Si mas de la mitad de los precios no cambio, la mediana de log(ratio) es 0 y
    el MAD tambien. Sin fallback, cualquier variacion distinta de cero queda a
    infinitos MAD y la categoria entera se va a cuarentena.
    """
    base = {q(i): 1000.0 for i in range(40)}
    actual = {q(i): 1000.0 for i in range(40)}          # 35 sin cambio
    for i in range(35, 40):
        actual[q(i)] = 1080.0                           # 5 con +8%

    rel, _ = emparejar(base, actual)
    logs = [r.log_ratio for r in rel]
    assert mad(logs) == 0.0, "el escenario no reproduce MAD=0"

    r = indice_elemental("cat", base, actual)
    assert r.indice is not None
    assert r.n_quotes == 40, f"se descartaron {r.n_descartados} quotes con MAD=0"
    assert r.umbral_usado in ("iqr", "solo_tope_absoluto")


def test_con_pocos_quotes_no_se_aplica_el_mad():
    """La dispersion de 5 observaciones no es informativa."""
    base = {q(i): 100.0 for i in range(5)}
    actual = {q(0): 105.0, q(1): 106.0, q(2): 104.0, q(3): 107.0, q(4): 130.0}
    r = indice_elemental("cat", base, actual)
    assert r.umbral_usado == "solo_tope_absoluto"
    assert r.n_quotes == 5, "no deberia descartar nada: ninguno supera el tope"


def test_el_tope_se_aplica_aunque_la_categoria_este_dispersa():
    """El tope absoluto es red de seguridad: corre siempre, no solo como fallback."""
    base = {q(i): 100.0 for i in range(40)}
    actual = {q(i): 100.0 * (1 + 0.3 * (i % 5) / 5) for i in range(40)}  # dispersa
    actual[q(0)] = 5000.0
    r = indice_elemental("cat", base, actual)
    assert any(d.quote == q(0) and d.motivo == MOTIVO_OUTLIER_TOPE for d in r.descartes)


def test_los_outliers_se_miden_sobre_log_ratio_y_son_simetricos():
    """+100% y -50% son el mismo movimiento con signo opuesto."""
    subir = Relativo(q(1), 100.0, 200.0)
    bajar = Relativo(q(2), 100.0, 50.0)
    assert subir.log_ratio == pytest.approx(-bajar.log_ratio)


# --------------------------------------------------------------------------- #
# Laspeyres
# --------------------------------------------------------------------------- #


def test_laspeyres_es_media_aritmetica_ponderada():
    indices = {"a": 1.10, "b": 1.02}
    pesos = {"a": 0.25, "b": 0.75}
    ind, cubierto, total = laspeyres(indices, pesos)
    assert ind == pytest.approx(0.25 * 1.10 + 0.75 * 1.02)
    assert cubierto == pytest.approx(1.0)


def test_renormaliza_cuando_los_pesos_no_suman_uno():
    """Los pesos del INDEC son sobre la canasta completa; cubrimos una parte."""
    indices = {"01.1.1": 1.10}
    pesos = {"01.1.1": 0.0405, "01.1.2": 0.0698}    # solo tenemos el primero
    ind, cubierto, total = laspeyres(indices, pesos)
    assert ind == pytest.approx(1.10), "sin renormalizar daria 0.0405*1.10 = 0.045"
    assert cubierto == pytest.approx(0.0405)
    assert total == pytest.approx(0.1103)


def test_sin_renormalizar_el_numero_no_es_interpretable():
    indices = {"01.1.1": 1.10}
    pesos = {"01.1.1": 0.0405, "01.1.2": 0.0698}
    ind, _, _ = laspeyres(indices, pesos, modo=MODO_CRUDO)
    assert ind == pytest.approx(0.0405 * 1.10)


def test_renormalizar_hace_que_los_pesos_sumen_uno():
    p = renormalizar({"a": 0.0405, "b": 0.0345, "c": 0.0055})
    assert sum(p.values()) == pytest.approx(1.0)
    assert p["a"] > p["b"] > p["c"]


def test_una_clase_sin_dato_no_se_imputa_con_el_promedio():
    """Se excluye del numerador y del denominador."""
    r = agregar("01.1", "Alimentos", {"01.1.1": 1.10}, {"01.1.1": 0.04, "01.1.2": 0.07})
    assert r.indice == pytest.approx(1.10)
    assert r.cobertura == pytest.approx(0.04 / 0.11)


def test_agregado_sin_ningun_hijo_con_dato():
    r = agregar("01.1", "Alimentos", {}, {"01.1.1": 0.04})
    assert r.indice is None
    assert r.cobertura == 0.0


# --------------------------------------------------------------------------- #
# Encadenamiento
# --------------------------------------------------------------------------- #


def test_encadenamiento_compone_las_variaciones():
    serie = serie_encadenada([1.05, 1.05, 1.05], base=100.0)
    assert serie[0] == 100.0
    assert serie[-1] == pytest.approx(100 * 1.05**3)


def test_encadenar_es_multiplicativo_no_aditivo():
    """Tres meses al 5% dan 15,76% acumulado, no 15%."""
    serie = serie_encadenada([1.05, 1.05, 1.05])
    assert (serie[-1] / serie[0] - 1) * 100 == pytest.approx(15.7625)


def test_variacion_interanual():
    serie = {"2026-08": 130.0, "2025-08": 100.0}
    assert variacion_interanual(serie, "2026-08") == pytest.approx(0.30)
    assert variacion_interanual({"2026-08": 130.0}, "2026-08") is None


# --------------------------------------------------------------------------- #
# Extremo a extremo
# --------------------------------------------------------------------------- #


def test_de_quotes_a_indice_agregado():
    """Dos categorias con variacion conocida, agregadas con pesos del INDEC."""
    base_pan = {q(i): 1000.0 for i in range(30)}
    act_pan = {q(i): 1100.0 for i in range(30)}          # +10%
    base_lac = {("15", "3", f"E{i}"): 500.0 for i in range(30)}
    act_lac = {("15", "3", f"E{i}"): 510.0 for i in range(30)}   # +2%

    i_pan = indice_elemental("01.1.1", base_pan, act_pan)
    i_lac = indice_elemental("01.1.4", base_lac, act_lac)
    assert i_pan.indice == pytest.approx(1.10)
    assert i_lac.indice == pytest.approx(1.02)

    # Pesos reales del INDEC para GBA.
    pesos = {"01.1.1": 0.0405, "01.1.4": 0.0345}
    r = agregar("01.1", "Alimentos", {"01.1.1": 1.10, "01.1.4": 1.02}, pesos)

    esperado = (0.0405 * 1.10 + 0.0345 * 1.02) / (0.0405 + 0.0345)
    assert r.indice == pytest.approx(esperado)
    # El resultado cae entre las dos variaciones, mas cerca de la de mayor peso.
    assert 1.02 < r.indice < 1.10
    assert r.indice > 1.06
