"""Agrupacion de una misma historia contada por varios medios.

Es lo que separa un agregador de una lista de titulares pegados: cuando cinco
medios cuentan lo mismo, la portada debe enseñar una historia con cinco
coberturas, no cinco noticias repetidas.

El metodo es deliberadamente barato, porque corre en cada ejecucion sobre la
ventana reciente del dataset:

* Se normaliza el titular (sin tildes, sin vacias, sin nombre del medio).
* Quien decide es el **Jaccard** de sus palabras. Se probo antes a filtrar con
  un simhash de 64 bits, y midiendolo se vio que en titulares de cinco o seis
  palabras no vale: las parejas que si eran la misma historia daban distancias
  de 8 a 26 y las que no tenian nada que ver, de 27 a 32. Las bandas se solapan,
  asi que cualquier umbral perdia uniones buenas.
* Para no comparar todo contra todo se usa un **indice invertido** de palabras.
  Es un filtro previo que no se equivoca nunca: dos titulares sin ni una palabra
  en comun tienen Jaccard cero, luego jamas podrian unirse.
* Las historias se comparan solo dentro de la misma ventana de tiempo: dos
  finales de Champions con el mismo titular pero de años distintos no son la
  misma noticia.

El simhash se sigue calculando, pero solo como identificador estable de cada
historia.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .taxonomy import sin_tildes

BITS = 64
# Solapamiento minimo de palabras para confirmar la union.
JACCARD_MIN = 0.45
# Lo que se le exige de mas a dos noticias de verticales distintas. Una historia
# grande cruza nichos --una compra millonaria es economia y es tecnologia--, asi
# que no se pueden separar por decreto; pero coincidir "por casualidad" es mucho
# mas facil entre verticales, y por eso el liston sube.
EXTRA_ENTRE_VERTICALES = 0.2

# Por encima de esto dos titulares no son dos enfoques: son el mismo texto.
# Sirve para distinguir "el mismo medio lo ha contado dos veces" --que es una
# historia con dos piezas-- de "el mismo medio ha guardado dos veces lo mismo",
# que es un duplicado y no se publica.
JACCARD_IDENTICO = 0.92

# Jaccard castiga que un titular sea mucho mas largo que otro, y en un
# agregador eso pasa a todas horas: "Ayyoub Bouaddi transfer: Manchester City
# complete record-breaking £86m deal to sign 18-year-old Morocco midfielder"
# y "Man City complete the signing of Ayyoub Bouaddi" son la misma noticia y
# sacaban 0,24 --por debajo del liston--, asi que la portada las enseñaba como
# dos historias, una al lado de la otra.
#
# El solape mira otra cosa: cuanto del titular corto cabe dentro del largo.
# Ahi salen 0,6 y mas. Como es mas facil de disparar que Jaccard, se le ponen
# dos frenos: un minimo de palabras en comun --dos titulares de cuatro palabras
# que compartan tres tienen un solape altisimo sin hablar de lo mismo-- y que
# las dos noticias sean del mismo nicho. Entre nichos distintos sigue mandando
# Jaccard con su liston subido, que es lo que evita que una noticia de empresas
# y una de tecnologia se fundan por parecerse en el titular.
SOLAPE_MIN = 0.6
COMUNES_MIN = 4
# Ventana en la que dos noticias pueden ser la misma historia.
HORAS_VENTANA = 36

# Palabras que no distinguen una historia de otra.
VACIAS = frozenset("""
a al algo ante antes como con contra cual cuando de del desde donde dos el ella
ellas ellos en entre era eres es esa ese eso esta este esto ha han hasta hay la
las le les lo los mas me mi mientras muy no nos o os para pero por porque que
quien se segun ser si sin sobre su sus también tan te tiene todo tras tu un una
uno unos y ya
the a an and or but of for from to in on at by with without into over under
after before as is are was were be been being it its this that these those has
have had will would can could should may might do does did not no new say says
said after amid
que nao mais como para com uma dos das por sobre entre apos ainda
""".split())

_NO_PALABRA = re.compile(r"[^a-z0-9]+")
# Colas del tipo " - BBC News" o " | Marca" que los medios pegan al titular. El
# separador tiene que ir con espacios a los dos lados: sin eso, un titular como
# "gana 3-1 en el clasico" perdia la mitad por culpa del guion del resultado.
#
# Que la cola sea corta no basta para saber que es el nombre de un medio: con
# el limite en treinta caracteres se llevaba por delante titulares enteros.
# "'A very special day' - Bouaddi completes City move" se quedaba en "'A very
# special day'", perdia las palabras que lo emparentaban con las demas
# versiones de esa noticia y salia como una historia aparte.
#
# Lo que de verdad separa un medio de una frase es la caja: "BBC News" y
# "Marca" son nombres, con todas sus palabras en mayuscula; "Bouaddi completes
# City move" es una oracion, y lleva minusculas.
_COLA_MEDIO = re.compile(r"\s+[|\-–—·]\s+((?:\S+\s+){0,2}\S{2,20})$")


def _es_nombre_de_medio(cola: str) -> bool:
    """Si todas las palabras de la cola empiezan en mayuscula, es un nombre."""
    palabras_cola = [p for p in cola.split() if any(c.isalpha() for c in p)]
    return bool(palabras_cola) and all(p[:1].isupper() for p in palabras_cola)


def normalizar_titular(titulo: str) -> str:
    limpio = titulo.strip()
    coincidencia = _COLA_MEDIO.search(limpio)
    if coincidencia and _es_nombre_de_medio(coincidencia.group(1)):
        limpio = limpio[: coincidencia.start()]
    return sin_tildes(limpio)


def palabras(titulo: str) -> set[str]:
    """Palabras significativas del titular, sin plurales.

    Recortar la ``s`` final es una lematizacion pobrisima, pero basta para que
    "beats" y "beat" o "elecciones" y "eleccion" cuenten como la misma palabra,
    que es lo unico que se le pide aqui.
    """
    fichas = set()
    for bruta in _NO_PALABRA.split(normalizar_titular(titulo)):
        if len(bruta) <= 2 or bruta in VACIAS:
            continue
        fichas.add(bruta[:-1] if len(bruta) > 4 and bruta.endswith("s") else bruta)
    return fichas


def simhash(titulo: str) -> int:
    """Huella de 64 bits en la que titulares parecidos quedan cerca."""
    fichas = palabras(titulo)
    if not fichas:
        return 0
    vector = [0] * BITS
    for ficha in fichas:
        # sha1 da bits bien repartidos; el peso es 1 porque en un titular una
        # palabra rara vez se repite.
        h = int.from_bytes(hashlib.sha1(ficha.encode("utf-8")).digest()[:8], "big")
        for bit in range(BITS):
            vector[bit] += 1 if (h >> bit) & 1 else -1
    huella = 0
    for bit in range(BITS):
        if vector[bit] > 0:
            huella |= 1 << bit
    return huella


def solape(a: set[str], b: set[str]) -> float:
    """Cuanto del conjunto pequeno cabe dentro del grande."""
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _cuando(iso: str | None) -> datetime | None:
    if not iso:
        return None
    try:
        fecha = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    return fecha if fecha.tzinfo else fecha.replace(tzinfo=timezone.utc)


@dataclass
class Historia:
    """Una noticia y todas sus coberturas."""
    clave: str
    principal: dict
    piezas: list[dict] = field(default_factory=list)
    _palabras: set[str] = field(default_factory=set)

    @property
    def coberturas(self) -> int:
        return len(self.piezas)

    @property
    def fuentes(self) -> int:
        return len({p.get("source") for p in self.piezas if p.get("source")})


def agrupar(
    articulos: list[dict],
    jaccard_min: float = JACCARD_MIN,
    horas: int = HORAS_VENTANA,
) -> list[Historia]:
    """Agrupa noticias parecidas y devuelve las historias de mas a menos cobertura."""
    ventana = timedelta(hours=horas)
    historias: list[Historia] = []
    # palabra -> historias en las que aparece. Evita comparar cada noticia con
    # todas las anteriores sin descartar ni una union posible.
    indice: dict[str, list[int]] = {}

    # De mas reciente a mas antigua: la noticia que abre la historia --y por
    # tanto la que se muestra-- es siempre la ultima publicada.
    ordenados = sorted(
        articulos, key=lambda a: (a.get("published_at") or ""), reverse=True
    )

    for articulo in ordenados:
        titulo = articulo.get("title") or ""
        if not titulo:
            continue
        fichas = palabras(titulo)
        if not fichas:
            continue
        momento = _cuando(articulo.get("published_at"))

        candidatas = {i for ficha in fichas for i in indice.get(ficha, ())}

        encajada = False
        repetida = False
        for posicion in sorted(candidatas):
            historia = historias[posicion]
            liston = jaccard_min
            if historia.principal.get("vertical") != articulo.get("vertical"):
                liston += EXTRA_ENTRE_VERTICALES
            similitud = jaccard(historia._palabras, fichas)
            mismo_nicho = historia.principal.get("vertical") == articulo.get("vertical")
            if similitud < liston and not (
                mismo_nicho
                and solape(historia._palabras, fichas) >= SOLAPE_MIN
                and len(historia._palabras & fichas) >= COMUNES_MIN
            ):
                continue
            otro = _cuando(historia.principal.get("published_at"))
            if momento and otro and abs(momento - otro) > ventana:
                continue
            # Una misma fuente no cuenta dos veces la misma historia.
            if any(p.get("source") == articulo.get("source") for p in historia.piezas):
                # ...pero si además el titular es palabra por palabra el mismo,
                # no es que el medio lo haya contado dos veces: es la misma
                # noticia otra vez. Pasa cuando un medio la sirve en dos
                # dominios --la BBC en bbc.com y bbc.co.uk-- o cuando reedita
                # sin cambiar el titular. Antes se descartaba la unión y salía
                # como una historia aparte, así que la portada la enseñaba dos
                # veces seguidas.
                if similitud >= JACCARD_IDENTICO:
                    repetida = True
                    break
                continue
            historia.piezas.append(articulo)
            encajada = True
            break

        if repetida:
            continue

        if not encajada:
            posicion = len(historias)
            historias.append(
                Historia(
                    clave=f"h{simhash(titulo):016x}",
                    principal=articulo,
                    piezas=[articulo],
                    _palabras=fichas,
                )
            )
            for ficha in fichas:
                indice.setdefault(ficha, []).append(posicion)

    # Primero las historias con mas medios detras: son las que de verdad estan
    # pasando. A igualdad, la mas reciente.
    historias.sort(
        key=lambda h: (h.fuentes, h.principal.get("published_at") or ""), reverse=True
    )
    return historias
