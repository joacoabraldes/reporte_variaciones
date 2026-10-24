"""La comparacion contra el INDEC: la vara con la que se decide todo lo demas.

Hasta el 22/10/2026 vivia en scripts sueltos de una carpeta temporal: se perdia
al cerrar la sesion, no tenia tests (ponderaba las regiones tambien por el peso
de la clase y dejo una conclusion falsa un dia entero), leia nuestro numero del
texto impreso del reporte y usaba planillas bajadas a mano.

Ahora:

- **Las planillas** las baja captura (`relevar-indec`) y estan en el bucket.
- **El lado del INDEC** se arma con `agregacion.nacional_desde_regiones`, la
  misma funcion que reproduce su nacional publicado (formula 15), y se lee por
  FECHA: cada region trae sus meses en su propia fila.
- **Nuestro lado** se lee del `_detalle.csv` de `correr_indice.py --salida`,
  no del texto impreso.
- **Contra lo comparable.** Un rubro se compara contra la serie del INDEC de
  las MISMAS clases que medimos, con nuestros pesos de los dos lados. Transporte
  mide solo combustibles: va contra la serie de combustibles del INDEC, no
  contra todo Transporte. La division entera se muestra al lado, para leer.
"""

from __future__ import annotations

import math
import re
import unicodedata
from pathlib import Path

import yaml

from .agregacion import laspeyres, nacional_desde_regiones
from .ponderadores import PATH_INDICES, RAIZ_REPO, pesos_regionales

PATH_PARES = RAIZ_REPO / "config" / "pares_indec.yaml"

# Carnes, pescados, frutas y verduras: su muestra sale de dos cadenas y el
# INDEC los releva sobre todo en negocios tradicionales. Se mira aparte.
FRESCOS = {"01.1.2", "01.1.3", "01.1.6", "01.1.7"}

HOJA_APERTURAS = "Índices aperturas"
HOJA_PRECIOS_GBA = "GBA"
_PREFIJO_REGION = "Región "
_MESES = {m: i for i, m in enumerate(
    ["ENERO", "FEBRERO", "MARZO", "ABRIL", "MAYO", "JUNIO", "JULIO", "AGOSTO",
     "SEPTIEMBRE", "OCTUBRE", "NOVIEMBRE", "DICIEMBRE"], start=1)}


def _texto(v) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return ""
    return str(v).strip()


def _numero(v) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return None if math.isnan(v) else float(v)


def _mes_de_fecha(v) -> str | None:
    return f"{v.year:04d}-{v.month:02d}" if hasattr(v, "year") and hasattr(v, "month") else None


# --------------------------------------------------------------------------- #
# Las planillas (la lectura del .xls es un borde; lo demas trabaja sobre tablas)
# --------------------------------------------------------------------------- #


def aperturas_de_tabla(df) -> dict[str, dict[str, dict[str, float]]]:
    """`region -> apertura -> {AAAA-MM: nivel}` de la hoja de indices por apertura.

    Cada bloque empieza con una fila "Region X" que trae las fechas de cada
    columna; las aperturas van debajo hasta la region siguiente.
    """
    salida: dict[str, dict[str, dict[str, float]]] = {}
    region: str | None = None
    meses: dict[int, str] = {}
    for fila in df.itertuples(index=False):
        etiqueta = _texto(fila[0])
        if etiqueta.startswith(_PREFIJO_REGION):
            region = etiqueta[len(_PREFIJO_REGION):]
            meses = {j: m for j, v in enumerate(fila) if j and (m := _mes_de_fecha(v))}
            salida.setdefault(region, {})
        elif region and etiqueta:
            serie = {meses[j]: n for j in meses if (n := _numero(fila[j])) is not None}
            if serie:
                salida[region].setdefault(etiqueta, serie)
    return salida


def leer_aperturas(path: Path) -> dict[str, dict[str, dict[str, float]]]:
    import pandas as pd

    return aperturas_de_tabla(pd.ExcelFile(path).parse(HOJA_APERTURAS, header=None))


def _norm(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s)
                   if not unicodedata.combining(c)).upper()


def precios_de_tabla(df) -> dict[str, dict[str, float]]:
    """`producto -> {AAAA-MM: precio}` de la hoja GBA de precios promedio.

    El año va en una fila ("Año 2026", solo donde cambia) y el mes en la de abajo.
    """
    filas = [list(f) for f in df.itertuples(index=False)]
    i_anio = next(i for i, f in enumerate(filas)
                  if any(re.match(r"A.o \d{4}", _texto(v)) for v in f))
    anio, meses = None, {}
    for j, (a, m) in enumerate(zip(filas[i_anio], filas[i_anio + 1])):
        if (hallado := re.search(r"(\d{4})", _texto(a))) and j:
            anio = int(hallado.group(1))
        if anio and _norm(_texto(m)) in _MESES:
            meses[j] = f"{anio:04d}-{_MESES[_norm(_texto(m))]:02d}"
    salida: dict[str, dict[str, float]] = {}
    for f in filas[i_anio + 2:]:
        producto = _texto(f[0])
        serie = {meses[j]: n for j in meses if (n := _numero(f[j])) is not None}
        if producto and serie:
            salida.setdefault(producto, serie)
    return salida


