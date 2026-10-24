"""Calculo del indice sobre datos sinteticos.

Un bug aca es silencioso: el output sigue siendo un numero plausible. Por eso se
testea contra casos con resultado conocido a mano ANTES de correrlo sobre datos
reales.
"""

from __future__ import annotations

import math

import pytest

from reporte.agregacion import (
    MODO_CRUDO,
    agregados_por_division,
    agregar,
    clases_desde_categorias,
    clases_sin_ponderador,
    encadenar,
    laspeyres,
    renormalizar,
    serie_encadenada,
    variacion_interanual,
)
from reporte.elemental import (
    MOTIVO_OUTLIER_MAD,
    MOTIVO_OUTLIER_TOPE,
    MOTIVO_SIN_ACTUAL,
    MOTIVO_SIN_BASE,
    Relativo,
    detectar_outliers,
    emparejar,
    indice_elemental,
    jevons,
    mad,
)


def q(n: int) -> tuple:
    """Un quote sintetico: (comercio, sucursal, producto)."""
    return ("10", "7", f"EAN{n}")


# --------------------------------------------------------------------------- #
# Los dos casos obligatorios
# --------------------------------------------------------------------------- #


def test_precios_que_suben_y_vuelven_dan_cero_acumulado():
    """Sube 50% y despues baja 33,33%: el acumulado tiene que ser 0%.

    Este es el caso que separa Jevons de una media aritmetica de relativos. Con
    la aritmetica el indice quedaria por encima de 100 aunque los precios hayan
    vuelto exactamente al valor original (drift de Carli).
    """
    p0 = {q(i): 100.0 for i in range(20)}
    p1 = {q(i): 150.0 for i in range(20)}
    p2 = {q(i): 100.0 for i in range(20)}

    r1 = indice_elemental("cat", p0, p1)
    r2 = indice_elemental("cat", p1, p2)

    assert r1.indice == pytest.approx(1.5)
    assert r2.indice == pytest.approx(1.0 / 1.5)

    serie = serie_encadenada([r1.indice, r2.indice], base=100.0)
    assert serie[-1] == pytest.approx(100.0), "el indice no volvio a la base"


def test_una_canasta_de_un_solo_producto_reproduce_su_variacion_exacta():
    p0 = {q(1): 1000.0}
    p1 = {q(1): 1234.0}
    r = indice_elemental("cat", p0, p1)
    assert r.indice == pytest.approx(1.234)
    assert r.variacion_pct == pytest.approx(23.4)
    assert r.n_quotes == 1


# --------------------------------------------------------------------------- #
# Jevons
# --------------------------------------------------------------------------- #


def test_jevons_es_media_geometrica():
    """Con ratios 2 y 0,5 la media geometrica es 1; la aritmetica seria 1,25."""
    rel = [Relativo(q(1), 100, 200), Relativo(q(2), 100, 50)]
    assert jevons(rel) == pytest.approx(1.0)


def test_jevons_es_estable_con_muchos_quotes():
    """Se computa en logaritmos: multiplicar 5.000 ratios desbordaria."""
    rel = [Relativo(q(i), 100.0, 105.0) for i in range(5000)]
    assert jevons(rel) == pytest.approx(1.05)


def test_jevons_sin_quotes_devuelve_none():
    assert jevons([]) is None
    assert indice_elemental("cat", {}, {}).indice is None


# --------------------------------------------------------------------------- #
# Muestra emparejada
# --------------------------------------------------------------------------- #


def test_solo_entran_los_quotes_presentes_en_los_dos_meses():
    base = {q(1): 100.0, q(2): 200.0, q(3): 300.0}
    actual = {q(2): 220.0, q(3): 330.0, q(4): 400.0}
    rel, desc = emparejar(base, actual)
    assert {r.quote for r in rel} == {q(2), q(3)}
    motivos = {d.quote: d.motivo for d in desc}
    assert motivos[q(4)] == MOTIVO_SIN_BASE, "producto nuevo, no tiene con que compararse"
    assert motivos[q(1)] == MOTIVO_SIN_ACTUAL, "producto que desaparecio"


