"""Los datos del dashboard: tres tablas armadas desde toda la historia.

El dashboard no calcula nada: lee estas tablas (`scripts/datos_dashboard.py`
las escribe). Cada periodo toma su calculo mas reciente y dice cuando se
calculo (`historial.lo_ultimo_por_periodo`); la historia completa queda en
`salida/historial/`.

    periodos       una fila por semana o mes y serie: global, sin online, cada
                   rubro (division del INDEC) y, si se corrio, por region
    clases         cada clase de cada rubro, por periodo
    contra_indec   por mes, nuestro numero contra lo comparable del INDEC

Un valor sin dato queda vacio, nunca en cero: cero diria que no se movio.
"""

from __future__ import annotations

import calendar
from datetime import date

from .agregacion import division
from .historial import lo_ultimo_por_periodo
from .series import SERIE_GLOBAL, SERIE_POR_REGION, SERIE_SIN_ONLINE, division_de_serie

TIPO_DE_SERIE = {SERIE_GLOBAL: "global", SERIE_SIN_ONLINE: "sin_online",
                 SERIE_POR_REGION: "por_region"}
_ORDEN_TIPO = {"global": 0, "sin_online": 1, "por_region": 2, "rubro": 3}


def ventana_de(periodo: str) -> tuple[date, date]:
    """`"2026-W43"` -> lunes a domingo de esa semana ISO; `"2026-09"` -> el mes."""
    if "-W" in periodo:
        anio, semana = periodo.split("-W")
        lunes = date.fromisocalendar(int(anio), int(semana), 1)
        return lunes, date.fromordinal(lunes.toordinal() + 6)
    anio, mes = map(int, periodo.split("-"))
    return date(anio, mes, 1), date(anio, mes, calendar.monthrange(anio, mes)[1])


def _num(valor) -> float | None:
    return None if valor in ("", None) else float(valor)


def _ventana(periodo: str) -> dict:
    desde, hasta = ventana_de(periodo)
    return {"desde": desde.isoformat(), "hasta": hasta.isoformat()}


def tabla_periodos(
    historial: dict[str, list[dict]], pesos_division: dict[str, float] | None = None
) -> list[dict]:
    """`pesos_division` (% del IPC de cada division) deja decir cuanto de la
    division mide cada rubro: Vivienda mide el 6%, solo materiales."""
    pesos_division = pesos_division or {}
    vigentes = lo_ultimo_por_periodo(historial.get("series", []), ("ventana", "mes", "serie"))
    filas = []
    for f in vigentes:
        div = division_de_serie(f["serie"])
        tipo = "rubro" if div else TIPO_DE_SERIE.get(f["serie"], f["serie"])
        filas.append({
            "ventana": f["ventana"], "periodo": f["mes"], "base": f["base"],
            **_ventana(f["mes"]), "tipo": tipo, "division": div or "",
            "serie": f["serie"], "variacion_pct": _num(f["variacion_pct"]),
            "nivel": _num(f["nivel"]), "cobertura_ipc_pct": _num(f["cobertura_ipc_pct"]),
            "peso_division_pct": pesos_division.get(div) if div else None,
            "calculado_el": f["calculado_el"],
        })
    return sorted(filas, key=lambda f: (f["ventana"], f["periodo"],
                                        _ORDEN_TIPO.get(f["tipo"], 9), f["division"]))


def tabla_clases(historial: dict[str, list[dict]], nombres: dict[str, str]) -> list[dict]:
    clases = [f for f in historial.get("detalle", []) if f["nivel"] == "clase"]
    vigentes = lo_ultimo_por_periodo(clases, ("ventana", "mes", "codigo"))
    filas = [{
        "ventana": f["ventana"], "periodo": f["mes"], **_ventana(f["mes"]),
        "division": division(f["codigo"]), "clase": f["codigo"],
        "nombre": nombres.get(f["codigo"], f["codigo"]),
        "variacion_pct": (float(f["indice"]) - 1) * 100, "peso": _num(f["peso"]),
        "calculado_el": f["calculado_el"],
    } for f in vigentes]
    return sorted(filas, key=lambda f: (f["ventana"], f["periodo"], f["clase"]))


def tabla_contra_indec(historial: dict[str, list[dict]]) -> list[dict]:
    vigentes = lo_ultimo_por_periodo(historial.get("contra_indec", []),
                                     ("mes", "tipo", "codigo"))
    filas = [{
        "periodo": f["mes"], "base": f["base"], "tipo": f["tipo"], "codigo": f["codigo"],
        "nombre": f["nombre"], "nuestro_pct": _num(f["nuestro_pct"]),
        "indec_pct": _num(f["indec_pct"]), "diferencia": _num(f["diferencia"]),
        "nota": f["nota"], "calculado_el": f["calculado_el"],
    } for f in vigentes]
    return sorted(filas, key=lambda f: (f["periodo"], f["tipo"], f["codigo"]))
