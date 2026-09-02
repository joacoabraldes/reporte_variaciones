"""¿El filtro estadistico de outliers sirve, o solo trabaja el tope absoluto?

**La pregunta esta abierta desde el primer dia.** En ventana semanal el MAD no
hace nada: el 84% de los quotes tiene precio identico entre semanas, asi que la
mediana del log-ratio es 0, el MAD tambien, y hasta el rango intercuartil da 0.
La deteccion cae hasta el ultimo fallback y solo queda el tope absoluto.

Eso importa porque el tope es un numero elegido a mano. Si es lo unico que separa
un error de tipeo de un aumento real, conviene saberlo antes de octubre y no
despues.

En un mes se mueven muchos mas precios, asi que el MAD **deberia** tener con que
trabajar. Este script lo mide en vez de suponerlo, sobre la misma ventana que se
le pida:

    python scripts/medir_outliers.py --ventana semanal
    python scripts/medir_outliers.py --ventana mensual

Lo que NO hace es tocar los parametros segun el resultado. Medir primero, decidir
despues: mover el umbral hasta que el numero quede lindo es exactamente lo que no
hay que hacer.
"""

from __future__ import annotations

import argparse
import logging
import statistics as st
import sys
from datetime import date
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))

from reporte.elemental import (  # noqa: E402
    MOTIVO_OUTLIER_MAD,
    MOTIVO_OUTLIER_TOPE,
    detectar_outliers,
    emparejar,
    mad,
    rango_intercuartil,
)
from reporte.lectura import LectorBucket  # noqa: E402
from reporte.ponderadores import articulo_de_categoria  # noqa: E402
from reporte.periodo import (  # noqa: E402
    MENSUAL,
    SEMANAL,
    ParametrosVentana,
    meses_completos,
    quotes_del_periodo,
    semanas_iso_completas,
)

ANCHO = 92


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--ventana", default=SEMANAL, choices=[SEMANAL, MENSUAL])
    p.add_argument("--distancia", type=int, default=1,
                   help="compara periodos separados por N. Con --ventana semanal "
                        "y --distancia 4 el horizonte es ~mensual, que sirve de "
                        "proxy mientras no haya dos meses cerrados (default: 1)")
    p.add_argument("--desde", default="2026-07-27")
    p.add_argument("--hasta", default=date.today().isoformat())
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

    lec = LectorBucket()
    prods = lec.productos_clasificados()
    ex = set(lec.comercios_excluidos())
    art_de_cat = articulo_de_categoria()
    clasif = {
        r[0]: r[1]
        for r in lec.clasificacion().project("id_producto, categoria").fetchall()
    }
    articulo_de = {p_: art_de_cat[c] for p_, c in clasif.items() if c in art_de_cat}

    inv = lec.inventario(
        date.fromisoformat(args.desde), date.fromisoformat(args.hasta),
        comercios_excluidos=ex,
    )
    periodos = (
        meses_completos(inv.dias_presentes) if args.ventana == MENSUAL
        else semanas_iso_completas(inv.dias_presentes)
    )
    if len(periodos) < args.distancia + 1:
        print(f"hacen falta {args.distancia + 1} periodos completos; "
              f"hay {len(periodos)}")
        return 1

    pa = ParametrosVentana.desde_yaml(args.ventana)
    obs = lec.observaciones(
        min(inv.dias_presentes), max(inv.dias_presentes),
        productos=prods, inventario=inv,
    )
    quotes = {p_.etiqueta: quotes_del_periodo(obs, p_, pa) for p_ in periodos}

    print("=" * ANCHO)
    print(f"DETECCION DE OUTLIERS — ventana {args.ventana}")
    print("=" * ANCHO)
    print(f"tope absoluto [{1/pa.tope_ratio:.3f} , {pa.tope_ratio:.3f}]   "
          f"umbral MAD {pa.umbral_mad}   minimo para MAD {pa.minimo_quotes_mad}")
    if args.distancia > 1:
        print(f"comparando periodos separados por {args.distancia}: el horizonte "
              f"es de {args.distancia} {args.ventana}s, no de uno.")
    print()

    for base, actual in zip(periodos, periodos[args.distancia:]):
        a, b = quotes[base.etiqueta], quotes[actual.etiqueta]
        grupos_a, grupos_b = {}, {}
        for pr, g in ((a.precios, grupos_a), (b.precios, grupos_b)):
            for k, v in pr.items():
                art = articulo_de.get(k[2])
                if art:
                    g.setdefault(art, {})[k] = v

        print("-" * ANCHO)
        print(f"{base.etiqueta} -> {actual.etiqueta}")
        print("-" * ANCHO)
        print(f"{'articulo':<10} {'ratios':>9} {'sin cambio':>11} {'MAD':>9} "
              f"{'IQR/2':>9} {'umbral':>19} {'x tope':>7} {'x MAD':>7}")

        tot_ratios = tot_quietos = tot_tope = tot_mad = 0
        umbrales: dict[str, int] = {}
        for art in sorted(set(grupos_a) | set(grupos_b)):
            rel, _ = emparejar(grupos_a.get(art, {}), grupos_b.get(art, {}))
            if not rel:
                continue
            logs = [r.log_ratio for r in rel]
            quietos = sum(1 for r_ in rel if r_.ratio == 1.0)
            limpios, desc, umbral = detectar_outliers(
                rel, pa.umbral_mad, pa.tope_ratio, pa.minimo_quotes_mad
            )
            n_tope = sum(1 for d in desc if d.motivo == MOTIVO_OUTLIER_TOPE)
            n_mad = sum(1 for d in desc if d.motivo == MOTIVO_OUTLIER_MAD)
            umbrales[umbral] = umbrales.get(umbral, 0) + 1

            print(f"{art:<10} {len(rel):>9,} {100*quietos/len(rel):>10.1f}% "
                  f"{mad(logs):>9.5f} {rango_intercuartil(logs)/2:>9.5f} "
                  f"{umbral:>19} {n_tope:>7,} {n_mad:>7,}")
            tot_ratios += len(rel)
            tot_quietos += quietos
            tot_tope += n_tope
            tot_mad += n_mad

        print("-" * ANCHO)
        print(f"{'TOTAL':<10} {tot_ratios:>9,} {100*tot_quietos/tot_ratios:>10.1f}% "
              f"{'':>9} {'':>9} {'':>19} {tot_tope:>7,} {tot_mad:>7,}")
        print()
        print("  umbral efectivo por articulo: "
              + ", ".join(f"{k}={v}" for k, v in sorted(umbrales.items())))
        activos = umbrales.get("mad", 0) + umbrales.get("iqr", 0)
        print(f"  articulos donde el criterio estadistico llego a aplicarse: "
              f"{activos} de {sum(umbrales.values())}")
        if activos == 0:
            print("  -> el filtro estadistico NO discrimina: solo trabaja el tope.")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
