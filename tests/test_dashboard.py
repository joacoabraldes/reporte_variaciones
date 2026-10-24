"""Los datos del dashboard: tres tablas armadas desde toda la historia.

El dashboard no calcula nada: lee estas tablas. Cada periodo toma su calculo
mas reciente y dice cuando se calculo; nada de la historia se pierde.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from reporte.dashboard import tabla_clases, tabla_contra_indec, tabla_periodos, ventana_de
from reporte.historial import cargar_historial, guardar_corrida
from reporte.series import SERIE_GLOBAL, SERIE_SIN_ONLINE, division_de_serie, serie_de_rubro

NOMBRES = {"01": "Alimentos y bebidas no alcohólicas", "06": "Salud",
           "01.1.5": "Aceites, grasas y manteca", "06.1.1": "Productos farmacéuticos"}


def _serie(mes, serie, var, base="x"):
    return {"base": base, "mes": mes, "serie": serie, "variacion_pct": var,
            "nivel": "" if var == "" else 100 + var, "cobertura_ipc_pct": 26.97}


def _historial(tmp_path):
    guardar_corrida("semanal", datetime(2026, 10, 26, 9), {
        "series": [_serie("2026-W43", SERIE_GLOBAL, 0.5, "2026-W42"),
                   _serie("2026-W43", SERIE_SIN_ONLINE, 0.4, "2026-W42"),
                   _serie("2026-W43", serie_de_rubro("06", "Salud"), "", "2026-W42")],
        "detalle": [{"base": "2026-W42", "mes": "2026-W43", "nivel": "clase", "codigo": "01.1.5",
                     "indice": 1.01, "peso": 0.02},
                    {"base": "2026-W42", "mes": "2026-W43", "nivel": "articulo",
                     "codigo": "A0115102", "indice": 1.01, "peso": 0.54}],
    }, tmp_path)
    guardar_corrida("semanal", datetime(2026, 11, 2, 9), {
        "series": [_serie("2026-W43", SERIE_GLOBAL, 0.6, "2026-W42"),
                   _serie("2026-W44", SERIE_GLOBAL, 0.7, "2026-W43")],
    }, tmp_path)
    guardar_corrida("contra_indec", datetime(2026, 11, 14, 9), {"contra_indec": [
        {"base": "2026-09", "mes": "2026-10", "tipo": "rubro", "codigo": "07",
         "nombre": "Transporte", "nuestro_pct": 1.1, "indec_pct": 0.8, "diferencia": 0.3,
         "nota": ""}]}, tmp_path)
    return cargar_historial(tmp_path)


def test_el_nombre_de_un_rubro_es_parte_del_contrato():
    assert serie_de_rubro("06", "Salud") == "06 Salud"
    assert division_de_serie("06 Salud") == "06"
    assert division_de_serie(SERIE_GLOBAL) is None


def test_la_ventana_de_una_semana_y_de_un_mes():
    assert ventana_de("2026-W43") == (date(2026, 10, 19), date(2026, 10, 25))
    assert ventana_de("2026-09") == (date(2026, 9, 1), date(2026, 9, 30))


def test_los_periodos_toman_el_calculo_mas_reciente(tmp_path):
    filas = tabla_periodos(_historial(tmp_path))
    global_w43 = next(f for f in filas if f["periodo"] == "2026-W43" and f["tipo"] == "global")
    assert global_w43["variacion_pct"] == pytest.approx(0.6)
    assert global_w43["calculado_el"] == "2026-11-02T09:00:00"
    assert global_w43["desde"] == "2026-10-19" and global_w43["hasta"] == "2026-10-25"
    assert {(f["periodo"], f["tipo"]) for f in filas} == {
        ("2026-W43", "global"), ("2026-W43", "sin_online"), ("2026-W43", "rubro"),
        ("2026-W44", "global")}


def test_un_rubro_sin_dato_queda_vacio_y_no_en_cero(tmp_path):
    """Salud no tiene dato online esa semana: vacio. Un cero diria que no se movio."""
    salud = next(f for f in tabla_periodos(_historial(tmp_path)) if f["tipo"] == "rubro")
    assert salud["division"] == "06"
    assert salud["variacion_pct"] is None


def test_un_rubro_dice_cuanto_pesa_su_division(tmp_path):
    """Para mostrar cuanto de la division mide: Salud pesa 8% del IPC."""
    filas = tabla_periodos(_historial(tmp_path), pesos_division={"06": 8.0})
    salud = next(f for f in filas if f["tipo"] == "rubro")
    assert salud["peso_division_pct"] == 8.0
    assert all(f["peso_division_pct"] is None for f in filas if f["tipo"] != "rubro")


def test_las_clases_con_su_rubro_y_su_nombre(tmp_path):
    (fila,) = tabla_clases(_historial(tmp_path), NOMBRES)
    assert (fila["periodo"], fila["division"], fila["clase"], fila["nombre"]) == (
        "2026-W43", "01", "01.1.5", "Aceites, grasas y manteca")
    assert fila["variacion_pct"] == pytest.approx(1.0)


def test_la_comparacion_contra_el_indec(tmp_path):
    (fila,) = tabla_contra_indec(_historial(tmp_path))
    assert (fila["periodo"], fila["codigo"], fila["diferencia"]) == ("2026-10", "07", 0.3)
