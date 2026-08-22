"""Reconciliacion: nuestros quotes contra los del repo de captura.

`relevamiento_precios` ya colapsa las observaciones diarias a quotes mensuales y
los deja en `staged/quotes_mensuales/`. Este repo hace el mismo colapso por su
cuenta, con `periodo.py`, porque necesita ventanas parametrizables (semanas) que
aquella tabla no tiene.

Dos implementaciones independientes calculando lo mismo. **Tienen que dar
identico**: es la misma mediana sobre los mismos dias. Si no dan, una de las dos
esta mal, y el modo de falla es silencioso — el indice sigue devolviendo un
numero plausible.

Es la unica validacion del proyecto que no depende de datos sinteticos: los
tests comprueban casos calculados a mano, esto comprueba el camino real contra
un tercero.

Que se compara, y por que asi:

- **Solo los productos clasificados.** `quotes_mensuales` tiene todos los
  productos del supermercado; al indice solo entran los que tienen categoria.
  Fuera de esos la comparacion no dice nada util.
- **Sin excluir comercios y sin minimo de dias.** Los filtros de analisis son de
  este repo; alla la tabla los guarda todos a proposito. Para comparar hay que
  ponerse en las mismas condiciones.

    python scripts/reconciliar_mensual.py --anio 2026 --mes 7
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))

from reporte.lectura import LectorBucket  # noqa: E402
from reporte.periodo import (  # noqa: E402
    MENSUAL,
    ParametrosVentana,
    Periodo,
    quotes_del_periodo,
)

ANCHO = 78

# Sin minimo de dias y sin tope: la comparacion tiene que correr sobre el mismo
# universo que guarda el repo de captura, no sobre el filtrado del indice.
PARAMS_CRUDOS = ParametrosVentana(
    tipo=MENSUAL, minimo_dias_quote=1, tope_ratio=2.0,
    umbral_mad=3.5, minimo_quotes_mad=15,
)

# Diferencia relativa por encima de la cual dos precios no son "el mismo numero".
# No se compara con == porque son DOUBLE y pasan por dos motores distintos.
TOLERANCIA = 1e-9

SQL_CONTEO = """
SELECT
    count(*) FILTER (WHERE n.id_producto IS NULL)               AS solo_captura,
    count(*) FILTER (WHERE c.id_producto IS NULL)               AS solo_nuestro,
    count(*) FILTER (WHERE c.id_producto IS NOT NULL
                       AND n.id_producto IS NOT NULL)           AS comunes,
    count(*) FILTER (WHERE {distinto_precio})                   AS precio_distinto,
    count(*) FILTER (WHERE c.n_dias IS DISTINCT FROM n.n_dias)  AS dias_distinto
