"""Lectura del bucket. **Solo lectura**: este repo nunca escribe en GCS.

Tres decisiones que conviene entender antes de tocar esto:

**1. Se baja a cache local en vez de leer directo de `gs://`.** DuckDB sabe leer
Parquet remoto, pero su soporte de GCS va por la API S3-compatible y necesita
claves HMAC; no entiende Application Default Credentials. Verificado: con ADC
devuelve 403. Como la alternativa era hardcodear una clave, se baja con
`google-cloud-storage` (que si usa ADC) y DuckDB lee del disco.

**2. El "predicate pushdown por fecha" lo da el layout, no el motor.** Las
observaciones estan particionadas `anio=/mes=/dia=`, asi que pedir una semana
toca 7 carpetas y no las 22 que hay. No se lee lo que no se pidio.

**3. Lo que se cachea es el extracto filtrado, no el Parquet crudo.** Un dia
crudo son ~190 MB y de ahi sobrevive una fraccion minima: 987 productos
clasificados y media docena de columnas. Guardar el crudo llenaria el disco para
releer siempre lo mismo. El extracto se invalida solo si cambia la generacion de
algun blob de origen.

**Los huecos se reportan, no se rellenan.** Un dia que falta baja el `n_dias` de
todos los quotes de ese periodo y puede tirarlos abajo del minimo, sacandolos de
la muestra sin que nada falle. El indice sigue dando un numero plausible. Por eso
`Inventario` distingue tres cosas distintas: dias ausentes, dias presentes pero
con comercios faltantes, y dias completos.
"""

from __future__ import annotations

import dataclasses
import hashlib
import logging
import re
import shutil
import tempfile
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import duckdb

logger = logging.getLogger(__name__)

BUCKET_DEFAULT = "outlier-archivos-precios"

PREFIJO_OBSERVACIONES = "staged/observaciones"
PREFIJO_CLASIFICACION = "staged/clasificacion"
PREFIJO_COMERCIOS_EXCLUIDOS = "staged/comercios_excluidos"
PREFIJO_QUOTES_MENSUALES = "staged/quotes_mensuales"
PREFIJO_CATALOGO = "staged/catalogo_productos"

RAIZ_REPO = Path(__file__).resolve().parents[2]
CACHE_DEFAULT = RAIZ_REPO / ".cache"

# `staged/observaciones/anio=2026/mes=08/dia=17/sepa_1_comercio-sepa-4_...parquet`
RE_PARTICION = re.compile(r"anio=(\d{4})/mes=(\d{2})/dia=(\d{2})")
RE_COMERCIO = re.compile(r"comercio-sepa-(\d+)", re.IGNORECASE)

# Lo minimo para calcular el indice. Se pide explicito y no `SELECT *` porque el
# crudo tiene 21 columnas y las descripciones son lo mas pesado de todo.
COLUMNAS_INDICE = (
    "fecha",
    "id_comercio",
    "id_sucursal",
    "id_producto",
    "precio_lista",
    "precio_efectivo",
    "provincia",
)


@dataclasses.dataclass(frozen=True)
class ArchivoDia:
    """Un Parquet de observaciones: un comercio, un dia."""

    fecha: date
    ruta: str
    id_comercio: str | None
    tamanio: int
    generacion: int


