"""Ponderadores calculados desde la ENGHo.

La mayoria de los tests usa hogares sinteticos con resultado calculable a mano.
Los ultimos dos tocan los microdatos reales versionados en `docs/engho/`, porque
lo que hay que proteger es justamente que el mapeo contra la fuente real siga
cerrando: si el INDEC republica la base con otros codigos, tiene que fallar acá
y no aparecer como un numero raro tres pasos despues.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from reporte.ponderadores import (
    REGIONES,
    calcular,
    cargar_mapeo,
    clase_coicop,
    clase_del_indice,
    cobertura_por_clase,
    codigo_coicop,
    articulo_de_categoria,
    conectar,
    pesos_de_articulos,
    pesos_por_articulo,
)

RAIZ = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------- #
# El puente ENGHo <-> COICOP
# --------------------------------------------------------------------------- #


def test_el_codigo_engho_es_el_coicop_sin_puntos():
    """`A0111101` y `01.1.1.1.01` son el mismo producto: Facturas y churros."""
    assert codigo_coicop("A0111101") == "01.1.1.1.01"
    assert codigo_coicop("A0121301") == "01.2.1.3.01"
    assert codigo_coicop("A0115102") == "01.1.5.1.02"


def test_saca_la_clase_de_un_articulo():
    assert clase_coicop("A0111304") == "01.1.1"
    assert clase_coicop("A0121301") == "01.2.1"
    assert clase_coicop("A0114101") == "01.1.4"


def test_un_codigo_invalido_rompe():
    with pytest.raises(ValueError, match="invalido"):
        codigo_coicop("XYZ")


# --------------------------------------------------------------------------- #
# La clase del indice: la hoja que pondera el INDEC
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("articulo, hoja", [
    ("A0111101", "01.1.1"),   # facturas: el INDEC pondera la clase
    ("A0611110", "06.1.1"),   # fiebre o dolor: tambien la clase
    ("A0531108", "05.3"),     # heladera: el INDEC pondera el GRUPO 05.3
    ("A0532103", "05.3"),     # licuadora: otra clase ENGHo, la misma hoja
    ("A0511103", "05.1"),     # colchon
    ("A0551105", "05.5"),     # taladro
])
def test_la_clase_del_indice_es_la_hoja_que_pondera_el_indec(articulo, hoja):
    assert clase_del_indice(articulo) == hoja


def test_un_articulo_que_no_cae_en_ninguna_hoja_corta():
    with pytest.raises(ValueError, match="A9911101"):
        clase_del_indice("A9911101")


def test_en_alimentos_y_ropa_la_clase_del_indice_es_la_clase_coicop():
    """Regresion: la clase del indice no mueve nada de lo que ya se mide.

    Agosto->septiembre se calculo agrupando por `clase_coicop`; con la clase del
    indice, las mismas categorias tienen que caer en la misma clase.
    """
    for cat, spec in cargar_mapeo().items():
        art = spec["articulo"]
        if art.startswith(("A01", "A03")):
            assert clase_del_indice(art) == clase_coicop(art), cat


def test_los_pesos_se_reparten_dentro_de_la_hoja_del_indec():
    """Heladera y licuadora son dos clases de la ENGHo (A0531 y A0532) y una
    sola hoja del INDEC (05.3). El peso de cada una es sobre la hoja: 300 y 100
    de gasto dan 0,75 y 0,25. Repartido por clase ENGHo daria 1,0 y 1,0, y el
    indice de 05.3 saldria con los dos articulos pesando lo mismo, sin error.
    """
    con = _con_sintetica([
        (1, "A0531", "A0531108", 1.0, 300.0),
        (1, "A0532", "A0532103", 1.0, 100.0),
    ])
    pesos = pesos_por_articulo(con, "GBA")
    assert pesos["A0531108"][0] == pytest.approx(0.75)
    assert pesos["A0532103"][0] == pytest.approx(0.25)


# --------------------------------------------------------------------------- #
# El calculo, sobre hogares sinteticos
# --------------------------------------------------------------------------- #


def _con_sintetica(filas: list[tuple]) -> duckdb.DuckDBPyConnection:
    """filas = [(region, clase, articulo, pondera, monto)]."""
    con = duckdb.connect()
    con.execute(
        "CREATE TABLE gastos (region INT, clase VARCHAR, articulo VARCHAR, "
        "pondera DOUBLE, monto DOUBLE)"
    )
    con.executemany("INSERT INTO gastos VALUES (?,?,?,?,?)", filas)
    return con


def test_el_peso_es_la_participacion_dentro_de_la_clase():
    """Dos articulos en una clase, 300 y 100: 75% y 25%."""
    con = _con_sintetica([
        (1, "A0111", "A0111304", 1.0, 300.0),
        (1, "A0111", "A0111201", 1.0, 100.0),
    ])
    p = pesos_por_articulo(con, "GBA")
    assert p["A0111304"][0] == pytest.approx(0.75)
    assert p["A0111201"][0] == pytest.approx(0.25)
    assert p["A0111304"][1] == pytest.approx(300.0)


def test_una_venta_de_usados_no_entra_al_peso():
    """La ENGHo resta la venta de una bicicleta usada (gasto negativo) en la misma clase.

    Medimos precios de compra: el peso sale de lo que se compra. Con la venta en
    el denominador, la compra pesaba 113% de su clase.
    """
    con = _con_sintetica([
        (1, "A0713", "A0713101", 1.0, 113.0),    # compra de bicicleta
        (1, "A0713", "A0713201", 1.0, -13.0),    # venta de bicicleta usada
    ])
    p = pesos_por_articulo(con, "GBA")
    assert p["A0713101"][0] == pytest.approx(1.0)
    assert "A0713201" not in p


def test_el_factor_de_expansion_pesa():
    """Un hogar que representa a 10 pesa 10 veces mas que uno que representa a 1."""
    con = _con_sintetica([
        (1, "A0111", "A0111304", 10.0, 100.0),   # 1000 expandido
        (1, "A0111", "A0111201", 1.0, 100.0),    #  100 expandido
    ])
    p = pesos_por_articulo(con, "GBA")
    assert p["A0111304"][0] == pytest.approx(1000 / 1100)


def test_cada_clase_se_normaliza_por_su_cuenta():
    """El denominador es la clase, no el total: cada clase suma 1."""
    con = _con_sintetica([
        (1, "A0111", "A0111304", 1.0, 300.0),
        (1, "A0111", "A0111201", 1.0, 100.0),
        (1, "A0121", "A0121301", 1.0, 50.0),
    ])
    p = pesos_por_articulo(con, "GBA")
    assert p["A0121301"][0] == pytest.approx(1.0), "unico articulo de su clase"
    assert p["A0111304"][0] + p["A0111201"][0] == pytest.approx(1.0)


def test_las_regiones_no_se_mezclan():
    con = _con_sintetica([
        (1, "A0111", "A0111304", 1.0, 300.0),
        (2, "A0111", "A0111304", 1.0, 999.0),
        (1, "A0111", "A0111201", 1.0, 100.0),
    ])
    assert pesos_por_articulo(con, "GBA")["A0111304"][0] == pytest.approx(0.75)
    assert pesos_por_articulo(con, "Pampeana")["A0111304"][0] == pytest.approx(1.0)


def test_una_region_inexistente_falla_claro():
    con = _con_sintetica([(1, "A0111", "A0111304", 1.0, 1.0)])
    with pytest.raises(ValueError, match="region desconocida"):
        pesos_por_articulo(con, "Antartida")


def test_las_regiones_de_la_engho_son_las_seis_del_ipc():
    assert set(REGIONES.values()) == {
        "GBA", "Pampeana", "Noroeste", "Noreste", "Cuyo", "Patagonia"
    }


# --------------------------------------------------------------------------- #
# Articulos compartidos
# --------------------------------------------------------------------------- #


def test_dos_categorias_que_comparten_articulo_son_UNA_unidad():
    """La harina 000 y la 0000 son un solo articulo para el INDEC.

    Como el peso no las distingue, el calculo tampoco: no se reparte nada. El
    articulo es el nivel elemental, y sus quotes van a una sola media
    geometrica. Repartir seria inventar informacion que la fuente no tiene.
    """
    con = _con_sintetica([
        (1, "A0111", "A0111210", 1.0, 100.0),
        (1, "A0111", "A0111304", 1.0, 300.0),
    ])
    mapeo = {
        "harina_000": {"articulo": "A0111210"},
        "harina_0000": {"articulo": "A0111210"},
        "fideos": {"articulo": "A0111304"},
    }
    pesos = calcular("GBA", con=con, mapeo=mapeo)

    # Una fila por articulo, no por categoria: 2 y no 3.
    assert len(pesos) == 2
    por_art = {p.articulo: p for p in pesos}
    assert por_art["A0111210"].peso_en_clase == pytest.approx(0.25)
    assert set(por_art["A0111210"].categorias) == {"harina_000", "harina_0000"}
    assert por_art["A0111304"].categorias == ("fideos",)

    d = pesos_de_articulos(pesos)
    assert d == {"A0111210": pytest.approx(0.25), "A0111304": pytest.approx(0.75)}
    assert sum(d.values()) == pytest.approx(1.0)


def test_el_mapeo_producto_a_articulo_es_la_clave_de_agrupacion():
    m = articulo_de_categoria({"a": {"articulo": "A1"}, "b": {"articulo": "A1"}})
    assert m == {"a": "A1", "b": "A1"}, "las dos agrupan en el mismo articulo"


def test_un_articulo_sin_gasto_en_la_region_falla():
    con = _con_sintetica([(1, "A0111", "A0111304", 1.0, 100.0)])
    with pytest.raises(ValueError, match="no tiene gasto"):
        calcular("GBA", con=con, mapeo={"x": {"articulo": "A9999999"}})


def test_en_el_calculo_regional_sin_gasto_es_peso_cero():
    """La ENGHo no registra gasto en rimel en Patagonia: ahi pesa cero.

    Es una opcion explicita, para el calculo por region. Por defecto sigue
    cortando, porque en el nacional un articulo sin gasto es un error de mapeo.
    """
    con = _con_sintetica([(6, "A0111", "A0111304", 1.0, 100.0)])
    mapeo = {"fideos": {"articulo": "A0111304"}, "rimel": {"articulo": "A1213206"}}
    pesos = calcular("Patagonia", con=con, mapeo=mapeo, omitir_sin_gasto=True)
    assert [p.articulo for p in pesos] == ["A0111304"]


# --------------------------------------------------------------------------- #
# Cobertura
# --------------------------------------------------------------------------- #


def test_la_cobertura_mide_que_parte_de_la_clase_se_observa():
    """Medimos fideos (75%) y nada mas: cubrimos el 75% del gasto de la clase."""
    con = _con_sintetica([
        (1, "A0111", "A0111304", 1.0, 300.0),
        (1, "A0111", "A0111201", 1.0, 100.0),
    ])
    pesos = calcular("GBA", con=con, mapeo={"fideos": {"articulo": "A0111304"}})
    cob = cobertura_por_clase(pesos, con=con)
    assert cob["01.1.1"].cubierto == pytest.approx(0.75)
    assert cob["01.1.1"].n_articulos_clase == 2
    assert cob["01.1.1"].n_articulos == 1, "medimos uno de los dos"
    assert cob["01.1.1"].n_categorias == 1


def test_un_articulo_compartido_no_cuenta_doble_en_la_cobertura():
    con = _con_sintetica([
        (1, "A0111", "A0111210", 1.0, 100.0),
        (1, "A0111", "A0111304", 1.0, 300.0),
    ])
    pesos = calcular("GBA", con=con, mapeo={
        "harina_000": {"articulo": "A0111210"},
        "harina_0000": {"articulo": "A0111210"},
    })
    cob = cobertura_por_clase(pesos, con=con)
    assert cob["01.1.1"].cubierto == pytest.approx(0.25), "no 0.50"
    assert cob["01.1.1"].n_articulos == 1, "UN articulo, aunque sean dos categorias"
    assert cob["01.1.1"].n_categorias == 2, "las dos categorias siguen contandose"


# --------------------------------------------------------------------------- #
# Contra los microdatos reales
# --------------------------------------------------------------------------- #


CLASES_SEPA = {
    "01.1.1", "01.1.2", "01.1.3", "01.1.4", "01.1.5", "01.1.6", "01.1.7",
    "01.1.8", "01.1.9", "01.2.1", "01.2.2",
    # Vinos, cerveza y limpieza, desde el 27/10/2026.
    "02.1.2", "02.1.3", "05.6.1",
}
# Ropa y calzado de las tiendas online: categorias `online.<prenda>_<genero>`.
CLASES_ONLINE = {"03.1.2", "03.2.1"}
# Lo online que no es ropa, por rubro, y los combustibles. Son clases DEL
# INDICE: 05.1, 05.3 y 05.5 son grupos, porque el INDEC no abre esas clases.
CLASES_RUBROS = {
    "04.3.1", "05.1", "05.3", "05.5", "06.1.1", "06.1.2", "09.1.1", "09.1.3",
    "09.3.4", "12.1.3",
    # Fravega, 30/10/2026: camaras, memorias, neumaticos, bicicletas y deporte.
    "09.1.2", "09.1.4", "07.2.1", "07.1.3", "09.3.1", "09.3.2",
}
CLASES_COMBUSTIBLES = {"07.2.2"}
# Tarifas y precios publicados, desde el 30/10/2026: servicio domestico,
# prepagas, alquileres y colectivo.
CLASES_TARIFAS = {"05.6.2", "06.4", "04.1.1", "07.3.2"}
CLASES_TAXONOMIA = (CLASES_SEPA | CLASES_ONLINE | CLASES_RUBROS | CLASES_COMBUSTIBLES
                    | CLASES_TARIFAS)


def test_el_mapeo_del_repo_es_coherente_con_la_taxonomia():
    """La clase del indice de cada articulo tiene que ser una de las 37 de la taxonomia.

    Eran 6 clases y 23 categorias hasta 10/2026; se sumaron carnes, pescados,
    frutas, verduras y aguas/gaseosas/jugos (41 categorias, 15,79% del IPC),
    despues ropa y calzado online (32 categorias), y el 21/10 el resto de lo
    online (53 rubros) y los combustibles (5). El 26/10, 12 de verduras
    envasadas (congeladas, secas y en conserva). El 27/10, vinos, cerveza y
    limpieza (17 categorias, 3,40% del IPC). El 30/10, el servicio domestico
    (4 categorias de tarifas, 1,79%), y prepagas, alquileres y colectivo (3), y
    8 rubros de Fravega.
    """
    mapeo = cargar_mapeo()
    assert len(mapeo) == 198
    clases = {clase_del_indice(s["articulo"]) for s in mapeo.values()}
    assert clases == CLASES_TAXONOMIA

    def clases_de(prefijo, cond=lambda a: True):
        return {clase_del_indice(s["articulo"]) for c, s in mapeo.items()
                if c.startswith(prefijo) and cond(s["articulo"])}

    ropa = lambda a: a.startswith(("A031", "A032"))  # noqa: E731
    assert clases_de("online.", ropa) == CLASES_ONLINE
    assert clases_de("online.", lambda a: not ropa(a)) == CLASES_RUBROS
    assert clases_de("combustibles.") == CLASES_COMBUSTIBLES
    assert clases_de("tarifas.") == CLASES_TARIFAS
    assert sum(1 for c in mapeo if c.startswith("online.")) == 32 + 53 + 8


@pytest.mark.skipif(
    not (RAIZ / "docs" / "engho" / "engho2018_gastos.zip").exists(),
    reason="hacen falta los microdatos de la ENGHo en docs/engho/",
)
def test_los_pesos_reales_cierran_y_son_plausibles():
    """Sobre los microdatos de verdad, no sinteticos.

    Es el test que avisa si el INDEC republica la base con otros codigos: sin
    esto, un articulo que cambia de codigo se cae del mapeo y el peso queda mal
    sin que nada falle.
    """
    con = conectar()
    try:
        pesos = calcular("GBA", con=con)
        # 198 categorias sobre 193 articulos: cinco los comparten de a dos
        # (harina 000/0000, yerba 500 g/1 kg, yogur firme/bebible, lechuga
        # por kilo/por unidad, y malla/short de hombre).
        assert len(pesos) == 193
        assert sum(len(p.categorias) for p in pesos) == 198
        assert all(0.0 < p.peso_en_clase <= 1.0 for p in pesos)

        por_cat = {c: p for p in pesos for c in p.categorias}
        # El aceite de girasol es lejos lo mas pesado de su clase, y la sal es
        # una fraccion chica de "Otros alimentos". Si esto se da vuelta, algo se
        # rompio en el mapeo.
        assert por_cat["almacen.aceite_girasol_900ml"].peso_en_clase > 0.40
        assert por_cat["almacen.sal_fina_500g"].peso_en_clase < 0.10
        assert (por_cat["lacteos.leche_entera_1l"].peso_en_clase
                > por_cat["lacteos.leche_descremada_1l"].peso_en_clase)
        # Las gaseosas son la mitad de su clase (52% en GBA); la carne picada
        # pesa mas que las salchichas; la papa es la verdura mas pesada.
        assert por_cat["bebidas.gaseosas"].peso_en_clase > 0.40
        assert (por_cat["carnes.carne_picada_vacuna"].peso_en_clase
                > por_cat["carnes.salchichas_viena"].peso_en_clase)
        assert por_cat["verduras.papa"].peso_en_clase > 0.10
        # Las dos lechugas comparten articulo: un solo peso, no dos.
        assert por_cat["verduras.lechuga"] is por_cat["verduras.lechuga_unidad"]
        # El vino fino pesa mas que el comun; la cerveza es toda su clase; el
        # jabon liquido para ropa es lo mas pesado de limpieza.
        assert (por_cat["alcohol.vino_fino"].peso_en_clase
                > por_cat["alcohol.vino_comun"].peso_en_clase)
        assert por_cat["alcohol.cerveza"].peso_en_clase == pytest.approx(1.0)
        limpieza = [p for c, p in por_cat.items() if c.startswith("limpieza.")]
        assert max(limpieza, key=lambda p: p.peso_en_clase) is por_cat[
            "limpieza.jabon_liquido_ropa"]

        cob = cobertura_por_clase(pesos, con=con)
        assert set(cob) == CLASES_TAXONOMIA
        assert all(0.0 < c.cubierto <= 1.0 for c in cob.values())
        # Aceites es la clase mejor cubierta de las viejas y "Otros alimentos"
        # una de las peores. Aguas y gaseosas, con cuatro categorias, cubren
        # mas que cualquiera de las de almacen.
        assert cob["01.1.5"].cubierto > cob["01.1.9"].cubierto
        assert cob["01.2.2"].cubierto > cob["01.1.9"].cubierto
    finally:
        con.close()


# --------------------------------------------------------------------------- #
# Nacional: como se pasa de seis regiones a una sola cifra
# --------------------------------------------------------------------------- #


def test_nacional_suma_las_seis_regiones_en_vez_de_filtrar_una():
    """El gasto de la ENGHo ya viene expandido a poblacion: se suma, no se pondera.

    Ponderar con los pesos regionales del INDEC seria mezclar encuestas: esos
    pesos salen de la ENGHo 2004/05 y estos gastos de la 2017/18.
    """
    from reporte.ponderadores import REGION_NACIONAL

    con = _con_sintetica([
        (1, "A0111", "A0111304", 1.0, 300.0),   # GBA
        (2, "A0111", "A0111201", 1.0, 100.0),   # Pampeana
    ])
    # Cada region ve un solo articulo, asi que dentro de su clase pesa 100%.
    assert pesos_por_articulo(con, "GBA")["A0111304"][0] == pytest.approx(1.0)
    assert pesos_por_articulo(con, "Pampeana")["A0111201"][0] == pytest.approx(1.0)

    # Nacional los junta: 300 y 100 sobre 400.
    nac = pesos_por_articulo(con, REGION_NACIONAL)
    assert nac["A0111304"][0] == pytest.approx(0.75)
    assert nac["A0111201"][0] == pytest.approx(0.25)
    assert sum(v[0] for v in nac.values()) == pytest.approx(1.0)


def test_calcular_nacional_devuelve_los_mismos_articulos():
    from reporte.ponderadores import REGION_NACIONAL

    con = _con_sintetica([
        (1, "A0111", "A0111304", 1.0, 300.0),
        (2, "A0111", "A0111304", 1.0, 100.0),
    ])
    pesos = calcular(REGION_NACIONAL, con=con, mapeo={"f": {"articulo": "A0111304"}})
    assert pesos[0].region == REGION_NACIONAL
    assert pesos[0].gasto == pytest.approx(400.0), "las dos regiones sumadas"


def test_la_cobertura_nacional_no_filtra_por_region():
    from reporte.ponderadores import REGION_NACIONAL

    con = _con_sintetica([
        (1, "A0111", "A0111304", 1.0, 300.0),
        (2, "A0111", "A0111201", 1.0, 100.0),
    ])
    pesos = calcular(REGION_NACIONAL, con=con, mapeo={"f": {"articulo": "A0111304"}})
    cob = cobertura_por_clase(pesos, con=con)
    assert cob["01.1.1"].n_articulos_clase == 2, "los dos, no solo el de una region"
    assert cob["01.1.1"].cubierto == pytest.approx(0.75)
