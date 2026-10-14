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
from collections import defaultdict

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


# ==================================================== GUARDA de CRITICO 6 ===
# QUE HACE     corta la corrida si una clase de la taxonomia no tiene ponderador
# FORMULA      clases_taxonomia - claves(pond)
# POR QUE ASI  sin esto la clase se excluye sola y no deja rastro: no entra al
#              numerador ni al denominador de Laspeyres, asi que la cobertura
#              sigue informando 100% sobre una canasta mas chica
# LO VERIFICA  tests/test_indice.py::test_una_clase_de_la_taxonomia_sin_ponderador_se_detecta
# FALLA ASI    se dispara al agregar una clase a la taxonomia sin declararla en
#              config/indices_ipc.yaml, que es justo el proximo paso del plan
# ===========================================================================
def clases_sin_ponderador(
    clases_taxonomia: set[str], pond: dict[str, tuple[str, float]]
) -> list[str]:
    """Clases que estan en la taxonomia y no tienen ponderador.

    Es la guarda de la operacion 7. Una clase sin ponderador no entra ni al
    numerador ni al denominador de Laspeyres, asi que la cobertura sigue
    diciendo 100% y el indice queda calculado sobre menos canasta sin que nada
    avise. Devolver la lista permite cortar antes de dar un numero.
    """
    return sorted(c for c in clases_taxonomia if c not in pond)


# =========================================================== CRITICO 7 de 8 ===
# QUE HACE     clases COICOP -> nivel del indice
# FORMULA      suma( peso_renormalizado(clase) x indice(clase) ), sobre las
#              clases que tienen indice Y peso > 0
# POR QUE ASI  aritmetica ponderada porque arriba si hay ponderadores. Se
#              renormaliza porque los pesos del INDEC suman 1 sobre la canasta
#              completa y el indice cubre una parte: sin reescalar, el numero
#              sale dividido por la cobertura.
# LO VERIFICA  tests/test_indice.py::test_sin_renormalizar_el_numero_no_es_interpretable
#              y test_una_clase_sin_dato_no_se_imputa_con_el_promedio
# FALLA ASI    una clase con peso y sin dato queda afuera del numerador Y del
#              denominador, que es lo correcto, pero entonces el indice es de
#              una canasta mas chica. Eso se ve solo en la linea de cobertura:
#              si dice menos de 100%, falta una clase.
# ============================================================================
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


@dataclasses.dataclass(slots=True)
class ClasesDesdeCategorias:
    """Lo que entro a cada clase, y lo que quedo afuera.

    Los descartes se devuelven en vez de saltearse: una categoria que
    desaparece sin aviso deja el indice calculado sobre menos canasta, y el
    numero sigue pareciendo correcto.
    """

    indices: dict[str, float]
    sin_clase: list[str] = dataclasses.field(default_factory=list)
    sin_peso: list[str] = dataclasses.field(default_factory=list)
    categorias_por_clase: dict[str, int] = dataclasses.field(default_factory=dict)


# =========================================================== CRITICO 5 de 8 ===
# QUE HACE     media ponderada de las categorias elementales dentro de cada
#              clase COICOP. Es el escalon entre Jevons y Laspeyres.
# FORMULA      indice(clase) = suma( peso(cat) x indice(cat) ) / suma( peso(cat) )
#              sobre las categorias de esa clase que tienen peso > 0
# POR QUE ASI  aritmetica y ponderada porque aca ya hay ponderadores y los
#              rubros no son sustitutos entre si
# LO VERIFICA  tests/test_indice.py, los cinco tests que empiezan con
#              test_las_categorias_a_clases_*
# FALLA ASI    si una categoria pierde su clase COICOP o su peso, desaparece de
#              la cuenta. El indice de esa clase queda calculado sobre menos
#              articulos y no hay forma de verlo en el numero. Por eso los dos
#              descartes salen contados en `sin_clase` y `sin_peso`, y la
#              corrida los imprime.
# ============================================================================
def clases_desde_categorias(
    indices_cat: dict[str, float],
    clase_de_categoria: dict[str, str],
    pesos_cat: dict[str, float],
) -> ClasesDesdeCategorias:
    acum: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
    cuenta: dict[str, int] = defaultdict(int)
    sin_clase: list[str] = []
    sin_peso: list[str] = []

    for cat, indice in indices_cat.items():
        clase = clase_de_categoria.get(cat)
        if clase is None:
            sin_clase.append(cat)
            continue
        peso = pesos_cat.get(cat, 0.0)
        if peso <= 0:
            sin_peso.append(cat)
            continue
        acum[clase][0] += peso * indice
        acum[clase][1] += peso
        cuenta[clase] += 1

    return ClasesDesdeCategorias(
        indices={c: num / den for c, (num, den) in acum.items() if den > 0},
        sin_clase=sorted(sin_clase),
        sin_peso=sorted(sin_peso),
        categorias_por_clase=dict(cuenta),
    )


# =========================================================== CRITICO 8 de 8 ===
# QUE HACE     el nivel del indice mes a mes
# FORMULA      indice_t = indice_{t-1} x variacion_t
# POR QUE ASI  multiplicativo y nunca contra la base directa: la canasta rota, y
#              comparar contra un mes lejano tiraria la mitad de la muestra por
#              falta de emparejamiento
# LO VERIFICA  tests/test_indice.py::test_encadenar_es_multiplicativo_no_aditivo
# FALLA ASI    sumar variaciones en vez de multiplicarlas da casi lo mismo con
#              numeros chicos y se va acumulando. Con 2% por 12 meses la
#              diferencia es 26,8% contra 24%.
# ============================================================================
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
