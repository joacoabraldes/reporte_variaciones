"""Agregacion hacia arriba: Laspeyres ponderado y encadenamiento.

Arriba del nivel elemental **si** hay ponderadores, y los rubros no son
sustitutos entre si: que suba el pan no hace que la gente compre menos yerba de
la forma en que una marca de leche reemplaza a otra. Por eso aca la media es
**aritmetica ponderada** y no geometrica.

    I_clase = suma( peso_categoria x I_categoria )

Y el encadenamiento:

    indice_t = indice_{t-1} x variacion_t

Nunca se compara contra la base directamente: la canasta rota (productos que
entran y salen), y una comparacion directa contra un mes lejano tiraria la mitad
de la muestra por falta de emparejamiento.
"""

from __future__ import annotations

import dataclasses

# El indice cubre una parte de la canasta, no toda. Los pesos del INDEC suman 1
# sobre el total, asi que hay que reescalarlos sobre lo efectivamente cubierto.
MODO_RENORMALIZAR = "renormalizado"
MODO_CRUDO = "crudo"


@dataclasses.dataclass
class Ponderador:
    codigo: str
    nombre: str
    peso: float


@dataclasses.dataclass
class ResultadoAgregado:
    codigo: str
    nombre: str
    indice: float | None
    peso_cubierto: float          # suma de pesos originales con dato
    peso_total: float             # suma de pesos de todos los hijos
    componentes: dict[str, float] = dataclasses.field(default_factory=dict)
    n_quotes: int = 0

    @property
    def variacion_pct(self) -> float | None:
        return None if self.indice is None else (self.indice - 1.0) * 100.0

    @property
    def cobertura(self) -> float:
        """Que fraccion del peso teorico del agregado tiene dato."""
        return self.peso_cubierto / self.peso_total if self.peso_total else 0.0


def renormalizar(pesos: dict[str, float]) -> dict[str, float]:
    """Reescala los pesos para que sumen 1.

    Sin esto el resultado no es interpretable: si los pesos de lo cubierto suman
    0,10, la media ponderada da un numero diez veces menor que la variacion real.
    """
    total = sum(pesos.values())
    if total <= 0:
        return {}
    return {k: v / total for k, v in pesos.items()}


def laspeyres(
    indices: dict[str, float],
    pesos: dict[str, float],
    modo: str = MODO_RENORMALIZAR,
) -> tuple[float | None, float, float]:
    """Media aritmetica ponderada. Devuelve (indice, peso_cubierto, peso_total).

    Solo entran los codigos que tienen indice **y** peso. Los que tienen peso
    pero no dato se excluyen del numerador y del denominador: no se asume que se
    movieron como el promedio, simplemente no participan.
    """
    peso_total = sum(pesos.values())
    con_dato = {k: pesos[k] for k in indices if k in pesos and pesos[k] > 0}
    peso_cubierto = sum(con_dato.values())
    if not con_dato:
        return None, 0.0, peso_total

    usados = renormalizar(con_dato) if modo == MODO_RENORMALIZAR else con_dato
    return sum(usados[k] * indices[k] for k in usados), peso_cubierto, peso_total


def agregar(
    codigo: str,
    nombre: str,
    indices_hijos: dict[str, float],
    pesos_hijos: dict[str, float],
    n_quotes: int = 0,
    modo: str = MODO_RENORMALIZAR,
) -> ResultadoAgregado:
    indice, cubierto, total = laspeyres(indices_hijos, pesos_hijos, modo)
    return ResultadoAgregado(
        codigo=codigo, nombre=nombre, indice=indice,
        peso_cubierto=cubierto, peso_total=total,
        componentes=dict(indices_hijos), n_quotes=n_quotes,
    )


def encadenar(nivel_anterior: float, variacion: float) -> float:
    """`indice_t = indice_{t-1} x variacion_t`."""
    return nivel_anterior * variacion


def serie_encadenada(
    variaciones: list[float], base: float = 100.0
) -> list[float]:
    """Convierte una lista de variaciones mensuales en un nivel encadenado.

    El primer elemento devuelto es la base; despues, un nivel por variacion.
    """
    serie = [base]
    for v in variaciones:
        serie.append(encadenar(serie[-1], v))
    return serie


def variacion_interanual(serie: dict[str, float], periodo: str) -> float | None:
    """Variacion contra el mismo mes del anio anterior, si existe."""
    anio, mes = periodo.split("-")
    anterior = f"{int(anio) - 1}-{mes}"
    if anterior not in serie or serie[anterior] == 0:
        return None
    return serie[periodo] / serie[anterior] - 1.0
