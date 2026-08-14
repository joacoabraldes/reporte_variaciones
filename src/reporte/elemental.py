"""Indice elemental: media geometrica de relativos (Jevons).

Nivel mas bajo del calculo, donde no hay ponderadores porque los productos de
una categoria elemental son sustitutos entre si.

    I_categoria = exp( media( log(precio_t / precio_{t-1}) ) )

Se computa en logaritmos por estabilidad numerica: multiplicar cientos de ratios
y despues sacar la raiz n-esima desborda; sumar logaritmos no.

Por que geometrica y no aritmetica: la media aritmetica de relativos tiene
"drift de Carli" — si un precio sube 50% y despues baja 33,3% vuelve al valor
original, pero la media aritmetica de los dos relativos da 1,0833, o sea que el
indice trepa aunque los precios no se hayan movido. La geometrica da exactamente 1.
"""

from __future__ import annotations

import dataclasses
import math
import statistics

# Un quote tiene que existir en los dos meses para aportar un relativo: es la
# "muestra emparejada". Un producto nuevo no tiene contra que compararse.
MOTIVO_SIN_BASE = "sin_precio_mes_anterior"
MOTIVO_SIN_ACTUAL = "sin_precio_mes_actual"
MOTIVO_PRECIO_INVALIDO = "precio_no_positivo"
MOTIVO_OUTLIER_MAD = "outlier_mad"
MOTIVO_OUTLIER_TOPE = "outlier_tope_absoluto"
MOTIVO_POCOS_DIAS = "pocos_dias_observados"

# Tope absoluto: duplicar o partir al medio el precio en un mes. Un digito de mas
# al tipear multiplica por 10, asi que ×2 lo caza de sobra y deja pasar aumentos
# grandes pero legitimos.
TOPE_RATIO_DEFAULT = 2.0

# Umbral en cantidad de MAD. 3,5 es el valor habitual para deteccion robusta.
UMBRAL_MAD_DEFAULT = 3.5

# Sin un minimo de quotes, la dispersion no significa nada y el umbral es ruido.
MINIMO_QUOTES_MAD = 15


@dataclasses.dataclass(frozen=True)
class Relativo:
    """El cambio de precio de un quote entre dos meses."""

    quote: tuple
    precio_base: float
    precio_actual: float

    @property
    def ratio(self) -> float:
        return self.precio_actual / self.precio_base

    @property
    def log_ratio(self) -> float:
        return math.log(self.ratio)


@dataclasses.dataclass
class Descarte:
    quote: tuple
    motivo: str
    detalle: str = ""


@dataclasses.dataclass
class ResultadoElemental:
    categoria: str
    indice: float | None                    # 1.0 = sin variacion
    n_quotes: int                           # los que entraron al calculo
    n_descartados: int
    descartes: list[Descarte] = dataclasses.field(default_factory=list)
    umbral_usado: str = ""

    @property
    def variacion_pct(self) -> float | None:
        return None if self.indice is None else (self.indice - 1.0) * 100.0


def mad(valores: list[float]) -> float:
    """Mediana de los desvios absolutos respecto de la mediana."""
    if not valores:
        return 0.0
    m = statistics.median(valores)
    return statistics.median([abs(v - m) for v in valores])


