"""Imputacion de quotes ausentes: mantener viva la muestra entre periodos.

Un quote que esta en el periodo base y no en el actual se cae de la muestra
emparejada. Sin hacer nada, se pierde **dos veces**: en el periodo actual porque
no tiene precio con que comparar, y en el siguiente porque tampoco tiene base.

    quote X      agosto     septiembre   octubre
    ---------------------------------------------------------------
    real         $1.000     (falta)      $1.300
    sin imputar  --------   se cae       se cae (no tiene base)
    con imputar  --------   $1.100       ratio 1,18 sobre el sintetico

**Lo que no hay que hacer es arrastrar el ultimo precio.** Eso asume 0% de
variacion en el periodo que falta —sesgo a la baja— y despues descarga los dos
periodos de suba juntos en el siguiente. Deforma el indice dos veces, en
direcciones opuestas.

La imputacion usa la variacion de la categoria del quote:

    precio_imputado = precio_base * indice_elemental(categoria)

**Esto no mueve el indice del periodo en que se imputa, y es a proposito.**
Jevons es el promedio de los log-ratios; agregarle un valor que *es* el promedio
deja el promedio igual. El precio imputado no entra al calculo del periodo: se
devuelve aparte, en `base_proximo_periodo`, que es para lo unico que sirve.
Por eso este modulo no toca `elemental.py` ni `agregacion.py` — no hace falta
meter nada adentro del calculo.

**El supuesto, que hay que decir en voz alta:** imputar asi asume que el quote
ausente se movio como su categoria. Cuando falta un producto suelto es
razonable. Cuando se cae un comercio entero —el 20 perdio 11 dias de agosto— no
falta un quote sino todos los de esa cadena a la vez, y se les asigna el
movimiento de un promedio que quedo dominado por las cadenas que si reportaron.
Si esa cadena tiene una politica de precios distinta, la imputacion la borra.
`ResultadoImputacion.por_comercio()` esta para poder ver cuando pasa eso.
"""

from __future__ import annotations

import dataclasses
from collections import Counter

from .elemental import Descarte
from .periodo import ClaveQuote

MOTIVO_BAJA_POR_AUSENCIA = "ausente_demasiados_periodos"
MOTIVO_SIN_INDICE = "categoria_sin_indice"

# Cuantos periodos seguidos puede faltar un quote antes de salir de la muestra.
# Dos: mas que eso y el precio sintetico ya acumula tanta imputacion sobre
# imputacion que dejo de ser una medicion.
MAX_PERIODOS_AUSENTE = 2


@dataclasses.dataclass(frozen=True)
class PrecioImputado:
    """Un precio que no se observo y se estimo con la variacion de su categoria."""

    quote: ClaveQuote
    precio: float
    precio_base: float
    categoria: str
    indice_usado: float
    periodos_ausente: int

    @property
    def es_reimputacion(self) -> bool:
        """Se imputo sobre una base que ya era sintetica."""
        return self.periodos_ausente > 1


@dataclasses.dataclass
class ResultadoImputacion:
    """Lo que sale de imputar un periodo.

    `base_proximo_periodo` es el unico campo que se usa aguas abajo: es el
    `dict[quote, precio]` que hay que pasar como base del periodo siguiente.
    Lleva los precios observados **y** los imputados.
    """

    base_proximo_periodo: dict[ClaveQuote, float]
    ausencias: dict[ClaveQuote, int]
    imputados: list[PrecioImputado] = dataclasses.field(default_factory=list)
    reaparecidos: list[ClaveQuote] = dataclasses.field(default_factory=list)
    bajas: list[Descarte] = dataclasses.field(default_factory=list)
    sin_indice: list[Descarte] = dataclasses.field(default_factory=list)

    @property
    def n_imputados(self) -> int:
        return len(self.imputados)

    @property
    def n_bajas(self) -> int:
        return len(self.bajas)

    def por_comercio(self) -> dict[str, int]:
        """Cuantos quotes se imputaron por comercio.

        Un comercio que concentra las imputaciones no perdio productos sueltos:
        dejo de reportar. Ahi el supuesto de "se movio como su categoria" es mas
        fragil, porque a esa cadena se le esta asignando el movimiento de las
        otras.
        """
        return dict(Counter(imp.quote[0] for imp in self.imputados))


def imputar(
    base: dict[ClaveQuote, float],
    actual: dict[ClaveQuote, float],
    categorias: dict[ClaveQuote, str],
    indices: dict[str, float | None],
    ausencias: dict[ClaveQuote, int] | None = None,
    max_periodos_ausente: int = MAX_PERIODOS_AUSENTE,
) -> ResultadoImputacion:
    """Completa los quotes que estan en `base` y faltan en `actual`.

    `indices` son los indices elementales del periodo, ya calculados sobre la
    muestra emparejada: {categoria: indice}. Un indice `None` —categoria sin
    quotes suficientes— no se puede usar para imputar.

    `ausencias` es el estado que devuelve la llamada anterior: cuantos periodos
    seguidos lleva ausente cada quote. En el primer periodo va vacio.

    No imputa hacia atras: un quote que aparece en `actual` y no estaba en
    `base` es un producto nuevo, no tiene historia, y entra a la muestra recien
    en el periodo siguiente.
    """
    ausencias = dict(ausencias or {})
    resultado = ResultadoImputacion(base_proximo_periodo={}, ausencias={})

    # Lo observado pasa tal cual y resetea el contador de ausencias.
    for quote, precio in actual.items():
        resultado.base_proximo_periodo[quote] = precio
        resultado.ausencias[quote] = 0
        if ausencias.get(quote, 0) > 0:
            # Volvio despues de faltar: su ratio de este periodo se midio contra
            # un precio sintetico, no contra uno observado. Se cuenta aparte
            # para poder mirarlo sin cambiar el filtro de outliers.
            resultado.reaparecidos.append(quote)

    for quote, precio_base in base.items():
        if quote in actual:
            continue

        n_ausente = ausencias.get(quote, 0) + 1
        if n_ausente > max_periodos_ausente:
            resultado.bajas.append(
                Descarte(
                    quote,
                    MOTIVO_BAJA_POR_AUSENCIA,
                    f"{n_ausente} periodos seguidos sin observarse, "
                    f"maximo {max_periodos_ausente}",
                )
            )
            continue

        categoria = categorias.get(quote)
        indice = indices.get(categoria) if categoria is not None else None
        if indice is None or precio_base <= 0:
            # Sin indice de categoria no hay con que imputar. El quote se cae,
            # pero se sigue contando la ausencia: si la categoria se recupera el
            # periodo que viene, ya no tiene base y no vuelve. Queda registrado.
            resultado.sin_indice.append(
                Descarte(
                    quote,
                    MOTIVO_SIN_INDICE,
                    f"categoria {categoria!r} sin indice en este periodo",
                )
            )
            continue

        precio_imputado = precio_base * indice
        resultado.base_proximo_periodo[quote] = precio_imputado
        resultado.ausencias[quote] = n_ausente
        resultado.imputados.append(
            PrecioImputado(
                quote=quote,
                precio=precio_imputado,
                precio_base=precio_base,
                categoria=categoria,
                indice_usado=indice,
                periodos_ausente=n_ausente,
            )
        )

    return resultado
