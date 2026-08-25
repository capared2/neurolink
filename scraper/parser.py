"""De una pagina HTML a un registro de noticia, venga del medio que venga.

Un scraper de un solo medio puede permitirse selectores a medida. Con veintiun
maquetaciones distintas --y cambiando cada pocos meses-- hace falta una escalera
de respaldos que degrade sin romperse:

1. **JSON-LD** (``NewsArticle``). Casi todos lo publican, es dato estructurado
   y a veces trae hasta el cuerpo entero en ``articleBody``.
2. **Selectores de la fuente**, los que estan declarados en ``sources.py``.
3. **Selectores genericos** que cubren los CMS mas repetidos.
4. **Densidad de texto**: si nada de lo anterior encuentra el cuerpo, se elige
   el contenedor de la pagina con mas texto propio y menos enlaces. Es lo que
   salva a una fuente el dia que se rediseña.

Open Graph y las ``<meta>`` rellenan lo que falte en cualquiera de los pasos.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from . import taxonomy
from . import urls as urlutil
from .sources import Fuente

log = logging.getLogger(__name__)

TIPOS_ARTICULO = {
    "newsarticle", "article", "reportagenewsarticle", "sportsarticle",
    "analysisnewsarticle", "backgroundnewsarticle", "opinionnewsarticle",
    "reviewnewsarticle", "liveblogposting", "blogposting", "techarticle",
    "videoobject", "webpage",
}

# Contenedores habituales del cuerpo en los CMS mas extendidos.
CUERPO_GENERICO = (
    "[itemprop=articleBody]",
    "div.article-body", "div.article__body", "div.article-content",
    "div.entry-content", "div.post-content", "div.story-body",
    "div.c-entry-content", "div.rich-text", "div.body-content",
    "main article", "article", "main",
)

# Envoltorios que viven dentro del cuerpo pero no son la noticia.
RUIDO = re.compile(
    r"(related|recirc|recommend|newsletter|publicidad|advert|ad-|-ad\b|banner|"
    r"social|share|promo|comment|suscri|subscri|paywall|widget|taboola|outbrain|"
    r"footer|breadcrumb|tags?-list|most-read|lo-mas|trending|nav|menu|sidebar|"
    r"caption|byline|meta-|toolbar|inline-newsletter|read-more|leia-mais)",
    re.I,
)
ETIQUETAS_FUERA = (
    "script", "style", "noscript", "aside", "nav", "footer", "header", "form",
    "iframe", "svg", "button", "figure", "figcaption", "video", "audio",
)

# Frases que los medios cuelan como parrafo y no son noticia.
BASURA = re.compile(
    r"^(sign up|subscribe|read more|leia mais|lee tambien|lea tambien|advertisement|"
    r"publicidad|follow us|siguenos|share this|copyright|all rights reserved|"
    r"this article|content is not available|enable javascript|accept cookies)",
    re.I,
)

MIN_PARRAFO = 40          # caracteres para que un parrafo cuente como cuerpo
MIN_CUERPO_JSONLD = 80    # palabras para fiarnos del articleBody del JSON-LD


def _texto(nodo) -> str:
    return re.sub(r"\s+", " ", nodo.get_text(" ", strip=True)).strip() if nodo else ""


# ---------------------------------------------------------------------------
# Fechas
# ---------------------------------------------------------------------------

def parsear_fecha(valor) -> str | None:
    """Cualquier formato de fecha que publique un medio, a ISO-8601 en UTC."""
    if not valor:
        return None
    if isinstance(valor, (list, tuple)):
        valor = valor[0] if valor else None
        if not valor:
            return None
    if isinstance(valor, dict):
        valor = valor.get("@value") or valor.get("value")
        if not valor:
            return None

    bruto = str(valor).strip()
    if not bruto:
        return None

    try:
        analizada = datetime.fromisoformat(bruto.replace("Z", "+00:00"))
    except ValueError:
        try:
            analizada = parsedate_to_datetime(bruto)
        except (TypeError, ValueError):
            if bruto.isdigit() and 10 <= len(bruto) <= 13:
                # Marca de tiempo en segundos o milisegundos.
                marca = int(bruto[:10])
                analizada = datetime.fromtimestamp(marca, tz=timezone.utc)
            else:
                encontrada = re.search(r"(\d{4})-(\d{2})-(\d{2})", bruto)
                if not encontrada:
                    return None
                analizada = datetime(int(encontrada[1]), int(encontrada[2]), int(encontrada[3]))

    if analizada.tzinfo is None:
        analizada = analizada.replace(tzinfo=timezone.utc)
    return analizada.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# JSON-LD y metadatos
# ---------------------------------------------------------------------------

def _iterar_jsonld(sopa: BeautifulSoup):
    for etiqueta in sopa.find_all("script", attrs={"type": re.compile(r"ld\+json", re.I)}):
        bruto = (etiqueta.string or etiqueta.get_text() or "").strip()
        if not bruto:
            continue
        try:
            datos = json.loads(bruto)
        except json.JSONDecodeError:
            # Hay medios que emiten comas colgando o varios bloques pegados.
            try:
                datos = json.loads(re.sub(r",\s*([}\]])", r"\1", bruto))
            except json.JSONDecodeError:
                continue
        pila = [datos]
        while pila:
            elemento = pila.pop()
            if isinstance(elemento, list):
                pila.extend(elemento)
            elif isinstance(elemento, dict):
                if "@graph" in elemento:
                    pila.append(elemento["@graph"])
                yield elemento


def _articulo_jsonld(sopa: BeautifulSoup) -> dict:
    """El bloque JSON-LD mas completo que describa un articulo."""
    mejor: dict = {}
    mejor_peso = 0
    for elemento in _iterar_jsonld(sopa):
        tipos = elemento.get("@type") or elemento.get("type") or ""
        if isinstance(tipos, str):
            tipos = [tipos]
        nombres = {str(t).lower() for t in tipos}
        if not (nombres & TIPOS_ARTICULO):
            continue
        # Entre varios bloques gana el que mas informacion trae, y se premia el
        # que incluya el cuerpo.
        peso = len(json.dumps(elemento, default=str))
        if elemento.get("articleBody"):
            peso += 100_000
        if peso > mejor_peso:
            mejor, mejor_peso = elemento, peso
    return mejor


def _meta(sopa: BeautifulSoup, *nombres: str) -> str | None:
    for nombre in nombres:
        for atributo in ("property", "name", "itemprop"):
            etiqueta = sopa.find("meta", attrs={atributo: nombre})
            if etiqueta and etiqueta.get("content", "").strip():
                return etiqueta["content"].strip()
    return None


def _personas(valor) -> list[str]:
    salida: list[str] = []
    pila = [valor]
    while pila:
        elemento = pila.pop(0)
        if isinstance(elemento, str):
            if elemento.strip():
                salida.append(elemento.strip())
        elif isinstance(elemento, list):
            pila = list(elemento) + pila
        elif isinstance(elemento, dict):
            nombre = elemento.get("name")
            if isinstance(nombre, str) and nombre.strip():
                salida.append(nombre.strip())
    vistos: set[str] = set()
    return [n for n in salida if not (n.lower() in vistos or vistos.add(n.lower()))]


# ---------------------------------------------------------------------------
# Cuerpo
# ---------------------------------------------------------------------------

def _limpiar(nodo: Tag) -> BeautifulSoup:
    """Copia del contenedor sin scripts, adornos ni bloques de recirculacion."""
    trabajo = BeautifulSoup(str(nodo), "lxml")
    for etiqueta in trabajo.find_all(ETIQUETAS_FUERA):
        etiqueta.decompose()
    for atributo in ("class", "id", "data-testid"):
        for etiqueta in trabajo.find_all(attrs={atributo: RUIDO}):
            etiqueta.decompose()
    return trabajo


def _parrafos_de(nodo: Tag) -> list[str]:
    parrafos: list[str] = []
    vistos: set[str] = set()
    for hijo in _limpiar(nodo).find_all(["p", "h2", "h3", "h4", "li", "blockquote"]):
        texto = _texto(hijo)
        if len(texto) < MIN_PARRAFO or BASURA.match(texto):
            continue
        clave = texto.lower()
        if clave in vistos:
            continue
        vistos.add(clave)
        parrafos.append(texto)
    return parrafos


def _densidad(nodo: Tag) -> int:
    """Texto propio del contenedor, descontando lo que sea enlace.

    Un menu o un bloque de "lo mas leido" es casi todo texto enlazado; el
    cuerpo de una noticia es casi todo texto suelto. La resta separa a los dos
    sin saber nada del medio.
    """
    texto = len(_texto(nodo))
    enlazado = sum(len(_texto(a)) for a in nodo.find_all("a"))
    return texto - enlazado


def _mejor_contenedor(sopa: BeautifulSoup) -> Tag | None:
    """El bloque de la pagina que mas se parece al cuerpo de una noticia.

    Ultimo recurso cuando ni la fuente ni los selectores genericos aciertan.
    Solo se miran contenedores con varios parrafos de verdad, asi que la
    busqueda se queda en unas pocas decenas de nodos.
    """
    mejor: Tag | None = None
    mejor_puntos = 0
    for nodo in sopa.find_all(["article", "main", "section", "div"]):
        parrafos = [p for p in nodo.find_all("p", recursive=True) if len(_texto(p)) >= MIN_PARRAFO]
        if len(parrafos) < 3:
            continue
        puntos = _densidad(nodo)
        # Entre un contenedor y el que lo envuelve gana el interior si conserva
        # casi todo el texto: se queda con el cuerpo y deja fuera la pagina.
        if puntos > mejor_puntos:
            mejor, mejor_puntos = nodo, puntos
    return mejor


def extraer_cuerpo(sopa: BeautifulSoup, fuente: Fuente, datos: dict) -> tuple[str, list[str]]:
    """Devuelve ``(cuerpo, parrafos)`` bajando por la escalera de respaldos."""
    # 1. El propio JSON-LD, cuando trae el articulo entero.
    bruto = datos.get("articleBody")
    if isinstance(bruto, str) and len(bruto.split()) >= MIN_CUERPO_JSONLD:
        parrafos = [
            re.sub(r"\s+", " ", trozo).strip()
            for trozo in re.split(r"\n{2,}|\r\n{2,}", bruto)
            if len(trozo.strip()) >= MIN_PARRAFO
        ]
        if not parrafos:
            parrafos = [re.sub(r"\s+", " ", bruto).strip()]
        return "\n\n".join(parrafos), parrafos

    # 2. y 3. Selectores de la fuente, luego los genericos.
    for selector in (*fuente.cuerpo, *CUERPO_GENERICO):
        try:
            contenedor = sopa.select_one(selector)
        except Exception:              # un selector mal escrito no tumba el run
            continue
        if contenedor is None:
            continue
        parrafos = _parrafos_de(contenedor)
        if len(parrafos) >= 2:
            return "\n\n".join(parrafos), parrafos

    # 4. Densidad de texto.
    contenedor = _mejor_contenedor(sopa)
    if contenedor is not None:
        parrafos = _parrafos_de(contenedor)
        if parrafos:
            return "\n\n".join(parrafos), parrafos

    return "", []


# ---------------------------------------------------------------------------
# Imagenes
# ---------------------------------------------------------------------------

def _imagenes(sopa: BeautifulSoup, datos: dict, base: str) -> list[dict]:
    encontradas: list[dict] = []
    vistas: set[str] = set()

    def anadir(url, pie: str = "") -> None:
        if not isinstance(url, str) or not url.strip():
            return
        absoluta = urljoin(base, url.strip())
        if absoluta.startswith("data:") or absoluta in vistas:
            return
        if not re.match(r"^https?://", absoluta):
            return
        vistas.add(absoluta)
        encontradas.append({"url": absoluta, "caption": pie})

    pila = [datos.get("image"), datos.get("thumbnailUrl")]
    while pila:
        elemento = pila.pop(0)
        if isinstance(elemento, str):
            anadir(elemento)
        elif isinstance(elemento, list):
            pila = list(elemento) + pila
        elif isinstance(elemento, dict):
            anadir(elemento.get("url") or elemento.get("contentUrl"),
                   str(elemento.get("caption") or ""))

    anadir(_meta(sopa, "og:image", "twitter:image", "twitter:image:src"))

    for figura in sopa.select("figure")[:12]:
        imagen = figura.find("img")
        if imagen:
            anadir(
                imagen.get("src") or imagen.get("data-src") or imagen.get("data-original"),
                _texto(figura.find("figcaption")),
            )

    return encontradas[:10]


# ---------------------------------------------------------------------------

def parsear(html: str, url: str, fuente: Fuente) -> dict | None:
    """Construye el registro de una noticia. ``None`` si la pagina no lo es."""
    sopa = BeautifulSoup(html, "lxml")
    datos = _articulo_jsonld(sopa)

    # -- URL canonica ------------------------------------------------------
    etiqueta = sopa.find("link", rel=lambda v: v and "canonical" in v)
    canonica = urlutil.normalizar(
        urljoin(url, etiqueta["href"]) if etiqueta and etiqueta.get("href") else url
    )
    # Un canonical fuera del dominio de la fuente (tipico de los agregadores
    # que sindican a terceros) apunta al medio original. Se anota como origen
    # externo, pero la URL con la que trabajamos sigue siendo la nuestra: es la
    # que descargamos y la que da el identificador.
    origen_externo = None
    if not fuente.acepta_host(canonica):
        origen_externo = canonica
        canonica = urlutil.normalizar(url)

    # -- titular -----------------------------------------------------------
    titular = datos.get("headline") if isinstance(datos.get("headline"), str) else None
    titular = (
        titular
        or _meta(sopa, "og:title", "twitter:title")
        or _texto(sopa.find("h1"))
        or (_texto(sopa.find("title")) or "").split(" | ")[0]
    )
    if not titular or len(titular.strip()) < 8:
        log.debug("sin titular utilizable en %s", url)
        return None

    # -- cuerpo ------------------------------------------------------------
    cuerpo, parrafos = extraer_cuerpo(sopa, fuente, datos)

    resumen = datos.get("description") if isinstance(datos.get("description"), str) else None
    resumen = (resumen or _meta(sopa, "og:description", "description", "twitter:description") or "").strip()

    entradilla = _texto(
        sopa.select_one(".standfirst, .article__standfirst, .ue-c-article__standfirst, "
                        "h2.subtitle, .article-subtitle, .subtitulo, .subtitle")
    )

    # -- firmas y etiquetas ------------------------------------------------
    autores = _personas(datos.get("author"))
    if not autores:
        autores = [
            _texto(nodo)
            for nodo in sopa.select("[rel=author], .author-name, .byline__name, .author a")[:5]
            if _texto(nodo)
        ]
    autores = [a for a in autores if 2 < len(a) < 60][:5]

    etiquetas: list[str] = []
    palabras_clave = datos.get("keywords")
    if isinstance(palabras_clave, str):
        palabras_clave = [k.strip() for k in palabras_clave.split(",")]
    etiquetas += [str(k).strip() for k in (palabras_clave or []) if str(k).strip()]

    news_keywords = _meta(sopa, "news_keywords", "article:tag")
    if news_keywords:
        etiquetas += [k.strip() for k in news_keywords.split(",") if k.strip()]

    etiquetas += [
        _texto(nodo)
        for nodo in sopa.select(".tags a, .article-tags a, .ue-c-article__tags a, [rel=tag]")[:15]
        if _texto(nodo)
    ]

    vistas: set[str] = set()
    etiquetas = [
        e for e in etiquetas
        if 1 < len(e) < 50 and not (e.lower() in vistas or vistas.add(e.lower()))
    ][:20]

    # -- fechas ------------------------------------------------------------
    publicada = (
        parsear_fecha(datos.get("datePublished"))
        or parsear_fecha(_meta(sopa, "article:published_time", "pubdate", "date", "publish-date"))
        or parsear_fecha(_texto(sopa.find("time")) or (sopa.find("time") or {}).get("datetime"))
        or (f"{urlutil.fecha_en_ruta(canonica)}T00:00:00Z" if urlutil.fecha_en_ruta(canonica) else None)
    )
    modificada = parsear_fecha(datos.get("dateModified")) or parsear_fecha(
        _meta(sopa, "article:modified_time", "lastModified")
    )

    # -- clasificacion -----------------------------------------------------
    seccion = datos.get("articleSection")
    if isinstance(seccion, list):
        seccion = seccion[0] if seccion else ""
    if not isinstance(seccion, str):
        seccion = ""
    seccion = seccion or _meta(sopa, "article:section") or ""

    categoria = taxonomy.clasificar(
        segmentos=urlutil.segmentos(canonica),
        seccion=seccion,
        etiquetas=etiquetas,
        texto=f"{titular} {entradilla} {resumen}",
        vertical_por_defecto=fuente.vertical,
        tema_por_defecto=fuente.tema_por_defecto,
    )
    vertical, tema = categoria.split("/", 1)

    idioma = (sopa.html.get("lang") if sopa.html else None) or fuente.idioma
    idioma = idioma.split("-")[0].lower()[:5] if idioma else fuente.idioma

    return {
        "id": urlutil.identificador(canonica, fuente.clave),
        "url": canonica,
        "origen_externo": origen_externo,
        "category": categoria,
        "vertical": vertical,
        "topic": tema,
        "topic_name": taxonomy.nombre_tema(categoria),
        "section": seccion,
        "title": titular.strip(),
        "standfirst": entradilla,
        "summary": resumen,
        "body": cuerpo,
        "paragraphs": parrafos,
        "word_count": len(cuerpo.split()),
        "authors": autores,
        "tags": etiquetas,
        "published_at": publicada,
        "modified_at": modificada,
        "language": idioma,
        "country": fuente.pais,
        "images": _imagenes(sopa, datos, url),
        "videos": [
            urljoin(url, nodo.get("src"))
            for nodo in sopa.select("video source[src], video[src]")[:5]
            if nodo.get("src")
        ],
        "is_premium": bool(sopa.select_one("[data-premium=true], .premium, .paywall"))
        or datos.get("isAccessibleForFree") is False,
        "source": fuente.clave,
        "source_name": fuente.nombre,
        "scraped_at": _ahora(),
    }