def rango_intercuartil(valores: list[float]) -> float:
    if len(valores) < 4:
        return 0.0
    orden = sorted(valores)
    n = len(orden)
    q1 = orden[n // 4]
    q3 = orden[(3 * n) // 4]
    return q3 - q1


def detectar_outliers(
    relativos: list[Relativo],
    umbral_mad: float = UMBRAL_MAD_DEFAULT,
    tope_ratio: float = TOPE_RATIO_DEFAULT,
    minimo_quotes: int = MINIMO_QUOTES_MAD,
) -> tuple[list[Relativo], list[Descarte], str]:
    """Separa los relativos sospechosos. Devuelve (limpios, descartes, umbral_usado).

    Trabaja sobre `log(ratio)` y no sobre la variacion porcentual porque el
    log-return es simetrico: +100% y -50% son el mismo movimiento con signo
    opuesto (log 2 y -log 2), mientras que en porcentaje uno mide 100 y el otro 50.

    Dos filtros, y el absoluto se aplica SIEMPRE:

    1. **Tope absoluto** (`ratio` fuera de [1/tope, tope]). Es la red de seguridad:
       si una categoria esta muy dispersa, el umbral por MAD podria dejar pasar
       un x10, y este no.

    2. **MAD sobre log(ratio)**. Con un detalle que rompe todo si se ignora: en
       gondola muchisimos precios no cambian de un mes a otro, asi que si mas de
       la mitad de los ratios valen exactamente 1, la mediana es 0 y **el MAD da
       cero**. Ahi cualquier variacion distinta de cero queda a infinitos MAD y
       se manda la categoria entera a cuarentena. Por eso hay fallback a rango
       intercuartil, y si eso tambien da cero, solo queda el tope absoluto.
    """
    if not relativos:
        return [], [], "sin_datos"

    limpios: list[Relativo] = []
    descartes: list[Descarte] = []

    # 1. Tope absoluto, siempre.
    candidatos: list[Relativo] = []
    for r in relativos:
        if r.ratio >= tope_ratio or r.ratio <= 1.0 / tope_ratio:
            descartes.append(
                Descarte(r.quote, MOTIVO_OUTLIER_TOPE,
                         f"ratio {r.ratio:.3f} fuera de [{1/tope_ratio:.2f}, {tope_ratio:.2f}]")
            )
        else:
            candidatos.append(r)

    if len(candidatos) < minimo_quotes:
        # Muestra chica: la dispersion no es informativa, queda solo el tope.
        return candidatos, descartes, "solo_tope_absoluto"

    logs = [r.log_ratio for r in candidatos]
    mediana = statistics.median(logs)
    dispersion = mad(logs)
    umbral_usado = "mad"

    if dispersion == 0.0:
        # Pasa seguido: mas de la mitad de los precios no se movieron.
        dispersion = rango_intercuartil(logs) / 2.0
        umbral_usado = "iqr"
    if dispersion == 0.0:
        return candidatos, descartes, "solo_tope_absoluto"

    for r in candidatos:
        if abs(r.log_ratio - mediana) > umbral_mad * dispersion:
            descartes.append(
                Descarte(r.quote, MOTIVO_OUTLIER_MAD,
                         f"log_ratio {r.log_ratio:.4f} vs mediana {mediana:.4f} "
                         f"({umbral_usado} {dispersion:.4f})")
            )
        else:
            limpios.append(r)
    return limpios, descartes, umbral_usado


def emparejar(
    base: dict[tuple, float], actual: dict[tuple, float]
) -> tuple[list[Relativo], list[Descarte]]:
    """Muestra emparejada: solo los quotes presentes en los dos meses.

    Un producto que aparece este mes y no estaba el anterior no tiene contra que
    compararse, y uno que desaparecio tampoco. Los dos quedan afuera del calculo
    de la variacion, aunque el que desaparecio pueda imputarse despues.
    """
    relativos: list[Relativo] = []
    descartes: list[Descarte] = []

    for quote, p_actual in actual.items():
        p_base = base.get(quote)
        if p_base is None:
            descartes.append(Descarte(quote, MOTIVO_SIN_BASE))
        elif p_base <= 0 or p_actual <= 0:
            descartes.append(Descarte(quote, MOTIVO_PRECIO_INVALIDO,
                                      f"base={p_base} actual={p_actual}"))
        else:
            relativos.append(Relativo(quote, p_base, p_actual))

    for quote in base:
        if quote not in actual:
            descartes.append(Descarte(quote, MOTIVO_SIN_ACTUAL))

    return relativos, descartes


def jevons(relativos: list[Relativo]) -> float | None:
    """Media geometrica de los relativos, computada en logaritmos."""
    if not relativos:
        return None
    return math.exp(statistics.fmean(r.log_ratio for r in relativos))


def indice_elemental(
    categoria: str,
    base: dict[tuple, float],
    actual: dict[tuple, float],
    umbral_mad: float = UMBRAL_MAD_DEFAULT,
    tope_ratio: float = TOPE_RATIO_DEFAULT,
    minimo_quotes: int = MINIMO_QUOTES_MAD,
) -> ResultadoElemental:
    """Variacion de una categoria elemental entre dos meses.

    `base` y `actual` son {quote: precio}, donde quote es
    (id_comercio, id_sucursal, id_producto).
    """
    relativos, descartes = emparejar(base, actual)
    limpios, desc_outliers, umbral = detectar_outliers(
        relativos, umbral_mad, tope_ratio, minimo_quotes
    )
    descartes.extend(desc_outliers)
    return ResultadoElemental(
        categoria=categoria,
        indice=jevons(limpios),
        n_quotes=len(limpios),
        n_descartados=len(descartes),
        descartes=descartes,
        umbral_usado=umbral,
    )
