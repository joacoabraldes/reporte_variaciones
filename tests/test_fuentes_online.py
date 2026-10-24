"""De las fotos diarias online a un precio del mes.

El caso que importa es el de las promociones: Rapsodia dio +8,079% en un dia
porque se termino un 25% de descuento. Con el precio del mes (media de los dias,
formula 6 del INDEC) una promo corta pesa lo que dura, no el mes entero. Los
numeros de abajo estan hechos a mano.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from reporte.elemental import indice_elemental
from reporte.fuentes_online import (
    dias_por_mes,
    mes_de,
    precio_del_mes,
    precio_del_periodo,
    quotes_de_foto,
)


def test_quotes_de_foto_saltea_lo_que_no_se_puede_seguir():
    registros = [
        {"sitio": "puppis", "item_id": "A", "precio": 100.0},
        {"sitio": "puppis", "item_id": "", "precio": 50.0},      # sin clave
        {"sitio": "puppis", "item_id": "B", "precio": 0},        # sin precio
        {"sitio": "puppis", "item_id": "C", "precio": None},
        {"sitio": "puppis", "item_id": "A", "precio": 999.0},    # repetido
    ]
    assert quotes_de_foto(registros) == {("puppis", "A"): 100.0}


def test_el_sitio_entra_en_la_clave():
    q = quotes_de_foto([
        {"sitio": "topper", "item_id": "1", "precio": 10.0},
        {"sitio": "paruolo", "item_id": "1", "precio": 20.0},
    ])
    assert len(q) == 2


def test_mes_de():
    assert mes_de(date(2026, 10, 3)) == "2026-10"


def test_el_precio_del_mes_es_la_media_de_los_dias():
    fotos = {
        date(2026, 10, 1): {("s", "A"): 100.0},
        date(2026, 10, 2): {("s", "A"): 110.0},
        date(2026, 10, 3): {("s", "A"): 120.0},
    }
    assert precio_del_mes(fotos, minimo_dias=3) == {"2026-10": {("s", "A"): 110.0}}


def test_el_precio_de_una_semana_es_la_media_de_sus_dias():
    """Semana del 19 al 25/10/2026: A tiene 5 dias y entra; B tiene 4 y no.

    El 26 es de la semana siguiente y no cuenta: la ventana es cerrada.
    """
    fotos = {date(2026, 10, 19) + timedelta(d): {("s", "A"): 100.0 + d, ("s", "B"): 50.0}
             for d in range(5)}
    del fotos[date(2026, 10, 23)][("s", "B")]
    fotos[date(2026, 10, 26)] = {("s", "A"): 999.0}

    semana = precio_del_periodo(fotos, date(2026, 10, 19), date(2026, 10, 25), minimo_dias=5)

    assert semana == {("s", "A"): pytest.approx(102.0)}


def test_el_mes_como_ventana_da_lo_mismo_que_el_precio_del_mes():
    fotos = {date(2026, 10, 1) + timedelta(d): {("s", "A"): 100.0 + d} for d in range(31)}
    assert precio_del_periodo(fotos, date(2026, 10, 1), date(2026, 10, 31), minimo_dias=20) == \
        precio_del_mes(fotos, minimo_dias=20)["2026-10"]


def test_un_articulo_con_pocos_dias_no_tiene_precio_del_mes():
    fotos = {
        date(2026, 10, 1): {("s", "A"): 100.0, ("s", "B"): 50.0},
        date(2026, 10, 2): {("s", "A"): 100.0},
        date(2026, 10, 3): {("s", "A"): 100.0},
    }
    assert set(precio_del_mes(fotos, minimo_dias=3)["2026-10"]) == {("s", "A")}


def test_cada_mes_por_separado():
    fotos = {
        date(2026, 10, 31): {("s", "A"): 100.0},
        date(2026, 11, 1): {("s", "A"): 200.0},
    }
    meses = precio_del_mes(fotos, minimo_dias=1)
    assert meses == {"2026-10": {("s", "A"): 100.0}, "2026-11": {("s", "A"): 200.0}}
    assert dias_por_mes(fotos) == {"2026-10": 1, "2026-11": 1}


def _mes(anio, mes, dias, precio_por_dia):
    inicio = date(anio, mes, 1)
    return {inicio + timedelta(d): {("rapsodia", "X"): precio_por_dia(d)}
            for d in range(dias)}


def test_una_promo_corta_pesa_lo_que_dura_y_no_el_mes_entero():
    """El caso Rapsodia, en chico.

    Septiembre a precio lleno (100). En octubre hay un 25% de descuento los
    primeros 10 de 30 dias (75) y despues vuelve a 100.

    Comparando dos fotos sueltas —el ultimo dia de la promo contra el primero
    sin ella— da +33,3%, que es lo que paso con Rapsodia. Con el precio del mes:
    octubre = (10 x 75 + 20 x 100) / 30 = 91,667, o sea -8,33%: la promo existio
    un tercio del mes y pesa un tercio.
    """
    fotos = {}
    fotos.update(_mes(2026, 9, 30, lambda d: 100.0))
    fotos.update(_mes(2026, 10, 30, lambda d: 75.0 if d < 10 else 100.0))

    dos_fotos = indice_elemental(
        "x", fotos[date(2026, 10, 10)], fotos[date(2026, 10, 11)]
    )
    assert dos_fotos.variacion_pct == pytest.approx(33.333333, abs=1e-4)

    meses = precio_del_mes(fotos, minimo_dias=20)
    assert meses["2026-10"][("rapsodia", "X")] == pytest.approx(91.666667, abs=1e-4)
    mensual = indice_elemental("x", meses["2026-09"], meses["2026-10"])
    assert mensual.variacion_pct == pytest.approx(-8.333333, abs=1e-4)
