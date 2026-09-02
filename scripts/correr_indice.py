"""Corrida de diagnostico del indice, sobre la ventana que se le pida.

**La ventana es un parametro, no un supuesto.** Por eso hay un solo script y no
uno semanal y otro mensual: el metodo es identico —mediana por quote, muestra
emparejada, Jevons dentro de cada articulo, Laspeyres hacia arriba,
encadenamiento— y lo unico que cambia son dos numeros de `config/parametros.yaml`
(el minimo de dias por quote y el ancho del tope absoluto).

Dos scripts separados duplicarian la logica y con el tiempo se irian separando,
que es exactamente lo que este diseño evita: si el semanal y el mensual pudieran
divergir, lo que se aprende ensayando en semanal no diria nada sobre el mensual.

**Semanal** existe para poder mirar el pipeline sin esperar a que cierren dos
meses. **Mensual** es lo que hay que entregar. El primer numero mensual real sale
cuando cierre septiembre: julio arranca el 27 y no es un mes cerrado.

**Esto no va a la API ni a ningun dashboard.** Es un script, imprime a pantalla y
no persiste nada.

    python scripts/correr_indice.py
    python scripts/correr_indice.py --ventana mensual
    python scripts/correr_indice.py --salida
    python scripts/correr_indice.py --precio precio_efectivo
    python scripts/correr_indice.py --desde 2026-08-03 --hasta 2026-08-16
"""

from __future__ import annotations

import argparse
import contextlib
import logging
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))

from reporte.agregacion import agregar, laspeyres  # noqa: E402
from reporte.elemental import (  # noqa: E402
    detectar_outliers,
    emparejar,
    MOTIVO_OUTLIER_MAD,
    MOTIVO_OUTLIER_TOPE,
    MOTIVO_POCOS_DIAS,
    MOTIVO_SIN_ACTUAL,
    MOTIVO_SIN_BASE,
    indice_elemental,
)
from reporte.lectura import LectorBucket  # noqa: E402
from reporte.ponderadores import (  # noqa: E402
    REGION_NACIONAL,
    actualizar_por_precios,
    articulo_de_categoria,
    clase_coicop,
    calcular as calcular_pesos_engho,
    cobertura_por_clase,
    conectar as conectar_engho,
    factores_actualizacion,
    pesos_de_articulos,
    ponderadores_de_clase,
)
from reporte.periodo import (  # noqa: E402
    MENSUAL,
    SEMANAL,
    ClaveQuote,
    ParametrosVentana,
    Periodo,
    VariacionPeriodo,
    meses_completos,
    quotes_del_periodo,
    semanas_iso_completas,
    serie_encadenada,
)

# Primer dia capturado. SEPA no tiene historico previo.
INICIO_CAPTURA = date(2026, 7, 27)

ANCHO = 82


# --------------------------------------------------------------------------- #
# Ponderadores
# --------------------------------------------------------------------------- #


def cargar_ponderadores(region: str, actualizar: bool) -> dict[str, tuple[str, float]]:
    """Ponderadores de clase del INDEC, opcionalmente actualizados por precios.

    Los del archivo estan en base diciembre 2016. Laspeyres pide que el periodo
    de referencia de los ponderadores coincida con el de la base de precios, y el
    documento metodologico del INDEC recomienda "realizar (al menos) un ajuste
    por la evolucion de los precios". Eso es lo que hace `actualizar`.
    """
    pond = ponderadores_de_clase(region)
    if not actualizar:
        return pond
    return actualizar_por_precios(pond, factores_actualizacion(region))


# --------------------------------------------------------------------------- #
# Agrupacion de quotes por categoria
# --------------------------------------------------------------------------- #


def agrupar(
    precios: dict[ClaveQuote, float], clave_de_producto: dict[str, str]
) -> dict[str, dict[ClaveQuote, float]]:
    """Parte los quotes segun la clave de agrupacion de su producto.

    Se usa dos veces con claves distintas: por CATEGORIA para el reporte de
    diagnostico, y por ARTICULO de la ENGHo para el calculo. No son lo mismo:
    tres articulos cubren dos categorias nuestras cada uno, y el nivel elemental
    del indice es el articulo, que es donde hay ponderacion.
    """
    grupos: dict[str, dict[ClaveQuote, float]] = defaultdict(dict)
    for clave, precio in precios.items():
        k = clave_de_producto.get(clave[2])
        if k:
            grupos[k][clave] = precio
    return grupos


