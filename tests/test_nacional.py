"""Del indice de cada region al nacional: la formula 15 del INDEC.

El nacional de una agregacion —nivel general, una division, una clase— es la
suma de sus niveles regionales ponderados SOLO por el peso de cada region en el
gasto del pais (cuadro 6 de la metodologia). No por el peso de esa agregacion
dentro de cada region: eso parece mas fino y no es lo que hace el INDEC. El
test de aceptacion lo verifica contra lo que el INDEC publico.
"""

from __future__ import annotations

import pytest

from reporte.agregacion import nacional_desde_regiones, variacion_nacional
from reporte.ponderadores import nivel_de_la_canasta

# Cuadro 6 de la metodologia, igual que config/regiones.yaml.
PESOS = {
    "GBA": 0.4467, "Pampeana": 0.3419, "Noreste": 0.0451,
    "Noroeste": 0.0688, "Cuyo": 0.0518, "Patagonia": 0.0457,
}

# "Alimentos y bebidas no alcoholicas", agosto 2026, diciembre 2016 = 100.
# INDEC, sh_ipc_09_26.xls, hoja "Indices IPC Cobertura Nacional".
ALIMENTOS_AGO_2026 = {
    "GBA": 13199.7267, "Pampeana": 13153.6122, "Noroeste": 12828.2786,
    "Noreste": 13025.3565, "Cuyo": 13219.0007, "Patagonia": 12548.9139,
}
ALIMENTOS_AGO_2026_NACIONAL = 13121.2544


def test_reproduce_el_nacional_que_publica_el_indec():
    """Test de aceptacion: el numero publicado, desde los seis regionales.

    Los pesos se publican con cuatro decimales y ese redondeo deja 0,54 puntos
    sobre 13.121 (0,004%). Ponderando ademas por el peso de la division en cada
    region da 13.110,55: diez puntos de mas, que es como estaba armada la
    comparacion contra el INDEC hasta el 21/10/2026.
    """
    assert nacional_desde_regiones(ALIMENTOS_AGO_2026, PESOS) == pytest.approx(
        ALIMENTOS_AGO_2026_NACIONAL, rel=1e-4
    )


def test_los_pesos_de_la_config_son_los_del_cuadro_6():
    from reporte.ponderadores import pesos_regionales

    assert pesos_regionales() == PESOS


def test_dos_regiones_a_mano():
    # 0,75 x 120 + 0,25 x 100 = 115
    assert nacional_desde_regiones(
        {"A": 120.0, "B": 100.0}, {"A": 0.75, "B": 0.25}
    ) == pytest.approx(115.0)


def test_si_falta_una_region_corta():
    """Sin Cuyo, el resto no es "el nacional un poco mas chico": es otro numero."""
    sin_cuyo = {r: v for r, v in ALIMENTOS_AGO_2026.items() if r != "Cuyo"}
    with pytest.raises(ValueError, match="Cuyo"):
        nacional_desde_regiones(sin_cuyo, PESOS)


def test_una_region_sin_peso_corta():
    with pytest.raises(ValueError, match="Atlantida"):
        nacional_desde_regiones({**ALIMENTOS_AGO_2026, "Atlantida": 100.0}, PESOS)


def test_pesos_que_no_suman_uno_cortan():
    """Es la forma que toma el error de ponderar tambien por la division:
    los pesos region x division suman 0,27, no 1."""
    with pytest.raises(ValueError, match="suman"):
        nacional_desde_regiones({"A": 100.0, "B": 100.0}, {"A": 0.5, "B": 0.4})


def test_la_variacion_nacional_desde_las_regionales_y_sus_niveles():
    """El mismo caso de abajo, armado como lo usa la corrida por region.

    Cada region trae su variacion del mes y el nivel del que parte:
    (0,5 x 200 x 1,10 + 0,5 x 100 x 1,00) / (0,5 x 200 + 0,5 x 100) = 160/150.
    """
    v = variacion_nacional({"A": 1.10, "B": 1.00}, {"A": 200.0, "B": 100.0},
                           {"A": 0.5, "B": 0.5})
    assert v == pytest.approx(160 / 150)


def test_el_nivel_de_partida_de_la_canasta_en_una_region():
    """El nivel del INDEC de la parte de la canasta que medimos, en una region.

    Laspeyres de los niveles de clase con los pesos base de la region:
    dos clases que pesan 0,3 y 0,1 y estan en 100 y 140 -> (30 + 14) / 0,4 = 110.
    """
    pond = {"01.1.1": ("Pan", 0.3), "01.1.4": ("Leche", 0.1), "04.1.1": ("Alquiler", 0.5)}
    factores = {"01.1.1": 100.0, "01.1.4": 140.0}
    assert nivel_de_la_canasta(pond, factores, {"01.1.1", "01.1.4"}) == pytest.approx(110.0)


def test_el_nivel_de_partida_sin_el_factor_de_una_clase_corta():
    with pytest.raises(ValueError, match="01.1.4"):
        nivel_de_la_canasta({"01.1.4": ("Leche", 0.1)}, {}, {"01.1.4"})


def test_la_variacion_nacional_sale_de_niveles_no_de_variaciones():
    """Combinar variaciones regionales con los pesos fijos da otro numero.

    A sube 10% desde 200 y B queda en 100, con pesos 0,5 y 0,5:
      con niveles       (0,5x220 + 0,5x100) / (0,5x200 + 0,5x100) = 160/150 = +6,67%
      con variaciones    0,5 x 10% + 0,5 x 0%                              = +5,00%
    La region que acumulo mas inflacion pesa mas. Por eso los niveles
    regionales de partida tienen que ser los del INDEC y no 100 en cada una.
    """
    pesos = {"A": 0.5, "B": 0.5}
    antes = nacional_desde_regiones({"A": 200.0, "B": 100.0}, pesos)
    despues = nacional_desde_regiones({"A": 220.0, "B": 100.0}, pesos)
    assert despues / antes - 1 == pytest.approx(10 / 150)
