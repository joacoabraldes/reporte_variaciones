"""Ventana temporal: mismo metodo para semanas y meses.

Lo que se testea acá es que la ventana sea de verdad un parametro: que la misma
serie de observaciones diarias, agregada por semana o por mes, de quotes
coherentes, y que nada del tipo de ventana se filtre hacia el calculo.
"""

from __future__ import annotations

from datetime import date

import duckdb
import pytest

from reporte.elemental import MOTIVO_POCOS_DIAS, indice_elemental
from reporte.periodo import (
    MENSUAL,
    SEMANAL,
    ParametrosVentana,
    Periodo,
    VariacionPeriodo,
    meses_completos,
    quotes_del_periodo,
    semanas_iso_completas,
    serie_encadenada,
    validar_encadenable,
)

CON = duckdb.connect()


def obs(filas: list[tuple]) -> duckdb.DuckDBPyRelation:
    """Relacion de observaciones diarias sinteticas.

    filas = [(fecha, comercio, sucursal, producto, precio_lista, precio_efectivo)]
    """
    valores = ", ".join(
        f"(DATE '{f}', '{c}', '{s}', '{p}', {pl}, {pe})" for f, c, s, p, pl, pe in filas
    )
    return CON.sql(
        f"SELECT * FROM (VALUES {valores}) "
        f"AS t(fecha, id_comercio, id_sucursal, id_producto, "
        f"precio_lista, precio_efectivo)"
    )


def dias(inicio: date, n: int) -> list[date]:
    from datetime import timedelta

    return [inicio + timedelta(days=i) for i in range(n)]


def params(tipo, minimo_dias: int) -> ParametrosVentana:
    return ParametrosVentana(
        tipo=tipo, minimo_dias_quote=minimo_dias,
        tope_ratio=2.0, umbral_mad=3.5, minimo_quotes_mad=15,
    )


# --------------------------------------------------------------------------- #
# Construccion de periodos
# --------------------------------------------------------------------------- #


def test_la_semana_iso_va_de_lunes_a_domingo():
    p = Periodo.semana_iso(2026, 33)
    assert p.inicio == date(2026, 8, 10)
    assert p.fin == date(2026, 8, 16)
    assert p.etiqueta == "2026-W33"
    assert p.tipo == SEMANAL
    assert len(p.dias_esperados) == 7


def test_el_mes_va_del_uno_al_ultimo():
    p = Periodo.mes(2026, 8)
    assert (p.inicio, p.fin) == (date(2026, 8, 1), date(2026, 8, 31))
    assert p.etiqueta == "2026-08"
    assert p.tipo == MENSUAL
    assert len(p.dias_esperados) == 31


def test_solo_entran_las_semanas_completas():
    """La captura arranco el 27/07/2026, que fue lunes: W31, W32 y W33 enteras."""
    ps = semanas_iso_completas(date(2026, 7, 27), date(2026, 8, 17))
    assert [p.etiqueta for p in ps] == ["2026-W31", "2026-W32", "2026-W33"]
    # El 17 es lunes de la W34: la semana esta incompleta y no entra.
    assert all(p.fin <= date(2026, 8, 17) for p in ps)


def test_un_mes_incompleto_no_entra():
    """Julio y agosto de 2026 estan incompletos: no hay ningun mes cerrado."""
    assert meses_completos(date(2026, 7, 27), date(2026, 8, 17)) == []


def test_un_periodo_invertido_no_se_puede_construir():
    with pytest.raises(ValueError, match="invertido"):
        Periodo(date(2026, 8, 10), date(2026, 8, 1), "raro", SEMANAL)


# --------------------------------------------------------------------------- #
# La misma serie diaria, por semana y por mes
# --------------------------------------------------------------------------- #


def test_semana_y_mes_dan_quotes_coherentes_sobre_la_misma_serie():
    """Un precio constante da la misma mediana con cualquier ventana.

    Es la comprobacion de que la ventana es un parametro y no cambia el metodo.
    """
    filas = [(d, "10", "7", "EAN1", 1000.0, 1000.0) for d in dias(date(2026, 8, 1), 31)]
    r = obs(filas)

    sem = quotes_del_periodo(r, Periodo.semana_iso(2026, 32), params(SEMANAL, 5))
    men = quotes_del_periodo(r, Periodo.mes(2026, 8), params(MENSUAL, 20))

    clave = ("10", "7", "EAN1")
    assert sem.precios[clave] == 1000.0
    assert men.precios[clave] == 1000.0
    assert sem.n_dias[clave] == 7
    assert men.n_dias[clave] == 31


def test_la_mediana_ignora_un_precio_mal_cargado():
    """Un cero de mas un solo dia no mueve la mediana."""
    filas = [(d, "10", "7", "EAN1", 1000.0, 1000.0) for d in dias(date(2026, 8, 10), 7)]
    filas[3] = (filas[3][0], "10", "7", "EAN1", 10000.0, 10000.0)

    r = quotes_del_periodo(obs(filas), Periodo.semana_iso(2026, 33), params(SEMANAL, 5))
    assert r.precios[("10", "7", "EAN1")] == 1000.0


