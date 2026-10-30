"""Lista las operaciones críticas del cálculo, leyéndolas del código.

    python scripts/ruta_critica.py           # el índice, para leer en orden
    python scripts/ruta_critica.py --full    # con el bloque completo de cada una

Las diez operaciones donde se fabrica el número están marcadas en el código con
un bloque de seis campos. Este script las junta y las ordena. **El índice sale
del código y no de un documento aparte** a propósito: un documento se
desincroniza y nadie se entera, y el problema que esto viene a resolver es
justamente que lo que está escrito diga lo que el código hace.

Si falta una operación o sobra, este script lo dice: cuenta cuántas encontró
contra cuántas declaran ser.

El campo que importa es FALLA ASI. Un error en estas diez no tira una excepción:
devuelve un número plausible, y la única forma de encontrarlo es leer.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
DONDE = ("src", "scripts")
CAMPOS = ("QUE HACE", "FORMULA", "POR QUE ASI", "LO VERIFICA", "FALLA ASI")

# "CRITICO 3 de 10", y tolera el "CRITICO 7 de 10, la guarda" de los lugares donde
# una operacion tiene su control en otro archivo.
# Exige el marco de "===" para no confundir una mencion en prosa con un
# marcador de verdad. El propio archivo se saltea por el mismo motivo.
CABECERA = re.compile(r"=== CRITICO\s+(\d+)\s+de\s+(\d+)\s*===")
GUARDA = re.compile(r"=== GUARDA de CRITICO\s+(\d+)\s*===")


def bloques() -> list[dict]:
    salida: list[dict] = []
    for sub in DONDE:
        for archivo in sorted((RAIZ / sub).rglob("*.py")):
            if archivo.name == "ruta_critica.py":
                continue
            lineas = archivo.read_text(encoding="utf-8").splitlines()
            for i, linea in enumerate(lineas):
                m = CABECERA.search(linea)
                g = None if m else GUARDA.search(linea)
                if not m and not g:
                    continue
                campos: dict[str, str] = {}
                actual = None
                for siguiente in lineas[i + 1 : i + 30]:
                    texto = siguiente.lstrip().lstrip("#").strip()
                    if set(texto) <= {"=", " "} and texto:
                        break
                    encontrado = next(
                        (c for c in CAMPOS if texto.startswith(c)), None
                    )
                    if encontrado:
                        actual = encontrado
                        campos[actual] = texto[len(encontrado) :].strip()
                    elif actual and texto:
                        campos[actual] += " " + texto
                salida.append(
                    {
                        "n": int((m or g).group(1)),
                        "total": int(m.group(2)) if m else 0,
                        "guarda": g is not None,
                        "archivo": archivo.relative_to(RAIZ).as_posix(),
                        "linea": i + 1,
                        "campos": campos,
                    }
                )
    return sorted(salida, key=lambda b: (b["n"], b["archivo"]))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--full", action="store_true",
                        help="imprime los seis campos de cada operacion")
    args = parser.parse_args()

    encontrados = bloques()
    if not encontrados:
        print("No hay ninguna operacion marcada. Buscar 'CRITICO' en el codigo.")
        return 1

    operaciones = [b for b in encontrados if not b["guarda"]]
    guardas = [b for b in encontrados if b["guarda"]]
    declarado = operaciones[0]["total"]
    numeros = sorted({b["n"] for b in operaciones})

    print()
    print(f"RUTA CRITICA DEL INDICE  ({len(numeros)} de {declarado} operaciones)")
    print("=" * 78)
    for b in operaciones:
        print(f"\n{b['n']}/{b['total']}  {b['archivo']}:{b['linea']}")
        print(f"      {b['campos'].get('QUE HACE', '(sin QUE HACE)')}")
        if args.full:
            for campo in CAMPOS[1:]:
                if campo in b["campos"]:
                    print(f"      {campo:<12} {b['campos'][campo]}")

    if guardas:
        print()
        print("-" * 78)
        print("GUARDAS: cortan la corrida antes de dar un numero mal")
        for b in guardas:
            print(f"\n  de la {b['n']}  {b['archivo']}:{b['linea']}")
            print(f"      {b['campos'].get('QUE HACE', '(sin QUE HACE)')}")
            if args.full:
                for campo in CAMPOS[1:]:
                    if campo in b["campos"]:
                        print(f"      {campo:<12} {b['campos'][campo]}")

    faltan = [n for n in range(1, declarado + 1) if n not in numeros]
    print()
    print("=" * 78)
    if faltan:
        print(f"FALTAN marcadas las operaciones: {faltan}")
        return 1
    sin_verifica = [
        f"{b['n']} ({b['archivo']})"
        for b in encontrados
        if "LO VERIFICA" not in b["campos"]
    ]
    if sin_verifica:
        print(f"SIN test declarado: {', '.join(sin_verifica)}")
        return 1
    print(f"Las {declarado} estan marcadas y las {declarado} declaran el test que las verifica.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