def leer_precios_promedio(path: Path) -> dict[str, dict[str, float]]:
    import pandas as pd

    return precios_de_tabla(pd.ExcelFile(path).parse(HOJA_PRECIOS_GBA, header=None))


# --------------------------------------------------------------------------- #
# El lado del INDEC
# --------------------------------------------------------------------------- #


def variacion_indec(
    aperturas: dict[str, dict[str, dict[str, float]]],
    apertura: str | list[str],
    base: str,
    mes: str,
    pesos_region: dict[str, float] | None = None,
) -> float:
    """El indice nacional del INDEC entre `base` y `mes` (1,0166 = +1,66%).

    Formula 15: el nacional es la suma de los niveles regionales ponderados solo
    por el peso de la region. `apertura` puede ser una lista de nombres exactos y
    se toma el primero que este en cada region ("etc," en GBA, "etc." afuera).
    """
    pesos_region = pesos_region or pesos_regionales()
    nombres = apertura if isinstance(apertura, list) else [apertura]
    niveles: dict[str, dict[str, float]] = {base: {}, mes: {}}
    for region in pesos_region:
        series = aperturas.get(region, {})
        nombre = next((n for n in nombres if n in series), None)
        if nombre is None:
            raise ValueError(f"la region {region} no tiene la apertura {nombres}")
        for m in (base, mes):
            if m not in series[nombre]:
                raise ValueError(f"la apertura {nombre!r} de {region} no tiene el mes {m}")
            niveles[m][region] = series[nombre][m]
    return (nacional_desde_regiones(niveles[mes], pesos_region)
            / nacional_desde_regiones(niveles[base], pesos_region))


def aperturas_del_indice(path: Path | None = None) -> tuple[dict, dict]:
    """`(clase -> apertura, division -> apertura)` de config/indices_ipc.yaml."""
    cfg = yaml.safe_load((path or PATH_INDICES).read_text(encoding="utf-8"))
    clases = {str(c): s["apertura"] for c, s in (cfg.get("clases") or {}).items()}
    divisiones = {str(d): a for d, a in (cfg.get("divisiones") or {}).items()}
    return clases, divisiones


def cargar_pares(path: Path | None = None) -> dict[str, str]:
    """`articulo ENGHo -> producto del INDEC` de config/pares_indec.yaml."""
    cfg = yaml.safe_load((path or PATH_PARES).read_text(encoding="utf-8"))
    return {str(a): str(p) for a, p in (cfg.get("pares") or {}).items()}


# --------------------------------------------------------------------------- #
# La comparacion
# --------------------------------------------------------------------------- #


def rubro_contra_indec(
    nuestro: dict[str, float], indec: dict[str, float], pesos: dict[str, float]
) -> tuple[float | None, float | None]:
    """Nuestro rubro y lo comparable del INDEC, con NUESTROS pesos de los dos lados.

    Entran las clases donde tenemos dato; el INDEC se arma sobre esas mismas.
    """
    clases = {c: pesos[c] for c in nuestro if c in pesos}
    n, _, _ = laspeyres({c: nuestro[c] for c in clases}, clases)
    i, _, _ = laspeyres({c: indec[c] for c in clases if c in indec}, clases)
    return n, i


def errores_por_producto(
    nuestro: dict[str, float],
    indec: dict[str, float],
    pares: dict[str, str],
) -> tuple[list[dict], float | None, float | None]:
    """Nuestro articulo contra el precio promedio del INDEC, producto por producto.

    `indec` es `producto -> indice del mes`. Devuelve las filas, el error medio y
    el absoluto medio, en puntos. Un par sin dato de alguno de los dos no entra.
    """
    filas = [
        {"articulo": a, "producto": p,
         "nuestro_pct": (nuestro[a] - 1) * 100, "indec_pct": (indec[p] - 1) * 100,
         "error": (nuestro[a] - indec[p]) * 100}
        for a, p in pares.items() if a in nuestro and p in indec
    ]
    if not filas:
        return filas, None, None
    errores = [f["error"] for f in filas]
    return filas, sum(errores) / len(errores), sum(map(abs, errores)) / len(errores)
