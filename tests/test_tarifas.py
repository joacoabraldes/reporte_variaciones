"""Tarifas y precios fijados por norma dentro del indice.

Captura transcribe cada norma y publica `staged/tarifas/fuente=X/fecha=Y/valores.csv`,
una fila por item y mes. Aca se decide que articulo de la ENGHo es cada item
(`config/tarifas.yaml` -> categoria -> `config/mapeo_categorias_engho.yaml`) y
sus valores entran como quotes al grupo de su articulo: el mismo emparejado, el
mismo Jevons y los mismos pesos que SEPA. No hay un segundo calculo.

Los numeros de prueba son los de la Resolucion 6/2026 de Casas Particulares.
"""

from __future__ import annotations

import math

import pytest

from reporte.lectura import COLUMNAS_TARIFAS, leer_tarifas
from reporte.ponderadores import articulo_de_categoria, clase_del_indice
from reporte.tarifas import (
    cargar_clasificacion,
    fuentes_solo_con_afiliados,
    quotes_por_articulo,
)


def _fila(item, mes, precio, fuente="servicio_domestico"):
    return {"fuente": fuente, "item_id": item, "descripcion": item, "unidad": "hora",
            "mes": mes, "precio": precio, "norma": "Resolucion 6/2026", "url": "https://x"}


FILAS = [
    _fila("tareas_generales_con_retiro_hora", "2026-08", 3804.66),
    _fila("tareas_generales_con_retiro_hora", "2026-09", 3914.64),
    _fila("supervisor_con_retiro_hora", "2026-08", 4523.11),
    _fila("supervisor_con_retiro_hora", "2026-09", 4645.33),
    _fila("cuidado_personas_con_retiro_hora", "2026-08", 4072.38),
    _fila("cuidado_personas_con_retiro_hora", "2026-09", 4185.94),
    _fila("tareas_generales_sin_retiro_hora", "2026-09", 4185.94),   # sin clasificar
]
CLASIF = {
    ("servicio_domestico", "tareas_generales_con_retiro_hora"): "tarifas.servicio_domestico_por_hora",
    ("servicio_domestico", "supervisor_con_retiro_hora"): "tarifas.servicio_domestico_por_hora",
    ("servicio_domestico", "cuidado_personas_con_retiro_hora"): "tarifas.cuidado_de_personas",
}
ART = {"tarifas.servicio_domestico_por_hora": "A0562101",
       "tarifas.cuidado_de_personas": "A0562105"}


def test_cada_item_es_un_quote_del_grupo_de_su_articulo():
    q = quotes_por_articulo(FILAS, CLASIF, ART, "2026-09")
    assert set(q) == {"A0562101", "A0562105"}
    assert q["A0562101"] == {
        ("tarifa", "servicio_domestico", "tareas_generales_con_retiro_hora"): 3914.64,
        ("tarifa", "servicio_domestico", "supervisor_con_retiro_hora"): 4645.33,
    }


def test_un_item_sin_clasificar_no_entra():
    q = quotes_por_articulo(FILAS, CLASIF, ART, "2026-09")
    claves = {k for g in q.values() for k in g}
    assert ("tarifa", "servicio_domestico", "tareas_generales_sin_retiro_hora") not in claves


def test_un_mes_sin_valores_no_da_quotes():
    assert quotes_por_articulo(FILAS, CLASIF, ART, "2026-10") == {}


def test_la_clave_no_choca_con_sepa_ni_con_online():
    """SEPA es (comercio, sucursal, producto) con un comercio numerico; online es de dos."""
    q = quotes_por_articulo(FILAS, CLASIF, ART, "2026-08")
    for clave in (k for g in q.values() for k in g):
        assert len(clave) == 3 and clave[0] == "tarifa"


def test_una_categoria_sin_articulo_corta():
    """Si faltara en el mapeo, el item se perderia sin que nada avise."""
    with pytest.raises(ValueError, match="tarifas.servicio_domestico_por_hora"):
        quotes_por_articulo(FILAS, CLASIF, {"tarifas.cuidado_de_personas": "A0562105"},
                            "2026-09")


def test_el_servicio_domestico_de_septiembre_sube_lo_que_dice_la_resolucion():
    """Jevons de los items del articulo: la media geometrica de sus aumentos."""
    base = quotes_por_articulo(FILAS, CLASIF, ART, "2026-08")["A0562101"]
    actual = quotes_por_articulo(FILAS, CLASIF, ART, "2026-09")["A0562101"]
    jevons = math.exp(sum(math.log(actual[k] / base[k]) for k in base) / len(base))
    assert jevons == pytest.approx(math.sqrt(3914.64 / 3804.66 * 4645.33 / 4523.11))
    assert jevons - 1 > 0.018      # el 1,8% mas la suma fija que se incorpora