def test_la_ventana_recorta_de_verdad():
    """Los dias fuera del periodo no entran en la mediana."""
    filas = [(d, "10", "7", "EAN1", 100.0, 100.0) for d in dias(date(2026, 8, 3), 7)]
    filas += [(d, "10", "7", "EAN1", 900.0, 900.0) for d in dias(date(2026, 8, 10), 7)]

    r = obs(filas)
    w32 = quotes_del_periodo(r, Periodo.semana_iso(2026, 32), params(SEMANAL, 5))
    w33 = quotes_del_periodo(r, Periodo.semana_iso(2026, 33), params(SEMANAL, 5))
    assert w32.precios[("10", "7", "EAN1")] == 100.0
    assert w33.precios[("10", "7", "EAN1")] == 900.0


def test_se_pueden_pedir_las_dos_series_de_precio():
    filas = [(d, "10", "7", "EAN1", 100.0, 80.0) for d in dias(date(2026, 8, 10), 7)]
    p, pa = Periodo.semana_iso(2026, 33), params(SEMANAL, 5)
    assert quotes_del_periodo(obs(filas), p, pa, "precio_lista").precios[
        ("10", "7", "EAN1")] == 100.0
    assert quotes_del_periodo(obs(filas), p, pa, "precio_efectivo").precios[
        ("10", "7", "EAN1")] == 80.0


# --------------------------------------------------------------------------- #
# Minimo de dias
# --------------------------------------------------------------------------- #


def test_un_quote_con_pocos_dias_se_descarta_y_se_reporta():
    """Es la señal de que un comercio dejo de reportar: no se pierde en silencio."""
    completo = [(d, "10", "7", "A", 100.0, 100.0) for d in dias(date(2026, 8, 10), 7)]
    parcial = [(d, "20", "7", "B", 200.0, 200.0) for d in dias(date(2026, 8, 10), 3)]

    r = quotes_del_periodo(
        obs(completo + parcial), Periodo.semana_iso(2026, 33), params(SEMANAL, 5)
    )
    assert ("10", "7", "A") in r.precios
    assert ("20", "7", "B") not in r.precios
    assert r.n_dias[("20", "7", "B")] == 3, "el conteo se conserva aunque se descarte"

    d = r.descartados_por_dias[0]
    assert d.quote == ("20", "7", "B")
    assert d.motivo == MOTIVO_POCOS_DIAS
    assert "3 dias" in d.detalle


def test_el_minimo_sale_de_los_parametros_de_la_ventana():
    """El mismo quote entra con el minimo semanal y no con el mensual."""
    filas = [(d, "10", "7", "A", 100.0, 100.0) for d in dias(date(2026, 8, 10), 7)]
    r = obs(filas)
    p = Periodo.semana_iso(2026, 33)

    assert quotes_del_periodo(r, p, params(SEMANAL, 5)).n_quotes == 1
    # 20 dias es el minimo mensual: sobre una semana no lo alcanza nadie.
    assert quotes_del_periodo(r, p, params(SEMANAL, 20)).n_quotes == 0


def test_los_parametros_del_yaml_se_aplican_segun_la_ventana():
    """La config real del repo, no una inventada por el test."""
    sem = ParametrosVentana.desde_yaml(SEMANAL)
    men = ParametrosVentana.desde_yaml(MENSUAL)

    assert sem.minimo_dias_quote == 5
    assert men.minimo_dias_quote == 20
    assert sem.tope_ratio == 1.4, "la banda semanal tiene que ser mas angosta"
    assert men.tope_ratio == 2.0
    assert sem.tope_ratio < men.tope_ratio
    # Lo que NO cambia entre ventanas.
    assert sem.umbral_mad == men.umbral_mad == 3.5
    assert sem.minimo_quotes_mad == men.minimo_quotes_mad == 15


def test_los_parametros_se_eligen_solos_a_partir_del_periodo():
    assert ParametrosVentana.para(Periodo.semana_iso(2026, 33)).tipo == SEMANAL
    assert ParametrosVentana.para(Periodo.mes(2026, 8)).tipo == MENSUAL


def test_una_ventana_sin_parametros_falla_claro(tmp_path):
    p = tmp_path / "parametros.yaml"
    p.write_text("ventanas:\n  mensual: {minimo_dias_quote: 20, tope_ratio: 2.0, "
                 "umbral_mad: 3.5, minimo_quotes_mad: 15}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no hay parametros para la ventana"):
        ParametrosVentana.desde_yaml(SEMANAL, p)


