"""Variacion de las fuentes que no son SEPA, desde las fotos que publica captura.

    python scripts/variacion_online.py                  # ultimas dos fotos, por sitio
    python scripts/variacion_online.py --mensual        # precio del mes vs mes anterior
    python scripts/variacion_online.py --combustibles --antiguedad 6
    python scripts/variacion_online.py --origen ../relevamiento_precios   # sin bucket

**El calculo es el mismo que el del indice.** Las fotos se convierten a
`{quote: precio}` y de ahi entran a `elemental.indice_elemental`: la misma
muestra emparejada, el mismo filtro de valores raros y el mismo Jevons, ya
testeados y marcados como ruta critica. No hay un segundo calculo que pueda
divergir del primero.

**De donde lee.** Del bucket, que es la interfaz entre los dos repos:
`staged/relevamiento_online/` y `staged/combustibles/`, que publica
`precios.cli publicar-online` en el repo de captura. Baja solo lo que falta a
`.cache/etapa5/`. Con `--origen` lee del disco del repo de captura, para cuando
no hay red; antes era la unica forma y contradecia la regla del proyecto.

Aca no se importa nada del otro repo: se lee el JSON y se arman los quotes. El
contrato son los campos del archivo —`sitio`, `item_id`, `precio`—.

**Dos fotos o el mes.** Por defecto compara las ultimas dos fotos de cada sitio,
que sirve para ver que la captura anda pero mide el calendario de promociones
(Rapsodia +8% en un dia). El numero que vale es `--mensual`: precio del mes de
cada articulo como media de sus dias, formula 6 del INDEC, ver
`reporte.fuentes_online`.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import statistics
import sys
from datetime import date
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))

from reporte.elemental import indice_elemental  # noqa: E402
from reporte.fuentes_online import (  # noqa: E402
    dias_por_mes,
    precio_del_mes,
    quotes_de_foto,
)
from reporte.periodo import MENSUAL, ParametrosVentana  # noqa: E402

BUCKET = "outlier-archivos-precios"
CACHE = RAIZ / ".cache" / "etapa5"
PREFIJOS = (
    ("staged/relevamiento_online/", Path("datos_online")),
    ("staged/combustibles/", Path("datos_oficiales") / "combustibles"),
)


def bajar_del_bucket(cache: Path) -> Path:
    """Baja al cache las fotos que falten y devuelve la raiz.

    Deja el mismo arbol que el repo de captura (`datos_online/sitio=.../`,
    `datos_oficiales/combustibles/fecha=.../`), asi el resto del script no
    distingue de donde vinieron.
    """
    from google.cloud import storage

    cli = storage.Client()
    bucket = cli.bucket(BUCKET)
    bajadas = 0
    for prefijo, sub in PREFIJOS:
        for blob in cli.list_blobs(bucket, prefix=prefijo):
            local = cache / sub / blob.name[len(prefijo):]
            if local.exists() and local.stat().st_size == blob.size:
                continue
            local.parent.mkdir(parents=True, exist_ok=True)
            blob.download_to_filename(str(local))
            bajadas += 1
    print(f"  fotos bajadas del bucket: {bajadas} (el resto ya estaba en {cache.name}/)")
    return cache


def fechas(carpeta: Path, nombre_archivo: str) -> list[date]:
    if not carpeta.exists():
        return []
    return sorted(
        date.fromisoformat(p.name.split("=", 1)[1])
        for p in carpeta.iterdir()
        if p.is_dir() and p.name.startswith("fecha=")
        and (p / nombre_archivo).exists()
    )


def foto(carpeta: Path, dia: date) -> dict[tuple, float]:
    f = carpeta / f"fecha={dia.isoformat()}" / "datos.json"
    return quotes_de_foto(json.loads(f.read_text(encoding="utf-8")))


def sitios(raiz: Path) -> list[str]:
    if not raiz.exists():
        return []
    return sorted(p.name.split("=", 1)[1] for p in raiz.iterdir()
                  if p.is_dir() and p.name.startswith("sitio="))


def informar_online(raiz: Path) -> int:
    """Ultimas dos fotos de cada sitio. Sirve para ver que la captura anda."""
    if not sitios(raiz):
        print(f"  No hay fotos en {raiz}")
        return 1
    print(f"  {'sitio':<14} {'base':<11} {'actual':<11} {'empar.':>7} "
          f"{'variacion':>11}  desc.")
    print("  " + "-" * 74)
    con_dato = 0
    for sitio in sitios(raiz):
        carpeta = raiz / f"sitio={sitio}"
        dias = fechas(carpeta, "datos.json")
        if len(dias) < 2:
            print(f"  {sitio:<14} {str(dias[0]) if dias else '-':<11} "
                  f"{'':<11} {'':>7} {'necesita 2':>11}")
            continue
        base, actual = dias[-2], dias[-1]
        r = indice_elemental(sitio, foto(carpeta, base), foto(carpeta, actual))
        v = r.variacion_pct
        con_dato += v is not None
        print(f"  {sitio:<14} {base!s:<11} {actual!s:<11} {r.n_quotes:>7} "
              f"{f'{v:+.3f}%' if v is not None else 'sin dato':>11}  "
              f"{r.n_descartados}")
    print()
    print("  Ojo: dos fotos sueltas miden el calendario de promociones. El numero")
    print("  que vale es --mensual.")
    return 0 if con_dato else 1


def informar_mensual(raiz: Path, minimo_dias: int) -> int:
    """Precio del mes (media de los dias) contra el mes anterior, por sitio."""
    if not sitios(raiz):
        print(f"  No hay fotos en {raiz}")
        return 1
    print(f"  minimo de dias por articulo en el mes: {minimo_dias}")
    print()
    print(f"  {'sitio':<14} {'dias por mes':<26} {'empar.':>7} {'variacion':>11}")
    print("  " + "-" * 64)
    con_dato = 0
    for sitio in sitios(raiz):
        carpeta = raiz / f"sitio={sitio}"
        fotos = {d: foto(carpeta, d) for d in fechas(carpeta, "datos.json")}
        por_dias = dias_por_mes(fotos)
        meses = precio_del_mes(fotos, minimo_dias)
        con_precio = [m for m in sorted(meses) if meses[m]]
        resumen = " ".join(f"{m[5:]}:{n}" for m, n in por_dias.items())
        if len(con_precio) < 2:
            print(f"  {sitio:<14} {resumen:<26} {'':>7} {'faltan meses':>11}")
            continue
        base, actual = con_precio[-2], con_precio[-1]
        r = indice_elemental(sitio, meses[base], meses[actual])
        v = r.variacion_pct
        con_dato += v is not None
        print(f"  {sitio:<14} {resumen:<26} {r.n_quotes:>7} "
              f"{f'{v:+.3f}%' if v is not None else 'sin dato':>11}")
    if not con_dato:
        print()
        print(f"  Todavia no hay dos meses con articulos vistos {minimo_dias}+ dias.")
        print("  El relevamiento online arranco el 14/10/2026.")
    return 0 if con_dato else 1


def quotes_combustibles(
    carpeta: Path, dia: date, antiguedad: int | None
) -> dict[tuple, float]:
    """Lee una foto de surtidor y arma los quotes.

    La clave es `(idempresa, direccion, localidad, idproducto, idtipohorario)`:
    el horario entra porque diurno y nocturno son dos precios distintos del
    mismo surtidor. Si una clave viene repetida se promedia, que es la formula 6
    del INDEC y la misma decision que la operacion 1 del indice.

    `utf-8-sig` porque el archivo trae BOM: con `utf-8` la primera columna se
    llama `\\ufeffindice_tiempo` y el corte por antiguedad deja de filtrar.
    """
    f = carpeta / f"fecha={dia.isoformat()}" / "vigentes.csv"
    mes = dia.isoformat()[:7]
    tope = int(mes[:4]) * 12 + int(mes[5:7])
    acum: dict[tuple, list[float]] = {}
    for fila in csv.DictReader(io.StringIO(f.read_text(encoding="utf-8-sig"))):
        try:
            precio = float(fila["precio"])
        except (TypeError, ValueError, KeyError):
            continue
        if precio <= 0:
            continue
        if antiguedad is not None:
            desde = (fila.get("indice_tiempo") or "").split("-")
            if len(desde) >= 2:
                try:
                    if tope - (int(desde[0]) * 12 + int(desde[1])) > antiguedad:
                        continue
                except ValueError:
                    pass
        clave = (
            fila["idempresa"], fila["direccion"].strip().upper(),
            fila["localidad"].strip().upper(), fila["idproducto"],
            fila["idtipohorario"],
        )
        acum.setdefault(clave, []).append(precio)
    return {k: statistics.fmean(v) for k, v in acum.items()}


def informar_combustibles(raiz: Path, antiguedad: int | None) -> int:
    dias = fechas(raiz, "vigentes.csv")
    if len(dias) < 2:
        print(f"  {len(dias)} foto(s): la variacion necesita dos.")
        print("  El archivo de Energia es una foto del precio VIGENTE, no de un")
        print("  mes, asi que la serie se arma guardando una foto por mes.")
        print("  Si hace falta el numero ya, esta el CSV historico (778 MB).")
        return 1
    base, actual = dias[-2], dias[-1]
    qb = quotes_combustibles(raiz, base, antiguedad)
    qa = quotes_combustibles(raiz, actual, antiguedad)
    r = indice_elemental("07.2.2", qb, qa)
    v = r.variacion_pct
    print(f"  corte de antiguedad: "
          f"{antiguedad if antiguedad is not None else 'sin corte'}")
    print(f"  {base} -> {actual}   {len(qb)} y {len(qa)} quotes, "
          f"{r.n_quotes} emparejados")
    print(f"  variacion: {f'{v:+.4f}%' if v is not None else 'sin dato'}  "
          f"({r.n_descartados} descartados, umbral {r.umbral_usado})")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mensual", action="store_true",
                        help="precio del mes (media de los dias) vs el mes anterior")
    parser.add_argument("--minimo-dias", type=int, default=None,
                        help="dias minimos de un articulo en el mes; por defecto "
                             "el de SEPA (ventanas.mensual en parametros.yaml)")
    parser.add_argument("--combustibles", action="store_true",
                        help="el dataset de surtidor en vez de los catalogos")
    parser.add_argument("--antiguedad", type=int, default=None,
                        help="solo con --combustibles: descarta el quote cuyo "
                             "precio vigente tenga mas de N meses")
    parser.add_argument("--origen", type=Path, default=None,
                        help="lee del disco del repo de captura en vez del bucket")
    args = parser.parse_args()

    print()
    print("VARIACION  (mismo calculo que el indice: emparejada + Jevons)")
    print("=" * 78)
    if args.origen:
        raiz = args.origen
        print(f"  fotos de: {raiz} (disco)")
    else:
        print(f"  fotos de: gs://{BUCKET}/staged/")
        raiz = bajar_del_bucket(CACHE)
    print()

    if args.combustibles:
        salida = informar_combustibles(raiz / "datos_oficiales" / "combustibles",
                                       args.antiguedad)
    elif args.mensual:
        minimo = args.minimo_dias
        if minimo is None:
            minimo = ParametrosVentana.desde_yaml(MENSUAL).minimo_dias_quote
        salida = informar_mensual(raiz / "datos_online", minimo)
    else:
        salida = informar_online(raiz / "datos_online")
    print()
    return salida


if __name__ == "__main__":
    sys.exit(main())
