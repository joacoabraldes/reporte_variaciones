"""Lectura del bucket: inventario de dias y deteccion de huecos.

Un hueco no detectado no rompe nada: baja el `n_dias` de los quotes de ese
periodo, algunos caen abajo del minimo y salen de la muestra, y el indice sigue
devolviendo un numero plausible. Por eso los tests son sobre que el hueco se
**vea**, no sobre que se arregle.

No tocan la red: el cliente de GCS se inyecta.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from reporte.lectura import (
    ArchivoDia,
    LectorBucket,
    comercio_de_ruta,
    construir_inventario,
    fecha_de_ruta,
)


# --------------------------------------------------------------------------- #
# Cliente falso
# --------------------------------------------------------------------------- #


class _Blob:
    def __init__(self, name: str, size: int = 1_000, generation: int = 1) -> None:
        self.name = name
        self.size = size
        self.generation = generation


class _BlobDescargable(_Blob):
    def download_to_filename(self, ruta: str) -> None:
        Path(ruta).write_text(f"contenido de {self.name}", encoding="utf-8")


class _ClienteFalso:
    def __init__(self, nombres: list[str]) -> None:
        self._blobs = [_Blob(n) for n in nombres]

    def list_blobs(self, bucket, prefix: str = ""):
        return [b for b in self._blobs if b.name.startswith(prefix)]

    # bucket(nombre).blob(nombre).download_to_filename(ruta), como el de GCS.
    def bucket(self, nombre: str) -> "_ClienteFalso":
        return self

    def blob(self, nombre: str) -> _BlobDescargable:
        return _BlobDescargable(nombre)


def _ruta(fecha: date, comercio: str) -> str:
    return (
        f"staged/observaciones/anio={fecha.year:04d}/mes={fecha.month:02d}/"
        f"dia={fecha.day:02d}/sepa_1_comercio-sepa-{comercio}_{fecha}_09-05-10.parquet"
    )


def _archivo(fecha: date, comercio: str) -> ArchivoDia:
    return ArchivoDia(
        fecha=fecha, ruta=_ruta(fecha, comercio), id_comercio=comercio,
        tamanio=1_000, generacion=1,
    )


# --------------------------------------------------------------------------- #
# Parseo de rutas
# --------------------------------------------------------------------------- #


def test_saca_la_fecha_y_el_comercio_de_la_ruta():
    r = _ruta(date(2026, 8, 17), "15")
    assert fecha_de_ruta(r) == date(2026, 8, 17)
    assert comercio_de_ruta(r) == "15"


def test_una_ruta_sin_particion_no_rompe():
    assert fecha_de_ruta("staged/clasificacion/clasificacion.parquet") is None


# --------------------------------------------------------------------------- #
# Huecos: dias ausentes
# --------------------------------------------------------------------------- #


def test_un_dia_faltante_en_el_medio_se_detecta():
    """EL caso: el 15 no esta, y el rango va del 14 al 17."""
    archivos = [
        _archivo(date(2026, 8, d), c)
        for d in (14, 16, 17)
        for c in ("10", "15")
    ]
    inv = construir_inventario(archivos, date(2026, 8, 14), date(2026, 8, 17))

    assert inv.dias_faltantes == [date(2026, 8, 15)]
    assert inv.dias_presentes == [date(2026, 8, 14), date(2026, 8, 16), date(2026, 8, 17)]
    assert not inv.completo
    assert "DIAS AUSENTES" in inv.informe()
    assert "2026-08-15" in inv.informe()


def test_sin_huecos_el_inventario_esta_completo():
    archivos = [
        _archivo(date(2026, 8, d), c) for d in (14, 15, 16) for c in ("10", "15")
    ]
    inv = construir_inventario(archivos, date(2026, 8, 14), date(2026, 8, 16))
    assert inv.dias_faltantes == []
    assert inv.completo
    assert "ninguno" in inv.informe()


def test_los_dias_fuera_del_rango_no_entran():
    archivos = [_archivo(date(2026, 8, d), "10") for d in (13, 14, 15, 16)]
    inv = construir_inventario(archivos, date(2026, 8, 14), date(2026, 8, 15))
    assert inv.dias_presentes == [date(2026, 8, 14), date(2026, 8, 15)]


# --------------------------------------------------------------------------- #
# Huecos: el que no se ve
# --------------------------------------------------------------------------- #


def test_un_comercio_que_falta_un_dia_se_reporta():
    """El dia esta y el conteo de dias cierra, pero un comercio no reporto.

    Sus quotes pierden un dia de observacion y pueden caer abajo del minimo sin
    que nada avise. Es el hueco silencioso.
    """
    archivos = [_archivo(date(2026, 8, 14), c) for c in ("10", "15", "20")]
    archivos += [_archivo(date(2026, 8, 15), c) for c in ("10", "20")]  # falta el 15
    archivos += [_archivo(date(2026, 8, 16), c) for c in ("10", "15", "20")]
    inv = construir_inventario(archivos, date(2026, 8, 14), date(2026, 8, 16))

    assert inv.dias_faltantes == [], "los tres dias estan presentes"
    assert inv.comercios_faltantes == {date(2026, 8, 15): {"15"}}
    assert not inv.completo, "no esta completo aunque no falte ningun dia"
    assert "DIAS INCOMPLETOS" in inv.informe()


# --------------------------------------------------------------------------- #
# Listado contra el bucket (con cliente falso)
# --------------------------------------------------------------------------- #


def test_el_inventario_lee_el_bucket_y_arma_el_rango(tmp_path):
    nombres = [
        _ruta(date(2026, 8, d), c) for d in (14, 15, 16) for c in ("10", "15")
    ]
    lector = LectorBucket(cache=tmp_path, cliente=_ClienteFalso(nombres))
    inv = lector.inventario(date(2026, 8, 14), date(2026, 8, 16))

    assert len(inv.dias_presentes) == 3
    assert inv.comercios == {"10", "15"}
    assert inv.tamanio_total == 6_000


def test_los_comercios_excluidos_no_se_listan(tmp_path):
    """Farmacias y estaciones de servicio: no se bajan ni se miran."""
    nombres = [_ruta(date(2026, 8, 14), c) for c in ("10", "15", "24")]
    lector = LectorBucket(cache=tmp_path, cliente=_ClienteFalso(nombres))

    inv = lector.inventario(date(2026, 8, 14), date(2026, 8, 14),
                            comercios_excluidos={"24"})
    assert inv.comercios == {"10", "15"}

    sin_filtro = lector.inventario(date(2026, 8, 14), date(2026, 8, 14))
    assert sin_filtro.comercios == {"10", "15", "24"}


def test_pedir_un_rango_vacio_falla_claro(tmp_path):
    lector = LectorBucket(cache=tmp_path, cliente=_ClienteFalso([]))
    with pytest.raises(FileNotFoundError, match="no hay observaciones"):
        lector.observaciones(date(2026, 9, 1), date(2026, 9, 7))


# --------------------------------------------------------------------------- #
# Cache
# --------------------------------------------------------------------------- #


def test_la_clave_de_cache_cambia_si_cambia_la_generacion(tmp_path):
    """Si el repo de captura reprocesa un dia, el extracto viejo no sirve mas."""
    lector = LectorBucket(cache=tmp_path, cliente=_ClienteFalso([]))
    a1 = [_archivo(date(2026, 8, 14), "10")]
    a2 = [dataclass_replace(a1[0], generacion=2)]

    cols = ("fecha", "precio_lista")
    assert lector._clave_cache(a1, cols, None) != lector._clave_cache(a2, cols, None)


def test_la_clave_de_cache_cambia_si_cambia_el_filtro(tmp_path):
    lector = LectorBucket(cache=tmp_path, cliente=_ClienteFalso([]))
    a = [_archivo(date(2026, 8, 14), "10")]
    cols = ("fecha", "precio_lista")
    assert lector._clave_cache(a, cols, {"1"}) != lector._clave_cache(a, cols, {"2"})
    assert lector._clave_cache(a, cols, None) != lector._clave_cache(a, cols, {"1"})


def test_la_clave_de_cache_es_estable(tmp_path):
    """Mismo origen y mismo filtro: mismo extracto, no se vuelve a bajar."""
    lector = LectorBucket(cache=tmp_path, cliente=_ClienteFalso([]))
    a = [_archivo(date(2026, 8, 14), "10"), _archivo(date(2026, 8, 14), "15")]
    cols = ("fecha", "precio_lista")
    assert lector._clave_cache(a, cols, {"1", "2"}) == lector._clave_cache(
        list(reversed(a)), cols, {"2", "1"}
    )


def dataclass_replace(obj, **cambios):
    import dataclasses

    return dataclasses.replace(obj, **cambios)


# --------------------------------------------------------------------------- #
# Tope de memoria de DuckDB
# --------------------------------------------------------------------------- #


def test_la_conexion_tiene_tope_de_memoria_y_de_hilos(tmp_path, monkeypatch):
    """Sin tope DuckDB toma el 80% de la RAM.

    El 20/10/2026 el recalculo de agosto->septiembre murio por memoria y se
    llevo puesto el editor. Las variables son las mismas del repo de captura.
    """
    from reporte.lectura import _conectar

    monkeypatch.setenv("PRECIOS_MEMORIA_DUCKDB", "1GB")
    monkeypatch.setenv("PRECIOS_HILOS_DUCKDB", "2")
    con = _conectar(tmp_path)
    memoria = con.execute("SELECT current_setting('memory_limit')").fetchone()[0]
    hilos = con.execute("SELECT current_setting('threads')").fetchone()[0]
    assert "GiB" in memoria or "MiB" in memoria
    assert float(memoria.split()[0]) < 1.5 if "GiB" in memoria else True
    assert int(hilos) == 2


def test_sin_variables_usa_los_defaults(tmp_path, monkeypatch):
    from reporte.lectura import HILOS_DEFAULT, _conectar

    monkeypatch.delenv("PRECIOS_MEMORIA_DUCKDB", raising=False)
    monkeypatch.delenv("PRECIOS_HILOS_DUCKDB", raising=False)
    con = _conectar(tmp_path)
    memoria = con.execute("SELECT current_setting('memory_limit')").fetchone()[0]
    assert float(memoria.split()[0]) < 4.5      # 4GB, no el 80% de la maquina
    assert int(con.execute("SELECT current_setting('threads')").fetchone()[0]) == HILOS_DEFAULT


# --------------------------------------------------------------------------- #
# Prefijos: una carpeta no es un prefijo de texto
# --------------------------------------------------------------------------- #


def test_baja_las_fotos_de_combustibles_al_mismo_arbol_que_captura(tmp_path):
    """`combustibles/fecha=Y/vigentes.csv`: lo que lee `online_indice.cargar_combustibles`."""
    nombres = [
        "staged/combustibles/fecha=2026-11-03/vigentes.csv",
        "staged/combustibles/fecha=2026-11-03/notas.txt",
        "staged/relevamiento_online/sitio=easy/fecha=2026-11-03/datos.json",
    ]
    lector = LectorBucket(cache=tmp_path, cliente=_ClienteFalso(nombres))

    raiz = lector.bajar_fotos_combustibles()

    assert raiz == tmp_path / "etapa5" / "datos_oficiales" / "combustibles"
    foto = raiz / "fecha=2026-11-03" / "vigentes.csv"
    assert foto.read_text(encoding="utf-8") == (
        "contenido de staged/combustibles/fecha=2026-11-03/vigentes.csv"
    )
    assert not (raiz / "fecha=2026-11-03" / "notas.txt").exists()


def test_las_planillas_del_indec_son_la_ultima_copia_de_cada_una(tmp_path):
    """Captura no guarda una copia igual a la anterior, asi que cada planilla
    puede tener su ultima version en una fecha distinta."""
    nombres = [
        "staged/indec/fecha=2026-10-21/sh_ipc_aperturas.xls",
        "staged/indec/fecha=2026-10-21/sh_ipc_precios_promedio.xls",
        "staged/indec/fecha=2026-11-13/sh_ipc_aperturas.xls",
    ]
    lector = LectorBucket(cache=tmp_path, cliente=_ClienteFalso(nombres))

    planillas = lector.bajar_planillas_indec()

    raiz = tmp_path / "etapa5" / "indec"
    assert planillas == {
        "sh_ipc_aperturas.xls": raiz / "fecha=2026-11-13" / "sh_ipc_aperturas.xls",
        "sh_ipc_precios_promedio.xls": raiz / "fecha=2026-10-21" / "sh_ipc_precios_promedio.xls",
    }
    assert all(p.exists() for p in planillas.values())


def test_una_foto_ya_bajada_no_se_vuelve_a_bajar(tmp_path):
    """Captura nunca pisa una foto publicada: la del cache es la misma."""
    foto = tmp_path / "etapa5" / "datos_oficiales" / "combustibles" / "fecha=2026-11-03" / "vigentes.csv"
    foto.parent.mkdir(parents=True)
    foto.write_text("la que ya estaba", encoding="utf-8")
    lector = LectorBucket(cache=tmp_path, cliente=_ClienteFalso(
        ["staged/combustibles/fecha=2026-11-03/vigentes.csv"]))

    lector.bajar_fotos_combustibles()

    assert foto.read_text(encoding="utf-8") == "la que ya estaba"


def test_una_carpeta_no_trae_las_que_empiezan_igual(tmp_path):
    """El 20/10/2026 "staged/clasificacion" trajo tambien la clasificacion online.

    GCS no tiene carpetas: sin la barra al final, el prefijo matchea cualquier
    nombre que empiece igual.
    """
    nombres = [
        "staged/clasificacion/clasificacion.parquet",
        "staged/clasificacion_online/clasificacion_online.parquet",
    ]
    lector = LectorBucket(cache=tmp_path, cliente=_ClienteFalso(nombres))
    for prefijo in ("staged/clasificacion", "staged/clasificacion/"):
        assert [b.name for b in lector._listar_carpeta(prefijo)] == [
            "staged/clasificacion/clasificacion.parquet"
        ]