# --------------------------------------------------------------------------- #
# El supuesto de ponderacion por debajo de clase
# --------------------------------------------------------------------------- #

# El INDEC publica pesos hasta CLASE (01.1.1) y nada mas fino. Pero Jevons se
# calcula un nivel mas abajo, en las 15 categorias elementales, asi que para
# subir de categoria a clase hace falta un peso que ese archivo no trae.
#
# RESUELTO: el peso existe en los microdatos de la ENGHo 2017/18, que el INDEC
# publica completos. El codigo de articulo de la ENGHo es el codigo COICOP de
# producto, asi que el join es directo. Ver `reporte/ponderadores.py`.
#
# Los tres criterios viejos se conservan como banda de sensibilidad: muestran
# cuanto se habria equivocado el numero cuando eran lo unico que habia.
#   iguales    neutro, pero dificilmente la harina 0000 sea un cuarto del gasto
#              en pan y cereales.
#   productos  la variedad mide en cuantas formas viene el producto, no cuanto
#              se compra, y amplifica errores de clasificacion.
#   quotes     productos x sucursales: presencia en gondola, no consumo.

# `engho` es el unico que es un DATO; los otros tres son supuestos y quedan
# como banda de sensibilidad, para poder ver cuanto se habria equivocado el
# numero antes de tenerlo.
CRITERIOS = ("engho", "iguales", "productos", "quotes")

DESCRIPCION_CRITERIO = {
    "engho": "gasto de los hogares, ENGHo 2017/18  <-- DATO",
    "iguales": "pesos iguales dentro de la clase (supuesto)",
    "productos": "por cantidad de productos clasificados (supuesto)",
    "quotes": "por cantidad de quotes emparejados (supuesto)",
}


def pesos_de_criterio(
    criterio: str,
    categorias,
    n_productos: dict[str, int],
    n_quotes: dict[str, int],
    engho: dict[str, float] | None = None,
) -> dict[str, float]:
    if criterio == "engho":
        # Una categoria sin peso en la ENGHo queda en cero: no se le inventa uno.
        return {c: (engho or {}).get(c, 0.0) for c in categorias}
    if criterio == "iguales":
        return {c: 1.0 for c in categorias}
    if criterio == "productos":
        return {c: float(n_productos.get(c, 0)) for c in categorias}
    if criterio == "quotes":
        return {c: float(n_quotes.get(c, 0)) for c in categorias}
    raise ValueError(f"criterio de ponderacion desconocido: {criterio!r}")


def agregar_categorias_a_clases(
    indices_cat: dict[str, float],
    clase_de_categoria: dict[str, str],
    pesos_cat: dict[str, float],
) -> dict[str, float]:
    """Media ponderada de las categorias dentro de cada clase COICOP."""
    acum: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
    for cat, indice in indices_cat.items():
        clase = clase_de_categoria.get(cat)
        if clase is None:
            continue
        peso = pesos_cat.get(cat, 0.0)
        acum[clase][0] += peso * indice
        acum[clase][1] += peso
    return {c: num / den for c, (num, den) in acum.items() if den > 0}


# --------------------------------------------------------------------------- #
# Reporte
# --------------------------------------------------------------------------- #


def titulo(texto: str) -> None:
    print()
    print("=" * ANCHO)
    print(texto)
    print("=" * ANCHO)


class Tee:
    """Escribe en la terminal y en el archivo a la vez.

    Guardar el reporte no tiene que costar perderlo de vista mientras corre: la
    descarga tarda minutos y conviene ver como avanza.
    """

    def __init__(self, *destinos) -> None:
        self._destinos = destinos

    def write(self, texto: str) -> int:
        for d in self._destinos:
            d.write(texto)
        return len(texto)

    def flush(self) -> None:
        for d in self._destinos:
            d.flush()


