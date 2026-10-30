"""Las carnicerias del IPCVA: el canal tradicional que SEPA no tiene.

El INDEC calcula cada variedad por separado en supermercados y en negocios
tradicionales y las combina con la proporcion fija de lo que compran los hogares
en cada tipo de negocio (formula 8 de la metodologia). Aca: supermercados es el
Jevons de SEPA, carnicerias es el precio por corte del IPCVA, y la proporcion
sale de la ENGHo 2017/18 (variable `tipo_negocio`).

Los numeros de prueba estan hechos a mano o son del informe del IPCVA de
septiembre de 2026.
"""

from __future__ import annotations

import math

import duckdb
import pytest

from reporte.carnicerias import (
    CORTES_DEL_IPCVA,
    cargar_config,
    relativos_por_plaza,
    tradicional_por_articulo,
)
from reporte.elemental import combinar_canales
from reporte.lectura import COLUMNAS_IPCVA, leer_precios_ipcva
from reporte.ponderadores import conectar, participacion_supermercados

PESOS = {"GBA": 0.4467, "Pampeana": 0.3419, "Noroeste": 0.0688,
         "Cuyo": 0.0518, "Patagonia": 0.0457, "Noreste": 0.0451}


def _fila(plaza, corte, mes, precio, mes_informe="2026-09"):
    return {"mes_informe": mes_informe, "plaza": plaza, "corte": corte,
            "mes": mes, "precio": precio}


# Del informe de septiembre de 2026 (tablas 3, 5 y 7).
FILAS = [
    _fila("AMBA", "Picada común", "2026-08", 10847.0),
    _fila("AMBA", "Picada común", "2026-09", 10839.0),
    _fila("Rosario", "Picada común", "2026-08", 10224.0),
    _fila("Rosario", "Picada común", "2026-09", 10223.0),
    _fila("Córdoba", "Picada común", "2026-08", 11598.0),
    _fila("Córdoba", "Picada común", "2026-09", 11524.0),
]
PLAZAS = {"AMBA": "GBA", "Rosario": "Pampeana", "Córdoba": "Pampeana"}


# --------------------------------------------------------------------------- #
# La combinacion de los dos canales (formula 8)
# --------------------------------------------------------------------------- #


def test_combina_los_dos_canales_con_la_proporcion_de_supermercados():
    """SEPA -10%, carnicerias 0%, 25% del gasto en supermercados: 0,9^0,25."""
    r = combinar_canales({"A0112108": 0.90}, {"A0112108": 1.00}, {"A0112108": 0.25})
    assert r["A0112108"] == pytest.approx(0.9 ** 0.25)


def test_es_geometrica_y_no_aritmetica():
    """+10% y -10% al 50%: la geometrica da 0,99499, la aritmetica daria 1."""
    r = combinar_canales({"a": 1.10}, {"a": 0.90}, {"a": 0.5})
    assert r["a"] == pytest.approx(math.sqrt(1.10 * 0.90))


def test_todo_en_supermercados_es_sepa_y_nada_es_el_ipcva():
    assert combinar_canales({"a": 0.9}, {"a": 1.1}, {"a": 1.0})["a"] == pytest.approx(0.9)
    assert combinar_canales({"a": 0.9}, {"a": 1.1}, {"a": 0.0})["a"] == pytest.approx(1.1)


def test_un_articulo_sin_carniceria_queda_como_esta():
    r = combinar_canales({"a": 0.9, "b": 1.05}, {"a": 1.0}, {"a": 0.5, "b": 0.5})
    assert r["b"] == 1.05


def test_una_carniceria_sin_sepa_no_agrega_un_articulo():
    """Sin el lado de supermercados el articulo no tiene peso calculado: no entra."""
    r = combinar_canales({"a": 0.9}, {"a": 1.0, "lomo": 1.02}, {"a": 0.5, "lomo": 0.3})
    assert set(r) == {"a"}


def test_sin_la_proporcion_de_supermercados_corta():
    """Si faltara, el articulo quedaria con SEPA solo y nadie se enteraria."""
    with pytest.raises(ValueError, match="A0112108"):
        combinar_canales({"A0112108": 0.9}, {"A0112108": 1.0}, {})


def test_una_proporcion_fuera_de_cero_a_uno_corta():
    with pytest.raises(ValueError, match="proporcion"):
        combinar_canales({"a": 0.9}, {"a": 1.0}, {"a": 1.2})


# --------------------------------------------------------------------------- #
# La proporcion de supermercados, de la ENGHo
# --------------------------------------------------------------------------- #


def _con_sintetica(filas):
    """filas = [(region, articulo, tipo_negocio, pondera, monto)]."""
    con = duckdb.connect()
    con.execute("CREATE TABLE gastos (region INT, articulo VARCHAR, tipo_negocio INT, "
                "pondera DOUBLE, monto DOUBLE)")
    con.executemany("INSERT INTO gastos VALUES (?,?,?,?,?)", filas)
    return con


def test_la_proporcion_es_el_gasto_en_supermercados_sobre_el_total():
    """300 en supermercado (1), 600 en especializado (3), 100 en autoservicio (2)."""
    con = _con_sintetica([(1, "A0112108", 1, 1.0, 300.0), (1, "A0112108", 3, 1.0, 600.0),
                          (1, "A0112108", 2, 1.0, 100.0)])
    assert participacion_supermercados(con, "GBA")["A0112108"] == pytest.approx(0.30)