FROM captura c
FULL OUTER JOIN nuestro n USING (id_comercio, id_sucursal, id_producto)
"""

# Se compara con tolerancia relativa: son DOUBLE calculados por dos motores.
DISTINTO_PRECIO = (
    "c.id_producto IS NOT NULL AND n.id_producto IS NOT NULL "
    f"AND abs(c.precio - n.precio) > {TOLERANCIA} * greatest(abs(c.precio), 1)"
)


def titulo(texto: str) -> None:
    print()
    print("=" * ANCHO)
    print(texto)
    print("=" * ANCHO)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--anio", type=int, default=2026)
    p.add_argument("--mes", type=int, default=7)
    p.add_argument("--columna", default="precio_lista",
                   choices=["precio_lista", "precio_efectivo"])
    p.add_argument("-v", "--verboso", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verboso else logging.WARNING,
        format="%(levelname)s %(message)s",
    )
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

    lector = LectorBucket()
    con = lector._con
    columna_alla = f"{args.columna}_mediana"

    titulo(f"RECONCILIACION {args.anio}-{args.mes:02d}  ({args.columna})")

    # -- 1. lo que calculo el repo de captura ------------------------------- #

    productos = lector.productos_clasificados()
    con.register("qm", lector.quotes_mensuales(args.anio, args.mes))

    # El filtro por producto va como tabla y no como IN (...) de mil literales:
    # es mas rapido y no arma un SQL de decenas de KB.
    con.execute("CREATE OR REPLACE TEMP TABLE productos_idx (p VARCHAR)")
    con.executemany(
        "INSERT INTO productos_idx VALUES (?)", [(x,) for x in productos]
    )
    con.execute(
        f"""CREATE OR REPLACE TEMP TABLE captura AS
            SELECT id_comercio, id_sucursal, id_producto,
                   {columna_alla} AS precio, n_dias
            FROM qm
            WHERE id_producto IN (SELECT p FROM productos_idx)
              AND {columna_alla} IS NOT NULL
              AND {columna_alla} > 0"""
    )
    rango = con.execute(
        "SELECT min(primera_fecha), max(ultima_fecha) FROM qm"
    ).fetchone()
    n_captura = con.execute("SELECT count(*) FROM captura").fetchone()[0]

    print(f"dias que cubre la tabla    {rango[0]} .. {rango[1]}")
    print(f"quotes del repo de captura {n_captura:>14,}")

    # -- 2. lo mismo, calculado aca ----------------------------------------- #

    periodo = Periodo(
        inicio=rango[0], fin=rango[1],
        etiqueta=f"{args.anio}-{args.mes:02d}", tipo=MENSUAL,
    )
    obs = lector.observaciones(periodo.inicio, periodo.fin, productos=productos)
    nuestro = quotes_del_periodo(obs, periodo, PARAMS_CRUDOS, args.columna)
    print(f"quotes calculados aca      {nuestro.n_quotes:>14,}")

    con.execute(
        "CREATE OR REPLACE TEMP TABLE nuestro "
        "(id_comercio VARCHAR, id_sucursal VARCHAR, id_producto VARCHAR, "
        " precio DOUBLE, n_dias BIGINT)"
    )
    con.executemany(
        "INSERT INTO nuestro VALUES (?,?,?,?,?)",
        [(c, s, p, v, nuestro.n_dias[(c, s, p)])
         for (c, s, p), v in nuestro.precios.items()],
    )

    # -- 3. comparar -------------------------------------------------------- #

    (solo_captura, solo_nuestro, comunes,
     precio_distinto, dias_distinto) = con.execute(
        SQL_CONTEO.format(distinto_precio=DISTINTO_PRECIO)
    ).fetchone()

    titulo("RESULTADO")
    print(f"{'quotes en las dos tablas':<34}{comunes:>14,}")
    print(f"{'solo en el repo de captura':<34}{solo_captura:>14,}")
    print(f"{'solo aca':<34}{solo_nuestro:>14,}")
    print()
    print(f"{'precios que NO coinciden':<34}{precio_distinto:>14,}")
    print(f"{'n_dias que NO coinciden':<34}{dias_distinto:>14,}")

    if not (solo_captura or solo_nuestro or precio_distinto or dias_distinto):
        print()
        print("COINCIDEN EXACTAMENTE.")
        print("Las dos implementaciones dan el mismo resultado sobre los mismos dias.")
        return 0

    # -- 4. si no coinciden, mostrar de que se trata ------------------------ #

    titulo("DISCREPANCIAS (primeras 10 de cada tipo)")
    for etiqueta, cond in (
        ("solo en captura", "n.id_producto IS NULL"),
        ("solo aca", "c.id_producto IS NULL"),
        ("precio distinto", DISTINTO_PRECIO),
        ("n_dias distinto", "c.n_dias IS DISTINCT FROM n.n_dias"),
    ):
        filas = con.execute(
            f"""SELECT coalesce(c.id_comercio, n.id_comercio),
                       coalesce(c.id_sucursal, n.id_sucursal),
                       coalesce(c.id_producto, n.id_producto),
                       c.precio, n.precio, c.n_dias, n.n_dias
                FROM captura c
                FULL OUTER JOIN nuestro n USING (id_comercio, id_sucursal, id_producto)
                WHERE {cond} LIMIT 10"""
        ).fetchall()
        if not filas:
            continue
        print()
        print(f"-- {etiqueta} --")
        print(f"  {'comercio':>8} {'suc':>6} {'producto':>15} "
              f"{'captura':>12} {'aca':>12} {'d_cap':>6} {'d_aca':>6}")
        for f in filas:
            pc = "-" if f[3] is None else f"{f[3]:,.2f}"
            pn = "-" if f[4] is None else f"{f[4]:,.2f}"
            print(f"  {f[0]:>8} {f[1]:>6} {f[2]:>15} {pc:>12} {pn:>12} "
                  f"{str(f[5] if f[5] is not None else '-'):>6} "
                  f"{str(f[6] if f[6] is not None else '-'):>6}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