def _ruta_salida(valor: str) -> Path:
    """`--salida` sin valor guarda en `salida/diagnostico_<hoy>.txt`."""
    if valor != "AUTO":
        return Path(valor)
    return RAIZ / "salida" / f"diagnostico_{date.today().isoformat()}.txt"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--desde", default=INICIO_CAPTURA.isoformat())
    p.add_argument("--hasta", default=date.today().isoformat())
    p.add_argument("--precio", default="precio_lista",
                   choices=["precio_lista", "precio_efectivo"])
    p.add_argument("--region", default=REGION_NACIONAL,
                   help="region de los ponderadores; 'nacional' combina las seis "
                        "(default: nacional)")
    p.add_argument("--salida", nargs="?", const="AUTO", metavar="RUTA",
                   help="ademas de imprimir, guarda el reporte en un txt "
                        "(sin valor: salida/diagnostico_<hoy>.txt)")
    p.add_argument("--ventana", default=SEMANAL, choices=[SEMANAL, MENSUAL],
                   help="ventana temporal del calculo (default: semanal)")
    p.add_argument("--sin-actualizar", action="store_true",
                   help="no actualiza los ponderadores por la evolucion de "
                        "precios (para comparar contra la version anterior)")
    p.add_argument("-v", "--verboso", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verboso else logging.WARNING,
        format="%(levelname)s %(message)s",
    )

    # La consola de Windows usa cp1252 y rompe los acentos de los nombres del
    # INDEC ("Azucar" sale "Az?car"). El reporte es para leer, asi que se fuerza
    # UTF-8 en la terminal y en el archivo.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

    if not args.salida:
        return _correr(args)

    destino = _ruta_salida(args.salida)
    destino.parent.mkdir(parents=True, exist_ok=True)
    with destino.open("w", encoding="utf-8") as fh:
        with contextlib.redirect_stdout(Tee(sys.stdout, fh)):
            codigo = _correr(args)
    print(f"\nreporte guardado en {destino}")
    return codigo


