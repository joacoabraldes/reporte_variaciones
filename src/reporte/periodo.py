"""Ventana temporal: de observaciones diarias a quotes del periodo.

**El metodo no cambia entre semanas y meses.** Mediana por quote dentro de la
ventana, muestra emparejada, Jevons abajo, Laspeyres arriba, encadenamiento. Lo
unico que cambia son los parametros de `config/parametros.yaml`: cuantos dias
tiene que tener un quote para entrar, y que tan ancha es la banda del tope
absoluto. Semanal existe para poder mirar el pipeline hoy, con 22 dias
capturados, en vez de esperar a que haya dos meses cerrados.

**Este modulo es la frontera.** Es el ultimo lugar donde se sabe si la ventana
es una semana o un mes. Lo que sale para abajo es un `dict[quote, precio]`, que
es exactamente lo que `elemental.emparejar()` ya recibe hoy: el calculo no puede
distinguir el origen ni tiene por que. Si en algun momento hace falta preguntar
`periodo.tipo` dentro de `elemental.py` o `agregacion.py`, algo se filtro.

**El mensual no se deriva encadenando semanas.** Cuatro variaciones semanales
encadenadas no dan la variacion mensual: cada par de semanas tiene su propia
muestra emparejada, y los quotes que entran no son los mismos. Son dos series
paralelas, las dos calculadas desde las observaciones diarias.
`validar_encadenable()` impide mezclar tipos.
"""

from __future__ import annotations

import calendar
import dataclasses
from datetime import date, timedelta
from pathlib import Path
from typing import Literal

import duckdb
import yaml

from .agregacion import encadenar
from .elemental import MOTIVO_POCOS_DIAS, Descarte

TipoPeriodo = Literal["semanal", "mensual"]

SEMANAL: TipoPeriodo = "semanal"
MENSUAL: TipoPeriodo = "mensual"

RAIZ_REPO = Path(__file__).resolve().parents[2]
PATH_PARAMETROS = RAIZ_REPO / "config" / "parametros.yaml"

# (id_comercio, id_sucursal, id_producto). El id_comercio no es opcional:
# id_sucursal es un codigo interno de cada cadena y la sucursal "7" existe en
# varias a la vez.
ClaveQuote = tuple[str, str, str]

COLUMNA_LISTA = "precio_lista"
COLUMNA_EFECTIVO = "precio_efectivo"


# --------------------------------------------------------------------------- #
# Periodo
# --------------------------------------------------------------------------- #


@dataclasses.dataclass(frozen=True)
class Periodo:
    """Una ventana temporal cerrada. `fin` es inclusivo."""

    inicio: date
    fin: date
    etiqueta: str
    tipo: TipoPeriodo

    def __post_init__(self) -> None:
        if self.fin < self.inicio:
            raise ValueError(f"periodo invertido: {self.inicio} .. {self.fin}")
        if self.tipo not in (SEMANAL, MENSUAL):
            raise ValueError(f"tipo de periodo desconocido: {self.tipo!r}")

    @classmethod
    def semana_iso(cls, anio: int, semana: int) -> "Periodo":
        """Semana ISO: arranca lunes, termina domingo."""
        inicio = date.fromisocalendar(anio, semana, 1)
        return cls(
            inicio=inicio,
            fin=inicio + timedelta(days=6),
            etiqueta=f"{anio}-W{semana:02d}",
            tipo=SEMANAL,
        )

    @classmethod
    def mes(cls, anio: int, mes: int) -> "Periodo":
        ultimo = calendar.monthrange(anio, mes)[1]
        return cls(
            inicio=date(anio, mes, 1),
            fin=date(anio, mes, ultimo),
            etiqueta=f"{anio}-{mes:02d}",
            tipo=MENSUAL,
        )

    @property
    def dias_esperados(self) -> list[date]:
        n = (self.fin - self.inicio).days + 1
        return [self.inicio + timedelta(days=i) for i in range(n)]

    def contiene(self, f: date) -> bool:
        return self.inicio <= f <= self.fin

    def __str__(self) -> str:
        return self.etiqueta