def test_un_producto_nuevo_no_altera_la_variacion():
    """Aunque el producto nuevo tenga un precio muy distinto."""
    base = {q(i): 100.0 for i in range(20)}
    actual = {q(i): 110.0 for i in range(20)}
    r_sin = indice_elemental("cat", base, actual)
    actual[q(999)] = 100000.0
    r_con = indice_elemental("cat", base, actual)
    assert r_con.indice == pytest.approx(r_sin.indice)


# --------------------------------------------------------------------------- #
# Outliers
# --------------------------------------------------------------------------- #


def test_el_tope_absoluto_caza_un_error_de_tipeo():
    """Un digito de mas multiplica por 10."""
    base = {q(i): 1000.0 for i in range(30)}
    actual = {q(i): 1050.0 for i in range(30)}
    actual[q(0)] = 10000.0                      # se tipeo un cero de mas
    r = indice_elemental("cat", base, actual)
    assert r.indice == pytest.approx(1.05)
    assert any(d.motivo == MOTIVO_OUTLIER_TOPE and d.quote == q(0) for d in r.descartes)


def test_el_mad_cero_no_manda_todo_a_cuarentena():
    """EL caso que rompe la deteccion ingenua.

    Si mas de la mitad de los precios no cambio, la mediana de log(ratio) es 0 y
    el MAD tambien. Sin fallback, cualquier variacion distinta de cero queda a
    infinitos MAD y la categoria entera se va a cuarentena.
    """
    base = {q(i): 1000.0 for i in range(40)}
    actual = {q(i): 1000.0 for i in range(40)}          # 35 sin cambio
    for i in range(35, 40):
        actual[q(i)] = 1080.0                           # 5 con +8%

    rel, _ = emparejar(base, actual)
    logs = [r.log_ratio for r in rel]
    assert mad(logs) == 0.0, "el escenario no reproduce MAD=0"

    r = indice_elemental("cat", base, actual)
    assert r.indice is not None
    assert r.n_quotes == 40, f"se descartaron {r.n_descartados} quotes con MAD=0"
    assert r.umbral_usado in ("iqr", "solo_tope_absoluto")


def test_un_precio_que_no_cambio_nunca_es_un_valor_raro():
    """El caso de la carne picada de DIA en septiembre 2026.

    20 precios bajan alrededor de 15% (x0,84, x0,85, x0,86) y 10 no se mueven.
    Como casi todo bajo, la mediana queda en -15% y los quietos parecen raros:
    el MAD los mandaba a cuarentena, y la categoria daba -15% en vez de -10%.
    El filtro esta para cazar errores de tipeo, y un precio igual al del mes
    anterior no puede ser uno. Entran los 30:

        exp( (7 ln 0,84 + 7 ln 0,85 + 6 ln 0,86 + 10 ln 1) / 30 )
    """
    factores = [0.84, 0.85, 0.86]
    base = {q(i): 1000.0 for i in range(30)}
    actual = {q(i): 1000.0 * factores[i % 3] for i in range(20)}
    actual.update({q(i): 1000.0 for i in range(20, 30)})

    r = indice_elemental("cat", base, actual)

    esperado = math.exp(
        (7 * math.log(0.84) + 7 * math.log(0.85) + 6 * math.log(0.86)) / 30
    )
    assert r.n_quotes == 30, f"se descartaron {r.n_descartados} precios que no cambiaron"
    assert r.indice == pytest.approx(esperado)


