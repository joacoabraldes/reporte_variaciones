"""Corrida de diagnostico del indice sobre ventanas semanales.

**El objetivo no es el numero.** Con 22 dias capturados y meses incompletos, la
variacion semanal no es un dato publicable: es un ensayo para ver como se
comporta el pipeline antes de que en octubre haya que entregar el mensual. Lo
que importa es si la cuarentena se llena, si la muestra emparejada colapsa, o si
una categoria no llega al minimo de quotes.

El metodo es el mismo que va a correr sobre meses: mediana por quote, muestra
emparejada, Jevons dentro de cada categoria, Laspeyres hacia arriba,
encadenamiento. Cambian dos parametros (`config/parametros.yaml`) y nada mas.

**Esto no va a la API ni a ningun dashboard.** Es un script, imprime a pantalla
y no persiste nada.

    python scripts/correr_semanal.py
    python scripts/correr_semanal.py --salida
    python scripts/correr_semanal.py --salida salida/prueba.txt
    python scripts/correr_semanal.py --precio precio_efectivo
    python scripts/correr_semanal.py --desde 2026-08-03 --hasta 2026-08-16
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
    MOTIVO_OUTLIER_MAD,
    MOTIVO_OUTLIER_TOPE,
    MOTIVO_POCOS_DIAS,
    MOTIVO_SIN_ACTUAL,
    MOTIVO_SIN_BASE,
    indice_elemental,
)
from reporte.lectura import LectorBucket  # noqa: E402
from reporte.periodo import (  # noqa: E402
    SEMANAL,
    ClaveQuote,
    ParametrosVentana,
    Periodo,
    VariacionPeriodo,
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


def cargar_ponderadores(region: str) -> dict[str, tuple[str, float]]:
    """`clase COICOP -> (nombre, peso)` para una region."""
    datos = yaml.safe_load((RAIZ / "config" / "ponderadores.yaml").read_text("utf-8"))
    pond = {}
    for codigo, spec in (datos.get("ponderaciones") or {}).items():
        if region in spec:
            pond[str(codigo)] = (spec.get("nombre", codigo), float(spec[region]))
    return pond


# --------------------------------------------------------------------------- #
# Agrupacion de quotes por categoria
# --------------------------------------------------------------------------- #


def agrupar_por_categoria(
    precios: dict[ClaveQuote, float], categoria_de: dict[str, str]
) -> dict[str, dict[ClaveQuote, float]]:
    """Parte los quotes segun la categoria elemental de su producto."""
    grupos: dict[str, dict[ClaveQuote, float]] = defaultdict(dict)
    for clave, precio in precios.items():
        cat = categoria_de.get(clave[2])
        if cat:
            grupos[cat][clave] = precio
    return grupos


# --------------------------------------------------------------------------- #
# El supuesto de ponderacion por debajo de clase
# --------------------------------------------------------------------------- #

# El INDEC publica pesos hasta CLASE (01.1.1) y nada mas fino; verificado contra
# docs/ponderadores_ipc.xls, que llega hasta ahi. Pero Jevons se calcula un nivel
# mas abajo, en las 15 categorias elementales, asi que para subir de categoria a
# clase hace falta un peso que NO EXISTE en la fuente.
#
# Cualquier cosa que se ponga ahi es un supuesto. En vez de elegir uno y que
# quede invisible dentro del numero, se calcula el indice con los tres y se
# reporta la banda: asi el que lee ve cuanto del resultado es dato y cuanto es
# supuesto.
#
# Ninguno es correcto:
#   iguales    neutro, pero dificilmente la harina 0000 sea un cuarto del gasto
#              en pan y cereales.
#   productos  la variedad mide en cuantas formas viene el producto (los fideos
#              se subdividen en mil formas, la harina en dos), no cuanto se
#              compra. Ademas amplifica errores de clasificacion.
#   quotes     productos x sucursales: presencia en gondola, no consumo.
#
# Un ponderador es participacion en el GASTO (precio x cantidad) y SEPA no
# publica cantidades. Sin una fuente externa mas fina, esto no se resuelve.
# PENDIENTE: ver si la microdata de la ENGHo tiene apertura por variedad.

CRITERIOS = ("iguales", "productos", "quotes")

DESCRIPCION_CRITERIO = {
    "iguales": "pesos iguales dentro de la clase",
    "productos": "por cantidad de productos clasificados",
    "quotes": "por cantidad de quotes emparejados",
}


def pesos_de_categoria(
    criterio: str,
    categorias,
    n_productos: dict[str, int],
    n_quotes: dict[str, int],
) -> dict[str, float]:
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
    p.add_argument("--region", default="GBA",
                   help="region de los ponderadores del INDEC (default: GBA)")
    p.add_argument("--salida", nargs="?", const="AUTO", metavar="RUTA",
                   help="ademas de imprimir, guarda el reporte en un txt "
                        "(sin valor: salida/diagnostico_<hoy>.txt)")
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

    periodos = semanas_iso_completas(min(inv.dias_presentes), max(inv.dias_presentes))
    parametros = ParametrosVentana.desde_yaml(SEMANAL)

    titulo("2. PERIODOS")
    print(f"semanas ISO completas: {len(periodos)}")
    for per in periodos:
        print(f"  {per.etiqueta}  {per.inicio} .. {per.fin}")
    print()
    print(f"parametros de la ventana '{parametros.tipo}':")
    print(f"  minimo de dias por quote   {parametros.minimo_dias_quote}")
    print(f"  tope absoluto del ratio     [{1/parametros.tope_ratio:.3f} , "
          f"{parametros.tope_ratio:.3f}]")
    print(f"  umbral MAD                  {parametros.umbral_mad}")
    print(f"  minimo de quotes para MAD   {parametros.minimo_quotes_mad}")

    if len(periodos) < 2:
        print("\nhacen falta al menos 2 periodos completos para una variacion")
        return 1

    # -- 3. quotes por periodo ---------------------------------------------- #

    productos = lector.productos_clasificados()
    clasif = {
        r[0]: (r[1], r[2])
        for r in lector.clasificacion()
        .project("id_producto, categoria, clase")
        .fetchall()
    }
    categoria_de = {k: v[0] for k, v in clasif.items()}
    clase_de_categoria = {v[0]: v[1] for v in clasif.values()}

    # Cuantos productos distintos tiene cada categoria: uno de los criterios de
    # ponderacion que se contrastan mas abajo.
    n_productos: dict[str, int] = defaultdict(int)
    for categoria, _ in clasif.values():
        n_productos[categoria] += 1

    obs = lector.observaciones(
        min(inv.dias_presentes), max(inv.dias_presentes),
        productos=productos, inventario=inv,
    )

    titulo("3. QUOTES POR PERIODO")
    print(f"{'periodo':<12} {'con dato':>12} {'sin min dias':>14} {'dias':>6} {'huecos':>8}")
    print("-" * ANCHO)
    resultados = {}
    for per in periodos:
        q = quotes_del_periodo(obs, per, parametros, args.precio)
        resultados[per.etiqueta] = q
        print(f"{per.etiqueta:<12} {q.n_quotes:>12,} {q.n_descartados:>14,} "
              f"{len(q.dias_presentes):>6} {len(q.huecos):>8}")

    # -- 4. variacion periodo contra periodo -------------------------------- #

    pond = cargar_ponderadores(args.region)
    variaciones: list[VariacionPeriodo] = []

    for base_per, act_per in zip(periodos, periodos[1:]):
        q_base = resultados[base_per.etiqueta]
        q_act = resultados[act_per.etiqueta]

        titulo(f"4. {base_per.etiqueta} -> {act_per.etiqueta}")

        emparejados = set(q_base.precios) & set(q_act.precios)
        print(f"quotes en {base_per.etiqueta:<10} {len(q_base.precios):>12,}")
        print(f"quotes en {act_per.etiqueta:<10} {len(q_act.precios):>12,}")
        print(f"EMPAREJADOS (los que cuentan) {len(emparejados):>12,}  "
              f"({100*len(emparejados)/max(len(q_act.precios),1):.1f}% del actual)")

        g_base = agrupar_por_categoria(q_base.precios, categoria_de)
        g_act = agrupar_por_categoria(q_act.precios, categoria_de)

        # -- por categoria elemental --
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

        # -- categorias -> clase COICOP (Laspeyres, con el peso que no existe) --
        cuenta_cats = defaultdict(int)
        for cat in indices_cat:
            if cat in clase_de_categoria:
                cuenta_cats[clase_de_categoria[cat]] += 1

        por_criterio: dict[str, dict[str, float]] = {}
        for criterio in CRITERIOS:
            pesos_cat = pesos_de_categoria(
                criterio, indices_cat, n_productos, n_quotes_cat
            )
            por_criterio[criterio] = agregar_categorias_a_clases(
                indices_cat, clase_de_categoria, pesos_cat
            )
        indices_clase = por_criterio["iguales"]

        print()
        print(f"{'clase COICOP':<40} {'peso':>7} {'iguales':>9} {'x prod':>9} "
              f"{'x quotes':>9} {'cats':>5}")
        print("-" * ANCHO)
        for clase in sorted(indices_clase):
            nombre, peso = pond.get(clase, (clase, 0.0))
            vals = "".join(
                f"{(por_criterio[c][clase]-1)*100:>+9.3f}" for c in CRITERIOS
            )
            print(f"{clase} {nombre[:34]:<34} {peso:>7.4f}{vals} "
                  f"{cuenta_cats[clase]:>5}")

        # -- clases -> nivel del indice (Laspeyres con pesos del INDEC) --
        pesos_universo = {
            c: pond[c][1] for c in set(clase_de_categoria.values()) if c in pond
        }
        agregados = {
            c: agregar("PILOTO", "Cobertura del piloto", por_criterio[c], pesos_universo)
            for c in CRITERIOS
        }
        agregado = agregados["iguales"]

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

    # -- 5. serie encadenada ------------------------------------------------ #

    if len(variaciones) >= 1:
        titulo("5. SERIE ENCADENADA")
        print("(diagnostico interno: NO es una serie publicable)")
        print()
        for etiqueta, nivel in serie_encadenada(variaciones, base=100.0):
            print(f"  {etiqueta:<12} {nivel:>10.4f}")

    # -- 6. lo que hay que mirar -------------------------------------------- #

    titulo("6. ADVERTENCIAS")
    print("- Meses incompletos: julio y agosto no estan cerrados. Esto NO es el")
    print("  indice mensual y no se deriva encadenando estas semanas.")
    print("- PONDERADORES POR DEBAJO DE CLASE: el INDEC llega hasta 01.1.1 y no")
    print("  publica nada mas fino (verificado contra docs/ponderadores_ipc.xls).")
    print("  Para subir de categoria elemental a clase hace falta un peso que NO")
    print("  EXISTE en la fuente, asi que se reportan los tres criterios y su")
    print("  banda. El numero de arriba usa 'pesos iguales' por convencion.")
    print("  Ninguno de los tres es un ponderador de verdad: un ponderador es")
    print("  participacion en el GASTO (precio x cantidad) y SEPA no publica")
    print("  cantidades vendidas.")
    print("  PENDIENTE: buscar una fuente mas fina. La microdata de la ENGHo")
    print("  releva gasto a un nivel mas desagregado del que despues se publica;")
    print("  si tiene apertura por variedad, el supuesto desaparece.")
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
