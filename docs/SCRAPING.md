# Scraping de catálogos online: qué agrega y qué no

Medido con `scripts/sondeo_online.py` sobre dos cadenas, contra lo que esas
mismas cadenas le declararon a SEPA el 9 de octubre de 2026.

La pregunta que contesta: **¿scrapear las cadenas agrega algo que SEPA no tenga?**

## El resultado, y por qué hacían falta dos cadenas

| | DIA | Vea |
|---|---|---|
| `id_comercio` en SEPA | 15 | 9 |
| sucursales que informan | 975 | 268 |
| EAN online que están en SEPA | **94,7 %** | **68,8 %** |
| precio online dentro del rango de sus sucursales | 93,3 % | 98,5 % |
| precio online igual a la mediana de sus sucursales | **86,3 %** | **39,7 %** |
| p10 / p90 de la diferencia contra esa mediana | 0,00 % / 0,00 % | −7,17 % / +4,57 % |

Sobre 285 y 282 EAN respectivamente, de doce búsquedas de alimentos.

**Con una sola cadena la conclusión habría sido equivocada.** DIA declara a SEPA
casi todo lo que vende online y al mismo precio: scrapear DIA no agrega nada.
Vea declara dos tercios y a su propio precio: ahí sí hay diferencia.

## Qué son los EAN que no matchean

De los 282 de Vea:

| | cantidad | qué significa |
|---|---|---|
| Vea los declara a SEPA | 194 · 68,8 % | ya los tenemos |
| existen en SEPA por otra cadena | 50 · 17,7 % | el producto está en SEPA; Vea no lo declara, o lo declara con otro código |
| no están en SEPA por nadie | 38 · 13,5 % | esto es lo único que el scraping agrega de verdad |

Esa fila del medio es un hallazgo sobre la fuente, no sobre el scraping: **hay
productos que una cadena vende online y no le informa a SEPA**. No se puede
distinguir desde acá si es incumplimiento o un código distinto para el mismo
producto; para separarlo habría que cruzar por descripción y marca.

## El precio online es un precio declarado, no otro precio

En las dos cadenas el precio online cae dentro del rango de sus sucursales en
más del 93 % de los casos. No es un canal con precios propios: es uno de los
precios que la cadena ya informa.

Lo que cambia entre cadenas es **a qué precio del rango se pega**. DIA pone
online la mediana exacta de sus 975 sucursales el 86,3 % de las veces, con una
dispersión típica entre sucursales de 6,65 %, así que pegar la mediana no es
casualidad. Vea sólo el 39,7 %, y se mueve entre −7 % y +4,6 %.

### Farmacity: el mismo precio, más promociones que SEPA no ve

Farmacity es la única tienda no-súper del relevamiento que también informa a SEPA
(es el informante 24; el índice la excluye por surtido chico). Calibrada el
17/10/2026 contra lo que declaró el 06/10, con términos de farmacia y perfumería:

| | |
|---|---|
| EAN online que declara a SEPA | **74 de 280 · 26%** (perfumería y limpieza; medicamentos no) |
| precio online igual al declarado (±0,5%) | **46 · 62%** |
| online más barato que todas sus sucursales | **25 · 34%**, mediana −35% |
| de esos, precio de lista de la web = lista de SEPA | **16 de 25** |
| productos con promo declarada a SEPA | **0** |

El patrón es claro: la web tiene el mismo precio de lista que SEPA, y encima aplica
promociones de 35-40% que Farmacity no le declara a SEPA. Las diferencias son
redondas (−35,0%, −40,0%), que es la firma de una promo y no de inflación entre el
6 y el 17.

Dos cosas que salen de acá. **Farmacity informa a SEPA de forma intermitente**: el
6/10 sí, el 1, 9, 12 y 13 no. Y **el problema de las promociones no es de
Rapsodia**: el precio que cobra una web mezcla descuentos que el canal declarado no
tiene. Las fotos online guardan los dos precios (`precio` y `precio_lista`), así que
se puede medir cada serie por separado cuando se decida cuál va al índice.

## Las trampas de la fuente

**`ListPrice` viene en otra unidad, y depende de la cadena.** VTEX devuelve
`Price` y `ListPrice`. Medido sobre los catálogos guardados:

| | items | `ListPrice` = 100 × el precio |
|---|---|---|
| Vea | 288 | **288, todos** |
| DIA | 287 | 0 |

