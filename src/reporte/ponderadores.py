"""Ponderadores calculados desde los microdatos de la ENGHo.

**Reemplaza un supuesto por un dato.** El INDEC publica ponderaciones hasta
CLASE (`01.1.1 Pan y cereales`) y nada mas fino; verificado contra
`docs/ponderadores_ipc.xls` y contra la hoja "Ponderaciones" de
`sh_ipc_aperturas.xls`. Pero Jevons se calcula un nivel mas abajo, en las
categorias elementales, asi que para subir de categoria a clase hacia falta un
peso que no estaba publicado. Se usaban pesos iguales, y eso movia el resultado
agregado alrededor de un 50%.

El peso si existe. La metodologia del IPC (seccion 4.2) dice como se construyo
el suyo:

    "se estimaron partiendo de los gastos de los hogares urbanos de la ENGHo
    2004/05 por region y de las variedades que se relevaban en diciembre de
    2015. Primero, se procedio a estimar el gasto promedio de los hogares por
    variedad relevada y se construyo asi la estructura de ponderadores"

Este modulo hace **ese mismo procedimiento** con la encuesta mas nueva que hay
publicada, la ENGHo 2017/18, cuyos microdatos el INDEC distribuye completos:
901.804 registros de gasto de 21.543 hogares, con region y factor de expansion.

    peso(articulo, region) =  suma(pondera x monto) del articulo
                              ---------------------------------
                              suma(pondera x monto) del agregado

**El codigo de articulo de la ENGHo es el codigo COICOP de producto**, escrito
sin puntos y con una `A` adelante:

    ENGHo   A0111101     ->  COICOP  01.1.1.1.01     Facturas y churros

Por eso el join contra la taxonomia es directo y no hace falta tabla de
equivalencias: ver `codigo_coicop()`.

**Lo que este modulo NO hace todavia.** Laspeyres pide que el periodo de
referencia de los ponderadores coincida con el de la base de precios, y el
propio documento del INDEC recomienda actualizarlos por la evolucion de los
precios hasta esa base. Lo que sale de aca son participaciones de gasto a
precios de 2017/18, sin actualizar. Es un paso conocido y pendiente, no una
omision silenciosa: `PesoCategoria.actualizado` lo deja marcado.
"""

from __future__ import annotations

import dataclasses
import zipfile
from pathlib import Path

import duckdb
import yaml

RAIZ_REPO = Path(__file__).resolve().parents[2]
PATH_MAPEO = RAIZ_REPO / "config" / "mapeo_categorias_engho.yaml"
DIR_ENGHO = RAIZ_REPO / "docs" / "engho"

ZIP_GASTOS = DIR_ENGHO / "engho2018_gastos.zip"
ZIP_ARTICULOS = DIR_ENGHO / "engho2018_articulos.zip"

# Las seis regiones de la ENGHo son exactamente las seis del IPC. La ENGHo la
# llama "Metropolitana" y el IPC "GBA"; es la misma.
REGIONES = {
    1: "GBA",
    2: "Pampeana",
    3: "Noroeste",
    4: "Noreste",
    5: "Cuyo",
    6: "Patagonia",
}
REGION_POR_NOMBRE = {v: k for k, v in REGIONES.items()}

# Pedir "nacional" suma el gasto de las seis regiones en vez de filtrar por una.
#
# NO se combinan las seis con los pesos regionales del INDEC, y el motivo es que
# esos pesos salen de la ENGHo 2004/05: usarlos sobre gasto de la 2017/18 seria
# mezclar dos encuestas. Los factores de expansion de la ENGHo ya son
# poblacionales, asi que sumar da la participacion nacional directamente.
#
# Medido: la participacion regional implicita de la ENGHo 2017/18 es GBA 0,4381
# y Pampeana 0,3226, contra 0,4467 y 0,3419 del INDEC. Son trece anios de
# diferencia entre encuestas, no un error.
#
# El nivel CLASE es otra historia: ahi los ponderadores SI son del INDEC (base
# 2004/05) y hay que combinarlos con los pesos regionales del INDEC, que son de
# la misma cosecha. Eso vive en el script, no aca.
REGION_NACIONAL = "nacional"


