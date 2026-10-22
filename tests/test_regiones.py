"""La region del INDEC de cada sucursal, y el contrato de `staged/sucursales/`.

GBA es CABA mas los 24 partidos del conurbano; el resto de la provincia de
Buenos Aires va a Pampeana (seccion 2 de la metodologia). SEPA dice la
provincia pero no el partido: lo pone captura con el API georef, por las
coordenadas. Los codigos de departamento salieron de georef el 21/10/2026.
"""

from __future__ import annotations

import duckdb
import pytest

from reporte.lectura import COLUMNAS_SUCURSALES, leer_sucursales
from reporte.ponderadores import pesos_regionales
from reporte.regiones import cargar_regiones, region_de_sucursal

REG = cargar_regiones()


@pytest.mark.parametrize("provincia, provincia_id, departamento_id, region", [
    ("CABA", "02", "02007", "GBA"),
    ("Buenos Aires", "06", "06490", "GBA"),        # Lomas de Zamora
    ("Buenos Aires", "06", "06861", "GBA"),        # Vicente Lopez
    ("Buenos Aires", "06", "06056", "Pampeana"),   # Bahia Blanca
    ("Buenos Aires", "06", "06441", "Pampeana"),   # La Plata: no es de los 24
    ("Córdoba", "14", "14014", "Pampeana"),
    ("Neuquén", "58", None, "Patagonia"),          # fuera de Buenos Aires alcanza la provincia
    ("Tucumán", "90", None, "Noroeste"),
    ("Corrientes", "18", None, "Noreste"),
    ("Mendoza", "50", None, "Cuyo"),
    # SEPA no sabe la provincia: la dice georef por las coordenadas.
    ("DESCONOCIDA", "14", "14014", "Pampeana"),
    ("DESCONOCIDA", "06", "06490", "GBA"),
    # Declarada en Buenos Aires con coordenadas en CABA: es GBA igual.
    ("Buenos Aires", "02", "02007", "GBA"),
])
def test_la_region_de_cada_sucursal(provincia, provincia_id, departamento_id, region):
    assert region_de_sucursal(provincia, provincia_id, departamento_id, REG) == region


@pytest.mark.parametrize("provincia, provincia_id, departamento_id", [
    # Buenos Aires sin partido: no se adivina si es GBA o Pampeana.
    ("Buenos Aires", None, None),
    ("Buenos Aires", "06", None),
    # Declarada en Buenos Aires y las coordenadas caen en otra provincia: algo
    # esta mal cargado, y no se elige.
    ("Buenos Aires", "14", "14014"),
    # Ni SEPA ni georef saben donde esta.
    ("DESCONOCIDA", None, None),
])
def test_sin_datos_para_decidir_no_se_inventa_la_region(provincia, provincia_id, departamento_id):
    assert region_de_sucursal(provincia, provincia_id, departamento_id, REG) is None


def test_una_provincia_que_no_esta_en_la_config_corta():
    with pytest.raises(ValueError, match="Atlantida"):
        region_de_sucursal("Atlantida", None, None, REG)


def test_los_24_partidos_del_gba():
    assert len(REG.partidos_gba) == 24
    assert all(d.startswith("06") and len(d) == 5 for d in REG.partidos_gba)


def test_las_regiones_son_las_seis_que_pondera_el_indec():
    assert set(REG.provincias.values()) - {"REQUIERE_LOCALIDAD"} == set(pesos_regionales())
    assert len(REG.provincia_por_id) == 24


# --------------------------------------------------------------------------- #
# El contrato con captura: las columnas de staged/sucursales/
# --------------------------------------------------------------------------- #


def _parquet(tmp_path, columnas: tuple[str, ...]):
    ruta = tmp_path / "sucursales.parquet"
    valores = {"latitud": "-34.76", "longitud": "-58.40"}
    duckdb.sql("SELECT " + ", ".join(
        f"{valores.get(c, repr('x'))} AS {c}" for c in columnas
    )).write_parquet(str(ruta))
    return ruta


def test_el_contrato_de_sucursales_es_el_que_publica_captura():
    """Escrito literal: es la misma tupla que `precios.geo.georef.COLUMNAS`."""
    assert COLUMNAS_SUCURSALES == ("id_comercio", "id_sucursal", "provincia", "provincia_id",
                                   "departamento_id", "departamento", "latitud", "longitud")


def test_lee_la_tabla_con_las_columnas_del_contrato(tmp_path):
    filas = leer_sucursales([_parquet(tmp_path, COLUMNAS_SUCURSALES)])
    assert len(filas) == 1 and set(filas[0]) == set(COLUMNAS_SUCURSALES)


def test_si_falta_una_columna_corta_al_leer(tmp_path):
    """Si captura cambia la tabla sin avisar, se ve aca y no en un numero raro."""
    sin_depto = tuple(c for c in COLUMNAS_SUCURSALES if c != "departamento_id")
    with pytest.raises(ValueError, match="departamento_id"):
        leer_sucursales([_parquet(tmp_path, sin_depto)])