def test_no_cambio_aunque_el_promedio_difiera_en_el_ultimo_bit():
    """El mismo precio con centavos, promediado sobre 31 y sobre 30 dias.

    Pasa con el 7% de los precios entre $1.000 y $3.000 con centavos: con una
    comparacion exacta, esos precios quietos volverian a parecer movidos.
    """
    precio = 1000.04
    agosto = sum([precio] * 31) / 31
    septiembre = sum([precio] * 30) / 30
    assert agosto != septiembre, "el escenario no reproduce la diferencia de un bit"
    factores = [0.84, 0.85, 0.86]
    base = {q(i): 1000.0 for i in range(20)}
    actual = {q(i): 1000.0 * factores[i % 3] for i in range(20)}
    base.update({q(i): agosto for i in range(20, 30)})
    actual.update({q(i): septiembre for i in range(20, 30)})

    assert indice_elemental("cat", base, actual).n_quotes == 30


def test_un_salto_que_no_llega_al_tope_lo_sigue_cazando_el_mad():
    """Regresion del arreglo de arriba: el MAD sigue sirviendo para lo que esta.

    Precios que suben 0%, 1% y 2%, y uno que salta x1,8: no llega al tope de x2,
    pero esta a decenas de MAD de la mediana.
    """
    base = {q(i): 100.0 for i in range(40)}
    actual = {q(i): 100.0 * (1.00, 1.01, 1.02)[i % 3] for i in range(40)}
    actual[q(1)] = 180.0

    r = indice_elemental("cat", base, actual)

    assert any(d.quote == q(1) and d.motivo == MOTIVO_OUTLIER_MAD for d in r.descartes)
    assert r.n_quotes == 39


def test_con_pocos_quotes_no_se_aplica_el_mad():
    """La dispersion de 5 observaciones no es informativa."""
    base = {q(i): 100.0 for i in range(5)}
    actual = {q(0): 105.0, q(1): 106.0, q(2): 104.0, q(3): 107.0, q(4): 130.0}
    r = indice_elemental("cat", base, actual)
    assert r.umbral_usado == "solo_tope_absoluto"
    assert r.n_quotes == 5, "no deberia descartar nada: ninguno supera el tope"


def test_el_tope_se_aplica_aunque_la_categoria_este_dispersa():
    """El tope absoluto es red de seguridad: corre siempre, no solo como fallback."""
    base = {q(i): 100.0 for i in range(40)}
    actual = {q(i): 100.0 * (1 + 0.3 * (i % 5) / 5) for i in range(40)}  # dispersa
    actual[q(0)] = 5000.0
    r = indice_elemental("cat", base, actual)
    assert any(d.quote == q(0) and d.motivo == MOTIVO_OUTLIER_TOPE for d in r.descartes)


def test_los_outliers_se_miden_sobre_log_ratio_y_son_simetricos():
    """+100% y -50% son el mismo movimiento con signo opuesto."""
    subir = Relativo(q(1), 100.0, 200.0)
    bajar = Relativo(q(2), 100.0, 50.0)
    assert subir.log_ratio == pytest.approx(-bajar.log_ratio)


# --------------------------------------------------------------------------- #
# Laspeyres
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# CRITICO 5 de 9: categorias elementales -> clases COICOP
#
# Era la unica de las ocho operaciones criticas sin un solo test, y es donde se
# perdian categorias en silencio. Los numeros de abajo estan hechos a mano.
# --------------------------------------------------------------------------- #


def test_las_categorias_a_clases_es_media_ponderada():
    """Peso 1 al +10% y peso 3 al +20%: (1x1,10 + 3x1,20) / 4 = 1,175."""
    r = clases_desde_categorias(
        {"a": 1.10, "b": 1.20},
        {"a": "01.1.1", "b": "01.1.1"},
        {"a": 1.0, "b": 3.0},
    )
    assert r.indices["01.1.1"] == pytest.approx(1.175)
    assert r.categorias_por_clase == {"01.1.1": 2}
    assert r.sin_clase == [] and r.sin_peso == []