def _prepaga(item, mes, precio, capitas):
    return {**_fila(item, mes, precio, fuente="prepagas"), "capitas": str(capitas)}


PREPAGAS = [
    _prepaga("614081|7|CAPITAL FEDERAL|0-27|I|0|0", "2026-08", 180000.0, 35636),
    _prepaga("614081|7|CAPITAL FEDERAL|0-27|I|0|0", "2026-09", 184385.0, 35636),
    _prepaga("614081|7|SAN LUIS|0-27|I|0|0", "2026-09", 184385.0, 0),
]


def test_una_fuente_entera_se_clasifica_con_un_comodin():
    """Los planes de las prepagas son miles: van todos a la misma categoria."""
    clasif = {("prepagas", "*"): "tarifas.prepaga_cuota"}
    q = quotes_por_articulo(PREPAGAS, clasif, {"tarifas.prepaga_cuota": "A0641101"}, "2026-09")
    assert len(q["A0641101"]) == 2


def test_un_plan_sin_afiliados_no_entra_si_la_fuente_lo_pide():
    """Un plan ofrecido en una region donde nadie lo tiene no es un precio que se pague."""
    clasif = {("prepagas", "*"): "tarifas.prepaga_cuota"}
    q = quotes_por_articulo(PREPAGAS, clasif, {"tarifas.prepaga_cuota": "A0641101"}, "2026-09",
                            solo_con_afiliados={"prepagas"})
    assert list(q["A0641101"]) == [("tarifa", "prepagas", "614081|7|CAPITAL FEDERAL|0-27|I|0|0")]


def test_la_clasificacion_del_repo_usa_categorias_del_mapeo_y_clases_del_indec():
    """Servicio domestico (30/10), y prepagas, alquileres y colectivo (30/10, a la noche)."""
    clasif = cargar_clasificacion()
    art_de_cat = articulo_de_categoria()
    assert clasif, "config/tarifas.yaml no clasifica nada"
    assert set(clasif.values()) <= set(art_de_cat)
    assert {clase_del_indice(art_de_cat[c]) for c in clasif.values()} == {
        "05.6.2", "06.4", "04.1.1", "07.3.2"}
    assert fuentes_solo_con_afiliados() == {"prepagas"}


def test_el_contrato_de_tarifas_es_el_que_publica_captura():
    """Escrito literal: es la misma tupla que `precios.online.tarifas.COLUMNAS`."""
    assert COLUMNAS_TARIFAS == ("fuente", "item_id", "descripcion", "unidad", "mes",
                                "precio", "norma", "url")


def _csv(ruta, columnas, filas):
    ruta.parent.mkdir(parents=True, exist_ok=True)
    lineas = [",".join(columnas)] + [",".join(str(f[c]) for c in columnas) for f in filas]
    ruta.write_text("\n".join(lineas) + "\n", encoding="utf-8")
    return ruta


def test_el_lector_conserva_las_columnas_de_mas(tmp_path):
    """Las prepagas traen los afiliados ademas del contrato: el filtro los necesita."""
    columnas = COLUMNAS_TARIFAS + ("capitas",)
    ruta = _csv(tmp_path / "fuente=prepagas_202609" / "fecha=2026-10-30" / "valores.csv",
                columnas, [_prepaga("a", "2026-09", 1.0, 7)])
    assert leer_tarifas([ruta])[0]["capitas"] == "7"


def test_lee_la_ultima_copia_de_cada_fuente(tmp_path):
    vieja = _csv(tmp_path / "fuente=servicio_domestico" / "fecha=2026-10-29" / "valores.csv",
                 COLUMNAS_TARIFAS, [_fila("a", "2026-09", 1.0)])
    nueva = _csv(tmp_path / "fuente=servicio_domestico" / "fecha=2026-10-30" / "valores.csv",
                 COLUMNAS_TARIFAS, [_fila("a", "2026-09", 2.0)])
    otra = _csv(tmp_path / "fuente=colectivo" / "fecha=2026-10-29" / "valores.csv",
                COLUMNAS_TARIFAS, [_fila("b", "2026-09", 3.0, fuente="colectivo")])
    filas = leer_tarifas([vieja, nueva, otra])
    assert sorted((f["fuente"], f["precio"]) for f in filas) == [
        ("colectivo", 3.0), ("servicio_domestico", 2.0)]


def test_si_falta_una_columna_del_contrato_corta(tmp_path):
    sin_mes = tuple(c for c in COLUMNAS_TARIFAS if c != "mes")
    ruta = _csv(tmp_path / "fuente=x" / "fecha=2026-10-30" / "valores.csv", sin_mes,
                [_fila("a", "2026-09", 1.0)])
    with pytest.raises(ValueError, match="mes"):
        leer_tarifas([ruta])