En Cencosud la lista viene multiplicada por 100: 2.890 de precio y 289.000 de
lista para la misma leche de 1 litro. Tomar `ListPrice` da un precio cien veces
mayor y sigue pareciendo un número. Se usa `Price`, que es el que paga el
cliente, y la sonda cuenta e informa cuántos items traen la lista en otra unidad.

En DIA los dos campos coinciden en 263 de 287 items; en los otros 24 la lista es
mayor, que es un descuento normal.

**No todas las cadenas son VTEX.** Verificado a mano, anotado en
`config/cadenas.yaml`:

| cadena | catálogo |
|---|---|
| DIA, Vea, Jumbo, Disco | VTEX: un pedido a `/api/catalog_system/pub/products/search/{termino}` devuelve EAN, marca, categoría y los dos precios |
| Coto | **no**: `cotodigital3` redirige a `coto.com.ar` y esa ruta devuelve el HTML de una aplicación de página única |
| La Anónima | **no**: redirige a una página normal, sin catálogo en esa ruta |

**El `id_comercio` de SEPA no es adivinable.** Es un código de informante. Sale
de leer `comercio_nombre` en las observaciones de un día. Los identificados
están en `config/cadenas.yaml`; Jumbo y Disco faltan, y hay que ver si Cencosud
informa por bandera o todo junto.

## Por qué medir las cadenas si están obligadas a informar

Las cadenas de supermercado **tienen** que declarar a SEPA, así que scrapearlas
no iba a agregar productos: el 94,7 % era previsible. Lo que esa medición sirvió
para establecer es otra cosa, y hacía falta antes de confiar en cualquier precio
scrapeado: **si un precio publicado en una web es un precio real**. SEPA es la
única verdad conocida contra la cual compararlo, y el resultado es que sí — cae
dentro del rango declarado en más del 93 % de los casos.

Sin esa calibración, se scrapea una fuente sin contraparte y no hay forma de
saber si el número significa algo.

## Lo que SEPA no cubre, y ahí sí hay que scrapear

La góndola de consumo masivo es el 37,4 % del IPC y SEPA ya la da. El otro
62,6 % no está ni puede estar. De eso, el 23,1 % es comercio online que no es
supermercado, y es lo único que se puede leer sin mandar gente a la calle.

**Catorce sitios y un dataset oficial**, todos verificados a mano y todos sin
credenciales ni costo. Los pesos son nacionales, calculados con
`config/ponderadores.yaml` y los pesos regionales del INDEC:

| clase COICOP | peso | de dónde |
|---|---|---|
| 03.1.2 Prendas de vestir | **6,739 %** | 47 Street, Tascani, Portsaid, Desiderata, Legacy, Rapsodia |
| 07.2.2 Combustibles y lubricantes | **4,237 %** | dataset oficial de Energía |
| 06.1 Productos medicinales | 4,198 % | Farmacity |
| 03.2.1 Zapatos y otros calzados | 2,621 % | Topper, Paruolo, 47 Street |
| 12.1.3 Artículos de atención personal | 2,217 % | Pigmento, Juleriaque |
| 09.1 Equipos audiovisuales e informática | 1,273 % | Frávega |
| 05.3 Artefactos para el hogar | 1,237 % | Frávega |
| 04.3.1 Materiales para la reparación de la vivienda | 0,525 % | Easy |
| 05.1 Muebles | 0,596 % | Easy |
| 09.3.4 Mascotas | 0,551 % | Puppis |
| 05.5 Herramientas | 0,303 % | Easy |
| | **24,5 %** | |

(Decía 25,1 %: contaba el grupo 04.3 entero, y Easy no vende los servicios de
reparación de 04.3.2.)

Con el **26,97 %** que mide el índice desde SEPA (las 11 clases de alimentos),
las fuentes alcanzan el **51,46 % del IPC nacional**. El mapa completo, con lo
que falta y lo que no tiene fuente, está en el CLAUDE.md del repo.

Ojo con el 10,03 % que circulaba como "lo que cubre el índice": ese es el peso
de las mismas 6 clases en **GBA**, no en el país. El nacional pondera las seis
regiones y da 11,184 %.

**Tener una fuente para una clase no es medir bien la clase.** Diez términos de
búsqueda en seis tiendas de ropa no son una muestra representativa de 03.1.2, y
los ponderadores dentro de la clase no están. Lo que estos números dicen es qué
parte del IPC **dejó de ser inalcanzable**, no qué parte ya está bien medida.