def test_las_categorias_a_clases_no_mezcla_clases():
    r = clases_desde_categorias(
        {"pan": 1.50, "leche": 1.10},
        {"pan": "01.1.1", "leche": "01.1.4"},
        {"pan": 1.0, "leche": 1.0},
    )
    assert r.indices == pytest.approx({"01.1.1": 1.50, "01.1.4": 1.10})


def test_las_categorias_a_clases_cuenta_la_que_no_tiene_clase():
    """El descarte que importa: antes se salteaba con un `continue`."""
    r = clases_desde_categorias(
        {"pan": 1.50, "huerfana": 9.99},
        {"pan": "01.1.1"},
        {"pan": 1.0, "huerfana": 1.0},
    )
    assert r.indices["01.1.1"] == pytest.approx(1.50)
    assert r.sin_clase == ["huerfana"]


def test_las_categorias_a_clases_cuenta_la_que_no_tiene_peso():
    """Una categoria con peso cero no participa, y queda registrada.

    Sin el registro, perder el peso de una categoria baja la canasta sobre la
    que se calcula la clase y el numero sigue pareciendo correcto.
    """
    r = clases_desde_categorias(
        {"pan": 1.50, "galletitas": 9.99},
        {"pan": "01.1.1", "galletitas": "01.1.1"},
        {"pan": 1.0},
    )
    assert r.indices["01.1.1"] == pytest.approx(1.50)
    assert r.sin_peso == ["galletitas"]
    assert r.categorias_por_clase == {"01.1.1": 1}


def test_las_categorias_a_clases_sin_nada_que_agregar():
    r = clases_desde_categorias({"a": 1.1}, {}, {"a": 1.0})
    assert r.indices == {}
    assert r.sin_clase == ["a"]


def test_una_clase_de_la_taxonomia_sin_ponderador_se_detecta():
    """La guarda de la operacion 7.

    Sin esto la clase se excluye sola, no entra al denominador, y la cobertura
    sigue informando 100% sobre una canasta mas chica.
    """
    pond = {"01.1.1": ("Pan y cereales", 0.4)}
    assert clases_sin_ponderador({"01.1.1"}, pond) == []
    assert clases_sin_ponderador({"01.1.1", "01.1.2"}, pond) == ["01.1.2"]
    assert clases_sin_ponderador({"01.1.4", "01.1.2"}, pond) == ["01.1.2", "01.1.4"]


def test_laspeyres_es_media_aritmetica_ponderada():
    indices = {"a": 1.10, "b": 1.02}
    pesos = {"a": 0.25, "b": 0.75}
    ind, cubierto, total = laspeyres(indices, pesos)
    assert ind == pytest.approx(0.25 * 1.10 + 0.75 * 1.02)
    assert cubierto == pytest.approx(1.0)


def test_renormaliza_cuando_los_pesos_no_suman_uno():
    """Los pesos del INDEC son sobre la canasta completa; cubrimos una parte."""
    indices = {"01.1.1": 1.10}
    pesos = {"01.1.1": 0.0405, "01.1.2": 0.0698}    # solo tenemos el primero
    ind, cubierto, total = laspeyres(indices, pesos)
    assert ind == pytest.approx(1.10), "sin renormalizar daria 0.0405*1.10 = 0.045"
    assert cubierto == pytest.approx(0.0405)
    assert total == pytest.approx(0.1103)


def test_los_rubros_son_las_divisiones_del_indec():
    """Juan (22/10/2026): rubros separados y uno global. El rubro es la division.

    Alimentos: pan +2% con peso 0,3 y carnes +4% con peso 0,1
        -> (0,3 x 1,02 + 0,1 x 1,04) / 0,4 = 1,025
    Equipamiento: 05.3 +10% sola (05.1 tiene peso y no dato: no entra)
    """
    indices = {"01.1.1": 1.02, "01.1.2": 1.04, "05.3": 1.10}
    pesos = {"01.1.1": 0.3, "01.1.2": 0.1, "05.3": 0.02, "05.1": 0.01}

    rubros = agregados_por_division(indices, pesos)

    assert set(rubros) == {"01", "05"}
    assert rubros["01"].indice == pytest.approx(1.025)
    assert rubros["05"].indice == pytest.approx(1.10)
    assert rubros["05"].cobertura == pytest.approx(0.02 / 0.03)


