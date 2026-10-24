"""Nuestro indice contra el del INDEC: por clase, por rubro y por producto.

Lee el `_detalle.csv` que deja `correr_indice.py --salida` (nada de texto
impreso) y las planillas del INDEC que publica captura en `staged/indec/`.

    python scripts/contra_indec.py --detalle salida/agosep_detalle.csv
    python scripts/contra_indec.py --detalle salida/x_detalle.csv --mes 2026-11
    python scripts/contra_indec.py --detalle ... --planillas datos_oficiales/indec/fecha=Y

Cada rubro se compara contra **lo comparable**: las mismas clases que medimos,
con la serie del INDEC de cada una (config/indices_ipc.yaml) y nuestros pesos
de los dos lados. La division entera del INDEC va al lado, para leer: Transporte
mide solo combustibles y su par es la serie de combustibles.
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))

from reporte.agregacion import division  # noqa: E402
from reporte.contra_indec import (  # noqa: E402
    FRESCOS,
    aperturas_del_indice,
    cargar_pares,
    errores_por_producto,
    leer_aperturas,
    leer_precios_promedio,
    rubro_contra_indec,
    variacion_indec,
)
from reporte.historial import guardar_corrida  # noqa: E402
from reporte.ponderadores import (  # noqa: E402
    PATH_INDICES,
    REGION_NACIONAL,
    clase_del_indice,
    ponderadores_de_clase,
)

ANCHO = 86
COLUMNAS_SALIDA = ("tipo", "codigo", "nombre", "nuestro_pct", "indec_pct", "diferencia", "nota")


def pct(x: float | None) -> str:
    return "     -" if x is None else f"{(x - 1) * 100:>+7.2f}"


def leer_detalle(path: Path, mes: str | None) -> tuple[str, str, dict, dict, dict]:
    """`(base, mes, clases, pesos de clase, articulos)` del mes pedido (o el ultimo)."""
    filas = list(csv.DictReader(path.open(encoding="utf-8")))
    if not filas:
        raise SystemExit(f"{path} esta vacio")
    mes = mes or max(f["mes"] for f in filas)
    del_mes = [f for f in filas if f["mes"] == mes]
    if not del_mes:
        raise SystemExit(f"{path} no tiene el mes {mes}")
    base = del_mes[0]["base"]
    clases = {f["codigo"]: float(f["indice"]) for f in del_mes if f["nivel"] == "clase"}
    pesos = {f["codigo"]: float(f["peso"]) for f in del_mes
             if f["nivel"] == "clase" and f["peso"]}
    articulos = {f["codigo"]: float(f["indice"]) for f in del_mes if f["nivel"] == "articulo"}
    return base, mes, clases, pesos, articulos


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--detalle", required=True, help="el _detalle.csv de correr_indice.py")
    p.add_argument("--mes", help="mes a comparar (AAAA-MM); default: el ultimo del detalle")
    p.add_argument("--planillas", help="carpeta con las planillas del INDEC; default: el bucket")
    args = p.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

    detalle = Path(args.detalle)
    base, mes, nuestro, pesos, articulos = leer_detalle(detalle, args.mes)

    if args.planillas:
        planillas = {f.name: f for f in Path(args.planillas).glob("*.xls")}
    else:
        from reporte.lectura import LectorBucket
        planillas = LectorBucket().bajar_planillas_indec()
    aperturas = leer_aperturas(planillas["sh_ipc_aperturas.xls"])
    precios = leer_precios_promedio(planillas["sh_ipc_precios_promedio.xls"])

    if mes not in aperturas.get("GBA", {}).get("Nivel general", {}):
        print(f"El INDEC todavia no publico {mes}: la planilla llega hasta "
              f"{max(aperturas['GBA']['Nivel general'])}.")
        return 2

    ap_clase, ap_division = aperturas_del_indice()
    cfg = yaml.safe_load(PATH_INDICES.read_text(encoding="utf-8"))["clases"]
    crudos = ponderadores_de_clase(REGION_NACIONAL)
    indec = {c: variacion_indec(aperturas, ap_clase[c], base, mes) for c in nuestro}
    salida: list[dict] = []

    def fila(tipo, codigo, nombre, n, i, nota=""):
        salida.append({"tipo": tipo, "codigo": codigo, "nombre": nombre,
                       "nuestro_pct": None if n is None else (n - 1) * 100,
                       "indec_pct": None if i is None else (i - 1) * 100,
                       "diferencia": None if n is None or i is None else (n - i) * 100,
                       "nota": nota})

    print("=" * ANCHO)
    print(f"NUESTRO INDICE CONTRA EL INDEC   {base} -> {mes}   (nacional, formula 15)")
    print("=" * ANCHO)

    # -- por clase --------------------------------------------------------- #
    print(f"\n{'clase':<8} {'nombre':<34} {'peso':>7} {'nuestro':>8} {'INDEC':>8} {'dif':>7}")
    print("-" * ANCHO)
    for c in sorted(nuestro):
        sup = cfg.get(c, {}).get("usa_nivel_superior")
        nota = f"serie del INDEC: {sup}" if sup else ""
        print(f"{c:<8} {crudos.get(c, (c,))[0][:34]:<34} {pesos.get(c, 0):>7.4f} "
              f"{pct(nuestro[c])} {pct(indec[c])} {(nuestro[c]-indec[c])*100:>+7.2f}"
              f"{'   (' + nota + ')' if nota else ''}")
        fila("clase", c, crudos.get(c, (c,))[0], nuestro[c], indec[c], nota)

    # -- agregados de alimentos ------------------------------------------- #
    alimentos = [c for c in nuestro if division(c) == "01"]
    if alimentos:
        print("\nALIMENTOS, con nuestros pesos de clase de los dos lados")
        for nombre, sub in [("division alimentos", alimentos),
                            ("sin frescos", [c for c in alimentos if c not in FRESCOS])]:
            n, i = rubro_contra_indec({c: nuestro[c] for c in sub}, indec, pesos)
            print(f"  {nombre:<24} nuestro {pct(n)}   INDEC {pct(i)}   dif {(n-i)*100:+.2f}")
            fila("agregado", nombre, nombre, n, i)
        publicada = variacion_indec(aperturas, ap_division["01"], base, mes)
        print(f"  {'(publicada por el INDEC, con sus pesos)':<44} {pct(publicada)}")

    # -- por rubro ------------------------------------------------------------ #
    print(f"\n{'rubro':<38} {'nuestro':>8} {'INDEC comp.':>11} {'dif':>7} "
          f"{'div. entera':>11} {'mide':>6}")
    print("-" * ANCHO)
    por_div: dict[str, list[str]] = defaultdict(list)
    for c in nuestro:
        por_div[division(c)].append(c)
    for d in sorted(por_div):
        cs = por_div[d]
        n, i = rubro_contra_indec({c: nuestro[c] for c in cs}, indec, pesos)
        entera = variacion_indec(aperturas, ap_division[d], base, mes) if d in ap_division else None
        mide = sum(crudos[c][1] for c in cs) / crudos[d][1] if d in crudos else None
        print(f"{d + ' ' + crudos.get(d, (d,))[0]:<38.38} {pct(n)} {pct(i):>11} "
              f"{(n-i)*100:>+7.2f} {pct(entera):>11} {mide*100 if mide else 0:>5.0f}%")
        fila("rubro", d, crudos.get(d, (d,))[0], n, i,
             f"division entera {(entera-1)*100:+.2f}%" if entera else "")
    print("  'INDEC comp.' = las mismas clases que medimos; 'div. entera' = la division")
    print("  del INDEC tal cual, que incluye lo que no medimos.")

    # -- por producto -------------------------------------------------------- #
    pares = cargar_pares()
    indec_prod = {pr: precios[pr][mes] / precios[pr][base] for pr in pares.values()
                  if pr in precios and base in precios[pr] and mes in precios[pr]}
    print(f"\nPOR PRODUCTO (precio promedio del INDEC en GBA), puntos de diferencia")
    for nombre, cond in [("envasados", lambda a: clase_del_indice(a) not in FRESCOS),
                         ("frescos", lambda a: clase_del_indice(a) in FRESCOS)]:
        sub = {a: pr for a, pr in pares.items() if cond(a)}
        filas, medio, absoluto = errores_por_producto(articulos, indec_prod, sub)
        if not filas:
            continue
        print(f"  {nombre:<10} {len(filas):>3} productos   error medio {medio:+.2f}   "
              f"error absoluto medio {absoluto:.2f}")
        for f in sorted(filas, key=lambda f: -abs(f["error"]))[:5]:
            print(f"      {f['articulo']} {f['producto'][:30]:<30} nuestro {f['nuestro_pct']:+7.2f}"
                  f"  INDEC {f['indec_pct']:+7.2f}  dif {f['error']:+7.2f}")
        for f in filas:
            salida.append({"tipo": "producto", "codigo": f["articulo"], "nombre": f["producto"],
                           "nuestro_pct": f["nuestro_pct"], "indec_pct": f["indec_pct"],
                           "diferencia": f["error"], "nota": nombre})

    destino = detalle.with_name(detalle.stem.replace("_detalle", "") + "_contra_indec.csv")
    with destino.open("w", encoding="utf-8", newline="") as fh:
        escritor = csv.DictWriter(fh, fieldnames=COLUMNAS_SALIDA)
        escritor.writeheader()
        escritor.writerows(salida)
    print(f"\ncomparacion guardada en {destino}")

    # Se suma a la historia que lee el dashboard, al lado del detalle; nunca pisa.
    corrida = guardar_corrida(
        "contra_indec", datetime.now(),
        {"contra_indec": [{"base": base, "mes": mes, **f} for f in salida]},
        raiz=detalle.parent / "historial",
    )
    print(f"comparacion guardada en la historia: {corrida}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
