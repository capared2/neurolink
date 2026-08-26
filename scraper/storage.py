"""Dataset particionado por ``vertical/tema`` y estado reanudable por fuente.

El dataset es la API publica del proyecto: el sitio no se reconstruye cuando
entran noticias, simplemente lee estos JSON. De ahi que la forma de los
ficheros importe tanto como su contenido.

Que hay en ``data/``::

    index.json                    catalogo: verticales, temas, ficheros, totales
    latest.json                   ultimas noticias, sin cuerpo (listados)
    portada.json                  historias agrupadas: el rio universal
    fuentes.json                  salud de cada fuente en la ultima ejecucion
    imagenes.json                 hosts de imagen que el sitio puede servir
    <vertical>/<tema>/part-NNNN.json
    <vertical>/<tema>/lookup.json  id de noticia -> fichero que la contiene
    seo/sitemap*.xml

``latest.json`` y ``portada.json`` se derivan a proposito **sin la identidad
del medio**: son los ficheros que alimentan los listados del sitio, y asi el
frontend no puede enseñar por accidente algo que no debe. El archivo completo
de cada noticia si la conserva, porque el scraper la necesita para su propio
estado y para no contar dos veces la misma historia.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from . import config, dedupe, taxonomy

log = logging.getLogger(__name__)

PLANTILLA_PARTE = "part-{:04d}.json"

# `paragraphs` no se guarda: es `body` partido por los saltos dobles, y
# guardarlo aparte doblaba el peso de cada noticia --y con el, los milisegundos
# de CPU que le cuesta al sitio parsear el fichero para pintar una sola--.
# El frontend lo reconstruye con un split, que es gratis al lado de eso.
CAMPOS_DERIVADOS = ("paragraphs",)

# Lo justo para pintar una tarjeta en un listado.
CAMPOS_TARJETA = (
    "id", "category", "vertical", "topic", "topic_name", "title",
    "standfirst", "summary", "published_at", "language", "word_count",
    "is_premium",
)


def ahora() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def escribir_json(ruta: Path, contenido) -> None:
    """Escritura atomica: una ejecucion interrumpida nunca deja medio JSON."""
    ruta.parent.mkdir(parents=True, exist_ok=True)
    temporal = ruta.with_suffix(ruta.suffix + ".tmp")
    with temporal.open("w", encoding="utf-8") as fichero:
        json.dump(contenido, fichero, ensure_ascii=False, separators=(",", ":"))
        fichero.write("\n")
    os.replace(temporal, ruta)


def leer_json(ruta: Path, defecto):
    if not ruta.exists():
        return defecto
    try:
        with ruta.open(encoding="utf-8") as fichero:
            return json.load(fichero)
    except (json.JSONDecodeError, OSError) as exc:
        log.error("JSON corrupto en %s (%s); se empieza ese fichero de cero", ruta, exc)
        return defecto


def ficha_imagen(url: str | None) -> str | None:
    """La imagen de portada, como ruta del propio sitio.

    Una URL de imagen apuntando al medio original delata de donde salio la
    noticia en cada `<img src>` de la pagina, ademas de cargar sus servidores
    y filtrarles el referer de nuestros lectores. Lo que viaja a los listados
    es un testigo que el sitio resuelve por su cuenta en `/img/`.
    """
    if not url:
        return None
    testigo = base64.urlsafe_b64encode(url.encode("utf-8")).decode("ascii").rstrip("=")
    return f"/img/{testigo}"


def _tarjeta(articulo: dict) -> dict:
    tarjeta = {campo: articulo.get(campo) for campo in CAMPOS_TARJETA}
    imagenes = articulo.get("images") or []
    tarjeta["image"] = ficha_imagen(imagenes[0]["url"] if imagenes else None)
    return tarjeta


class Almacen:
    """Escribe ``data/<vertical>/<tema>/part-NNNN.json`` de tamaño acotado."""

    def __init__(self, data_dir: str | Path, tam_parte: int = config.DEFAULT_SHARD_SIZE):
        self.data_dir = Path(data_dir)
        self.tam_parte = max(1, tam_parte)
        self.entradas_sitemap: list[dict] = []
        self._lock = threading.Lock()
        self._buffer: dict[str, list[dict]] = {}

    def anadir(self, articulo: dict) -> None:
        for campo in CAMPOS_DERIVADOS:
            articulo.pop(campo, None)
        with self._lock:
            self._buffer.setdefault(articulo["category"], []).append(articulo)

    def dir_categoria(self, categoria: str) -> Path:
        return self.data_dir.joinpath(*categoria.split("/"))

    def _partes(self, categoria: str) -> list[Path]:
        carpeta = self.dir_categoria(categoria)
        if not carpeta.is_dir():
            return []
        return sorted(p for p in carpeta.glob("part-*.json") if p.is_file())

    def volcar(self) -> dict[str, int]:
        """Guarda lo acumulado. Devuelve cuantas noticias por categoria."""
        with self._lock:
            buffer, self._buffer = self._buffer, {}
        escritas: dict[str, int] = {}
        for categoria, articulos in buffer.items():
            if not articulos:
                continue
            cuantas = self._anexar(categoria, articulos)
            if cuantas:
                escritas[categoria] = cuantas
        return escritas

    def _anexar(self, categoria: str, articulos: list[dict]) -> int:
        carpeta = self.dir_categoria(categoria)
        carpeta.mkdir(parents=True, exist_ok=True)

        partes = self._partes(categoria)
        numero = int(partes[-1].stem.split("-")[-1]) if partes else 1
        actual = carpeta / PLANTILLA_PARTE.format(numero)
        contenido = leer_json(actual, None)
        cubo = contenido.get("articles", []) if isinstance(contenido, dict) else []

        conocidas = {a.get("url") for a in cubo}
        # Y las del resto del tema, no solo las del fichero abierto. Mirar solo
        # el ultimo dejaba pasar una noticia ya guardada en un fichero anterior
        # --pasa si el estado se pierde, o si una noticia cambia de tema y
        # vuelve-- y acababa dos veces en el archivo. El lookup ya tiene ese
        # mapa hecho y pesa unos pocos kilobytes.
        lookup = leer_json(carpeta / "lookup.json", {})
        ids_del_tema = set(lookup.get("parts", {})) if isinstance(lookup, dict) else set()
        total = 0
        nuevas_aqui = 0
        for articulo in articulos:
            if articulo["url"] in conocidas or articulo.get("id") in ids_del_tema:
                continue
            if len(cubo) >= self.tam_parte:
                if nuevas_aqui:
                    self._guardar_parte(actual, categoria, numero, cubo)
                numero += 1
                actual = carpeta / PLANTILLA_PARTE.format(numero)
                cubo, conocidas, nuevas_aqui = [], set(), 0
            cubo.append(articulo)
            conocidas.add(articulo["url"])
            total += 1
            nuevas_aqui += 1

        # Solo se reescribe el fichero si algo cambio. El dataset se versiona en
        # git: volver a escribirlo por un `updated_at` nuevo llenaria el
        # historial de commits que no cambian ni una noticia.
        if nuevas_aqui:
            self._guardar_parte(actual, categoria, numero, cubo)
        return total

    @staticmethod
    def _guardar_parte(ruta: Path, categoria: str, numero: int, articulos: list[dict]) -> None:
        articulos.sort(key=lambda a: (a.get("published_at") or "", a.get("id") or ""))
        escribir_json(ruta, {
            "category": categoria,
            "part": numero,
            "count": len(articulos),
            "updated_at": ahora(),
            "articles": articulos,
        })

    # -- indices -----------------------------------------------------------
    def reconstruir_indices(self, salud_fuentes: dict | None = None) -> dict:
        """Regenera index.json, latest.json, portada.json y los lookup.

        Se recorre el dataset una sola vez y de esa pasada salen todos los
        derivados, incluidas las entradas que necesitan los sitemaps.
        """
        categorias: list[dict] = []
        recientes: list[dict] = []
        lookups: dict[str, dict[str, int]] = {}
        hosts_imagen: set[str] = set()
        self.entradas_sitemap = []
        total = 0

        for parte in sorted(self.data_dir.rglob("part-*.json")):
            contenido = leer_json(parte, {})
            categoria = contenido.get("category")
            if not categoria:
                continue

            lookup = lookups.setdefault(categoria, {})
            for articulo in contenido.get("articles", []):
                if articulo.get("id"):
                    lookup[articulo["id"]] = contenido.get("part", 1)
                self.entradas_sitemap.append({
                    "id": articulo.get("id"),
                    "category": articulo.get("category"),
                    "title": articulo.get("title", ""),
                    "published_at": articulo.get("published_at"),
                    "modified_at": articulo.get("modified_at"),
                    "language": articulo.get("language"),
                })
                for imagen in articulo.get("images") or []:
                    host = urlsplit(imagen.get("url", "")).netloc.lower()
                    if host:
                        hosts_imagen.add(host)
                tarjeta = _tarjeta(articulo)
                # La vertical y la fuente hacen falta para agrupar; la fuente se
                # quita justo despues, antes de escribir nada.
                tarjeta["_source"] = articulo.get("source")
                recientes.append(tarjeta)

            relativa = parte.relative_to(self.data_dir).as_posix()
            entrada = next((c for c in categorias if c["category"] == categoria), None)
            if entrada is None:
                entrada = {
                    "category": categoria,
                    "vertical": categoria.split("/")[0],
                    "name": taxonomy.nombre_tema(categoria),
                    "articles": 0,
                    "files": [],
                }
                categorias.append(entrada)
            entrada["articles"] += contenido.get("count", 0)
            entrada["files"].append({"file": relativa, "count": contenido.get("count", 0)})
            total += contenido.get("count", 0)

        categorias.sort(key=lambda c: (-c["articles"], c["category"]))
        recientes.sort(key=lambda a: (a.get("published_at") or "", a.get("id") or ""), reverse=True)
        recientes = recientes[: config.LATEST_LIMIT]

        self._escribir_portada(recientes)

        for tarjeta in recientes:
            tarjeta.pop("_source", None)
        escribir_json(self.data_dir / "latest.json", {
            "generated_at": ahora(),
            "count": len(recientes),
            "articles": recientes,
        })

        for categoria, lookup in lookups.items():
            escribir_json(self.dir_categoria(categoria) / "lookup.json", {
                "category": categoria,
                "count": len(lookup),
                "parts": lookup,
            })

        verticales = []
        for clave, nombre in taxonomy.VERTICALES.items():
            hijas = [c for c in categorias if c["vertical"] == clave]
            if hijas:
                verticales.append({
                    "vertical": clave,
                    "name": nombre,
                    "articles": sum(c["articles"] for c in hijas),
                    "topics": len(hijas),
                })
        verticales.sort(key=lambda v: -v["articles"])

        indice = {
            "generated_at": ahora(),
            "total_articles": total,
            "total_categories": len(categorias),
            "verticals": verticales,
            "categories": categorias,
        }
        escribir_json(self.data_dir / "index.json", indice)

        # El sitio sirve las imagenes por su propio dominio para no delatar de
        # donde sale cada noticia. Publicar aqui los hosts que de verdad
        # aparecen en el dataset le permite comprobarlos y no acabar siendo un
        # proxy abierto por el que colar cualquier URL.
        escribir_json(self.data_dir / "imagenes.json", {
            "generated_at": ahora(),
            "count": len(hosts_imagen),
            "hosts": sorted(hosts_imagen),
        })

        if salud_fuentes is not None:
            escribir_json(self.data_dir / "fuentes.json", {
                "generated_at": ahora(),
                "count": len(salud_fuentes),
                "sources": salud_fuentes,
            })

        return indice

    def _escribir_portada(self, recientes: list[dict]) -> None:
        """El rio universal: todas las verticales mezcladas y agrupadas.

        Cada entrada es una historia, no una noticia: si cinco medios cuentan
        lo mismo, sale una vez con el numero de coberturas. Los medios no se
        nombran, solo se cuentan.
        """
        for tarjeta in recientes:
            tarjeta["source"] = tarjeta.get("_source")

        agrupadas = dedupe.agrupar(recientes)

        # Cada tarjeta se queda marcada con la historia a la que pertenece. Es
        # lo que le permite al sitio no repetir una misma noticia en un listado
        # sin tener que recalcular nada: cinco medios contando lo mismo son
        # cinco tarjetas con la misma marca.
        for historia in agrupadas:
            for pieza in historia.piezas:
                pieza["story"] = historia.clave

        # La portada se reparte entre las verticales en vez de dejarla al
        # volumen del dia. Se recorre por orden --lo que mas medios cuentan y
        # mas reciente es-- saltando la vertical que ya llego a su cupo, y si
        # al final faltan huecos porque las demas no dan para mas, se completa
        # sin tope: mejor una portada llena y algo escorada que medio vacia.
        tope = max(1, int(config.PORTADA_LIMIT * config.PORTADA_TOPE_VERTICAL))
        elegidas: list = []
        cuantas: dict[str, int] = {}
        for vuelta in (True, False):
            for historia in agrupadas:
                if len(elegidas) >= config.PORTADA_LIMIT:
                    break
                if historia in elegidas:
                    continue
                vertical = (historia.principal.get("category") or "/").split("/")[0]
                if vuelta and cuantas.get(vertical, 0) >= tope:
                    continue
                cuantas[vertical] = cuantas.get(vertical, 0) + 1
                elegidas.append(historia)

        historias = []
        for historia in elegidas:
            principal = {c: historia.principal.get(c) for c in (*CAMPOS_TARJETA, "image", "story")}
            principal["coverage"] = historia.fuentes
            # Otras versiones de la misma historia, sin decir de quien son.
            principal["also"] = [
                {
                    "id": pieza.get("id"),
                    "category": pieza.get("category"),
                    "title": pieza.get("title"),
                    "published_at": pieza.get("published_at"),
                }
                for pieza in historia.piezas[1:5]
            ]
            historias.append(principal)

        for tarjeta in recientes:
            tarjeta.pop("source", None)

        escribir_json(self.data_dir / "portada.json", {
            "generated_at": ahora(),
            "count": len(historias),
            "stories": historias,
        })


# ---------------------------------------------------------------------------
# Estado
# ---------------------------------------------------------------------------

class EstadoFuente:
    """Cola, vistas y fallos de una sola fuente.

    Cada fuente lleva su propia carpeta para que una que se rompa --o una que
    se añada a mitad de camino-- no toque el progreso de las demas.
    """

    def __init__(
        self,
        clave: str,
        raiz: str | Path,
        max_fallos: int = config.DEFAULT_MAX_FAILURES,
        reintentos_vacio: int = config.DEFAULT_EMPTY_RETRIES,
    ):
        self.clave = clave
        self.carpeta = Path(raiz) / "fuentes" / clave
        self.max_fallos = max(1, max_fallos)
        self.reintentos_vacio = max(1, reintentos_vacio)

        self.vistas: set[str] = self._leer_lineas(self.carpeta / "seen.txt")
        self.pendientes: list[str] = [
            u for u in self._leer_ordenadas(self.carpeta / "pending.txt") if u not in self.vistas
        ]
        fallidas = leer_json(self.carpeta / "failed.json", {})
        self.fallidas: dict[str, int] = fallidas if isinstance(fallidas, dict) else {}
        vacias = leer_json(self.carpeta / "empty.json", {})
        self.vacias: dict[str, int] = vacias if isinstance(vacias, dict) else {}
        self._lock = threading.Lock()

    @staticmethod
    def _leer_lineas(ruta: Path) -> set[str]:
        if not ruta.exists():
            return set()
        with ruta.open(encoding="utf-8") as fichero:
            return {linea.strip() for linea in fichero if linea.strip()}

    @staticmethod
    def _leer_ordenadas(ruta: Path) -> list[str]:
        if not ruta.exists():
            return []
        with ruta.open(encoding="utf-8") as fichero:
            return [linea.strip() for linea in fichero if linea.strip()]

    def _agotadas(self) -> set[str]:
        """URLs que ya no merece la pena volver a pedir."""
        return (
            {u for u, n in self.fallidas.items() if n >= self.max_fallos}
            | {u for u, n in self.vacias.items() if n >= self.reintentos_vacio}
        )

    def encolar(self, candidatas) -> int:
        with self._lock:
            conocidas = self.vistas | set(self.pendientes) | self._agotadas()
            anadidas = 0
            for url in candidatas:
                if url not in conocidas:
                    self.pendientes.append(url)
                    conocidas.add(url)
                    anadidas += 1
            return anadidas

    def tomar(self, cuantas: int) -> list[str]:
        with self._lock:
            lote = self.pendientes[:cuantas]
            self.pendientes = self.pendientes[cuantas:]
            return lote

    def marcar_vista(self, url: str) -> None:
        with self._lock:
            self.vistas.add(url)
            self.fallidas.pop(url, None)
            self.vacias.pop(url, None)

    def marcar_fallida(self, url: str) -> None:
        with self._lock:
            self.fallidas[url] = self.fallidas.get(url, 0) + 1

    def marcar_vacia(self, url: str) -> None:
        """Llego sin cuerpo. Se reintentara un numero acotado de veces.

        Los directos y las cronicas en curso se llenan de texto cuando acaba el
        acontecimiento, asi que no se dan por vistos: se vuelven a pedir
        mientras sigan apareciendo en las fuentes.
        """
        with self._lock:
            self.vacias[url] = self.vacias.get(url, 0) + 1

    def guardar(self) -> dict:
        self.carpeta.mkdir(parents=True, exist_ok=True)
        with self._lock:
            self._escribir_lineas(self.carpeta / "seen.txt", sorted(self.vistas))
            self._escribir_lineas(self.carpeta / "pending.txt", self.pendientes)
            escribir_json(self.carpeta / "failed.json", dict(sorted(self.fallidas.items())))
            escribir_json(self.carpeta / "empty.json", dict(sorted(self.vacias.items())))
            return {
                "seen": len(self.vistas),
                "pending": len(self.pendientes),
                "failed": len(self.fallidas),
                "empty": len(self.vacias),
                "abandoned": len(self._agotadas()),
            }

    @staticmethod
    def _escribir_lineas(ruta: Path, lineas) -> None:
        ruta.parent.mkdir(parents=True, exist_ok=True)
        temporal = ruta.with_suffix(ruta.suffix + ".tmp")
        with temporal.open("w", encoding="utf-8") as fichero:
            for linea in lineas:
                fichero.write(f"{linea}\n")
        os.replace(temporal, ruta)


class Estado:
    """El estado de todas las fuentes de una ejecucion."""

    def __init__(self, raiz: str | Path, max_fallos: int, reintentos_vacio: int):
        self.raiz = Path(raiz)
        self.max_fallos = max_fallos
        self.reintentos_vacio = reintentos_vacio
        self.fuentes: dict[str, EstadoFuente] = {}

    def de(self, clave: str) -> EstadoFuente:
        if clave not in self.fuentes:
            self.fuentes[clave] = EstadoFuente(
                clave, self.raiz, self.max_fallos, self.reintentos_vacio
            )
        return self.fuentes[clave]

    @property
    def pendientes(self) -> int:
        return sum(len(e.pendientes) for e in self.fuentes.values())

    def guardar(self, resumen: dict | None = None) -> dict:
        por_fuente = {clave: estado.guardar() for clave, estado in sorted(self.fuentes.items())}
        contenido = {
            "updated_at": ahora(),
            "pending": self.pendientes,
            "sources": por_fuente,
        }
        contenido.update(resumen or {})
        escribir_json(self.raiz / "run.json", contenido)
        return por_fuente