def test_un_rubro_sin_ninguna_clase_con_dato_no_tiene_indice():
    """Hasta noviembre no hay dos meses de fotos: Salud tiene peso y no dato."""
    rubros = agregados_por_division({"01.1.1": 1.02}, {"01.1.1": 0.3, "06.1.1": 0.04})
    assert rubros["01"].indice == pytest.approx(1.02)
    assert rubros["06"].indice is None


def test_sin_renormalizar_el_numero_no_es_interpretable():
    indices = {"01.1.1": 1.10}
    pesos = {"01.1.1": 0.0405, "01.1.2": 0.0698}
    ind, _, _ = laspeyres(indices, pesos, modo=MODO_CRUDO)
    assert ind == pytest.approx(0.0405 * 1.10)


def test_renormalizar_hace_que_los_pesos_sumen_uno():
    p = renormalizar({"a": 0.0405, "b": 0.0345, "c": 0.0055})
    assert sum(p.values()) == pytest.approx(1.0)
    assert p["a"] > p["b"] > p["c"]


def test_una_clase_sin_dato_no_se_imputa_con_el_promedio():
    """Se excluye del numerador y del denominador."""
    r = agregar("01.1", "Alimentos", {"01.1.1": 1.10}, {"01.1.1": 0.04, "01.1.2": 0.07})
    assert r.indice == pytest.approx(1.10)
    assert r.cobertura == pytest.approx(0.04 / 0.11)


def test_agregado_sin_ningun_hijo_con_dato():
    r = agregar("01.1", "Alimentos", {}, {"01.1.1": 0.04})
    assert r.indice is None
    assert r.cobertura == 0.0


# --------------------------------------------------------------------------- #
# Encadenamiento
# --------------------------------------------------------------------------- #


def test_encadenamiento_compone_las_variaciones():
    serie = serie_encadenada([1.05, 1.05, 1.05], base=100.0)
    assert serie[0] == 100.0
    assert serie[-1] == pytest.approx(100 * 1.05**3)


def test_encadenar_es_multiplicativo_no_aditivo():
    """Tres meses al 5% dan 15,76% acumulado, no 15%."""
    serie = serie_encadenada([1.05, 1.05, 1.05])
    assert (serie[-1] / serie[0] - 1) * 100 == pytest.approx(15.7625)


def test_variacion_interanual():
    serie = {"2026-08": 130.0, "2025-08": 100.0}
    assert variacion_interanual(serie, "2026-08") == pytest.approx(0.30)
    assert variacion_interanual({"2026-08": 130.0}, "2026-08") is None


# --------------------------------------------------------------------------- #
# Extremo a extremo
# --------------------------------------------------------------------------- #


def test_de_quotes_a_indice_agregado():
    """Dos categorias con variacion conocida, agregadas con pesos del INDEC."""
    base_pan = {q(i): 1000.0 for i in range(30)}
    act_pan = {q(i): 1100.0 for i in range(30)}          # +10%
    base_lac = {("15", "3", f"E{i}"): 500.0 for i in range(30)}
    act_lac = {("15", "3", f"E{i}"): 510.0 for i in range(30)}   # +2%

    i_pan = indice_elemental("01.1.1", base_pan, act_pan)
    i_lac = indice_elemental("01.1.4", base_lac, act_lac)
    assert i_pan.indice == pytest.approx(1.10)
    assert i_lac.indice == pytest.approx(1.02)

    # Pesos reales del INDEC para GBA.
    pesos = {"01.1.1": 0.0405, "01.1.4": 0.0345}
    r = agregar("01.1", "Alimentos", {"01.1.1": 1.10, "01.1.4": 1.02}, pesos)

    esperado = (0.0405 * 1.10 + 0.0345 * 1.02) / (0.0405 + 0.0345)
    assert r.indice == pytest.approx(esperado)
    # El resultado cae entre las dos variaciones, mas cerca de la de mayor peso.
    assert 1.02 < r.indice < 1.10
    assert r.indice > 1.06


