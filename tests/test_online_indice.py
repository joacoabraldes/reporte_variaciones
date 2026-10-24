"""Lo online dentro del indice: quotes por articulo ENGHo y suma con SEPA.

Los numeros estan hechos a mano. El que importa es el ultimo: con lo online
sumado, el articulo sale de UN Jevons sobre los quotes de las dos fuentes, no de
un promedio de dos indices.
"""

from __future__ import annotations

import math
from datetime import date, timedelta

import pytest

from reporte.elemental import indice_elemental
from reporte.online_indice import (
    clases_online,
    clasificacion_por_item,
    por_categoria,
    quotes_por_articulo,
    sumar_grupos,
)

CLASIF = clasificacion_por_item([
    ("tascani", "1", "online.jean_hombre", "03.1.2", "A0312103"),
    ("legacy", "9", "online.jean_hombre", "03.1.2", "A0312103"),
    ("topper", "5", "online.zapatillas_hombre", "03.2.1", "A0321107"),
])


NOV = (date(2026, 11, 1), date(2026, 11, 30))


def _mes(anio, mes, dias, precios):
    """`dias` fotos diarias desde el 1, con el mismo precio por item."""
    return {date(anio, mes, 1) + timedelta(d): dict(precios) for d in range(dias)}


def test_quotes_del_mes_agrupados_por_articulo():
    fotos = _mes(2026, 11, 25, {("tascani", "1"): 100.0, ("legacy", "9"): 200.0,
                                ("topper", "5"): 50.0})
    q = quotes_por_articulo(fotos, CLASIF, NOV[0], NOV[1], minimo_dias=20)
    assert q == {
        "A0312103": {("tascani", "1"): 100.0, ("legacy", "9"): 200.0},
        "A0321107": {("topper", "5"): 50.0},
    }


def test_por_categoria_reagrupa_los_mismos_quotes():
    por_art = {"A0312103": {("tascani", "1"): 100.0, ("legacy", "9"): 200.0},
               "A0321107": {("topper", "5"): 50.0}}
    assert por_categoria(por_art, CLASIF) == {
        "online.jean_hombre": {("tascani", "1"): 100.0, ("legacy", "9"): 200.0},
        "online.zapatillas_hombre": {("topper", "5"): 50.0},
    }


def test_un_item_sin_clasificar_no_entra():
    fotos = _mes(2026, 11, 25, {("tascani", "1"): 100.0, ("tascani", "777"): 10.0})
    q = quotes_por_articulo(fotos, CLASIF, NOV[0], NOV[1], minimo_dias=20)
    assert ("tascani", "777") not in q.get("A0312103", {})


def test_octubre_no_alcanza_el_minimo_de_dias():
    """Las fotos arrancaron el 14/10: 18 dias como mucho, contra 20 minimos."""
    fotos = {date(2026, 10, 14) + timedelta(d): {("tascani", "1"): 100.0}
             for d in range(18)}
    assert quotes_por_articulo(fotos, CLASIF, date(2026, 10, 1), date(2026, 10, 31), minimo_dias=20) == {}


def test_por_semana_alcanzan_cinco_dias_de_fotos():
    """La primera semana con rubros online: 19 al 25/10 contra 12 al 18/10.

    Las fotos arrancaron el miercoles 14: la semana del 12 tiene 5 dias (14 a
    18) y alcanza el minimo semanal. Un articulo que falto un dia queda afuera.
    """
    fotos = {date(2026, 10, 14) + timedelta(d): {("tascani", "1"): 100.0, ("legacy", "9"): 200.0}
             for d in range(12)}
    del fotos[date(2026, 10, 16)][("legacy", "9")]

    semana = quotes_por_articulo(fotos, CLASIF, date(2026, 10, 12), date(2026, 10, 18),
                                 minimo_dias=5)

    assert semana == {"A0312103": {("tascani", "1"): 100.0}}


def test_sumar_no_pisa_y_suma_articulos_nuevos():
    sepa = {"A0111101": {(1, 1, "p"): 10.0}}
    online = {"A0312103": {("tascani", "1"): 100.0}}
    juntos = sumar_grupos(sepa, online)
    assert juntos == {"A0111101": {(1, 1, "p"): 10.0},
                      "A0312103": {("tascani", "1"): 100.0}}
    assert sepa == {"A0111101": {(1, 1, "p"): 10.0}}   # no muta la entrada


def test_una_clave_repetida_corta():
    sepa = {"A": {("x", "1"): 1.0}}
    with pytest.raises(ValueError, match="no deberian poder chocar"):
        sumar_grupos(sepa, {"A": {("x", "1"): 2.0}})


def test_las_clases_que_agrega_lo_online():
    assert clases_online(CLASIF) == {"03.1.2", "03.2.1"}


def test_el_articulo_sale_de_un_solo_jevons_sobre_las_dos_fuentes():
    """Dos quotes de SEPA que suben 10% y uno online que sube 21%.

    Un solo Jevons: (1,10 x 1,10 x 1,21)^(1/3) = 1,1335, o sea +13,35%.
    Promediar los dos indices por fuente daria (1,10 + 1,21) / 2 = +15,5%.
    """
    base = sumar_grupos({"A": {(1, 1, "p"): 100.0, (1, 2, "p"): 100.0}},
                        {"A": {("tascani", "1"): 100.0}})
    actual = sumar_grupos({"A": {(1, 1, "p"): 110.0, (1, 2, "p"): 110.0}},
                          {"A": {("tascani", "1"): 121.0}})
    r = indice_elemental("A", base["A"], actual["A"], umbral_mad=math.inf)
    assert r.n_quotes == 3
    assert r.indice == pytest.approx((1.10 * 1.10 * 1.21) ** (1 / 3))


