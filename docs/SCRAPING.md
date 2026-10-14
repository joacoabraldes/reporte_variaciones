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

**MercadoLibre está cerrado.** La API devuelve `forbidden` en todos los
endpoints, incluido el de búsqueda: hoy exige OAuth con una app registrada. Y la
web pública redirige a un muro de verificación de cuenta, así que el HTML vuelve
sin un solo precio. Lo que hace falta no es código, es una cuenta y una app.

**Tres puertas que sí están abiertas**, verificadas a mano, y corren la misma
plataforma que las cadenas, así que el código de `src/reporte/online.py` sirve
tal cual:

| sitio | rubro | peso en el IPC | trae EAN |
|---|---|---|---|
| Farmacity | productos medicinales | 4,20 % | sí |
| Frávega | audiovisuales, informática y artefactos del hogar | 2,51 % | **no** |
| Easy | muebles, herramientas y mantenimiento de la vivienda | 1,45 % | sí |

Que Frávega no traiga EAN no es fatal para un índice: no hace falta un código de
barras, hace falta **seguir el mismo artículo mes a mes**, que es exactamente lo
que hace el INDEC —"el encuestador deberá buscar el mismo bien observando los
atributos que detalló la primera vez"—. El EAN sólo era necesario para cruzar
contra SEPA, y acá no hay contra qué cruzar.

## El relevamiento propio, andando

`scripts/relevar_online.py` releva los tres sitios y guarda un archivo por sitio
y por día. Primer relevamiento:

| sitio | artículos distintos | con EAN |
|---|---|---|
| Easy | 240 | 240 |
| Farmacity | 204 | 190 |
| Frávega | 238 | **77** |
| | **682** | |

Diez términos de búsqueda por sitio, fijados en `config/cadenas.yaml`. **La
canasta no se cambia:** sacar un término rompe la muestra emparejada, porque los
artículos que salen no tienen con qué compararse. Agregar está bien.

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

## El sesgo que esta medición no puede sacar

La web se consulta hoy y SEPA tiene hasta unos días antes, así que parte de la
diferencia es inflación de esos días y no discrepancia. Para separarlas hay que
scrapear **el mismo día** que SEPA publica.

Eso es exactamente lo que esta sonda sirve para decidir: con estos números, para
DIA no vale la pena, y para Vea habría que ver si el 13,5 % de productos nuevos
justifica el scraper.

## Reproducir los números

```bash
python scripts/sondeo_online.py --cadena dia --dia 2026-10-09
python scripts/sondeo_online.py --cadena vea --dia 2026-10-09
```

El catálogo online queda guardado en `.cache_sondeo/online_<cadena>_<fecha>.json`
la primera vez, así que volver a correrlo no le pide nada al sitio y da el mismo
resultado. Con `--refrescar` lo pide de nuevo.

Las cuentas están en `src/reporte/online.py` y se testean en
`tests/test_online.py` con números hechos a mano. El script sólo busca los datos
y los muestra: lo que está en `scripts/` no se testea, y la auditoría de la ruta
crítica ya mostró qué pasa cuando una cuenta vive ahí.