### VTEX no es universal, y en indumentaria casi no está

Lo primero que probé fue el endpoint de VTEX, que es el que usan las cadenas de
supermercado. En indumentaria falla: de 20 tiendas probadas el 14/10/2026,

| plataforma | tiendas |
|---|---|
| Salesforce Commerce | Dexter, Stock Center, Puma — OCAPI pide `client_id` |
| Magento | Paruolo y Rapsodia **sirven**, por `/graphql` sin credenciales |
| Shopify | Prune — `/products.json` dio 404 |
| TiendaNube | Cardón |
| no responden | Yagmour, Awada, Wanama, Kevingston, Cheeky y ocho más |

Por eso `src/reporte/online.py` tiene ahora **dos aplanadores**, uno por
plataforma, y el script elige por el campo `plataforma` del yaml. Agregar una
tercera plataforma no toca el script. Todo esto está anotado en
`config/fuentes_online.yaml` del repo de captura, bajo `sin_catalogo`, para que
nadie vuelva a probarlas.

Los libros (09.5.1, 0,679 %) quedaron afuera: Cúspide, Tematika, Yenny y
Hernández devuelven HTML, ninguno JSON.

Que Frávega no traiga EAN no es fatal para un índice: no hace falta un código de
barras, hace falta **seguir el mismo artículo mes a mes**, que es exactamente lo
que hace el INDEC —"el encuestador deberá buscar el mismo bien observando los
atributos que detalló la primera vez"—. El EAN sólo era necesario para cruzar
contra SEPA, y acá no hay contra qué cruzar.

## El relevamiento propio, andando

`precios.cli relevar-online`, en el repo de captura, releva los catorce sitios
y guarda un archivo por sitio y por día. Relevamiento del 14/10/2026:

| sitio | artículos | con EAN |
|---|---|---|
| 47 Street | 1.217 | 63 |
| Portsaid | 1.176 | 1.176 |
| Tascani | 1.157 | 1.152 |
| Topper | 978 | 107 |
| Desiderata | 784 | 784 |
| Juleriaque | 622 | 621 |
| Legacy | 527 | 408 |
| Puppis | 336 | 311 |
| Pigmento | 333 | 298 |
| Easy | 240 | 240 |
| Frávega | 239 | 74 |
| Farmacity | 204 | 190 |
| Paruolo | 186 | **0** |
| Rapsodia | 137 | **0** |
| | **8.136** | 5.424 |

Diez términos de búsqueda por sitio, fijados en `config/cadenas.yaml`. **La
canasta no se cambia:** sacar un término rompe la muestra emparejada, porque los
artículos que salen no tienen con qué compararse. Agregar está bien.

Las dos tiendas de Magento no traen EAN, igual que Frávega. No es un problema
para el índice —la clave es el SKU de la tienda— pero sí significa que no se
pueden cruzar contra ninguna otra fuente.

**La búsqueda de Magento es más difusa que la de VTEX.** En Paruolo los diez
términos devuelven 24 resultados cada uno y quedan 200 artículos distintos de
240 observaciones: hay solapamiento, y algún término trae cosas que no pidió.
No invalida el índice, porque lo que se sigue es el mismo SKU mes a mes, pero la
canasta es menos controlada que en VTEX.

### Cómo se sigue el mismo artículo

La clave es `(sitio, item_id)`, donde `item_id` es el SKU de la tienda. **No el
EAN**: Frávega lo trae en 77 de 238 artículos, así que como clave no sirve. El
sitio va en la clave porque dos tiendas pueden usar el mismo número de SKU.

Es el equivalente de la clave de SEPA —`(comercio, sucursal, producto)`— con la
tienda online como única sucursal.

### El cálculo es el mismo que el del índice

Las observaciones se convierten a `{quote: precio}` y de ahí entran a
`elemental.indice_elemental`: **la misma muestra emparejada, el mismo filtro de
valores raros y el mismo Jevons**, que ya están testeados y marcados como ruta
crítica. No hay un segundo cálculo que pueda divergir del primero.

Verificado sobre los datos reales: emparejar el relevamiento contra sí mismo da
`+0,000 %` exacto y empareja los 682 artículos, lo que confirma que la clave es
estable y que el cruce funciona. **La variación de verdad necesita dos días**, así
que el primer número sale en el segundo relevamiento.

