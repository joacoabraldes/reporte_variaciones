"""¿El precio que la cadena publica en su web es el que le declara a SEPA?

    python scripts/sondeo_online.py --cadena dia --dia 2026-10-09
    python scripts/sondeo_online.py --cadena vea --dia 2026-10-09 --refrescar

Es una **sonda**, no parte del cálculo del índice: sirve para decidir si vale la
pena scrapear antes de construir nada. Nada de lo que sale de acá entra al
número.

Lo que contesta, y por qué importa cada cosa:

1. **Cobertura de EAN.** Cuántos de los productos que la cadena publica online
   aparecen en lo que esa misma cadena le declara a SEPA. Si es casi todo,
   scrapear no agrega productos y sólo agrega frecuencia.

2. **Si el precio online cae dentro del rango declarado.** La cadena tiene
   cientos de sucursales con precios distintos, así que la pregunta bien hecha
   no es "¿coincide?" sino "¿está dentro del rango de sus sucursales?". Un
   precio sistemáticamente afuera de TODAS es señal; uno adentro no dice nada.

**El sesgo que tiene y no se puede sacar:** la web se consulta hoy y SEPA tiene
hasta unos días antes, así que parte de la diferencia es inflación de esos días.
Separarlas requiere scrapear el mismo día que SEPA publica, que es justamente lo
que esta sonda sirve para decidir si vale la pena montar.

Las cuentas están en `src/reporte/online.py`, que es donde se testean. Acá sólo
está ir a buscar los datos y mostrarlos.

Es amable con el sitio: un pedido por término, con pausa, tope de productos, y el
catálogo queda en un snapshot para no volver a pedirlo.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))

from reporte.online import (  # noqa: E402
    ProductoOnline,
    comparar,
    productos_de_respuesta,
    resumir,
)

PATH_CADENAS = RAIZ / "config" / "cadenas.yaml"
CACHE = RAIZ / ".cache_sondeo"
BUCKET = "outlier-archivos-precios"

AGENTE = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)

TERMINOS_DEFAULT = (
    "leche", "arroz", "aceite", "yerba", "azucar", "fideos", "harina",
    "yogur", "queso", "manteca", "mermelada", "galletitas",
)

# Lo que VTEX devuelve de una vez.
POR_PEDIDO = 24


def cargar_cadenas() -> dict:
    return yaml.safe_load(PATH_CADENAS.read_text(encoding="utf-8"))


def buscar(host: str, termino: str, espera: float) -> list[ProductoOnline]:
    url = (
        f"https://{host}/api/catalog_system/pub/products/search/"
        f"{urllib.parse.quote(termino)}?_from=0&_to={POR_PEDIDO - 1}"
    )
    pedido = urllib.request.Request(url, headers={"User-Agent": AGENTE})
    try:
        with urllib.request.urlopen(pedido, timeout=30) as r:
            datos = json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as exc:
        print(f"  {termino:<14} ERROR {exc}")
        return []
    finally:
        time.sleep(espera)
    encontrados = productos_de_respuesta(datos)
    print(f"  {termino:<14} {len(datos):>3} productos -> "
          f"{len(encontrados):>3} con EAN y precio")
    return encontrados


def catalogo_online(
    clave: str, host: str, terminos: list[str], espera: float, refrescar: bool
) -> list[ProductoOnline]:
    """El catálogo, de un snapshot si existe.

    Guardarlo tiene dos razones: no volver a pedirle al sitio lo mismo, y poder
    reproducir después los números de un informe.
    """
    CACHE.mkdir(exist_ok=True)
    snapshot = CACHE / f"online_{clave}_{date.today().isoformat()}.json"
    if snapshot.exists() and not refrescar:
        crudo = json.loads(snapshot.read_text(encoding="utf-8"))
        online = [ProductoOnline(**d) for d in crudo]
        print(f"   del snapshot {snapshot.name}: {len(online)} items "
              f"(--refrescar para volver a pedirlo)")
        return online

    online: list[ProductoOnline] = []
    for t in terminos:
        online.extend(buscar(host, t, espera))
    snapshot.write_text(
        json.dumps([dataclasses.asdict(p) for p in online], ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"   guardado en {snapshot.name}")
    return online


def precios_declarados(
    id_comercio: str, dia: date, eans: set[str]
) -> dict[str, list[float]]:
    """Lo que ese comercio le declaró a SEPA ese día, por EAN y sucursal."""
    import duckdb
    from google.cloud import storage

    CACHE.mkdir(exist_ok=True)
    cli = storage.Client()
    bucket = cli.bucket(BUCKET)
    prefijo = (
        f"staged/observaciones/anio={dia.year}/mes={dia.month:02d}/"
        f"dia={dia.day:02d}/"
    )
    blobs = [
        b for b in cli.list_blobs(bucket, prefix=prefijo)
        if b.name.endswith(".parquet") and f"comercio-sepa-{id_comercio}_" in b.name
    ]
    if not blobs:
        raise SystemExit(
            f"No hay observaciones del comercio {id_comercio} el {dia} en el bucket"
        )
    locales = []
    for b in blobs:
        local = CACHE / b.name.split("/")[-1]
        if not local.exists():
            print(f"   bajando {local.name} ({b.size / 1e6:.1f} MB)")
            bucket.blob(b.name).download_to_filename(str(local))
        locales.append(local.as_posix())

    con = duckdb.connect()
    lista = ", ".join(f"'{e}'" for e in sorted(eans))
    filas = con.sql(f"""
        SELECT id_producto, precio_lista
        FROM read_parquet({locales!r})
        WHERE id_producto IN ({lista})
          AND precio_lista IS NOT NULL AND precio_lista > 0
    """).fetchall()
    salida: dict[str, list[float]] = {}
    for ean, precio in filas:
        salida.setdefault(str(ean), []).append(float(precio))
    return salida


def main() -> int:
    cfg = cargar_cadenas()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cadena", choices=sorted(cfg["cadenas"]), default="dia")
    parser.add_argument("--dia", required=True,
                        help="dia de SEPA a comparar, YYYY-MM-DD")
    parser.add_argument("--terminos", default=",".join(TERMINOS_DEFAULT))
    parser.add_argument("--espera", type=float, default=1.0,
                        help="segundos entre pedidos al sitio")
    parser.add_argument("--refrescar", action="store_true",
                        help="vuelve a pedirle el catalogo al sitio")
    args = parser.parse_args()

    cadena = cfg["cadenas"][args.cadena]
    if not cadena.get("id_comercio"):
        raise SystemExit(
            f"{cadena['nombre']}: falta identificar su id de comercio en SEPA. "
            "Sale de leer comercio_nombre en las observaciones de un dia."
        )
    dia = date.fromisoformat(args.dia)
    terminos = [t.strip() for t in args.terminos.split(",") if t.strip()]

    print()
    print(f"SONDEO  {cadena['nombre']}  ({cadena['host']})")
    print(f"        web de hoy contra lo declarado a SEPA el {dia}")
    print("=" * 78)

    print("\n1. catalogo online")
    online = catalogo_online(
        args.cadena, cadena["host"], terminos, args.espera, args.refrescar
    )
    unicos = {p.ean: p for p in online}
    print(f"\n   {len(online)} items, {len(unicos)} EAN distintos")
    trampa = [p for p in unicos.values() if p.list_price_sospechoso]
    if trampa:
        print(f"   {len(trampa)} items con ListPrice en otra unidad: "
              f"ej. precio {trampa[0].precio:,.0f} vs lista "
              f"{trampa[0].list_price_crudo:,.0f}")

    print("\n2. lo declarado a SEPA")
    declarado = precios_declarados(cadena["id_comercio"], dia, set(unicos))

    comparaciones, sin_match = comparar(list(unicos.values()), declarado)
    r = resumir(comparaciones, sin_match)

    print("\n3. resultado")
    print("=" * 78)
    print(f"   EAN online que estan en SEPA      {r.en_sepa:>5} de {r.ean_online}"
          f"  ({r.pct(r.en_sepa):.1f}%)")
    print(f"   EAN online que SEPA no tiene      {r.sin_match:>5} de {r.ean_online}"
          f"  ({r.pct(r.sin_match):.1f}%)")
    if not comparaciones:
        return 0

    n = r.en_sepa
    print()
    print(f"   precio online dentro del rango    {r.dentro_del_rango:>5}"
          f"  ({r.pct(r.dentro_del_rango, n):.1f}%)")
    print(f"   arriba de TODAS las sucursales    {r.arriba_de_todas:>5}"
          f"  ({r.pct(r.arriba_de_todas, n):.1f}%)")
    print(f"   abajo de TODAS las sucursales     {r.abajo_de_todas:>5}"
          f"  ({r.pct(r.abajo_de_todas, n):.1f}%)")
    print()
    print(f"   igual a la mediana de sus sucursales  {r.iguales_a_la_mediana:>5}"
          f"  ({r.pct(r.iguales_a_la_mediana, n):.1f}%)")
    print()
    print("   diferencia contra la mediana de sus sucursales:")
    print(f"     mediana  {statistics.median(r.difs_ordenadas):>+8.2f}%")
    print(f"     p10      {r.percentil(0.10):>+8.2f}%")
    print(f"     p90      {r.percentil(0.90):>+8.2f}%")

    print()
    print("   los diez con mas diferencia:")
    for c in sorted(comparaciones, key=lambda c: -abs(c.dif_vs_mediana_pct))[:10]:
        print(f"     {c.dif_vs_mediana_pct:>+8.1f}%  online {c.precio_online:>9,.0f}  "
              f"sepa {c.minimo:>8,.0f}..{c.maximo:>9,.0f}  "
              f"({c.sucursales:>3} suc)  {c.nombre[:30]}")
    print()
    print(f"   OJO: la web es de hoy y SEPA es del {dia}: parte de la diferencia")
    print("        es inflacion de esos dias, no discrepancia.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
