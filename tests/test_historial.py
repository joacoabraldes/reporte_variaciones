"""La historia de resultados: cada corrida se suma y ninguna se pisa.

Joaquin (23/10/2026): el dashboard tiene que estar en un unico lugar y "no
mueran los datos viejos": cada semana y cada mes nuevos se suman, y se puede ir
a cualquier fecha. Donde se guarda en forma permanente (bucket o git) se decide
despues; esta es la unica puerta, y es lo unico que cambia ese dia.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from reporte.historial import cargar_historial, guardar_corrida, lo_ultimo_por_periodo


def _serie(mes: str, serie: str, var: float) -> dict:
    return {"base": "x", "mes": mes, "serie": serie, "variacion_pct": var, "nivel": 100 + var,
            "cobertura_ipc_pct": 26.97}


LUNES = datetime(2026, 10, 26, 9, 0, 0)
LUNES_SIGUIENTE = datetime(2026, 11, 2, 9, 0, 0)


def test_las_corridas_se_suman(tmp_path):
    guardar_corrida("semanal", LUNES, {"series": [_serie("2026-W43", "global", 0.5)]}, tmp_path)
    guardar_corrida("semanal", LUNES_SIGUIENTE,
                    {"series": [_serie("2026-W44", "global", 0.7)]}, tmp_path)

    filas = cargar_historial(tmp_path)["series"]

    assert sorted((f["mes"], f["calculado_el"]) for f in filas) == [
        ("2026-W43", "2026-10-26T09:00:00"), ("2026-W44", "2026-11-02T09:00:00")]
    assert all(f["ventana"] == "semanal" for f in filas)


def test_una_corrida_que_ya_existe_no_se_reescribe(tmp_path):
    guardar_corrida("semanal", LUNES, {"series": [_serie("2026-W43", "global", 0.5)]}, tmp_path)
    with pytest.raises(FileExistsError):
        guardar_corrida("semanal", LUNES, {"series": [_serie("2026-W43", "global", 9.9)]},
                        tmp_path)
    (fila,) = cargar_historial(tmp_path)["series"]
    assert float(fila["variacion_pct"]) == 0.5


def test_un_periodo_recalculado_muestra_lo_ultimo_y_conserva_lo_anterior(tmp_path):
    """El 2/11 se recalcula la semana 43 (por ejemplo, con el filtro nuevo)."""
    guardar_corrida("semanal", LUNES, {"series": [_serie("2026-W43", "global", 0.5)]}, tmp_path)
    guardar_corrida("semanal", LUNES_SIGUIENTE, {"series": [
        _serie("2026-W43", "global", 0.6), _serie("2026-W44", "global", 0.7)]}, tmp_path)

    todas = cargar_historial(tmp_path)["series"]
    vigentes = lo_ultimo_por_periodo(todas, ("ventana", "mes", "serie"))

    assert len(todas) == 3, "lo anterior no se borra"
    w43 = next(f for f in vigentes if f["mes"] == "2026-W43")
    assert float(w43["variacion_pct"]) == 0.6
    assert w43["calculado_el"] == "2026-11-02T09:00:00"
    assert len(vigentes) == 2


def test_semanas_y_meses_no_se_mezclan(tmp_path):
    guardar_corrida("semanal", LUNES, {"series": [_serie("2026-W43", "global", 0.5)]}, tmp_path)
    guardar_corrida("mensual", LUNES, {"series": [_serie("2026-09", "global", 2.3)]}, tmp_path)
    vigentes = lo_ultimo_por_periodo(cargar_historial(tmp_path)["series"],
                                     ("ventana", "mes", "serie"))
    assert {(f["ventana"], f["mes"]) for f in vigentes} == {
        ("semanal", "2026-W43"), ("mensual", "2026-09")}


def test_sin_historia_no_rompe(tmp_path):
    assert cargar_historial(tmp_path / "no_existe") == {}