def _correr(args) -> int:
    desde = date.fromisoformat(args.desde)
    hasta = date.fromisoformat(args.hasta)

    lector = LectorBucket()

    # -- 1. que hay en el bucket ------------------------------------------- #

    excluidos = lector.comercios_excluidos()
    inv = lector.inventario(desde, hasta, comercios_excluidos=set(excluidos))

    titulo("1. QUE HAY EN EL BUCKET")
    print(inv.informe())
    print()
    print(f"comercios excluidos del indice: {', '.join(sorted(excluidos, key=int))}")
    for c, motivo in sorted(excluidos.items(), key=lambda x: int(x[0])):
        print(f"  {c:>3}  {motivo[:70]}")

    if not inv.dias_presentes:
        print("\nno hay observaciones en el rango pedido")
        return 1

    # -- 2. periodos que se pueden formar ----------------------------------- #

    # La ventana es un parametro, no un supuesto: el metodo es identico y solo
    # cambian los dos numeros de config/parametros.yaml. Semanal existe para
    # poder mirar el pipeline sin esperar a que cierren dos meses; mensual es lo
    # que hay que entregar.
    if args.ventana == MENSUAL:
        periodos = meses_completos(inv.dias_presentes)
    else:
        periodos = semanas_iso_completas(inv.dias_presentes)
    parametros = ParametrosVentana.desde_yaml(args.ventana)

    titulo("2. PERIODOS")
    etiqueta = "meses" if args.ventana == MENSUAL else "semanas ISO"
    print(f"{etiqueta} completos: {len(periodos)}")
    for per in periodos:
        print(f"  {per.etiqueta}  {per.inicio} .. {per.fin}")
    print()
    print(f"ponderadores        {args.region}"
          + ("" if args.sin_actualizar else ", actualizados por precios"))
    print()
    print(f"parametros de la ventana '{parametros.tipo}':")
    print(f"  minimo de dias por quote   {parametros.minimo_dias_quote}")
    print(f"  tope absoluto del ratio     [{1/parametros.tope_ratio:.3f} , "
          f"{parametros.tope_ratio:.3f}]")
    print(f"  umbral MAD                  {parametros.umbral_mad}")
    print(f"  minimo de quotes para MAD   {parametros.minimo_quotes_mad}")

    if not periodos:
        print("\nno hay ningun periodo completo en el rango")
        return 1

    # Con UN solo periodo no hay variacion, pero los quotes y la cobertura si se
    # pueden calcular. Eso es lo que valida el camino antes de que haya dos meses
    # cerrados: si algo se rompe, mejor verlo ahora que en octubre.
    solo_uno = len(periodos) == 1
    if solo_uno:
        print()
        print("Un solo periodo completo: no hay variacion que calcular.")
        if args.ventana == MENSUAL:
            print("Julio arranca el 27, asi que no es un mes cerrado; el primer")
            print("numero mensual sale cuando cierre septiembre.")
        print("Se calculan igual los quotes y la cobertura, para validar el camino.")

    # -- 4. quotes por periodo ---------------------------------------------- #

    productos = lector.productos_clasificados()
    clasif = {
        r[0]: (r[1], r[2])
        for r in lector.clasificacion()
        .project("id_producto, categoria, clase")
        .fetchall()
    }
    categoria_de = {k: v[0] for k, v in clasif.items()}
    clase_de_categoria = {v[0]: v[1] for v in clasif.values()}

    # id_producto -> articulo de la ENGHo, componiendo con la categoria. Es la
    # clave de agrupacion del calculo: el nivel elemental es el articulo.
    art_de_cat = articulo_de_categoria()
    articulo_de = {
        p: art_de_cat[c] for p, (c, _) in clasif.items() if c in art_de_cat
    }
    sin_articulo = {c for c, _ in clasif.values()} - set(art_de_cat)
    if sin_articulo:
        raise SystemExit(
            "estas categorias no tienen articulo en config/mapeo_categorias_engho.yaml: "
            + ", ".join(sorted(sin_articulo))
        )

    # articulo -> categorias que caen en el, para poder nombrar las filas.
    cat_de_articulo: dict[str, list[str]] = defaultdict(list)
    for categoria, art in sorted(art_de_cat.items()):
        cat_de_articulo[art].append(categoria)

    # Cuantos productos distintos tiene cada articulo: uno de los criterios de
    # ponderacion que se contrastan mas abajo.
    n_prod_art: dict[str, int] = defaultdict(int)
    for producto, (categoria, _) in clasif.items():
        n_prod_art[art_de_cat[categoria]] += 1

    # -- ponderadores de la ENGHo (el dato que reemplaza al supuesto) ------- #

    con_engho = conectar_engho()
    try:
        pesos_art_engho = calcular_pesos_engho(args.region, con=con_engho)
        pesos_engho = pesos_de_articulos(pesos_art_engho)
        cobertura = cobertura_por_clase(pesos_art_engho, con=con_engho)
    finally:
        con_engho.close()

    obs = lector.observaciones(
        min(inv.dias_presentes), max(inv.dias_presentes),
        productos=productos, inventario=inv,
    )

    titulo("3. COBERTURA DEL GASTO (ENGHo 2017/18)")
    print("Que fraccion del gasto de cada clase mide el indice. Hoy se aplica el")
    print("100% del peso de cada clase midiendo solo una parte de sus articulos.")
    print()
    print(f"{'clase':<8} {'cubierto':>10} {'art. medidos':>13} "
          f"{'art. clase':>11} {'categorias':>11}")
    print("-" * ANCHO)
    for clase in sorted(cobertura):
        c = cobertura[clase]
        print(f"{clase:<8} {c.pct:>9.1f}% {c.n_articulos:>13} "
              f"{c.n_articulos_clase:>11} {c.n_categorias:>11}")
    print()
    print(f"{'articulo':<10} {'peso en su clase':>17}   categorias que lo miden")
    print("-" * ANCHO)
    for pa in sorted(pesos_art_engho, key=lambda x: -x.peso_en_clase):
        print(f"{pa.articulo:<10} {pa.peso_en_clase*100:>16.2f}%   "
              f"{', '.join(pa.categorias)}")

    titulo("4. QUOTES POR PERIODO")
    print(f"{'periodo':<12} {'con dato':>12} {'sin min dias':>14} {'dias':>6} {'huecos':>8}")
    print("-" * ANCHO)
    resultados = {}
    for per in periodos:
        q = quotes_del_periodo(obs, per, parametros, args.precio)
        resultados[per.etiqueta] = q
        print(f"{per.etiqueta:<12} {q.n_quotes:>12,} {q.n_descartados:>14,} "
              f"{len(q.dias_presentes):>6} {len(q.huecos):>8}")

    # -- 5. variacion periodo contra periodo -------------------------------- #

    if solo_uno:
        titulo("5. VALIDACION DEL CAMINO")
        print("Un solo periodo: no hay variacion, pero el camino quedo ejercitado")
        print("de punta a punta sobre datos reales — lectura del bucket, colapso a")
        print("quotes con los parametros de la ventana, ponderadores y cobertura.")
        if inv.comercios_faltantes:
            print()
            print(f"{len(inv.comercios_faltantes)} dias tienen comercios que no "
                  f"reportaron; sus quotes")
            print("pueden caer bajo el minimo de dias y salir de la muestra.")
        print("=" * ANCHO)
        return 0

    pond = cargar_ponderadores(args.region, not args.sin_actualizar)
    variaciones: list[VariacionPeriodo] = []

    for base_per, act_per in zip(periodos, periodos[1:]):
        q_base = resultados[base_per.etiqueta]
        q_act = resultados[act_per.etiqueta]

        titulo(f"5. {base_per.etiqueta} -> {act_per.etiqueta}")

        emparejados = set(q_base.precios) & set(q_act.precios)
        print(f"quotes en {base_per.etiqueta:<10} {len(q_base.precios):>12,}")
        print(f"quotes en {act_per.etiqueta:<10} {len(q_act.precios):>12,}")
        print(f"EMPAREJADOS (los que cuentan) {len(emparejados):>12,}  "
              f"({100*len(emparejados)/max(len(q_act.precios),1):.1f}% del actual)")

        g_base = agrupar(q_base.precios, categoria_de)
        g_act = agrupar(q_act.precios, categoria_de)

        # -- por categoria elemental: SOLO DIAGNOSTICO --
        # El calculo no usa estos indices. La unidad del indice es el articulo
        # de la ENGHo, que es donde hay ponderacion; estas filas estan para
        # poder ver que hizo cada categoria por separado.
        print()
        print(f"{'categoria':<34} {'quotes':>8} {'var %':>9} {'tope':>6} {'MAD':>6} "
              f"{'umbral':>10}")
        print("-" * ANCHO)

        indices_cat: dict[str, float] = {}
        n_quotes_cat: dict[str, int] = {}
        cuarentena = {MOTIVO_OUTLIER_TOPE: 0, MOTIVO_OUTLIER_MAD: 0,
                      MOTIVO_SIN_BASE: 0, MOTIVO_SIN_ACTUAL: 0}
        flacas: list[tuple[str, int]] = []

        for cat in sorted(set(g_base) | set(g_act)):
            res = indice_elemental(
                cat, g_base.get(cat, {}), g_act.get(cat, {}),
                umbral_mad=parametros.umbral_mad,
                tope_ratio=parametros.tope_ratio,
                minimo_quotes=parametros.minimo_quotes_mad,
            )
            n_tope = sum(1 for d in res.descartes if d.motivo == MOTIVO_OUTLIER_TOPE)
            n_mad = sum(1 for d in res.descartes if d.motivo == MOTIVO_OUTLIER_MAD)
            for d in res.descartes:
                if d.motivo in cuarentena:
                    cuarentena[d.motivo] += 1

            var = "  s/dato" if res.indice is None else f"{res.variacion_pct:+8.3f}"
            print(f"{cat:<34} {res.n_quotes:>8,} {var:>9} {n_tope:>6,} {n_mad:>6,} "
                  f"{res.umbral_usado:>10}")

            if res.indice is not None:
                indices_cat[cat] = res.indice
                n_quotes_cat[cat] = res.n_quotes
            if res.n_quotes < parametros.minimo_quotes_mad:
                flacas.append((cat, res.n_quotes))

        # -- EL CALCULO: un Jevons por ARTICULO de la ENGHo -------------- #
        # Tres articulos cubren dos categorias nuestras cada uno. Como el peso
        # no las distingue, tampoco las distingue el calculo: sus quotes entran
        # a la MISMA media geometrica. Asi el peso relativo entre las dos sale
        # solo, por cuantos ratios aporta cada una, sin elegir un criterio de
        # reparto y sin salir del espacio geometrico.
        ga_base = agrupar(q_base.precios, articulo_de)
        ga_act = agrupar(q_act.precios, articulo_de)

        indices_art: dict[str, float] = {}
        indices_carli: dict[str, float] = {}
        n_quotes_art: dict[str, int] = {}
        for art in sorted(set(ga_base) | set(ga_act)):
            res = indice_elemental(
                art, ga_base.get(art, {}), ga_act.get(art, {}),
                umbral_mad=parametros.umbral_mad,
                tope_ratio=parametros.tope_ratio,
                minimo_quotes=parametros.minimo_quotes_mad,
            )
            # El mismo conjunto de ratios, promediado a mano en vez de en
            # logaritmos. NO se usa para calcular: es la comparacion que muestra
            # el drift de Carli sobre datos reales.
            limpios, _, _ = detectar_outliers(
                emparejar(ga_base.get(art, {}), ga_act.get(art, {}))[0],
                parametros.umbral_mad, parametros.tope_ratio,
                parametros.minimo_quotes_mad,
            )
            if limpios:
                indices_carli[art] = sum(r.ratio for r in limpios) / len(limpios)
            if res.indice is not None:
                indices_art[art] = res.indice
                n_quotes_art[art] = res.n_quotes

        clase_de_articulo = {a: clase_coicop(a) for a in indices_art}
        cuenta_cats = defaultdict(int)
        for art in indices_art:
            cuenta_cats[clase_de_articulo[art]] += 1

        por_criterio: dict[str, dict[str, float]] = {}
        for criterio in CRITERIOS:
            pesos_art = pesos_de_criterio(
                criterio, indices_art, n_prod_art, n_quotes_art, pesos_engho
            )
            por_criterio[criterio] = agregar_categorias_a_clases(
                indices_art, clase_de_articulo, pesos_art
            )
        indices_clase = por_criterio["engho"]

        # -- Jevons vs promedio simple, por articulo --------------------- #
        print()
        print("FORMULA ELEMENTAL: Jevons (la que se usa) vs promedio simple")
        print(f"{'articulo':<10} {'quotes':>9} {'Jevons %':>10} {'simple %':>10} "
              f"{'dif':>8}   producto")
        print("-" * ANCHO)
        difs = []
        for art in sorted(indices_art, key=lambda a: -n_quotes_art[a]):
            j = (indices_art[art] - 1) * 100
            c = (indices_carli.get(art, indices_art[art]) - 1) * 100
            difs.append(c - j)
            nom = ", ".join(cat_de_articulo.get(art, [art]))
            print(f"{art:<10} {n_quotes_art[art]:>9,} {j:>+10.3f} {c:>+10.3f} "
                  f"{c-j:>+8.3f}   {nom[:32]}")
        print("-" * ANCHO)
        print(f"{'':<10} {'':>9} {'':>10} {'sesgo medio':>10} "
              f"{sum(difs)/len(difs):>+8.3f}   el promedio simple SIEMPRE da >= Jevons")

        print()
        print(f"{'clase COICOP':<36} {'peso':>7} {'ENGHo':>9} {'iguales':>9} "
              f"{'x prod':>9} {'x quotes':>9} {'arts':>5}")
        print("-" * ANCHO)
        for clase in sorted(indices_clase):
            nombre, peso = pond.get(clase, (clase, 0.0))
            vals = "".join(
                f"{(por_criterio[c][clase]-1)*100:>+9.3f}" for c in CRITERIOS
            )
            print(f"{clase} {nombre[:30]:<30} {peso:>7.4f}{vals} "
                  f"{cuenta_cats[clase]:>5}")

        # -- clases -> nivel del indice (Laspeyres con pesos del INDEC) --
        pesos_universo = {
            c: pond[c][1] for c in set(clase_de_categoria.values()) if c in pond
        }
        agregados = {
            c: agregar("PILOTO", "Cobertura del piloto", por_criterio[c], pesos_universo)
            for c in CRITERIOS
        }
        # El mismo camino de agregacion pero con los elementales de Carli.
        clases_carli = agregar_categorias_a_clases(
            indices_carli, clase_de_articulo,
            pesos_de_criterio("engho", indices_carli, n_prod_art, n_quotes_art,
                              pesos_engho),
        )
        agregado_carli = agregar("PILOTO", "P", clases_carli, pesos_universo)
        agregado = agregados["engho"]

        print()
        if agregado.indice is None:
            print("sin dato agregado")
        else:
            print("VARIACION AGREGADA")
            for c in CRITERIOS:
                print(f"  {agregados[c].variacion_pct:>+8.4f}%   "
                      f"{DESCRIPCION_CRITERIO[c]}")
            valores = [a.variacion_pct for a in agregados.values()]
            print(f"  {'':>8}    sensibilidad al criterio: "
                  f"{max(valores)-min(valores):.4f} puntos")
            if agregado_carli.indice is not None:
                print()
                print(f"  {agregado_carli.variacion_pct:>+8.4f}%   el mismo calculo "
                      f"con promedio simple en vez de Jevons")
                print(f"  {'':>8}    drift de Carli: "
                      f"{agregado_carli.variacion_pct - agregado.variacion_pct:+.4f} "
                      f"puntos de aumento que no existe")
            print()
            cubierto = sum(
                pond[c][1] for c in indices_clase if c in pond
            )
            print(f"cobertura           {agregado.cobertura*100:.1f}% del peso del "
                  f"piloto ({cubierto:.4f} de {sum(pesos_universo.values()):.4f})")
            print(f"                    {cubierto*100:.2f}% del IPC nacional "
                  f"({args.region})")
            variaciones.append(VariacionPeriodo(base_per, act_per, agregado.indice))

        # -- diagnostico --
        print()
        print("CUARENTENA Y DESCARTES")
        total_desc = sum(cuarentena.values())
        print(f"  tope absoluto        {cuarentena[MOTIVO_OUTLIER_TOPE]:>10,}")
        print(f"  MAD                  {cuarentena[MOTIVO_OUTLIER_MAD]:>10,}")
        print(f"  sin precio base      {cuarentena[MOTIVO_SIN_BASE]:>10,}")
        print(f"  sin precio actual    {cuarentena[MOTIVO_SIN_ACTUAL]:>10,}")
        print(f"  total                {total_desc:>10,}")
        outliers = cuarentena[MOTIVO_OUTLIER_TOPE] + cuarentena[MOTIVO_OUTLIER_MAD]
        base_out = outliers + len(emparejados)
        if base_out:
            print(f"  outliers sobre emparejados: {100*outliers/base_out:.2f}%")

        if flacas:
            print()
            print(f"CATEGORIAS BAJO EL MINIMO DE {parametros.minimo_quotes_mad} QUOTES")
            for cat, n in flacas:
                print(f"  {cat:<40} {n:>6,}")

    # -- 6. serie encadenada ------------------------------------------------ #

    if len(variaciones) >= 1:
        titulo("6. SERIE ENCADENADA")
        print("(diagnostico interno: NO es una serie publicable)")
        print()
        for etiqueta, nivel in serie_encadenada(variaciones, base=100.0):
            print(f"  {etiqueta:<12} {nivel:>10.4f}")

    # -- 7. lo que hay que mirar -------------------------------------------- #

    titulo("7. ADVERTENCIAS")
    print("- Meses incompletos: julio y agosto no estan cerrados. Esto NO es el")
    print("  indice mensual y no se deriva encadenando estas semanas.")
    print("- Ponderadores por debajo de clase: RESUELTO. Salen del gasto de los")
    print("  hogares de la ENGHo 2017/18 (microdatos publicos del INDEC), con el")
    print("  mismo procedimiento que uso el INDEC para los suyos. Los otros tres")
    print("  criterios quedan arriba como banda de sensibilidad.")
    print("  PENDIENTE: los pesos son gasto a precios 2017/18 y Laspeyres pide")
    print("  que coincidan con la base de precios. Falta actualizarlos por la")
    print("  evolucion de los precios hasta la base.")
    print("- COBERTURA DEL GASTO: mirar la seccion 3b. El indice aplica el 100%")
    print("  del peso de cada clase midiendo solo una parte de sus articulos.")
    print("- Region: se usan los ponderadores de", args.region, "para todo el pais.")
    print("  El corte regional real necesita la localidad de cada sucursal.")
    print("- Clasificacion: 100% automatica, ningun producto revisado a mano.")
    if inv.comercios_faltantes:
        print(f"- {len(inv.comercios_faltantes)} dias tienen comercios que no reportaron:")
        print("  sus quotes pueden caer bajo el minimo de dias y salir de la muestra")
        print("  en un periodo si y en otro no.")
    print("=" * ANCHO)
    return 0


if __name__ == "__main__":
    sys.exit(main())