# --------------------------------------------------------------------------- #
# Ponderadores nacionales a partir de los regionales
# --------------------------------------------------------------------------- #


def test_los_pesos_regionales_suman_uno():
    """Si no suman 1, el ponderador nacional queda mal escalado en silencio."""
    from reporte.ponderadores import pesos_regionales

    assert sum(pesos_regionales().values()) == pytest.approx(1.0, abs=1e-9)


def test_las_divisiones_nacionales_suman_uno():
    """EL control del metodo: combinar las seis regiones tiene que dar 1.

    La tolerancia es 1e-4 y no 1e-9 porque el INDEC publica a cuatro decimales:
    su propia columna de Noreste suma 0,9999.
    """
    from reporte.ponderadores import ponderadores_de_clase

    pond = ponderadores_de_clase("nacional")
    divisiones = [c for c in pond if "." not in c and c != "0"]
    assert len(divisiones) == 12
    assert sum(pond[c][1] for c in divisiones) == pytest.approx(1.0, abs=1e-4)


def test_el_ponderador_nacional_queda_entre_el_minimo_y_el_maximo_regional():
    """Es un promedio ponderado: no puede caerse fuera del rango de sus partes."""
    from pathlib import Path

    import yaml

    from reporte.ponderadores import ponderadores_de_clase

    raiz = Path(__file__).resolve().parents[1]
    crudo = yaml.safe_load((raiz / "config" / "ponderadores.yaml").read_text("utf-8"))
    regs = ["GBA", "Pampeana", "Noreste", "Noroeste", "Cuyo", "Patagonia"]
    nac = ponderadores_de_clase("nacional")

    for clase in ("01.1.1", "01.1.4", "01.1.5", "01.1.8", "01.1.9", "01.2.1"):
        valores = [crudo["ponderaciones"][clase][r] for r in regs]
        assert min(valores) <= nac[clase][1] <= max(valores), clase


# --------------------------------------------------------------------------- #
# Actualizacion por precios
# --------------------------------------------------------------------------- #


def test_actualizar_reescala_por_el_precio_relativo():
    """Lo que subio mas que el promedio gana peso; lo que subio menos, pierde."""
    from reporte.ponderadores import actualizar_por_precios

    pond = {"A": ("a", 0.50), "B": ("b", 0.50)}
    act = actualizar_por_precios(pond, {"A": 2.0, "B": 1.0})

    assert act["A"][1] == pytest.approx(2 / 3)
    assert act["B"][1] == pytest.approx(1 / 3)
    assert act["A"][1] + act["B"][1] == pytest.approx(1.0)


def test_un_factor_parejo_no_cambia_nada():
    """Si todo subio igual, las proporciones quedan intactas.

    Es la razon por la que los pesos de la ENGHo NO se actualizan: el INDEC
    publica precios por clase, asi que dentro de una clase todos los articulos
    llevarian el mismo factor y se cancelaria en la renormalizacion.
    """
    from reporte.ponderadores import actualizar_por_precios

    pond = {"A": ("a", 0.30), "B": ("b", 0.70)}
    act = actualizar_por_precios(pond, {"A": 121.0, "B": 121.0})
    assert act["A"][1] == pytest.approx(0.30)
    assert act["B"][1] == pytest.approx(0.70)


