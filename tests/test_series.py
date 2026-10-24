"""Las series que se llevan mes a mes: el global, el de solo SEPA y un rubro por division.

Juan (22/10/2026): "que lleve ambos numeros por separado. El total (con lo
online dentro) y el que no tiene lo online. Como para llevar rubros separados,
y uno global." Esto arma las filas del CSV que acompaña al reporte.
"""

from __future__ import annotations

import pytest

from reporte.series import COLUMNAS_CSV, COLUMNAS_DETALLE, filas_de_detalle, filas_de_series

MESES = [("2026-10", "2026-11"), ("2026-11", "2026-12")]


def test_cada_serie_se_encadena_por_su_lado():
    """Sin online sube 2% y 1%: 102 y 103,02. Global sube 3% y 2%: 103 y 105,06."""
    filas = filas_de_series(MESES, {
        "sin_online": [(1.02, 0.2697), (1.01, 0.2697)],
        "global": [(1.03, 0.5080), (1.02, 0.5080)],
    })
    por = {(f["serie"], f["mes"]): f for f in filas}
    assert por[("sin_online", "2026-11")]["nivel"] == pytest.approx(102.0)
    assert por[("sin_online", "2026-12")]["nivel"] == pytest.approx(103.02)
    assert por[("global", "2026-12")]["nivel"] == pytest.approx(105.06)
    assert por[("global", "2026-11")]["variacion_pct"] == pytest.approx(3.0)
    assert por[("global", "2026-11")]["cobertura_ipc_pct"] == pytest.approx(50.80)


def test_una_serie_que_arranca_despues_parte_de_100():
    """Lo online no tiene dato hasta noviembre->diciembre: su nivel arranca ahi."""
    filas = filas_de_series(MESES, {"06": [(None, 0.0), (1.05, 0.0392)]})
    assert filas[0]["variacion_pct"] is None and filas[0]["nivel"] is None
    assert filas[1]["nivel"] == pytest.approx(105.0)


def test_un_hueco_en_el_medio_corta_la_cadena():
    """Encadenar por arriba de un mes sin dato seria inventar ese mes."""
    meses = MESES + [("2026-12", "2027-01")]
    filas = filas_de_series(meses, {"07": [(1.02, 0.04), (None, 0.0), (1.03, 0.04)]})
    assert [f["nivel"] for f in filas] == [pytest.approx(102.0), None, None]


def test_las_columnas_del_csv():
    filas = filas_de_series(MESES[:1], {"global": [(1.0, 0.5)]})
    assert tuple(filas[0]) == COLUMNAS_CSV


def test_el_detalle_es_el_contrato_con_la_comparacion():
    """Indice y peso de cada clase y de cada articulo: lo que lee `contra_indec`.

    Antes la comparacion leia el texto impreso del reporte, y mover una columna
    podia hacerle leer la equivocada sin error.
    """
    filas = filas_de_detalle(
        "2026-08", "2026-09",
        clases={"01.1.5": 1.10, "01.1.4": 1.00}, pesos_clase={"01.1.5": 0.02, "01.1.4": 0.08},
        articulos={"A0115102": 1.10}, pesos_articulo={"A0115102": 0.54},
    )
    assert COLUMNAS_DETALLE == ("base", "mes", "nivel", "codigo", "indice", "peso")
    assert [(f["nivel"], f["codigo"], f["indice"], f["peso"]) for f in filas] == [
        ("clase", "01.1.4", 1.00, 0.08),
        ("clase", "01.1.5", 1.10, 0.02),
        ("articulo", "A0115102", 1.10, 0.54),
    ]
    assert all(f["base"] == "2026-08" and f["mes"] == "2026-09" for f in filas)