@dataclasses.dataclass(frozen=True)
class PesoArticulo:
    """El peso de un articulo de la ENGHo dentro de su clase COICOP.

    **La unidad es el articulo, no nuestra categoria.** Tres articulos cubren
    dos categorias nuestras cada uno (harina 000/0000, yerba 500 g/1 kg, yogur
    firme/bebible) porque el INDEC no los separa. Repartir el peso del articulo
    entre las dos seria inventar informacion que la fuente no tiene.

    El nivel elemental de un indice se define como el agrupamiento mas chico que
    tiene ponderacion asignada; es la definicion de "variedad" del INDEC. Si el
    peso llega hasta "harina de trigo", ahi esta el nivel elemental, y hay que
    calcular UN Jevons sobre los quotes de las dos categorias juntas.

    De paso, el peso relativo entre 000 y 0000 sale solo: quedan mas ratios de
    la que mas quotes tiene dentro de la misma media geometrica. No hace falta
    elegir un criterio de reparto, y ademas queda en el espacio correcto en vez
    de combinarse con una media aritmetica por afuera.
    """

    articulo: str
    descripcion: str
    clase: str                        # COICOP con puntos, ej "01.1.1"
    region: str
    peso_en_clase: float              # participacion dentro de la clase
    gasto: float                      # gasto expandido, para auditar
    categorias: tuple[str, ...] = ()  # nuestras categorias que caen en el

    # Los pesos salen de gasto a precios 2017/18, sin actualizar a la base de
    # precios. Ver el docstring del modulo.
    actualizado: bool = False


@dataclasses.dataclass
class CoberturaClase:
    """Cuanto del gasto de una clase COICOP mide efectivamente el indice."""

    clase: str
    region: str
    cubierto: float             # suma de pesos de los articulos que medimos
    n_categorias: int
    n_articulos_clase: int

    @property
    def pct(self) -> float:
        return self.cubierto * 100.0


def codigo_coicop(articulo_engho: str) -> str:
    """`A0111101` -> `01.1.1.1.01`. Tambien acepta niveles mas cortos.

    El codigo de la ENGHo es `A` + division(2) + grupo(1) + clase(1) +
    subclase(1) + producto(2). Es el mismo COICOP que publica el INDEC en
    `coicop_argentina_2019.xls`, sin los puntos.
    """
    d = articulo_engho.strip().upper().lstrip("A")
    if not d.isdigit():
        raise ValueError(f"codigo de articulo invalido: {articulo_engho!r}")
    partes = [d[:2]]
    for corte in (3, 4, 5):
        if len(d) >= corte:
            partes.append(d[corte - 1: corte])
    if len(d) >= 7:
        partes.append(d[5:7])
    return ".".join(partes)


def clase_coicop(articulo_engho: str) -> str:
    """La clase (3 niveles) a la que pertenece un articulo. `A0111101` -> `01.1.1`."""
    return ".".join(codigo_coicop(articulo_engho).split(".")[:3])


def cargar_mapeo(path: Path | None = None) -> dict[str, dict]:
    """`categoria -> {articulo, descripcion, comparte_con}`."""
    path = path or PATH_MAPEO
    datos = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    cats = datos.get("categorias") or {}
    if not cats:
        raise ValueError(f"{path} no define ninguna categoria")
    for cat, spec in cats.items():
        if not spec.get("articulo"):
            raise ValueError(f"{path}: la categoria {cat!r} no tiene articulo")
    return cats


def _extraer(zip_path: Path, destino: Path) -> Path:
    """Descomprime el unico .txt del zip si no esta ya. Devuelve el path."""
    with zipfile.ZipFile(zip_path) as z:
        nombre = z.namelist()[0]
        salida = destino / nombre
        if not salida.exists():
            destino.mkdir(parents=True, exist_ok=True)
            z.extract(nombre, destino)
    return salida


def conectar(
    cache: Path | None = None,
    zip_gastos: Path | None = None,
    zip_articulos: Path | None = None,
) -> duckdb.DuckDBPyConnection:
    """Carga gastos y articulos de la ENGHo en tablas `gastos` y `articulos`."""
    cache = cache or (RAIZ_REPO / ".cache" / "engho")
    gastos = _extraer(zip_gastos or ZIP_GASTOS, cache)
    articulos = _extraer(zip_articulos or ZIP_ARTICULOS, cache)

    con = duckdb.connect()
    con.execute(
        f"CREATE TABLE gastos AS SELECT * FROM read_csv("
        f"'{gastos.as_posix()}', delim='|', header=true, sample_size=400000)"
    )
    # El .txt de articulos viene en UTF-8 aunque el de gastos no tenga acentos:
    # forzar latin-1 lo rompe.
    con.execute(
        f"CREATE TABLE articulos AS SELECT * FROM read_csv("
        f"'{articulos.as_posix()}', delim='|', header=true)"
    )
    return con