@dataclasses.dataclass
class Inventario:
    """Que dias hay en el bucket dentro del rango pedido, y que falta."""

    desde: date
    hasta: date
    archivos: dict[date, list[ArchivoDia]]

    @property
    def dias_pedidos(self) -> list[date]:
        n = (self.hasta - self.desde).days + 1
        return [self.desde + timedelta(days=i) for i in range(n)]

    @property
    def dias_presentes(self) -> list[date]:
        return sorted(d for d, a in self.archivos.items() if a)

    @property
    def dias_faltantes(self) -> list[date]:
        return [d for d in self.dias_pedidos if not self.archivos.get(d)]

    @property
    def comercios(self) -> set[str]:
        """Todos los comercios vistos en el rango."""
        return {
            a.id_comercio
            for archivos in self.archivos.values()
            for a in archivos
            if a.id_comercio
        }

    @property
    def comercios_faltantes(self) -> dict[date, set[str]]:
        """Dias presentes a los que les falta algun comercio del rango.

        Es el hueco que no se ve: el dia esta, el conteo de dias cierra, pero un
        comercio entero no reporto. Sus quotes pierden un dia de observacion y
        pueden caer abajo del minimo sin que nada avise.
        """
        todos = self.comercios
        faltan: dict[date, set[str]] = {}
        for d in self.dias_presentes:
            presentes = {a.id_comercio for a in self.archivos[d] if a.id_comercio}
            if todos - presentes:
                faltan[d] = todos - presentes
        return faltan

    @property
    def completo(self) -> bool:
        return not self.dias_faltantes and not self.comercios_faltantes

    @property
    def tamanio_total(self) -> int:
        return sum(a.tamanio for ar in self.archivos.values() for a in ar)

    def informe(self) -> str:
        """Texto para el reporte de diagnostico."""
        lineas = [
            f"rango pedido      {self.desde} .. {self.hasta} "
            f"({len(self.dias_pedidos)} dias)",
            f"dias presentes    {len(self.dias_presentes)}",
            f"comercios         {len(self.comercios)}",
            f"volumen           {self.tamanio_total / 1e6:,.0f} MB",
        ]
        if self.dias_faltantes:
            lineas.append(
                f"DIAS AUSENTES     {len(self.dias_faltantes)}: "
                + ", ".join(str(d) for d in self.dias_faltantes)
            )
        else:
            lineas.append("dias ausentes     ninguno")

        if self.comercios_faltantes:
            lineas.append(f"DIAS INCOMPLETOS  {len(self.comercios_faltantes)}:")
            for d, faltan in sorted(self.comercios_faltantes.items()):
                lineas.append(f"                  {d}: falta comercio " + ", ".join(sorted(faltan)))
        else:
            lineas.append("dias incompletos  ninguno")
        return "\n".join(lineas)


def fecha_de_ruta(ruta: str) -> date | None:
    """Saca la fecha de la particion Hive. `None` si la ruta no la tiene."""
    m = RE_PARTICION.search(ruta.replace("\\", "/"))
    if not m:
        return None
    return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))


def comercio_de_ruta(ruta: str) -> str | None:
    m = RE_COMERCIO.search(ruta)
    return m.group(1) if m else None


def construir_inventario(
    archivos: list[ArchivoDia], desde: date, hasta: date
) -> Inventario:
    """Agrupa por fecha. Puro: se testea sin tocar la red."""
    por_dia: dict[date, list[ArchivoDia]] = {}
    for a in archivos:
        if desde <= a.fecha <= hasta:
            por_dia.setdefault(a.fecha, []).append(a)
    return Inventario(desde=desde, hasta=hasta, archivos=por_dia)


def _sql_lista(valores) -> str:
    """Lista SQL de literales, con las comillas escapadas."""
    return ", ".join("'" + str(v).replace("'", "''") + "'" for v in sorted(valores))