### Dónde esto no encaja

Por la arquitectura del proyecto este repo **sólo lee** del bucket, y la captura
vive en `relevamiento_precios` porque un bug allá cuesta datos perdidos para
siempre. Relevar precios es capturar. Hoy queda en archivos locales, que alcanza
para medir si esto sirve, pero **si se decide que sigue tiene que mudarse** al
repo de captura o a uno propio, con su scheduler y su TTL. Dejarlo acá mezcla las
dos cosas que el proyecto separó a propósito.

## Combustibles: dato oficial, no scraping

La mejor fuente que apareció no se scrapea. La Resolución 314/2016 obliga a las
estaciones de servicio a informar sus precios, y la Secretaría de Energía los
publica en `datos.energia.gob.ar` con licencia CC-BY-4.0 y sin credenciales.

| | |
|---|---|
| clase | **07.2.2 Combustibles y lubricantes, 4,237 % del IPC** |
| quotes en la foto del 14/10/2026 | **36.617** |
| productos | nafta súper, nafta premium, gas oil grado 2 y 3, GNC |
| dimensión | empresa, dirección, localidad, provincia, latitud y longitud |
| actualización | diaria |

Es mejor que cualquier scraping: es oficial, no se puede bloquear, y trae la
dimensión completa de estación, así que la clave del quote sale calcada de la de
SEPA. Se baja con `precios.cli relevar-combustibles`, en el repo de captura.

### La trampa, que es la razón por la que hay un módulo aparte

El archivo se llama "precios vigentes" y trae una columna `indice_tiempo` con un
mes. Parece el mes de la observación. **No lo es: es el mes en que ese precio
entró en vigencia.**

| | |
|---|---|
| filas con `indice_tiempo` = 2026-09 | 3.174 |
| filas con `indice_tiempo` = 2026-10 | 3.260 |
| emparejadas entre las dos | **cero** |

Son disjuntas por construcción: una estación que no tocó el precio en octubre
figura sólo como 2026-09. Filtrar por mes —que es lo primero que uno hace— deja
la muestra en cero.

La lectura correcta es que el archivo es **una foto del precio actual de cada
quote**, así que hay que guardar una foto por mes. Un precio que no cambió
aparece igual en las dos fotos y aporta ratio 1,0, que es lo correcto. La
alternativa es el CSV histórico, que pesa 778 MB y da la serie entera de una vez.

### La decisión abierta: la antigüedad

En la foto del 14/10/2026, así se reparte la antigüedad del precio vigente:

| antigüedad | observaciones | |
|---|---|---|
| 0 a 3 meses | 7.077 | 19,3 % |
| 4 a 6 | 1.194 | 3,3 % |
| 7 a 12 | 3.244 | 8,9 % |
| 13 a 24 | 21.530 | **58,7 %** |
| más de 24 | 3.604 | 9,8 % |

El 68,5 % de los precios vigentes tiene más de un año. En un país con esta
inflación eso no es un precio estable: es una estación que **dejó de informar**.
Si se la deja, aporta ratio 1,0 para siempre y **diluye el índice hacia cero sin
avisar**, que es exactamente la clase de error que no tira excepción.

El INDEC trata al informante que no responde como ausente, no como sin cambio
(sección 3.3), así que excluirlo es lo fiel. Pero cuánto cortar es una elección:
va como parámetro `--antiguedad`, el script informa cuánto descarta, y se decide
midiendo —igual que el umbral del filtro de valores raros.

### Las regiones del dataset no son las del INDEC

Trae una columna `region` con los valores `PAMPEANA`, `CENTRO`, `NEA`, `NOA`,
`CUYO` y `PATAGONIA`, más 7.937 filas vacías. `CENTRO` no existe en el IPC y
**no hay GBA**. El caso que lo prueba: a `LOMAS DEL MIRADOR`, que es La Matanza y
por lo tanto GBA para el INDEC, el dataset la pone en `PAMPEANA`.

Por eso esa columna no se usa. La región sale de `provincia` con
`config/regiones.yaml`, igual que para SEPA — con el mismo problema pendiente de
partir Buenos Aires entre GBA y Pampeana. Lo que este dataset sí aporta para eso
es `localidad` con latitud y longitud en 36.617 puntos.

## Dónde vive cada cosa, y por qué se mudó