def test_una_clase_sin_factor_queda_afuera():
    """Mejor excluirla que dejarla con el peso viejo entre las actualizadas."""
    from reporte.ponderadores import actualizar_por_precios

    act = actualizar_por_precios(
        {"A": ("a", 0.50), "B": ("b", 0.50)}, {"A": 2.0}
    )
    assert set(act) == {"A"}
    assert act["A"][1] == pytest.approx(1.0)


def test_los_factores_reales_son_todos_mayores_a_uno():
    """Verifica el mapeo contra la planilla del INDEC, no solo la aritmetica.

    Si el INDEC republica con otros nombres de apertura, `factores_actualizacion`
    tiene que romper aca y no dejar una clase sin actualizar en silencio.
    """
    from reporte.ponderadores import factores_actualizacion

    f = factores_actualizacion("nacional")
    # Las 11 clases de la division alimentos (las 6 del piloto mas carnes,
    # pescados, frutas, verduras y aguas/gaseosas/jugos, 10/2026), ropa y
    # calzado, y desde el 21/10 el resto de lo online y los combustibles.
    assert set(f) == {
        "01.1.1", "01.1.2", "01.1.3", "01.1.4", "01.1.5", "01.1.6", "01.1.7",
        "01.1.8", "01.1.9", "01.2.1", "01.2.2", "03.1.2", "03.2.1",
        "04.3.1", "05.1", "05.3", "05.5", "06.1.1", "06.1.2", "07.2.2",
        "09.1.1", "09.1.3", "09.3.4", "12.1.3",
    }
    # Base diciembre 2016 = 100 y desde entonces los precios subieron mucho.
    assert all(v > 50 for v in f.values()), f


@pytest.mark.parametrize(
    "region", ["GBA", "Pampeana", "Noroeste", "Noreste", "Cuyo", "Patagonia"]
)
def test_los_factores_salen_en_las_seis_regiones(region):
    """El INDEC escribe distinto la misma serie segun la region.

    01.1.8 es "etc," en GBA y "etc." en las otras cinco. Con un solo nombre por
    clase, el calculo cortaba en cinco de las seis regiones; el nacional no se
    enteraba porque usa la serie de GBA.
    """
    from reporte.ponderadores import factores_actualizacion

    f = factores_actualizacion(region)
    assert len(f) == 24
    assert all(v > 50 for v in f.values()), f


def test_una_lista_de_aperturas_toma_la_que_existe(tmp_path):
    import yaml

    from reporte.ponderadores import factores_actualizacion

    mapeo = tmp_path / "indices.yaml"
    mapeo.write_text(
        yaml.safe_dump(
            {"clases": {"01.1.1": {"apertura": ["Nombre Que No Esta", "Pan y cereales"]}}},
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    assert set(factores_actualizacion("GBA", path_mapeo=mapeo)) == {"01.1.1"}


def test_una_lista_sin_ninguna_apertura_valida_falla_claro(tmp_path):
    """La lista no afloja el control: si ninguno de los nombres esta, corta."""
    import yaml

    from reporte.ponderadores import factores_actualizacion

    mapeo = tmp_path / "indices.yaml"
    mapeo.write_text(
        yaml.safe_dump({"clases": {"01.1.1": {"apertura": ["No Existe", "Tampoco"]}}}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="no esta"):
        factores_actualizacion("GBA", path_mapeo=mapeo)


def test_una_apertura_inexistente_falla_claro(tmp_path):
    """El modo de falla que importa: un nombre que cambia en la planilla.

    Sin esto, la clase se quedaria con su peso viejo mientras las demas se
    actualizan, y el agregado seguiria pareciendo razonable.
    """
    import yaml

    from reporte.ponderadores import factores_actualizacion

    mapeo = tmp_path / 'indices.yaml'
    mapeo.write_text(
        yaml.safe_dump({'clases': {'01.1.1': {'apertura': 'Rubro Que No Existe'}}}),
        encoding='utf-8',
    )
    with pytest.raises(ValueError, match='no esta'):
        factores_actualizacion('nacional', path_mapeo=mapeo)