class LectorBucket:
    """Acceso de solo lectura al bucket, con cache local de extractos.

    La autenticacion es por Application Default Credentials: no hay nada de
    credenciales en el codigo ni en la config. `cliente` se puede inyectar para
    testear sin red.
    """

    def __init__(
        self,
        bucket: str = BUCKET_DEFAULT,
        cache: Path | None = None,
        cliente=None,
    ) -> None:
        self.bucket_nombre = bucket
        self.cache = Path(cache) if cache else CACHE_DEFAULT
        self.cache.mkdir(parents=True, exist_ok=True)
        self._cliente = cliente
        self._con = duckdb.connect()

    # -- acceso al bucket --------------------------------------------------- #

    @property
    def cliente(self):
        if self._cliente is None:
            from google.cloud import storage

            self._cliente = storage.Client()
        return self._cliente

    def _listar(self, prefijo: str) -> list:
        return list(self.cliente.list_blobs(self.bucket_nombre, prefix=prefijo))

    def inventario(
        self,
        desde: date,
        hasta: date,
        comercios_excluidos: set[str] | None = None,
    ) -> Inventario:
        """Que dias de observaciones hay entre dos fechas.

        Lista mes a mes en vez del prefijo entero: pedir una semana de agosto no
        tiene por que enumerar julio.
        """
        excluidos = comercios_excluidos or set()
        archivos: list[ArchivoDia] = []

        for anio, mes in sorted({(d.year, d.month) for d in _rango(desde, hasta)}):
            prefijo = f"{PREFIJO_OBSERVACIONES}/anio={anio:04d}/mes={mes:02d}/"
            for blob in self._listar(prefijo):
                if not blob.name.endswith(".parquet"):
                    continue
                fecha = fecha_de_ruta(blob.name)
                if fecha is None or not (desde <= fecha <= hasta):
                    continue
                comercio = comercio_de_ruta(blob.name)
                if comercio and comercio in excluidos:
                    continue
                archivos.append(
                    ArchivoDia(
                        fecha=fecha,
                        ruta=blob.name,
                        id_comercio=comercio,
                        tamanio=blob.size or 0,
                        generacion=blob.generation or 0,
                    )
                )

        inv = construir_inventario(archivos, desde, hasta)
        if inv.dias_faltantes:
            logger.warning(
                "faltan %d dias en el rango: %s",
                len(inv.dias_faltantes),
                ", ".join(str(d) for d in inv.dias_faltantes),
            )
        if inv.comercios_faltantes:
            logger.warning(
                "%d dias presentes tienen comercios faltantes",
                len(inv.comercios_faltantes),
            )
        return inv

    # -- extractos ---------------------------------------------------------- #

    def _clave_cache(
        self, archivos: list[ArchivoDia], columnas: tuple[str, ...], productos
    ) -> str:
        """Hash del contenido de origen + el filtro pedido.

        Incluye la generacion de cada blob: si el repo de captura reprocesa un
        dia, el extracto viejo deja de servir y se rearma solo.
        """
        h = hashlib.sha256()
        for a in sorted(archivos, key=lambda x: x.ruta):
            h.update(f"{a.ruta}:{a.generacion}".encode())
        h.update(("|".join(columnas)).encode())
        if productos:
            for p in sorted(productos):
                h.update(str(p).encode())
        return h.hexdigest()[:16]

    def _extracto_dia(
        self,
        fecha: date,
        archivos: list[ArchivoDia],
        columnas: tuple[str, ...],
        productos: set[str] | None,
    ) -> Path:
        """Baja el dia, lo filtra y devuelve el Parquet chico. Cachea el resultado."""
        clave = self._clave_cache(archivos, columnas, productos)
        destino = self.cache / f"obs_{fecha.isoformat()}_{clave}.parquet"
        if destino.exists():
            logger.debug("cache hit %s", destino.name)
            return destino

        tmp = Path(tempfile.mkdtemp(prefix=f"obs_{fecha.isoformat()}_"))
        try:
            locales: list[str] = []
            bucket = self.cliente.bucket(self.bucket_nombre)
            for a in archivos:
                local = tmp / Path(a.ruta).name
                bucket.blob(a.ruta).download_to_filename(str(local))
                locales.append(local.as_posix())
            logger.info(
                "dia %s bajado (%d archivos, %.0f MB)",
                fecha,
                len(locales),
                sum(a.tamanio for a in archivos) / 1e6,
            )

            filtro = ""
            if productos:
                filtro = f"WHERE id_producto IN ({_sql_lista(productos)})"
            self._con.execute(
                f"COPY (SELECT {', '.join(columnas)} "
                f"FROM read_parquet([{_sql_lista(locales)}]) {filtro}) "
                f"TO '{destino.as_posix()}' (FORMAT PARQUET, COMPRESSION ZSTD)"
            )
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        return destino

    def observaciones(
        self,
        desde: date,
        hasta: date,
        columnas: tuple[str, ...] = COLUMNAS_INDICE,
        productos: set[str] | None = None,
        comercios_excluidos: set[str] | None = None,
        inventario: Inventario | None = None,
    ) -> duckdb.DuckDBPyRelation:
        """Observaciones diarias del rango, ya filtradas.

        `productos` recorta a los que estan clasificados: sin eso se bajan y
        guardan decenas de miles de productos que el indice no mira.
        """
        inv = inventario or self.inventario(desde, hasta, comercios_excluidos)
        if not inv.dias_presentes:
            raise FileNotFoundError(
                f"no hay observaciones entre {desde} y {hasta} en "
                f"gs://{self.bucket_nombre}/{PREFIJO_OBSERVACIONES}"
            )

        extractos = [
            self._extracto_dia(d, inv.archivos[d], columnas, productos).as_posix()
            for d in inv.dias_presentes
        ]
        return self._con.sql(f"SELECT * FROM read_parquet([{_sql_lista(extractos)}])")

    # -- tablas chicas ------------------------------------------------------ #

    def _bajar_prefijo(self, prefijo: str, subdir: str) -> list[str]:
        """Baja un prefijo entero a cache. Para tablas chicas (dimensiones)."""
        destino = self.cache / subdir
        destino.mkdir(parents=True, exist_ok=True)
        bucket = self.cliente.bucket(self.bucket_nombre)
        locales: list[str] = []
        for blob in self._listar(prefijo):
            if not blob.name.endswith(".parquet"):
                continue
            local = destino / f"{blob.generation}_{Path(blob.name).name}"
            if not local.exists():
                bucket.blob(blob.name).download_to_filename(str(local))
            locales.append(local.as_posix())
        if not locales:
            raise FileNotFoundError(
                f"no hay nada en gs://{self.bucket_nombre}/{prefijo}"
            )
        return locales

    def clasificacion(self) -> duckdb.DuckDBPyRelation:
        """`id_producto -> categoria -> clase COICOP`.

        La publica el repo de captura (`precios.cli publicar-clasificacion`).
        Sin esta tabla no hay forma de agrupar los quotes y no hay indice.
        """
        rutas = self._bajar_prefijo(PREFIJO_CLASIFICACION, "clasificacion")
        return self._con.sql(f"SELECT * FROM read_parquet([{_sql_lista(rutas)}])")

    def productos_clasificados(self) -> set[str]:
        """Los `id_producto` que tienen categoria. Es el filtro de lectura."""
        return {r[0] for r in self.clasificacion().project("id_producto").fetchall()}

    def comercios_excluidos(self) -> dict[str, str]:
        """`id_comercio -> motivo` de los informantes que no entran al indice.

        Farmacias y tiendas de estacion de servicio: SEPA obliga a informar a
        todo comercio de consumo masivo, no solo a supermercados. El piloto es
        de alimentos, asi que aportan ruido en vez de cobertura.

        Sale del bucket y no de una copia local para que no se desincronice: la
        decision se cambia en el repo de captura y se republica.
        """
        rutas = self._bajar_prefijo(PREFIJO_COMERCIOS_EXCLUIDOS, "comercios_excluidos")
        filas = self._con.sql(
            f"SELECT id_comercio, motivo FROM read_parquet([{_sql_lista(rutas)}])"
        ).fetchall()
        return {str(c): m for c, m in filas}

    def quotes_mensuales(self, anio: int, mes: int) -> duckdb.DuckDBPyRelation:
        """Quotes ya colapsados por el repo de captura.

        No los usa el camino semanal, que necesita el detalle diario. Sirven para
        reconciliar: un mes calculado aca tiene que dar identico a esto.
        """
        prefijo = f"{PREFIJO_QUOTES_MENSUALES}/anio={anio:04d}/mes={mes:02d}/"
        rutas = self._bajar_prefijo(prefijo, f"quotes_{anio:04d}{mes:02d}")
        return self._con.sql(f"SELECT * FROM read_parquet([{_sql_lista(rutas)}])")

    def cerrar(self) -> None:
        self._con.close()


def _rango(desde: date, hasta: date) -> list[date]:
    return [desde + timedelta(days=i) for i in range((hasta - desde).days + 1)]
