"""Relevamiento propio de los rubros que SEPA no cubre.

    python scripts/relevar_online.py                      # los tres sitios
    python scripts/relevar_online.py --sitio farmacity
    python scripts/relevar_online.py --variacion          # sin pedir nada, compara

Farmacity, Frávega y Easy: medicamentos, electrodomésticos y hogar. Juntos pesan
**8,2 % del IPC** y **ninguno está en SEPA**, que sólo cubre góndola de consumo
masivo. Acá no hay contra qué cruzar: es fuente primaria.

El trabajo es seguir **el mismo artículo** a lo largo del tiempo, que es lo que
hace el INDEC: *"el encuestador deberá buscar el mismo bien observando los
atributos que detalló la primera vez"*. La clave es el SKU de la tienda
(`item_id`), no el EAN, porque Frávega no trae EAN.

**El cálculo es el mismo que para SEPA.** Las observaciones se convierten a
`{quote: precio}` y de ahí entran a `elemental.indice_elemental`: la misma
muestra emparejada, el mismo filtro de valores raros y el mismo Jevons, ya
testeados y marcados como ruta crítica. No hay un segundo cálculo que pueda
divergir del primero.

**La canasta está en `config/cadenas.yaml` y no se cambia.** Sacar un término
rompe la muestra emparejada: los artículos que salen no tienen con qué
compararse. Agregar está bien; sacar, no.

---

**Donde esto no encaja, y conviene decirlo:** por la arquitectura del proyecto
este repo **sólo lee** del bucket, y la captura vive en `relevamiento_precios`
justamente porque un bug en la captura cuesta datos perdidos para siempre.
Relevar precios es capturar. Hoy el relevamiento queda en archivos locales
—suficiente para medir si esto sirve— pero si se decide que sigue, tiene que
mudarse al repo de captura o a uno propio, con su scheduler y su TTL. Dejarlo
acá indefinidamente mezcla las dos cosas que el proyecto separó a propósito.
"""

from __future__ import annotations

import argparse
import json
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

from reporte.elemental import indice_elemental  # noqa: E402
from reporte.online import (  # noqa: E402
    ObservacionOnline,
    observaciones_de_respuesta,
    quotes_de_observaciones,
)

PATH_CADENAS = RAIZ / "config" / "cadenas.yaml"
DATOS = RAIZ / "datos_online"
POR_PEDIDO = 24

AGENTE = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)


def sitios() -> dict:
    cfg = yaml.safe_load(PATH_CADENAS.read_text(encoding="utf-8"))
    return cfg["fuera_de_gondola"]


def pedir(host: str, termino: str, espera: float) -> list:
    url = (
        f"https://{host}/api/catalog_system/pub/products/search/"
        f"{urllib.parse.quote(termino)}?_from=0&_to={POR_PEDIDO - 1}"
    )
    pedido = urllib.request.Request(url, headers={"User-Agent": AGENTE})
    try:
        with urllib.request.urlopen(pedido, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as exc:
        print(f"    {termino:<22} ERROR {exc}")
        return []
    finally:
        time.sleep(espera)


def archivo_del_dia(clave: str, dia: date) -> Path:
    return DATOS / f"sitio={clave}" / f"fecha={dia.isoformat()}" / "datos.json"


def relevar(clave: str, cfg: dict, dia: date, espera: float) -> list[ObservacionOnline]:
    print(f"\n  {cfg['nombre']}  ({cfg['host']})")
    todas: list[ObservacionOnline] = []
    for termino in cfg["terminos"]:
        datos = pedir(cfg["host"], termino, espera)
        obs = observaciones_de_respuesta(clave, datos, dia.isoformat())
        print(f"    {termino:<22} {len(datos):>3} productos -> {len(obs):>3} con "
              f"SKU y precio")
        todas.extend(obs)

    unicas = {o.clave: o for o in todas}
    destino = archivo_del_dia(clave, dia)
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(
        json.dumps([vars_de(o) for o in unicas.values()], ensure_ascii=False),
        encoding="utf-8",
    )
    con_ean = sum(1 for o in unicas.values() if o.ean)
    print(f"    {'':<22} {len(todas)} observaciones, {len(unicas)} artículos "
          f"distintos, {con_ean} con EAN")
    print(f"    guardado en {destino.relative_to(RAIZ).as_posix()}")
    return list(unicas.values())


def vars_de(o: ObservacionOnline) -> dict:
    return {
        "sitio": o.sitio, "item_id": o.item_id, "nombre": o.nombre,
        "precio": o.precio, "fecha": o.fecha, "ean": o.ean, "marca": o.marca,
        "categoria": o.categoria, "precio_lista": o.precio_lista,
        "disponible": o.disponible,
    }


def leer(clave: str, dia: date) -> list[ObservacionOnline]:
    f = archivo_del_dia(clave, dia)
    if not f.exists():
        return []
    return [
        ObservacionOnline(**d)
        for d in json.loads(f.read_text(encoding="utf-8"))
    ]


def dias_relevados(clave: str) -> list[date]:
    carpeta = DATOS / f"sitio={clave}"
    if not carpeta.exists():
        return []
    return sorted(
        date.fromisoformat(p.name.split("=", 1)[1])
        for p in carpeta.iterdir()
        if p.is_dir() and p.name.startswith("fecha=")
        and (p / "datos.json").exists()
    )


def informar_variacion(clave: str, cfg: dict) -> None:
    dias = dias_relevados(clave)
    if len(dias) < 2:
        print(f"  {cfg['nombre']:<12} 1 relevamiento ({dias[0] if dias else '-'}): "
              f"la variación necesita dos")
        return

    base, actual = dias[-2], dias[-1]
    q_base = quotes_de_observaciones(leer(clave, base))
    q_actual = quotes_de_observaciones(leer(clave, actual))
    # El mismo calculo que para SEPA: muestra emparejada, filtro, Jevons.
    r = indice_elemental(clave, q_base, q_actual)
    var = r.variacion_pct
    print(f"  {cfg['nombre']:<12} {base} -> {actual}   "
          f"{len(q_base):>4} y {len(q_actual):>4} artículos, "
          f"{r.n_quotes:>4} emparejados   "
          f"{f'{var:+.3f}%' if var is not None else 'sin dato':>10}   "
          f"({r.n_descartados} descartados, umbral {r.umbral_usado})")


def main() -> int:
    cfg_sitios = sitios()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sitio", choices=sorted(cfg_sitios),
                        help="por defecto, los tres")
    parser.add_argument("--espera", type=float, default=1.0,
                        help="segundos entre pedidos a cada sitio")
    parser.add_argument("--variacion", action="store_true",
                        help="no releva: compara los dos ultimos relevamientos")
    args = parser.parse_args()

    claves = [args.sitio] if args.sitio else sorted(cfg_sitios)
    hoy = date.today()

    if not args.variacion:
        print()
        print("RELEVAMIENTO ONLINE  (rubros que SEPA no cubre)")
        print("=" * 78)
        for clave in claves:
            relevar(clave, cfg_sitios[clave], hoy, args.espera)

    print()
    print("VARIACION  (mismo calculo que el indice: emparejada + Jevons)")
    print("=" * 78)
    for clave in claves:
        informar_variacion(clave, cfg_sitios[clave])
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
