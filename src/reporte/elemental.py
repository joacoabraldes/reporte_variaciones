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


# ========================================================== CRITICO 3 de 10 ===
# QUE HACE     descarta ratios sospechosos antes de promediar
# FORMULA      1) tope absoluto: fuera si ratio >= tope o <= 1/tope
#              2) dispersion: fuera si |log_ratio - mediana| > umbral x dispersion
#              con dispersion = MAD, y si da 0 el rango intercuartil / 2, y si
#              eso tambien da 0 queda solo el tope. Un precio que NO cambio
#              (ratio 1) nunca sale por dispersion
# POR QUE ASI  sobre log(ratio) porque el log-return es simetrico: +50% y -33,3%
#              se cancelan. El filtro esta para cazar errores de tipeo, y un
#              precio igual al del mes anterior no puede ser uno
# LO VERIFICA  tests/test_indice.py::test_el_mad_cero_no_manda_todo_a_cuarentena,
#              test_el_tope_absoluto_caza_un_error_de_tipeo y
#              test_un_precio_que_no_cambio_nunca_es_un_valor_raro
# FALLA ASI    en gondola el 59,7% de los precios no se mueve, asi que la
#              dispersion da cero seguido y el criterio se cae a los fallbacks
#              sin avisar. Y al reves: cuando casi todo se movio igual, lo
#              quieto parece raro. Hasta el 21/10/2026 pasaba: la carne picada de
#              ago->sep dio -15,0% en vez de -9,7% porque mando a cuarentena los
#              1.208 precios que no cambiaron. El umbral se elige contra el INDEC.
# ============================================================================
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
        # Un precio que no cambio no es un error de tipeo, aunque casi todo lo
        # demas se haya movido y el quieto quede lejos de la mediana. Con
        # tolerancia: el precio del mes es un promedio, y el mismo precio con
        # centavos promediado sobre 31 y sobre 30 dias puede diferir en el
        # ultimo bit.
        no_cambio = math.isclose(r.precio_actual, r.precio_base, rel_tol=1e-9)
        if not no_cambio and abs(r.log_ratio - mediana) > umbral_mad * dispersion:
            descartes.append(
                Descarte(r.quote, MOTIVO_OUTLIER_MAD,
                         f"log_ratio {r.log_ratio:.4f} vs mediana {mediana:.4f} "
                         f"({umbral_usado} {dispersion:.4f})")
            )
        else:
            limpios.append(r)
    return limpios, descartes, umbral_usado


# ========================================================== CRITICO 2 de 10 ===
# QUE HACE     la muestra emparejada: que quotes entran a la variacion
# FORMULA      solo los quotes con precio en los DOS meses y los dos positivos
# POR QUE ASI  un producto nuevo no tiene contra que compararse, y uno que
#              desaparecio tampoco
# LO VERIFICA  tests/test_indice.py::test_solo_entran_los_quotes_presentes_en_los_dos_meses
# FALLA ASI    si entrara un quote sin base, su relativo seria division por cero
#              o un ratio inventado. Si se arrastrara el ultimo precio en vez de
#              dejarlo afuera, se estaria afirmando que no cambio.
# ============================================================================
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


# ========================================================== CRITICO 4 de 10 ===
# QUE HACE     la variacion de una categoria elemental
# FORMULA      exp( promedio( ln(precio_actual / precio_base) ) )
# POR QUE ASI  media geometrica y no aritmetica: abajo no hay ponderadores y los
#              productos son sustitutos. La aritmetica de relativos tiene drift
#              de Carli. En logaritmos porque multiplicar 5.000 ratios desborda.
# LO VERIFICA  tests/test_indice.py::test_jevons_es_media_geometrica y
#              test_precios_que_suben_y_vuelven_dan_cero_acumulado
# FALLA ASI    con promedio simple el indice sube 0,19 puntos de aumento que no
#              existe, y el sesgo es siempre hacia arriba. Medido: el promedio
#              simple da >= Jevons en los 20 articulos, sin excepcion.
# ============================================================================
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


# ========================================================== CRITICO 5 de 10 ===
# QUE HACE     junta en cada articulo el canal supermercado (el Jevons de SEPA)
#              con el tradicional (las carnicerias del IPCVA)
# FORMULA      I = I_super ^ s  x  I_tradicional ^ (1 - s),  s = parte del gasto
#              del articulo hecha en supermercados (ENGHo 2017/18, tipo_negocio)
# POR QUE ASI  es la formula 8 del INDEC: cada variedad se calcula por separado
#              en cada tipo de negocio y se combinan con la proporcion fija de
#              donde compran los hogares. Geometrica sobre relativos porque los
#              niveles de las dos fuentes no se comparan (el IPCVA anota el
#              precio mas bajo de cada corte; SEPA, el de lista de cada producto).
# LO VERIFICA  tests/test_carnicerias.py::test_combina_los_dos_canales_con_la_proporcion_de_supermercados
#              y test_sin_la_proporcion_de_supermercados_corta
# FALLA ASI    sin la proporcion, la carne entera sale de DIA: la picada da -9,7%
#              en agosto->septiembre porque DIA bajo el precio de lista, mientras
#              las carnicerias, donde se compra el 56% de la carne vacuna, no se
#              movieron. Una proporcion invertida (s por 1-s) da un numero tan
#              plausible como el correcto.
# ============================================================================
def combinar_canales(
    indices: dict[str, float],
    tradicional: dict[str, float],
    participacion_super: dict[str, float],
) -> dict[str, float]:
    """Los indices por articulo con el canal tradicional sumado donde lo hay.

    Un articulo sin dato tradicional queda como estaba. Uno con dato tradicional
    pero sin SEPA no se agrega: su peso es el del articulo medido en SEPA.
    """
    combinados = dict(indices)
    for art, i_trad in tradicional.items():
        if art not in indices:
            continue
        if art not in participacion_super:
            raise ValueError(f"{art} tiene precio de carniceria y no tiene la proporcion "
                             "de supermercados de la ENGHo")
        s = participacion_super[art]
        if not 0.0 <= s <= 1.0:
            raise ValueError(f"la proporcion de supermercados de {art} es {s}: tiene "
                             "que estar entre 0 y 1")
        combinados[art] = math.exp(s * math.log(indices[art]) + (1 - s) * math.log(i_trad))
    return combinados