def test_no_se_pueden_usar_parametros_de_otra_ventana():
    filas = [(d, "10", "7", "A", 100.0, 100.0) for d in dias(date(2026, 8, 10), 7)]
    with pytest.raises(ValueError, match="aplicados a un periodo"):
        quotes_del_periodo(
            obs(filas), Periodo.semana_iso(2026, 33), params(MENSUAL, 20)
        )


# --------------------------------------------------------------------------- #
# Huecos dentro del periodo
# --------------------------------------------------------------------------- #


def test_un_dia_sin_observaciones_en_el_medio_se_reporta():
    todos = dias(date(2026, 8, 10), 7)
    sin_el_jueves = [d for d in todos if d != date(2026, 8, 13)]
    filas = [(d, "10", "7", "A", 100.0, 100.0) for d in sin_el_jueves]

    r = quotes_del_periodo(obs(filas), Periodo.semana_iso(2026, 33), params(SEMANAL, 5))
    assert r.huecos == [date(2026, 8, 13)]
    assert len(r.dias_presentes) == 6


def test_una_semana_completa_no_tiene_huecos():
    filas = [(d, "10", "7", "A", 100.0, 100.0) for d in dias(date(2026, 8, 10), 7)]
    r = quotes_del_periodo(obs(filas), Periodo.semana_iso(2026, 33), params(SEMANAL, 5))
    assert r.huecos == []


# --------------------------------------------------------------------------- #
# La guarda del encadenamiento
# --------------------------------------------------------------------------- #


def test_no_se_puede_encadenar_una_semana_con_un_mes():
    """Cuatro semanas encadenadas no dan la variacion mensual."""
    with pytest.raises(ValueError, match="tipo distinto"):
        validar_encadenable(Periodo.semana_iso(2026, 31), Periodo.mes(2026, 8))


def test_no_se_pueden_encadenar_periodos_salteados():
    with pytest.raises(ValueError, match="no consecutivos"):
        validar_encadenable(Periodo.semana_iso(2026, 31), Periodo.semana_iso(2026, 33))


def test_dos_semanas_consecutivas_si_encadenan():
    validar_encadenable(Periodo.semana_iso(2026, 31), Periodo.semana_iso(2026, 32))
    validar_encadenable(Periodo.mes(2026, 7), Periodo.mes(2026, 8))


def test_la_variacion_valida_sus_periodos_al_construirse():
    with pytest.raises(ValueError, match="tipo distinto"):
        VariacionPeriodo(Periodo.semana_iso(2026, 31), Periodo.mes(2026, 8), 1.05)


def test_la_serie_encadenada_compone_las_variaciones():
    v1 = VariacionPeriodo(Periodo.semana_iso(2026, 31), Periodo.semana_iso(2026, 32), 1.05)
    v2 = VariacionPeriodo(Periodo.semana_iso(2026, 32), Periodo.semana_iso(2026, 33), 1.05)

    serie = serie_encadenada([v1, v2], base=100.0)
    assert [e for e, _ in serie] == ["2026-W31", "2026-W32", "2026-W33"]
    assert serie[-1][1] == pytest.approx(100 * 1.05**2)


def test_una_serie_con_un_salto_falla():
    v1 = VariacionPeriodo(Periodo.semana_iso(2026, 31), Periodo.semana_iso(2026, 32), 1.05)
    v3 = VariacionPeriodo(Periodo.semana_iso(2026, 33), Periodo.semana_iso(2026, 34), 1.05)
    with pytest.raises(ValueError, match="salto"):
        serie_encadenada([v1, v3])


# --------------------------------------------------------------------------- #
# Extremo a extremo: la frontera se sostiene
# --------------------------------------------------------------------------- #


def test_el_calculo_no_sabe_de_que_ventana_vino():
    """Los quotes de una semana y los de un mes entran igual a indice_elemental.

    Es LA regla del diseño: `elemental.py` recibe dos dict[quote, precio] y no
    tiene forma de distinguir el origen. Si algun dia hiciera falta preguntar
    `periodo.tipo` ahi adentro, algo se filtro.
    """
    base = [(d, "10", "7", f"E{i}", 100.0, 100.0)
            for d in dias(date(2026, 8, 3), 7) for i in range(20)]
    actual = [(d, "10", "7", f"E{i}", 110.0, 110.0)
              for d in dias(date(2026, 8, 10), 7) for i in range(20)]
    r = obs(base + actual)

    pa = params(SEMANAL, 5)
    q1 = quotes_del_periodo(r, Periodo.semana_iso(2026, 32), pa)
    q2 = quotes_del_periodo(r, Periodo.semana_iso(2026, 33), pa)

    # indice_elemental recibe dos diccionarios y nada mas.
    res = indice_elemental(
        "01.1.1", q1.precios, q2.precios,
        umbral_mad=pa.umbral_mad, tope_ratio=pa.tope_ratio,
        minimo_quotes=pa.minimo_quotes_mad,
    )
    assert res.indice == pytest.approx(1.10)
    assert res.n_quotes == 20