def pesos_por_articulo(
    con: duckdb.DuckDBPyConnection, region: str
) -> dict[str, tuple[float, float]]:
    """`articulo -> (peso dentro de su clase, gasto expandido)` para una region.

    El denominador es la clase y no el total: lo que hace falta es repartir el
    peso que el INDEC ya publica a nivel clase entre las categorias de adentro.
    """
    if region == REGION_NACIONAL:
        filtro, params = "TRUE", []
    else:
        cod = REGION_POR_NOMBRE.get(region)
        if cod is None:
            raise ValueError(
                f"region desconocida: {region!r} "
                f"(hay: {', '.join(REGIONES.values())}, {REGION_NACIONAL})"
            )
        filtro, params = "region = ?", [cod, cod]

    filas = con.execute(
        f"""
        WITH tot AS (
            SELECT clase, sum(pondera * monto) AS s
            FROM gastos WHERE {filtro} GROUP BY clase
        )
        SELECT g.articulo,
               sum(g.pondera * g.monto) / any_value(tot.s),
               sum(g.pondera * g.monto)
        FROM gastos g JOIN tot ON tot.clase = g.clase
        WHERE g.{filtro}
        GROUP BY g.articulo
        """.replace("g.TRUE", "TRUE"),
        params,
    ).fetchall()
    return {a: (float(p), float(gasto)) for a, p, gasto in filas}


def calcular(
    region: str = "GBA",
    con: duckdb.DuckDBPyConnection | None = None,
    mapeo: dict[str, dict] | None = None,
) -> list[PesoArticulo]:
    """Peso de cada articulo de la ENGHo que el indice mide, dentro de su clase.

    Devuelve una fila por ARTICULO, con la lista de categorias nuestras que caen
    en el. Las categorias que comparten articulo no se separan: ver
    `PesoArticulo`.
    """
    propio = con is None
    con = con or conectar()
    try:
        mapeo = mapeo or cargar_mapeo()
        pesos = pesos_por_articulo(con, region)

        agrupado: dict[str, list[str]] = {}
        desc: dict[str, str] = {}
        for cat, spec in sorted(mapeo.items()):
            art = spec["articulo"]
            agrupado.setdefault(art, []).append(cat)
            desc.setdefault(art, spec.get("descripcion", ""))

        salida: list[PesoArticulo] = []
        for art, cats in sorted(agrupado.items()):
            if art not in pesos:
                raise ValueError(
                    f"el articulo {art!r} (categorias {', '.join(cats)}) no tiene "
                    f"gasto en la region {region}"
                )
            peso, gasto = pesos[art]
            salida.append(
                PesoArticulo(
                    articulo=art, descripcion=desc[art], clase=clase_coicop(art),
                    region=region, peso_en_clase=peso, gasto=gasto,
                    categorias=tuple(cats),
                )
            )
        return salida
    finally:
        if propio:
            con.close()


def articulo_de_categoria(mapeo: dict[str, dict] | None = None) -> dict[str, str]:
    """`categoria -> articulo`. Es la clave de agrupacion del calculo."""
    return {c: s["articulo"] for c, s in (mapeo or cargar_mapeo()).items()}


def cobertura_por_clase(
    pesos: list[PesoArticulo], con: duckdb.DuckDBPyConnection | None = None
) -> dict[str, CoberturaClase]:
    """Que fraccion del gasto de cada clase COICOP cubre el indice.

    Es el numero que antes no se podia calcular. Hoy el indice aplica el 100%
    del peso de cada clase midiendo solo una parte de los articulos que la
    componen: si esa parte no es representativa, el resultado se sesga sin que
    nada falle.
    """
    propio = con is None
    con = con or conectar()
    try:
        region = pesos[0].region if pesos else "GBA"
        if region == REGION_NACIONAL:
            n_por_clase = dict(con.execute(
                "SELECT clase, count(DISTINCT articulo) FROM gastos GROUP BY clase"
            ).fetchall())
        else:
            n_por_clase = dict(con.execute(
                "SELECT clase, count(DISTINCT articulo) FROM gastos "
                "WHERE region = ? GROUP BY clase",
                [REGION_POR_NOMBRE[region]],
            ).fetchall())
        acum: dict[str, CoberturaClase] = {}
        for p in pesos:
            c = acum.get(p.clase)
            if c is None:
                clase_engho = "A" + p.clase.replace(".", "")
                c = CoberturaClase(
                    clase=p.clase, region=region, cubierto=0.0, n_categorias=0,
                    n_articulos_clase=int(n_por_clase.get(clase_engho, 0)),
                )
                acum[p.clase] = c
            c.cubierto += p.peso_en_clase
            c.n_categorias += len(p.categorias)
        return acum
    finally:
        if propio:
            con.close()


def pesos_de_articulos(pesos: list[PesoArticulo]) -> dict[str, float]:
    """`articulo -> peso en su clase`, listo para la agregacion."""
    return {p.articulo: p.peso_en_clase for p in pesos}