def test_la_proporcion_usa_el_factor_de_expansion_y_la_region():
    """GBA: 10x10 = 100 en super contra 100 afuera. Pampeana: 20 contra 80."""
    con = _con_sintetica([(1, "a", 1, 10.0, 10.0), (1, "a", 3, 1.0, 100.0),
                          (2, "a", 1, 1.0, 20.0), (2, "a", 3, 1.0, 80.0)])
    assert participacion_supermercados(con, "GBA")["a"] == pytest.approx(0.5)
    assert participacion_supermercados(con, "Pampeana")["a"] == pytest.approx(0.2)
    assert participacion_supermercados(con, "nacional")["a"] == pytest.approx(120 / 300)


@pytest.fixture(scope="module")
def engho():
    con = conectar()
    yield con
    con.close()


def test_en_la_engho_real_la_carne_se_compra_sobre_todo_fuera_del_super(engho):
    """Medido el 28/10/2026: la carne picada, 21,9% en supermercados en el pais."""
    p = participacion_supermercados(engho, "nacional")
    assert p["A0112108"] == pytest.approx(0.219, abs=0.001)
    assert participacion_supermercados(engho, "GBA")["A0112108"] < 0.5


# --------------------------------------------------------------------------- #
# Los precios del IPCVA
# --------------------------------------------------------------------------- #


def test_el_relativo_de_cada_plaza_sale_del_informe_del_mes_actual():
    rel = relativos_por_plaza(FILAS, "2026-08", "2026-09")
    assert rel[("AMBA", "Picada común")] == pytest.approx(10839 / 10847)


def test_no_mezcla_el_precio_base_de_un_informe_con_el_actual_de_otro():
    """El informe de agosto tambien trae agosto, quizas revisado: se usa el de septiembre."""
    filas = FILAS + [_fila("AMBA", "Picada común", "2026-08", 9999.0, mes_informe="2026-08")]
    rel = relativos_por_plaza(filas, "2026-08", "2026-09")
    assert rel[("AMBA", "Picada común")] == pytest.approx(10839 / 10847)


def test_sin_el_informe_del_mes_no_hay_relativos():
    assert relativos_por_plaza(FILAS, "2026-09", "2026-10") == {}


def test_cada_region_toma_sus_plazas_y_las_demas_las_de_todas():
    """GBA es AMBA; Pampeana, Rosario y Cordoba; el resto, GBA y Pampeana con sus pesos."""
    rel = relativos_por_plaza(FILAS, "2026-08", "2026-09")
    pares = {"A0112108": "Picada común"}
    gba = tradicional_por_articulo(rel, pares, PLAZAS, "GBA", PESOS)
    pam = tradicional_por_articulo(rel, pares, PLAZAS, "Pampeana", PESOS)
    noa = tradicional_por_articulo(rel, pares, PLAZAS, "Noroeste", PESOS)
    nac = tradicional_por_articulo(rel, pares, PLAZAS, "nacional", PESOS)
    amba = 10839 / 10847
    pampeana = math.sqrt(10223 / 10224 * 11524 / 11598)
    a = 0.4467 / (0.4467 + 0.3419)
    assert gba["A0112108"] == pytest.approx(amba)
    assert pam["A0112108"] == pytest.approx(pampeana)
    assert noa["A0112108"] == pytest.approx(amba ** a * pampeana ** (1 - a))
    assert nac["A0112108"] == noa["A0112108"]


def test_un_corte_que_el_informe_no_trae_corta():
    """Un nombre mal escrito dejaria el articulo con SEPA solo, sin aviso."""
    rel = relativos_por_plaza(FILAS, "2026-08", "2026-09")
    with pytest.raises(ValueError, match="Picada comun"):
        tradicional_por_articulo(rel, {"A0112108": "Picada comun"}, PLAZAS, "GBA", PESOS)


def test_los_pares_del_config_son_cortes_del_ipcva_y_articulos_de_carne():
    config = cargar_config()
    assert config.pares, "el config no tiene pares"
    assert set(config.pares.values()) <= CORTES_DEL_IPCVA
    assert all(a.startswith("A01121") for a in config.pares)
    assert set(config.plazas.values()) <= set(PESOS)


def test_el_contrato_del_ipcva_es_el_que_publica_captura():
    """Escrito literal: es la misma tupla que `precios.online.ipcva.COLUMNAS`."""
    assert COLUMNAS_IPCVA == ("mes_informe", "plaza", "corte", "mes", "precio")


def _csv(ruta, columnas, filas):
    ruta.parent.mkdir(parents=True, exist_ok=True)
    lineas = [",".join(columnas)] + [",".join(str(f[c]) for c in columnas) for f in filas]
    ruta.write_text("\n".join(lineas) + "\n", encoding="utf-8")
    return ruta


def test_lee_la_ultima_copia_de_cada_informe(tmp_path):
    vieja = _csv(tmp_path / "informe=2026-09" / "fecha=2026-10-20" / "p.csv", COLUMNAS_IPCVA,
                 [_fila("AMBA", "Nalga", "2026-09", 1.0)])
    nueva = _csv(tmp_path / "informe=2026-09" / "fecha=2026-10-28" / "p.csv", COLUMNAS_IPCVA,
                 [_fila("AMBA", "Nalga", "2026-09", 2.0)])
    filas = leer_precios_ipcva([vieja, nueva])
    assert [f["precio"] for f in filas] == [2.0]


def test_si_falta_una_columna_del_contrato_corta(tmp_path):
    sin_plaza = tuple(c for c in COLUMNAS_IPCVA if c != "plaza")
    ruta = _csv(tmp_path / "informe=2026-09" / "fecha=2026-10-28" / "p.csv", sin_plaza,
                [_fila("AMBA", "Nalga", "2026-09", 1.0)])
    with pytest.raises(ValueError, match="plaza"):
        leer_precios_ipcva([ruta])