def semanas_iso_completas(desde: date, hasta: date) -> list[Periodo]:
    """Semanas ISO que entran **enteras** en el rango de dias disponibles.

    Una semana a la que le falta un dia no es comparable contra una completa: la
    mediana de cada quote se calcula sobre menos observaciones y el minimo de
    dias empieza a descartar quotes por un motivo que no es real.
    """
    periodos: list[Periodo] = []
    vistas: set[tuple[int, int]] = set()
    d = desde
    while d <= hasta:
        anio, semana, _ = d.isocalendar()
        if (anio, semana) not in vistas:
            vistas.add((anio, semana))
            p = Periodo.semana_iso(anio, semana)
            if desde <= p.inicio and p.fin <= hasta:
                periodos.append(p)
        d += timedelta(days=1)
    return periodos


def meses_completos(desde: date, hasta: date) -> list[Periodo]:
    """Meses que entran enteros en el rango."""
    periodos: list[Periodo] = []
    for anio, mes in sorted({(d.year, d.month) for d in _rango(desde, hasta)}):
        p = Periodo.mes(anio, mes)
        if desde <= p.inicio and p.fin <= hasta:
            periodos.append(p)
    return periodos


# --------------------------------------------------------------------------- #
# Parametros por ventana
# --------------------------------------------------------------------------- #


@dataclasses.dataclass(frozen=True)
class ParametrosVentana:
    """Lo que cambia entre semanal y mensual. Nada mas cambia."""

    tipo: TipoPeriodo
    minimo_dias_quote: int
    tope_ratio: float
    umbral_mad: float
    minimo_quotes_mad: int

    @classmethod
    def desde_yaml(
        cls, tipo: TipoPeriodo, path: Path | None = None
    ) -> "ParametrosVentana":
        path = path or PATH_PARAMETROS
        datos = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        ventanas = datos.get("ventanas") or {}
        if tipo not in ventanas:
            raise ValueError(
                f"{path}: no hay parametros para la ventana {tipo!r} "
                f"(hay: {', '.join(sorted(ventanas))})"
            )
        spec = ventanas[tipo]
        faltan = {
            "minimo_dias_quote",
            "tope_ratio",
            "umbral_mad",
            "minimo_quotes_mad",
        } - set(spec)
        if faltan:
            raise ValueError(
                f"{path}: a la ventana {tipo!r} le faltan {', '.join(sorted(faltan))}"
            )
        return cls(
            tipo=tipo,
            minimo_dias_quote=int(spec["minimo_dias_quote"]),
            tope_ratio=float(spec["tope_ratio"]),
            umbral_mad=float(spec["umbral_mad"]),
            minimo_quotes_mad=int(spec["minimo_quotes_mad"]),
        )

    @classmethod
    def para(cls, periodo: Periodo, path: Path | None = None) -> "ParametrosVentana":
        return cls.desde_yaml(periodo.tipo, path)


# --------------------------------------------------------------------------- #
# Colapso a quotes
# --------------------------------------------------------------------------- #


@dataclasses.dataclass
class ResultadoQuotes:
    """Los quotes de un periodo. `precios` es lo unico que baja al calculo."""

    periodo: Periodo
    precios: dict[ClaveQuote, float]
    n_dias: dict[ClaveQuote, int]
    descartados_por_dias: list[Descarte] = dataclasses.field(default_factory=list)
    dias_presentes: list[date] = dataclasses.field(default_factory=list)

    @property
    def huecos(self) -> list[date]:
        """Dias del periodo sin ninguna observacion."""
        presentes = set(self.dias_presentes)
        return [d for d in self.periodo.dias_esperados if d not in presentes]

    @property
    def n_quotes(self) -> int:
        return len(self.precios)

    @property
    def n_descartados(self) -> int:
        return len(self.descartados_por_dias)


