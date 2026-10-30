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
    python scripts/correr_indice.py --ventana mensual --con-online   # + tiendas y surtidor
"""

from __future__ import annotations

import argparse
import csv
import contextlib
import dataclasses
import logging
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))

from reporte.agregacion import (  # noqa: E402
    agregados_por_division,
    agregar,
    clases_desde_categorias,
    clases_sin_ponderador,
    laspeyres,
    variacion_nacional,
)
from reporte.carnicerias import (  # noqa: E402
    cargar_config as cargar_config_carnicerias,
    relativos_por_plaza,
    tradicional_por_articulo,
)
from reporte.elemental import (  # noqa: E402
    combinar_canales,
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
from reporte.online_indice import (  # noqa: E402
    ANTIGUEDAD_MAXIMA_DIAS,
    cargar_combustibles,
    cargar_fotos,
    clasificacion_por_item,
    por_categoria,
    quotes_por_articulo,
    sumar_grupos,
)
from reporte.ponderadores import (  # noqa: E402
    REGION_NACIONAL,
    REGIONES,
    actualizar_por_precios,
    articulo_de_categoria,
    clase_del_indice,
    calcular as calcular_pesos_engho,
    cobertura_por_clase,
    conectar as conectar_engho,
    factores_actualizacion,
    nivel_de_la_canasta,
    participacion_supermercados,
    pesos_de_articulos,
    pesos_regionales,
    ponderadores_de_clase,
)
from reporte.historial import guardar_corrida  # noqa: E402
from reporte.tarifas import (  # noqa: E402
    cargar_clasificacion as cargar_clasificacion_tarifas,
    fuentes_solo_con_afiliados,
    quotes_por_articulo as quotes_de_tarifas,
)
from reporte.regiones import cargar_regiones, region_de_sucursal  # noqa: E402
from reporte.series import (  # noqa: E402
    COLUMNAS_CSV,
    COLUMNAS_DETALLE,
    SERIE_GLOBAL,
    SERIE_POR_REGION,
    SERIE_SIN_ONLINE,
    division_de_serie,
    serie_de_rubro,
    filas_de_detalle,
    filas_de_series,
)
from reporte.periodo import (  # noqa: E402
    AGREGADORES,
    AGREGADOR_DEFAULT,
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


def indices_por_articulo(
    ga_base: dict[str, dict], ga_act: dict[str, dict], parametros: ParametrosVentana
) -> tuple[dict[str, float], dict[str, int]]:
    """Un Jevons por articulo de la ENGHo (operaciones 2 a 4) y sus quotes.

    Se usa para el numero con todo, para el de solo SEPA y para cada region:
    el mismo emparejado, el mismo filtro y el mismo Jevons en los tres.
    """
    indices: dict[str, float] = {}
    n_quotes: dict[str, int] = {}
    for art in sorted(set(ga_base) | set(ga_act)):
        res = indice_elemental(
            art, ga_base.get(art, {}), ga_act.get(art, {}),
            umbral_mad=parametros.umbral_mad,
            tope_ratio=parametros.tope_ratio,
            minimo_quotes=parametros.minimo_quotes_mad,
        )
        if res.indice is not None:
            indices[art] = res.indice
            n_quotes[art] = res.n_quotes
    return indices, n_quotes


# --------------------------------------------------------------------------- #
# Por region, como el INDEC
# --------------------------------------------------------------------------- #


def variacion_por_region(
    precios_base: dict[ClaveQuote, float],
    precios_act: dict[ClaveQuote, float],
    articulo_de: dict[str, str],
    region_de: dict[tuple[str, str], str | None],
    pesos_engho_region: dict[str, dict[str, float]],
    pond_region: dict[str, dict[str, tuple[str, float]]],
    pond_base_region: dict[str, dict[str, tuple[str, float]]],
    factores_region: dict[str, dict[str, float]],
    clases_taxonomia: set[str],
    parametros: ParametrosVentana,
    tradicional_region: dict[str, dict[str, float]] | None = None,
    part_super_region: dict[str, dict[str, float]] | None = None,
    nacionales_base: dict[str, dict] | None = None,
    nacionales_act: dict[str, dict] | None = None,
) -> tuple[float, list[tuple], int]:
    """El nacional como lo arma el INDEC: cada region y despues la suma (formulas 7 a 15).

    En cada region: un Jevons por articulo con los quotes de sus sucursales,
    las clases con los pesos ENGHo de la region, y Laspeyres con los
    ponderadores de clase de la region. El nacional sale de
    `agregacion.variacion_nacional`, partiendo del nivel del INDEC de esa misma
    canasta en cada region (`ponderadores.nivel_de_la_canasta`, con los factores
    de la planilla de `docs/`, que termina en julio: un mes antes de la base).

    Devuelve (indice nacional, filas por region, quotes sin region).
    """
    def partir(precios):
        por: dict[str, dict] = defaultdict(dict)
        sin = 0
        for k, p in precios.items():
            r = region_de.get((k[0], k[1]))
            if r is None:
                sin += 1
            else:
                por[r][k] = p
        return por, sin

    base_r, _ = partir(precios_base)
    act_r, sin_region = partir(precios_act)

    variaciones: dict[str, float] = {}
    niveles: dict[str, float] = {}
    filas: list[tuple] = []
    for r in pesos_regionales():
        # Lo que vale lo mismo en todo el pais (una tarifa fijada por norma
        # nacional) entra igual en cada region.
        indices_art, n_quotes_art = indices_por_articulo(
            sumar_grupos(agrupar(base_r.get(r, {}), articulo_de), nacionales_base or {}),
            sumar_grupos(agrupar(act_r.get(r, {}), articulo_de), nacionales_act or {}),
            parametros,
        )
        if tradicional_region:
            indices_art = combinar_canales(
                indices_art, tradicional_region[r], part_super_region[r])
        n_quotes = sum(n_quotes_art.values())
        paso = clases_desde_categorias(
            indices_art, {a: clase_del_indice(a) for a in indices_art},
            pesos_engho_region[r],
        )
        universo = {c: pond_region[r][c][1] for c in clases_taxonomia
                    if c in pond_region[r]}
        agregado = agregar(r, r, paso.indices, universo)
        if agregado.indice is None:
            raise SystemExit(f"la region {r} no tiene ningun articulo con dato: "
                             "el nacional por regiones necesita las seis")
        variaciones[r] = agregado.indice
        niveles[r] = nivel_de_la_canasta(
            pond_base_region[r], factores_region[r], set(paso.indices))
        filas.append((r, n_quotes, agregado.indice, agregado.cobertura, niveles[r]))

    return (variacion_nacional(variaciones, niveles, pesos_regionales()),
            filas, sin_region)


# --------------------------------------------------------------------------- #
# Reporte
# --------------------------------------------------------------------------- #


def imprimir_carnicerias(
    indices_sepa: dict[str, float],
    tradicional: dict[str, float],
    part_super: dict[str, float],
    mes: str,
) -> None:
    """Cada articulo con carniceria: SEPA, IPCVA, la proporcion y lo que queda."""
    print()
    if not tradicional:
        print(f"CARNICERIAS: no hay informe del IPCVA de {mes}; la carne va con SEPA solo")
        return
    print("CARNICERIAS (IPCVA) Y SUPERMERCADOS (SEPA), formula 8")
    print(f"{'articulo':<10} {'SEPA %':>9} {'IPCVA %':>9} {'en super':>9} {'junto %':>9}")
    print("-" * ANCHO)
    for art in sorted(tradicional):
        s = part_super.get(art)
        if art not in indices_sepa or s is None:
            continue           # combinar_canales corta si falta la proporcion
        junto = indices_sepa[art] ** s * tradicional[art] ** (1 - s)
        print(f"{art:<10} {(indices_sepa[art]-1)*100:>+9.3f} {(tradicional[art]-1)*100:>+9.3f} "
              f"{s:>9.1%} {(junto-1)*100:>+9.3f}")


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
    # Overrides del filtro de outliers, para analisis de sensibilidad. Sin
    # ellos manda config/parametros.yaml, que es la configuracion de verdad.
    # Existen para poder mostrar el mismo mes con tres filtros distintos sin
    # editar el YAML entre corridas, que es como se pierde la trazabilidad de
    # con que parametros salio cada numero.
    # Como se colapsan los precios diarios de un quote al precio del mes.
    #
    # El default es 'media' porque es la formula del INDEC: "para el caso de los
    # supermercados... primero se calcula la media aritmetica de los precios
    # positivos observados durante el mes" (formula 6, seccion 5.1 de la
    # metodologia del IPC Nacional 2019, en docs/).
    #
    # Medido sobre agosto -> septiembre, pasar de mediana a media sube el indice
    # 0,12 puntos con el filtro default y 0,24 con el filtro apagado. La
    # geometrica da lo mismo que la media (0,02 puntos de diferencia): lo que
    # importa es mediana contra promedio, no cual promedio.
    #
    # 'mediana' sigue disponible y es lo que usa la reconciliacion, porque es lo
    # que calcula el repo de captura.
    p.add_argument("--agregador", default="media",
                   choices=sorted(AGREGADORES),
                   help="colapso de precios diarios a precio del mes")
    p.add_argument("--umbral-mad", type=float, default=None,
                   help="pisa el umbral MAD del YAML. 'inf' desactiva el filtro "
                        "estadistico y deja solo el tope absoluto")
    p.add_argument("--tope-ratio", type=float, default=None,
                   help="pisa la banda del tope absoluto del YAML")
    # Las tiendas online y el surtidor de combustibles (etapa 5). Sus quotes se
    # suman al grupo de su articulo ENGHo y de ahi es el mismo calculo que SEPA.
    # El precio de la ventana es la media de sus fotos diarias, con el minimo
    # de dias de la ventana: 20 en el mes, 5 en la semana (parametros.yaml).
    p.add_argument("--con-online", action="store_true",
                   help="suma las tiendas online y el surtidor de combustibles")
    # Como el INDEC: cada region por separado y despues el nacional con los
    # pesos regionales (formula 15). Sin esto se mezcla el pais en un solo
    # Jevons y cada region pesa por cuantas sucursales tiene en SEPA.
    p.add_argument("--por-region", action="store_true",
                   help="calcula cada region y arma el nacional como el INDEC")
    # Las carnicerias del IPCVA, el canal tradicional de la carne que SEPA no
    # tiene: en cada articulo se combinan con SEPA con la proporcion de la ENGHo
    # (formula 8 del INDEC, operacion 5). El IPCVA publica una vez por mes.
    p.add_argument("--con-carnicerias", action="store_true",
                   help="combina la carne de SEPA con las carnicerias del IPCVA")
    # Las tarifas y precios fijados por norma (servicio domestico...): captura
    # transcribe la norma, config/tarifas.yaml dice el articulo de cada item, y
    # sus valores entran como quotes al Jevons de su articulo.
    p.add_argument("--con-tarifas", action="store_true",
                   help="suma las tarifas y precios fijados por norma")
    p.add_argument("-v", "--verboso", action="store_true")
    args = p.parse_args(argv)
    if args.con_carnicerias and args.ventana != MENSUAL:
        p.error("--con-carnicerias es mensual: el IPCVA publica un informe por mes")
    if args.por_region and args.con_online:
        p.error("--por-region todavia no resuelve la region de lo online ni del "
                "surtidor; no cambia ningun numero hasta noviembre->diciembre")

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
    if args.umbral_mad is not None or args.tope_ratio is not None:
        parametros = dataclasses.replace(
            parametros,
            umbral_mad=(parametros.umbral_mad if args.umbral_mad is None
                        else args.umbral_mad),
            tope_ratio=(parametros.tope_ratio if args.tope_ratio is None
                        else args.tope_ratio),
        )

    titulo("2. PERIODOS")
    etiqueta = "meses" if args.ventana == MENSUAL else "semanas ISO"
    print(f"{etiqueta} completos: {len(periodos)}")
    for per in periodos:
        print(f"  {per.etiqueta}  {per.inicio} .. {per.fin}")
    print()
    print(f"ponderadores        {args.region}"
          + ("" if args.sin_actualizar else ", actualizados por precios"))
    print("fuentes             SEPA"
          + (" + tiendas online + surtidor de combustibles" if args.con_online else "")
          + (" + carnicerias del IPCVA" if args.con_carnicerias else "")
          + (" + tarifas fijadas por norma" if args.con_tarifas else ""))
    print()
    print(f"parametros de la ventana '{parametros.tipo}':")
    print(f"  agregador del mes           {args.agregador}"
          + ("  (el del INDEC para supermercados)" if args.agregador == "media" else ""))
    print(f"  minimo de dias por quote   {parametros.minimo_dias_quote}")
    print(f"  tope absoluto del ratio     [{1/parametros.tope_ratio:.3f} , "
          f"{parametros.tope_ratio:.3f}]")
    print(f"  umbral MAD                  {parametros.umbral_mad}"
          + ("  (desactivado: solo tope absoluto)"
             if parametros.umbral_mad == float("inf") else ""))
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
    # Las clases que mide SEPA, antes de sumar las online: es el universo del
    # numero "sin online" que pidio Juan (22/10/2026).
    clases_sepa = set(clase_de_categoria.values())

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

    # -- lo online: las tiendas y el surtidor ------------------------------ #
    # La tabla publicada trae categoria, clase y articulo, pero el articulo que
    # manda es el de config/mapeo_categorias_engho.yaml, igual que para SEPA.
    # Si las dos fuentes no coinciden se corta: quedarse con una en silencio es
    # elegir el peso sin saber cual.
    clasif_online: dict = {}
    fotos_online: dict = {}
    if args.con_online:
        clasif_online = clasificacion_por_item(lector.clasificacion_online())
        no_coinciden = sorted({
            (cat, clase, art) for cat, clase, art in clasif_online.values()
            if art_de_cat.get(cat) != art or clase_del_indice(art) != clase
        })
        if no_coinciden:
            raise SystemExit(
                "la clasificacion online no coincide con "
                "config/mapeo_categorias_engho.yaml (categoria, clase, articulo): "
                + ", ".join(map(str, no_coinciden[:10]))
            )
        for cat, clase, _ in clasif_online.values():
            clase_de_categoria[cat] = clase
        fotos_online = cargar_fotos(lector.bajar_fotos_online())

        # Combustibles: el surtidor entra por el mismo camino. Su categoria sale
        # del producto del dataset, y el articulo del mismo mapeo ENGHo.
        fotos_comb, cat_comb = cargar_combustibles(lector.bajar_fotos_combustibles())
        sin_mapeo = sorted(set(cat_comb.values()) - set(art_de_cat))
        if sin_mapeo:
            raise SystemExit(
                "estas categorias de combustibles no tienen articulo en "
                "config/mapeo_categorias_engho.yaml: " + ", ".join(sin_mapeo)
            )
        for clave, cat in cat_comb.items():
            art = art_de_cat[cat]
            clasif_online[clave] = (cat, clase_del_indice(art), art)
            clase_de_categoria[cat] = clase_del_indice(art)
        for dia, quotes in fotos_comb.items():
            fotos_online.setdefault(dia, {}).update(quotes)

    # -- las tarifas fijadas por norma ------------------------------------- #
    filas_tarifas: list[dict] = []
    clasif_tarifas: dict[tuple[str, str], str] = {}
    con_afiliados: set[str] = set()
    if args.con_tarifas:
        filas_tarifas = lector.tarifas()
        clasif_tarifas = cargar_clasificacion_tarifas()
        con_afiliados = fuentes_solo_con_afiliados()
        sin_mapeo = sorted(set(clasif_tarifas.values()) - set(art_de_cat))
        if sin_mapeo:
            raise SystemExit("estas categorias de tarifas no tienen articulo en "
                             "config/mapeo_categorias_engho.yaml: " + ", ".join(sin_mapeo))
        for cat in clasif_tarifas.values():
            clase_de_categoria[cat] = clase_del_indice(art_de_cat[cat])

    # articulo -> categorias que caen en el, para poder nombrar las filas.
    cat_de_articulo: dict[str, list[str]] = defaultdict(list)
    for categoria, art in sorted(art_de_cat.items()):
        cat_de_articulo[art].append(categoria)

    # Cuantos productos distintos tiene cada articulo: uno de los criterios de
    # ponderacion que se contrastan mas abajo.
    n_prod_art: dict[str, int] = defaultdict(int)
    for producto, (categoria, _) in clasif.items():
        n_prod_art[art_de_cat[categoria]] += 1
    for _, _, art in clasif_online.values():
        n_prod_art[art] += 1
    for cat in clasif_tarifas.values():
        n_prod_art[art_de_cat[cat]] += 1

    # -- ponderadores de la ENGHo (el dato que reemplaza al supuesto) ------- #

    con_engho = conectar_engho()
    try:
        # El mapeo tiene tambien los articulos online; sin --con-online no son
        # parte de esta corrida y no van a la tabla de cobertura.
        clases_en_uso = set(clase_de_categoria.values())
        pesos_art_engho = [
            pa for pa in calcular_pesos_engho(args.region, con=con_engho)
            if pa.clase in clases_en_uso
        ]
        pesos_engho = pesos_de_articulos(pesos_art_engho)
        cobertura = cobertura_por_clase(pesos_art_engho, con=con_engho)
        # Por region: los pesos ENGHo de cada una. Un articulo sin gasto en
        # una region pesa cero ahi (rimel en Patagonia), no corta.
        pesos_engho_region = {
            r: pesos_de_articulos(calcular_pesos_engho(
                r, con=con_engho, omitir_sin_gasto=True))
            for r in REGIONES.values()
        } if args.por_region else {}
        # Donde compran la carne los hogares: la proporcion fija de la formula 8.
        part_super = (participacion_supermercados(con_engho, args.region)
                      if args.con_carnicerias else {})
        part_super_region = {
            r: participacion_supermercados(con_engho, r) for r in REGIONES.values()
        } if args.con_carnicerias and args.por_region else {}
    finally:
        con_engho.close()
    filas_ipcva = lector.precios_ipcva() if args.con_carnicerias else []
    config_carnicerias = cargar_config_carnicerias()

    # La region de cada sucursal: captura publica el partido (georef) y este
    # repo decide cual es GBA (config/regiones.yaml).
    region_de: dict[tuple[str, str], str | None] = {}
    if args.por_region:
        regiones_cfg = cargar_regiones()
        region_de = {
            (s["id_comercio"], s["id_sucursal"]): region_de_sucursal(
                s["provincia"], s["provincia_id"], s["departamento_id"], regiones_cfg)
            for s in lector.sucursales()
        }

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
        q = quotes_del_periodo(obs, per, parametros, args.precio, args.agregador)
        resultados[per.etiqueta] = q
        print(f"{per.etiqueta:<12} {q.n_quotes:>12,} {q.n_descartados:>14,} "
              f"{len(q.dias_presentes):>6} {len(q.huecos):>8}")

    # Online: articulo -> {(sitio, item_id): media de las fotos del mes}, con el
    # mismo minimo de dias que SEPA. Un mes sin fotos suficientes queda vacio.
    online: dict[str, dict] = {}
    if args.con_online:
        print()
        print(f"{'online':<12} {'con dato':>12}   (fotos desde "
              f"{min(fotos_online) if fotos_online else '-'})")
        print("-" * ANCHO)
        for per in periodos:
            online[per.etiqueta] = quotes_por_articulo(
                fotos_online, clasif_online, per.inicio, per.fin,
                parametros.minimo_dias_quote,
            )
            n = sum(len(g) for g in online[per.etiqueta].values())
            print(f"{per.etiqueta:<12} {n:>12,}")

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

    # Por region: ponderadores de clase actualizados (para Laspeyres), y los
    # base y los factores (para el nivel del que parte cada region).
    pond_region = {r: cargar_ponderadores(r, not args.sin_actualizar)
                   for r in REGIONES.values()} if args.por_region else {}
    pond_base_region = {r: ponderadores_de_clase(r)
                        for r in REGIONES.values()} if args.por_region else {}
    factores_region = {r: factores_actualizacion(r)
                       for r in REGIONES.values()} if args.por_region else {}

    pond = cargar_ponderadores(args.region, not args.sin_actualizar)
    # Los pesos de `pond` estan renormalizados a 1 sobre las clases del piloto:
    # son los que necesita Laspeyres. Para decir que fraccion del IPC mide el
    # indice hacen falta los del archivo, sin renormalizar, que suman 1 sobre la
    # canasta completa. Calcularlo sobre `pond` da 100% siempre.
    pond_crudos = ponderadores_de_clase(args.region)
    variaciones: list[VariacionPeriodo] = []
    # Las series que se llevan mes a mes (global, sin online, rubros): una
    # entrada por mes en cada una, alineadas con `meses_series`.
    series: dict[str, list[tuple[float | None, float]]] = {}
    meses_series: list[tuple[str, str]] = []
    # Indice y peso de cada clase y articulo: el contrato con contra_indec.py.
    detalle: list[dict] = []

    for base_per, act_per in zip(periodos, periodos[1:]):
        q_base = resultados[base_per.etiqueta]
        q_act = resultados[act_per.etiqueta]

        titulo(f"5. {base_per.etiqueta} -> {act_per.etiqueta}")

        emparejados = set(q_base.precios) & set(q_act.precios)
        print(f"quotes en {base_per.etiqueta:<10} {len(q_base.precios):>12,}")
        print(f"quotes en {act_per.etiqueta:<10} {len(q_act.precios):>12,}")
        print(f"EMPAREJADOS (los que cuentan) {len(emparejados):>12,}  "
              f"({100*len(emparejados)/max(len(q_act.precios),1):.1f}% del actual)")

        # Online se suma a los mismos grupos: por categoria para el
        # diagnostico, por articulo para el calculo. Las claves no chocan.
        on_base = online.get(base_per.etiqueta, {})
        on_act = online.get(act_per.etiqueta, {})
        if args.con_online:
            emp_online = ({k for g in on_base.values() for k in g}
                          & {k for g in on_act.values() for k in g})
            print(f"  online emparejados          {len(emp_online):>12,}"
                  + ("  (sin dos meses de fotos: no cambia el numero)"
                     if not emp_online else ""))
            emparejados |= emp_online

        # Las tarifas: el valor del mes en que cierra cada periodo.
        def tarifas_de(per, agrupar_por):
            return quotes_de_tarifas(filas_tarifas, clasif_tarifas, agrupar_por,
                                     per.fin.strftime("%Y-%m"),
                                     solo_con_afiliados=con_afiliados) if args.con_tarifas else {}
        tar_base = tarifas_de(base_per, art_de_cat)
        tar_act = tarifas_de(act_per, art_de_cat)
        if args.con_tarifas:
            emp_tarifas = ({k for g in tar_base.values() for k in g}
                           & {k for g in tar_act.values() for k in g})
            print(f"  tarifas emparejadas         {len(emp_tarifas):>12,}"
                  + ("  (sin valores de los dos meses: no cambia el numero)"
                     if not emp_tarifas else ""))
            emparejados |= emp_tarifas
        por_cat = {c: c for c in clasif_tarifas.values()}

        g_base = sumar_grupos(sumar_grupos(agrupar(q_base.precios, categoria_de),
                                           por_categoria(on_base, clasif_online)),
                              tarifas_de(base_per, por_cat))
        g_act = sumar_grupos(sumar_grupos(agrupar(q_act.precios, categoria_de),
                                          por_categoria(on_act, clasif_online)),
                             tarifas_de(act_per, por_cat))

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
        ga_base = sumar_grupos(sumar_grupos(agrupar(q_base.precios, articulo_de), on_base),
                               tar_base)
        ga_act = sumar_grupos(sumar_grupos(agrupar(q_act.precios, articulo_de), on_act),
                              tar_act)

        indices_art, n_quotes_art = indices_por_articulo(ga_base, ga_act, parametros)

        # -- las carnicerias: el canal tradicional (operacion 5) ---------- #
        trad_art: dict[str, float] = {}
        rel_plaza: dict = {}
        if args.con_carnicerias:
            rel_plaza = relativos_por_plaza(
                filas_ipcva, base_per.etiqueta, act_per.etiqueta)
            trad_art = tradicional_por_articulo(
                rel_plaza, config_carnicerias.pares, config_carnicerias.plazas,
                args.region, pesos_regionales())
            imprimir_carnicerias(indices_art, trad_art, part_super, act_per.etiqueta)
            indices_art = combinar_canales(indices_art, trad_art, part_super)

        # El mismo conjunto de ratios, promediado a mano en vez de en
        # logaritmos. NO se usa para calcular: es la comparacion que muestra el
        # drift de Carli sobre datos reales.
        indices_carli: dict[str, float] = {}
        for art in sorted(set(ga_base) | set(ga_act)):
            limpios, _, _ = detectar_outliers(
                emparejar(ga_base.get(art, {}), ga_act.get(art, {}))[0],
                parametros.umbral_mad, parametros.tope_ratio,
                parametros.minimo_quotes_mad,
            )
            if limpios:
                indices_carli[art] = sum(r.ratio for r in limpios) / len(limpios)
        # Con el mismo canal tradicional, para que la diferencia sea solo la formula.
        indices_carli = combinar_canales(indices_carli, trad_art, part_super)

        clase_de_articulo = {a: clase_del_indice(a) for a in indices_art}
        cuenta_cats = defaultdict(int)
        for art in indices_art:
            cuenta_cats[clase_de_articulo[art]] += 1

        por_criterio: dict[str, dict[str, float]] = {}
        descartes_clase = None
        for criterio in CRITERIOS:
            pesos_art = pesos_de_criterio(
                criterio, indices_art, n_prod_art, n_quotes_art, pesos_engho
            )
            paso = clases_desde_categorias(
                indices_art, clase_de_articulo, pesos_art
            )
            por_criterio[criterio] = paso.indices
            if criterio == "engho":
                descartes_clase = paso
        indices_clase = por_criterio["engho"]

        # Los articulos que no llegaron a ninguna clase. Antes se salteaban en
        # silencio y el indice quedaba calculado sobre menos canasta.
        if descartes_clase.sin_clase:
            print()
            print(f"ATENCION {len(descartes_clase.sin_clase)} articulos sin clase "
                  f"COICOP: NO entran al indice")
            for a in descartes_clase.sin_clase[:10]:
                print(f"  - {a}")
        if descartes_clase.sin_peso:
            print()
            print(f"ATENCION {len(descartes_clase.sin_peso)} articulos sin peso "
                  f"ENGHo: NO entran al indice")
            for a in descartes_clase.sin_peso[:10]:
                print(f"  - {a}")

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
        #
        # Guarda de la operacion 7: ver clases_sin_ponderador en agregacion.py.
        clases_taxonomia = set(clase_de_categoria.values())
        sin_ponderador = clases_sin_ponderador(clases_taxonomia, pond)
        if sin_ponderador:
            print()
            print("ERROR: estas clases estan en la taxonomia y no tienen "
                  "ponderador:")
            for c in sin_ponderador:
                print(f"  - {c}")
            print("  Declararlas en config/indices_ipc.yaml o sacarlas de la "
                  "taxonomia.")
            print("  Si se las dejara afuera, el indice saldria sobre menos "
                  "canasta y la cobertura seguiria diciendo 100%.")
            return 1
        pesos_universo = {c: pond[c][1] for c in clases_taxonomia}
        agregados = {
            c: agregar("PILOTO", "Cobertura del piloto", por_criterio[c], pesos_universo)
            for c in CRITERIOS
        }
        # El mismo camino de agregacion pero con los elementales de Carli.
        clases_carli = clases_desde_categorias(
            indices_carli, clase_de_articulo,
            pesos_de_criterio("engho", indices_carli, n_prod_art, n_quotes_art,
                              pesos_engho),
        ).indices
        agregado_carli = agregar("PILOTO", "P", clases_carli, pesos_universo)
        agregado = agregados["engho"]

        # -- LOS DOS NUMEROS Y LOS RUBROS (Juan, 22/10/2026) ------------- #
        # El sin-online se RECALCULA con los quotes de SEPA solos, no se saca
        # restando clases: cada articulo es UN Jevons sobre las dos fuentes, y
        # si una clase tuviera las dos, restar dejaria lo online adentro.
        if args.con_online or args.con_tarifas:
            idx_sepa, _ = indices_por_articulo(
                agrupar(q_base.precios, articulo_de),
                agrupar(q_act.precios, articulo_de), parametros,
            )
            # Las carnicerias no son online: se quedan tambien en este numero.
            idx_sepa = combinar_canales(idx_sepa, trad_art, part_super)
            clases_solo_sepa = clases_desde_categorias(
                idx_sepa, {a: clase_del_indice(a) for a in idx_sepa}, pesos_engho,
            ).indices
            sin_online = agregar("SIN_ONLINE", "solo SEPA", clases_solo_sepa,
                                 {c: pond[c][1] for c in clases_sepa})
        else:
            clases_solo_sepa, sin_online = indices_clase, agregado
        rubros = agregados_por_division(indices_clase, pesos_universo)

        def del_ipc(clases) -> float:
            return sum(pond_crudos[c][1] for c in clases if c in pond_crudos)

        del_mes: dict[str, tuple[float | None, float]] = {}
        if args.con_online or args.con_tarifas:
            del_mes[SERIE_GLOBAL] = (agregado.indice, del_ipc(indices_clase))
        del_mes[SERIE_SIN_ONLINE] = (sin_online.indice, del_ipc(clases_solo_sepa))
        for div, res in rubros.items():
            del_mes[serie_de_rubro(div, pond_crudos[div][0])] = (res.indice, del_ipc(res.componentes))
        indice_region: float | None = None

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
                pond_crudos[c][1] for c in indices_clase if c in pond_crudos
            )
            print(f"cobertura           {agregado.cobertura*100:.1f}% del peso del "
                  f"piloto ({sum(pesos_universo.values()):.4f} renormalizado)")
            print(f"                    {cubierto*100:.2f}% del IPC {args.region} "
                  f"({cubierto:.4f} de 1.0000 de la canasta completa)")

            indice_mes = agregado.indice
            if args.por_region:
                indice_mes, filas, sin_region = variacion_por_region(
                    q_base.precios, q_act.precios, articulo_de, region_de,
                    pesos_engho_region, pond_region, pond_base_region,
                    factores_region, clases_taxonomia, parametros,
                    tradicional_region={
                        r: tradicional_por_articulo(
                            rel_plaza, config_carnicerias.pares,
                            config_carnicerias.plazas, r, pesos_regionales())
                        for r in REGIONES.values()
                    } if args.con_carnicerias else None,
                    part_super_region=part_super_region,
                    nacionales_base=tar_base, nacionales_act=tar_act,
                )
                print()
                print("POR REGION, COMO EL INDEC (cada region y despues el nacional)")
                print(f"{'region':<11} {'peso':>6} {'quotes':>10} {'var %':>9} "
                      f"{'cobertura':>10} {'nivel base':>11}")
                print("-" * ANCHO)
                pesos_r = pesos_regionales()
                for r, n, v, cob, nivel in filas:
                    print(f"{r:<11} {pesos_r[r]:>6.4f} {n:>10,} {(v-1)*100:>+9.4f} "
                          f"{cob*100:>9.1f}% {nivel:>11.2f}")
                print("-" * ANCHO)
                print(f"  {(indice_mes-1)*100:>+8.4f}%   nacional por regiones "
                      f"(formula 15)  <-- el que vale con --por-region")
                print(f"  {agregado.variacion_pct:>+8.4f}%   el pais mezclado en un "
                      f"solo Jevons, para comparar")
                print(f"  quotes sin region: {sin_region:,} (no entran al calculo "
                      f"por region)")
                indice_region = indice_mes
            variaciones.append(VariacionPeriodo(base_per, act_per, indice_mes))

        if args.por_region:
            del_mes[SERIE_POR_REGION] = (indice_region, del_ipc(indices_clase))
        print()
        print("LOS DOS NUMEROS Y LOS RUBROS (rubro = division del INDEC)")
        print(f"{'serie':<46} {'var %':>9} {'del IPC':>8} {'de la div':>10}")
        print("-" * ANCHO)
        for nombre, (indice, cubre) in del_mes.items():
            var = "   s/dato" if indice is None else f"{(indice-1)*100:>+9.4f}"
            div = division_de_serie(nombre) or ""
            de_div = (f"{100*cubre/pond_crudos[div][1]:>9.0f}%"
                      if div in rubros and pond_crudos.get(div, ("", 0))[1] else "")
            print(f"{nombre[:46]:<46} {var} {100*cubre:>7.2f}% {de_div:>10}")
        print("  'de la div' es cuanto de la division del INDEC mide el rubro: un")
        print("  rubro que mide poco de su division no es la division del INDEC.")
        meses_series.append((base_per.etiqueta, act_per.etiqueta))
        for nombre, valor in del_mes.items():
            series.setdefault(nombre, []).append(valor)
        detalle.extend(filas_de_detalle(
            base_per.etiqueta, act_per.etiqueta, indices_clase, pesos_universo,
            indices_art, pesos_engho,
        ))

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

    if meses_series:
        filas_series = filas_de_series(meses_series, series)
        print()
        print("POR SERIE: cada una encadenada por su lado, base 100 en su primer mes")
        print(f"{'serie':<46} " + " ".join(f"{m:>10}" for _, m in meses_series))
        print("-" * ANCHO)
        for nombre in series:
            niveles = [f["nivel"] for f in filas_series if f["serie"] == nombre]
            print(f"{nombre[:46]:<46} " + " ".join(
                f"{n:>10.4f}" if n is not None else f"{'-':>10}" for n in niveles))
        if args.salida:
            destino_csv = _ruta_salida(args.salida).with_suffix(".csv")
            with destino_csv.open("w", encoding="utf-8", newline="") as fh:
                escritor = csv.DictWriter(fh, fieldnames=COLUMNAS_CSV)
                escritor.writeheader()
                escritor.writerows(filas_series)
            print(f"\nseries guardadas en {destino_csv}")
            destino_detalle = destino_csv.with_name(destino_csv.stem + "_detalle.csv")
            with destino_detalle.open("w", encoding="utf-8", newline="") as fh:
                escritor = csv.DictWriter(fh, fieldnames=COLUMNAS_DETALLE)
                escritor.writeheader()
                escritor.writerows(detalle)
            print(f"detalle por clase y articulo en {destino_detalle} "
                  f"(lo lee scripts/contra_indec.py)")
            # La corrida se suma a la historia que lee el dashboard; nunca pisa.
            corrida = guardar_corrida(
                args.ventana, datetime.now(),
                {"series": filas_series, "detalle": detalle},
                raiz=destino_csv.parent / "historial",
            )
            print(f"corrida guardada en la historia: {corrida}")

    # -- 7. lo que hay que mirar -------------------------------------------- #

    titulo("7. ADVERTENCIAS")
    # La advertencia de la ventana semanal decia que agosto no estaba cerrado y
    # que esto no era el indice mensual. En la corrida mensual eso es falso y es
    # justo el tipo de texto que, leido de afuera, hace desconfiar del resto.
    if args.ventana == SEMANAL:
        print("- Serie DIAGNOSTICA: el indice semanal no se publica y el mensual")
        print("  NO se deriva encadenando estas semanas. Son dos muestras")
        print("  emparejadas distintas, calculadas cada una desde las")
        print("  observaciones diarias.")
    else:
        print(f"- Meses cerrados y comparados: {len(periodos)}"
              f" ({', '.join(p.etiqueta for p in periodos)}).")
        print("  Julio queda afuera: tiene 5 dias de captura, no es un mes.")
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
    if args.con_online:
        print("- Online: tiendas y surtidor, con la misma muestra emparejada,")
        print("  el mismo filtro y el mismo Jevons que SEPA. Diez busquedas por")
        print("  tienda no son una muestra representativa de la clase: tener")
        print("  fuente no es medirla bien.")
        print("- Combustibles: el dataset de Energia no tiene a YPF al dia (el")
        print("  97,9% de sus precios tiene mas de un año) y se cortan los")
        print(f"  precios de mas de {ANTIGUEDAD_MAXIMA_DIAS} dias: mide Shell, Axion,"
              " Puma, Gulf y el resto.")
    if args.con_tarifas:
        print("- Tarifas: valores transcriptos de cada norma (config/tarifas/ en")
        print("  captura), validados contra el aumento que dice la norma. Valen")
        print("  igual en todo el pais.")
    if inv.comercios_faltantes:
        print(f"- {len(inv.comercios_faltantes)} dias tienen comercios que no reportaron:")
        print("  sus quotes pueden caer bajo el minimo de dias y salir de la muestra")
        print("  en un periodo si y en otro no.")
    print("=" * ANCHO)
    return 0


if __name__ == "__main__":
    sys.exit(main())