| | dónde | por qué |
|---|---|---|
| relevar los catálogos y el surtidor | **`relevamiento_precios`** | relevar es capturar, y un catálogo online **no tiene histórico**: lo que no se guardó hoy no se recupera |
| calcular la variación | este repo | es el mismo Jevons del índice |
| la sonda contra SEPA | este repo | no releva: lee del bucket y compara |

```
relevamiento_precios                         reporte_variaciones (ESTE)
  precios.online.catalogos   VTEX y Magento    scripts/variacion_online.py
  precios.online.surtidor    combustibles        lee el JSON y calcula
  precios.online.relevar     HTTP y la foto    scripts/sondeo_online.py
  config/fuentes_online.yaml                     compara contra SEPA
  datos_online/  datos_oficiales/
```

**El círculo está cerrado desde el 17/10/2026.** Captura sube cada foto al bucket
el mismo día (`publicar-online`, que nunca pisa) y `variacion_online.py` las lee
de `staged/relevamiento_online/` y `staged/combustibles/`. No importa nada del
otro repo: el contrato son los campos del archivo —`sitio`, `item_id`, `precio`—.

## La primera variación real, y el problema que destapó

14 al 15 de octubre de 2026, los catorce sitios:

| sitio | emparejados | variación |
|---|---|---|
| trece sitios | 232 a 1.217 | **+0,000 %** |
| Rapsodia | 137 | **+8,079 %** |

Trece en cero es lo esperable: en un día la mayoría de los precios no se mueve.
Rapsodia no fue un bug. **55 de sus 137 artículos se movieron exactamente
×1,3333**, que es 1/0,75: se terminó un descuento del 25 %, y `precio_lista`
pasó a coincidir con el precio final.

| artículo | 14/10 | 15/10 | ratio |
|---|---|---|---|
| pantalón francesca | 119.250 | 159.000 | 1,333 |
| pijama corto mariette | 82.500 | 110.000 | 1,333 |
| remera cherish | 44.925 | 59.900 | 1,333 |

Es un precio real que cambió, pero mide el **calendario de promociones** y no la
inflación.

**Y el filtro de valores raros no lo agarra.** Descartó 0 de 137. Cuando 55 de
137 ratios se mueven idénticos, un filtro por dispersión los ve como la norma y
los outliers pasan a ser los que *no* se movieron. Es una interacción directa
con la decisión del umbral que ya estaba abierta: el filtro protege contra el
dígito de más al tipear, no contra una promoción de catálogo.

Lo que esto implica para el diseño: **hay que relevar varias veces por mes y
promediar el mes**, no sacar una foto mensual. Es la fórmula 6 del INDEC —media
aritmética de los precios del mes— que el índice ya aplica para SEPA en la
operación 1 de la ruta crítica. Por eso el relevamiento online es diario.

## El sesgo que esta medición no puede sacar

La web se consulta hoy y SEPA tiene hasta unos días antes, así que parte de la
diferencia es inflación de esos días y no discrepancia. Para separarlas hay que
scrapear **el mismo día** que SEPA publica.

Eso es exactamente lo que esta sonda sirve para decidir: con estos números, para
DIA no vale la pena, y para Vea habría que ver si el 13,5 % de productos nuevos
justifica el scraper.

## Reproducir los números

```bash
# la sonda de calibracion contra SEPA
python scripts/sondeo_online.py --cadena dia --dia 2026-10-09
python scripts/sondeo_online.py --cadena vea --dia 2026-10-09

# el relevamiento propio: en el repo de CAPTURA
python -m precios.cli relevar-online
python -m precios.cli relevar-combustibles

# el calculo: aca
python scripts/variacion_online.py
python scripts/variacion_online.py --combustibles --antiguedad 6

# la sonda de calibracion sigue aca, porque compara contra SEPA
```

El catálogo online queda guardado en `.cache_sondeo/online_<cadena>_<fecha>.json`
la primera vez, así que volver a correrlo no le pide nada al sitio y da el mismo
resultado. Con `--refrescar` lo pide de nuevo.

Las cuentas están en `src/reporte/online.py` y se testean en
`tests/test_online.py` con números hechos a mano. El script sólo busca los datos
y los muestra: lo que está en `scripts/` no se testea, y la auditoría de la ruta
crítica ya mostró qué pasa cuando una cuenta vive ahí.
