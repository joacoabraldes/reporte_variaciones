"""La comparacion contra el INDEC: la vara con la que se decide todo lo demas.

Vivia en scripts sueltos sin tests, y produjo un error que dejo una conclusion
falsa un dia entero (21/10/2026): ponderaba las regiones tambien por el peso de
la clase. Aca el lado del INDEC se arma con `nacional_desde_regiones`, la misma
funcion que reproduce su nacional publicado, y se lee por FECHA y no por
posicion: "las dos ultimas columnas" deja de ser agosto y septiembre el dia que
sale octubre.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from reporte.contra_indec import (
    FRESCOS,
    aperturas_de_tabla,
    aperturas_del_indice,
    cargar_pares,
    errores_por_producto,
    leer_aperturas,
    precios_de_tabla,
    rubro_contra_indec,
    variacion_indec,
)

RAIZ = Path(__file__).resolve().parents[1]

# "Alimentos y bebidas no alcoholicas", julio y agosto 2026, dic 2016 = 100.
# INDEC, sh_ipc_09_26.xls. El nacional publicado: 12.907,5083 -> 13.121,2544.
ALIMENTOS = {
    "GBA": (13015.3992, 13199.7267), "Pampeana": (12930.4533, 13153.6122),
    "Noroeste": (12578.6423, 12828.2786), "Noreste": (12739.7054, 13025.3565),
    "Cuyo": (12978.6132, 13219.0007), "Patagonia": (12275.6711, 12548.9139),
}
NACIONAL_PUBLICADO = 13121.2544 / 12907.5083
DIV = "Alimentos y bebidas no alcohólicas"


def _aperturas(series: dict[str, dict[str, tuple]]) -> dict:
    """`region -> apertura -> {mes: nivel}` con julio y agosto."""
    return {r: {ap: {"2026-07": v[0], "2026-08": v[1]} for ap, v in s.items()}
            for r, s in series.items()}


# --------------------------------------------------------------------------- #
# El lado del INDEC
# --------------------------------------------------------------------------- #


def test_reproduce_la_variacion_nacional_que_publica_el_indec():
    """Test de aceptacion: julio -> agosto 2026 desde los seis regionales.

    Los pesos regionales van con cuatro decimales: queda a 0,001 puntos.
    """
    ap = _aperturas({r: {DIV: v} for r, v in ALIMENTOS.items()})
    assert variacion_indec(ap, DIV, "2026-07", "2026-08") == pytest.approx(
        NACIONAL_PUBLICADO, abs=1e-5)


def test_una_apertura_que_se_escribe_distinto_segun_la_region():
    """01.1.8 es "etc," en GBA y "etc." en las otras cinco: se toma la que este."""
    series = {r: {"Azúcar, dulces, chocolate, golosinas, etc.": (100.0, 102.0)}
              for r in ALIMENTOS}
    series["GBA"] = {"Azúcar, dulces, chocolate, golosinas, etc,": (100.0, 102.0)}
    nombres = ["Azúcar, dulces, chocolate, golosinas, etc,",
               "Azúcar, dulces, chocolate, golosinas, etc."]
    assert variacion_indec(_aperturas(series), nombres, "2026-07", "2026-08") == \
        pytest.approx(1.02)


def test_una_apertura_que_falta_en_una_region_corta():
    series = {r: {DIV: v} for r, v in ALIMENTOS.items()}
    series["Cuyo"] = {}
    with pytest.raises(ValueError, match="Cuyo"):
        variacion_indec(_aperturas(series), DIV, "2026-07", "2026-08")


def test_un_mes_que_la_planilla_no_tiene_corta():
    ap = _aperturas({r: {DIV: v} for r, v in ALIMENTOS.items()})
    with pytest.raises(ValueError, match="2026-09"):
        variacion_indec(ap, DIV, "2026-08", "2026-09")


def test_la_planilla_se_lee_por_fecha_y_no_por_posicion():
    """Cada region trae sus fechas en su propia fila; los valores van debajo."""
    filas = [
        ["Índice de precios al consumidor", None, None, None],
        [None, None, None, None],
        ["Región GBA", pd.Timestamp("2026-06-01"), pd.Timestamp("2026-07-01"),
         pd.Timestamp("2026-08-01")],
        [None, None, None, None],
        ["Nivel general", 100.0, 101.0, 102.0],
        [DIV, 200.0, 202.0, 206.0],
        ["Región Cuyo", pd.Timestamp("2026-06-01"), pd.Timestamp("2026-07-01"),
         pd.Timestamp("2026-08-01")],
        [DIV, 300.0, 303.0, 309.0],
    ]
    ap = aperturas_de_tabla(pd.DataFrame(filas))
    assert ap["GBA"][DIV] == {"2026-06": 200.0, "2026-07": 202.0, "2026-08": 206.0}
    assert ap["Cuyo"][DIV]["2026-08"] == 309.0
    assert set(ap) == {"GBA", "Cuyo"}


def test_la_planilla_real_de_docs():
    """`docs/ipc_aperturas.xls` termina en julio y coincide con lo publicado despues."""
    ap = leer_aperturas(RAIZ / "docs" / "ipc_aperturas.xls")
    assert set(ap) == set(ALIMENTOS)
    assert ap["GBA"][DIV]["2026-07"] == pytest.approx(ALIMENTOS["GBA"][0])
    assert "2026-08" not in ap["GBA"][DIV]


def test_los_precios_promedio_se_leen_por_mes():
    """La hoja GBA: el año va en una fila y el mes en la de abajo."""
    filas = [
        ["Precios al consumidor... (GBA)", None, None, None, None],
        [None, None, None, None, None],
        ["Variedad", "Unidad de medida", "Año 2025", None, "Año 2026"],
        [None, None, "Noviembre", "Diciembre", "Enero"],
        [None, None, "Pesos", None, None],
        [None, None, None, None, None],
        ["Papa", "kg", 900.0, 950.0, 1000.0],
        ["Nalga", "kg", 15000.0, 15100.0, 15200.0],
    ]
    p = precios_de_tabla(pd.DataFrame(filas))
    assert p["Papa"] == {"2025-11": 900.0, "2025-12": 950.0, "2026-01": 1000.0}
    assert p["Nalga"]["2026-01"] == 15200.0


# --------------------------------------------------------------------------- #
# La comparacion
# --------------------------------------------------------------------------- #


def test_un_rubro_contra_lo_comparable_del_indec():
    """Prendas y calzado con NUESTROS pesos de clase de los dos lados.

    nuestro (0,3 x 1,21 + 0,1 x 1,00) / 0,4 = 1,1575
    INDEC   (0,3 x 1,02 + 0,1 x 1,01) / 0,4 = 1,0175
    Una clase sin dato nuestro (03.1.3) no entra a ninguno de los dos.
    """
    pesos = {"03.1.2": 0.3, "03.2.1": 0.1, "03.1.3": 0.05}
    nuestro = {"03.1.2": 1.21, "03.2.1": 1.00}
    indec = {"03.1.2": 1.02, "03.2.1": 1.01, "03.1.3": 1.50}
    n, i = rubro_contra_indec(nuestro, indec, pesos)
    assert n == pytest.approx(1.1575)
    assert i == pytest.approx(1.0175)


def test_todas_las_series_del_config_estan_en_las_seis_regiones():
    """El contrato con la planilla del INDEC: cada clase y cada division tiene su
    serie en las seis regiones. Si el INDEC cambia un nombre, corta aca."""
    ap = leer_aperturas(RAIZ / "docs" / "ipc_aperturas.xls")
    clases, divisiones = aperturas_del_indice()
    assert set(divisiones) == {"01", "03", "04", "05", "06", "07", "09", "12"}
    for nombre in [*clases.values(), *divisiones.values()]:
        assert variacion_indec(ap, nombre, "2026-06", "2026-07") > 0


def test_los_pares_con_el_indec_son_articulos_del_mapeo():
    """Los 32 productos que el INDEC publica con precio promedio y tienen par."""
    from reporte.ponderadores import cargar_mapeo

    pares = cargar_pares()
    assert len(pares) == 32
    assert set(pares) <= {s["articulo"] for s in cargar_mapeo().values()}


def test_los_frescos_son_carnes_pescados_frutas_y_verduras():
    assert FRESCOS == {"01.1.2", "01.1.3", "01.1.6", "01.1.7"}


def test_error_por_producto_medio_y_absoluto():
    """Papa nos da +2 y al INDEC -3: error +5. Aceite +1 y +2: error -1.

    Medio (+5 - 1) / 2 = +2; absoluto (5 + 1) / 2 = 3.
    """
    nuestro = {"A0117119": 1.02, "A0115102": 1.01, "A0111201": 1.05}
    indec = {"Papa": 0.97, "Aceite de girasol": 1.02}
    pares = {"A0117119": "Papa", "A0115102": "Aceite de girasol",
             "A0111201": "Arroz blanco simple"}          # sin dato del INDEC: no entra
    filas, medio, absoluto = errores_por_producto(nuestro, indec, pares)
    assert medio == pytest.approx(2.0)
    assert absoluto == pytest.approx(3.0)
    assert [f["articulo"] for f in filas] == ["A0117119", "A0115102"]