def test_cargar_fotos_junta_los_sitios_por_dia(tmp_path):
    import json

    from reporte.online_indice import cargar_fotos

    for sitio, item, precio in [("tascani", "1", 100.0), ("topper", "5", 50.0)]:
        d = tmp_path / f"sitio={sitio}" / "fecha=2026-11-03"
        d.mkdir(parents=True)
        (d / "datos.json").write_text(json.dumps(
            [{"sitio": sitio, "item_id": item, "precio": precio}]), encoding="utf-8")
    fotos = cargar_fotos(tmp_path)
    assert fotos == {date(2026, 11, 3): {("tascani", "1"): 100.0, ("topper", "5"): 50.0}}


def test_cargar_fotos_sin_nada_no_rompe(tmp_path):
    from reporte.online_indice import cargar_fotos

    assert cargar_fotos(tmp_path / "no_existe") == {}


# --------------------------------------------------------------------------- #
# Combustibles: el dataset de precios en surtidor de Energia
# --------------------------------------------------------------------------- #

COLUMNAS_SURTIDOR = ("idempresa", "empresabandera", "idproducto", "producto",
                     "tipohorario", "precio", "fecha_vigencia")


def _foto_surtidor(raiz, dia: date, filas: list[tuple]) -> None:
    import csv

    d = raiz / f"fecha={dia}"
    d.mkdir(parents=True)
    with (d / "vigentes.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(COLUMNAS_SURTIDOR)
        w.writerows(filas)


def test_combustibles_lee_el_diurno_y_corta_lo_viejo(tmp_path):
    """Las tres trampas del dataset real.

    Cada precio viene dos veces (Diurno y Nocturno); el 70% de los precios
    vigentes tiene mas de un año (estaciones que dejaron de informar, sobre todo
    YPF); y puede aparecer un producto que no es ninguno de los cinco.
    """
    from reporte.online_indice import cargar_combustibles

    _foto_surtidor(tmp_path, date(2026, 11, 3), [
        ("1376", "SHELL", "2", "Nafta (súper) entre 92 y 95 Ron", "Diurno", "1500",
         "2026-10-24 11:13:00"),
        ("1376", "SHELL", "2", "Nafta (súper) entre 92 y 95 Ron", "Nocturno", "1600",
         "2026-10-24 11:13:00"),
        ("999", "YPF", "2", "Nafta (súper) entre 92 y 95 Ron", "Diurno", "900",
         "2025-06-20 09:00:00"),
        ("1376", "SHELL", "77", "Kerosene", "Diurno", "800", "2026-10-24 11:13:00"),
    ])

    fotos, categoria = cargar_combustibles(tmp_path)

    clave = ("surtidor", "1376", "2")
    assert fotos == {date(2026, 11, 3): {clave: 1500.0}}
    assert categoria == {clave: "combustibles.nafta_super"}


def test_el_corte_de_antiguedad_es_un_parametro(tmp_path):
    from reporte.online_indice import cargar_combustibles

    _foto_surtidor(tmp_path, date(2026, 11, 3), [
        ("999", "YPF", "19", "Gas Oil Grado 2", "Diurno", "900", "2025-06-20 09:00:00"),
    ])
    assert cargar_combustibles(tmp_path)[0] == {}
    fotos, categoria = cargar_combustibles(tmp_path, antiguedad_dias=600)
    assert fotos == {date(2026, 11, 3): {("surtidor", "999", "19"): 900.0}}
    assert categoria == {("surtidor", "999", "19"): "combustibles.gasoil"}


def test_combustibles_sin_fotos_no_rompe(tmp_path):
    from reporte.online_indice import cargar_combustibles

    assert cargar_combustibles(tmp_path / "no_existe") == ({}, {})


def test_el_surtidor_entra_por_el_mismo_precio_del_mes(tmp_path):
    """Una estacion que cambia de precio el 16: el mes es la media de sus fotos.

    Noviembre: 15 dias a 1000 y 15 a 1100 -> 1050. Diciembre: 30 dias a 1155.
    Con la misma clasificacion que lo online, sale agrupado por articulo ENGHo.
    """
    from reporte.online_indice import cargar_combustibles

    for d in range(30):
        dia = date(2026, 11, 1) + timedelta(d)
        _foto_surtidor(tmp_path, dia, [
            ("1376", "SHELL", "2", "Nafta", "Diurno", "1000" if d < 15 else "1100",
             f"{dia} 08:00:00"),
        ])
    for d in range(30):
        dia = date(2026, 12, 1) + timedelta(d)
        _foto_surtidor(tmp_path, dia, [
            ("1376", "SHELL", "2", "Nafta", "Diurno", "1155", "2026-12-01 08:00:00"),
        ])
    fotos, categoria = cargar_combustibles(tmp_path)
    clasif = {k: (c, "07.2.2", "A0722102") for k, c in categoria.items()}

    nov = quotes_por_articulo(fotos, clasif, *NOV, minimo_dias=20)
    dic = quotes_por_articulo(fotos, clasif, date(2026, 12, 1), date(2026, 12, 31), minimo_dias=20)

    assert nov == {"A0722102": {("surtidor", "1376", "2"): pytest.approx(1050.0)}}
    assert dic == {"A0722102": {("surtidor", "1376", "2"): pytest.approx(1155.0)}}