def quotes_del_periodo(
    observaciones: duckdb.DuckDBPyRelation,
    periodo: Periodo,
    parametros: ParametrosVentana,
    columna_precio: str = COLUMNA_LISTA,
) -> ResultadoQuotes:
    """Colapsa las observaciones diarias del periodo a un precio por quote.

    La mediana y no el promedio: un solo precio mal cargado desplaza el promedio
    y no mueve la mediana.

    Los quotes con menos de `minimo_dias_quote` dias observados se descartan pero
    se devuelven en `descartados_por_dias`, no se pierden en silencio: son la
    señal de que un comercio dejo de reportar.
    """
    if parametros.tipo != periodo.tipo:
        raise ValueError(
            f"parametros de ventana {parametros.tipo!r} aplicados a un periodo "
            f"{periodo.tipo!r} ({periodo.etiqueta})"
        )
    if columna_precio not in (COLUMNA_LISTA, COLUMNA_EFECTIVO):
        raise ValueError(f"columna de precio desconocida: {columna_precio!r}")

    sql = f"""
        SELECT
            id_comercio, id_sucursal, id_producto,
            median({columna_precio})  AS precio,
            count(DISTINCT fecha)     AS n_dias
        FROM obs
        WHERE fecha BETWEEN DATE '{periodo.inicio}' AND DATE '{periodo.fin}'
          AND {columna_precio} IS NOT NULL
          AND {columna_precio} > 0
        GROUP BY 1, 2, 3
    """
    filas = observaciones.query("obs", sql).fetchall()

    dias = observaciones.query(
        "obs",
        f"SELECT DISTINCT fecha FROM obs "
        f"WHERE fecha BETWEEN DATE '{periodo.inicio}' AND DATE '{periodo.fin}'",
    ).fetchall()

    precios: dict[ClaveQuote, float] = {}
    n_dias: dict[ClaveQuote, int] = {}
    descartados: list[Descarte] = []

    for comercio, sucursal, producto, precio, n in filas:
        clave: ClaveQuote = (str(comercio), str(sucursal), str(producto))
        n = int(n)
        n_dias[clave] = n
        if n < parametros.minimo_dias_quote:
            descartados.append(
                Descarte(
                    clave,
                    MOTIVO_POCOS_DIAS,
                    f"{n} dias observados, minimo {parametros.minimo_dias_quote}",
                )
            )
            continue
        precios[clave] = float(precio)

    return ResultadoQuotes(
        periodo=periodo,
        precios=precios,
        n_dias=n_dias,
        descartados_por_dias=descartados,
        dias_presentes=sorted(f[0] for f in dias),
    )


# --------------------------------------------------------------------------- #
# Encadenamiento entre periodos
# --------------------------------------------------------------------------- #


@dataclasses.dataclass(frozen=True)
class VariacionPeriodo:
    """La variacion entre dos periodos consecutivos del mismo tipo."""

    base: Periodo
    actual: Periodo
    indice: float

    def __post_init__(self) -> None:
        validar_encadenable(self.base, self.actual)

    @property
    def etiqueta(self) -> str:
        return f"{self.base.etiqueta} -> {self.actual.etiqueta}"

    @property
    def variacion_pct(self) -> float:
        return (self.indice - 1.0) * 100.0


def validar_encadenable(anterior: Periodo, actual: Periodo) -> None:
    """Falla si los dos periodos no se pueden comparar ni encadenar.

    Dos guardas:

    1. **Mismo tipo.** Cuatro variaciones semanales encadenadas no dan la
       variacion mensual: cada par tiene su propia muestra emparejada y los
       quotes que entran no son los mismos. Mezclarlas produce un numero que
       parece razonable y no significa nada.

    2. **Consecutivos.** Saltear un periodo mide una variacion de dos ventanas
       pero la etiqueta como una: el encadenado queda mal escalado.
    """
    if anterior.tipo != actual.tipo:
        raise ValueError(
            f"no se pueden encadenar periodos de tipo distinto: "
            f"{anterior.etiqueta} es {anterior.tipo} y {actual.etiqueta} es "
            f"{actual.tipo}. Semanal y mensual son series paralelas, cada una "
            f"calculada desde las observaciones diarias."
        )
    if actual.inicio != anterior.fin + timedelta(days=1):
        raise ValueError(
            f"periodos no consecutivos: {anterior.etiqueta} termina el "
            f"{anterior.fin} y {actual.etiqueta} arranca el {actual.inicio}"
        )


def serie_encadenada(
    variaciones: list[VariacionPeriodo], base: float = 100.0
) -> list[tuple[str, float]]:
    """Encadena variaciones consecutivas. Devuelve [(etiqueta, nivel), ...].

    El primer elemento es el nivel base del periodo inicial.
    """
    if not variaciones:
        return []

    for previa, siguiente in zip(variaciones, variaciones[1:]):
        if previa.actual != siguiente.base:
            raise ValueError(
                f"la serie tiene un salto: {previa.etiqueta} no encadena con "
                f"{siguiente.etiqueta}"
            )

    serie = [(variaciones[0].base.etiqueta, base)]
    nivel = base
    for v in variaciones:
        nivel = encadenar(nivel, v.indice)
        serie.append((v.actual.etiqueta, nivel))
    return serie


def _rango(desde: date, hasta: date) -> list[date]:
    return [desde + timedelta(days=i) for i in range((hasta - desde).days + 1)]
