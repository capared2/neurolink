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

import html as htmlmod
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
    r"caption|byline|meta-|toolbar|inline-newsletter|read-more|leia-mais|"
    r"cookie|consent|gdpr|onetrust|privacy-(banner|notice)|didomi|sp_message)",
    re.I,
)

# El aviso de cookies es la trampa mas fea de todas: es largo, sale en todas
# las paginas del sitio y pasa de sobra cualquier minimo de palabras, asi que
# se cuela como si fuera el cuerpo de la noticia sin que nada chirrie. Un
# scraper que lo guarda no falla: publica basura en silencio, que es peor.
CONSENTIMIENTO = re.compile(
    r"when you visit any website|store or retrieve information on your browser|"
    r"strictly necessary cookies|these cookies (are|allow|enable)|"
    r"your (cookie|privacy) (preferences|settings)|manage consent|"
    r"utilizamos cookies|uso de cookies|politica de cookies|"
    r"usamos cookies|aceitar (todos os )?cookies",
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
# Un parrafo que es casi todo texto enlazado no es prosa: es una lista de
# enlaces promocionales --"Transfer Centre LIVE! | Fixtures & scores"-- que el
# medio intercala en el cuerpo.
MAX_DENSIDAD_ENLACE = 0.6

# URLs escritas dentro del texto. Se quitan por dos razones: quedan feas en una
# pagina que no enlaza a ninguna parte, y sobre todo dicen de que medio salio la
# noticia, que es justo lo que el sitio no debe enseñar.
URL_EN_TEXTO = re.compile(r"(?:https?://|www\.)\S+", re.I)


# Campos que se enseñan tal cual y no pueden llevar HTML a medio traducir.
_CAMPOS_DE_TEXTO = ("title", "standfirst", "summary", "body")


def pulir(registro: dict) -> dict:
    """Última pasada sobre un registro, venga de HTML o del REST de WordPress.

    Los dos caminos acababan guardando entidades sin traducir, así que la
    limpieza vive aquí y no repetida en cada uno.
    """
    for campo in _CAMPOS_DE_TEXTO:
        valor = registro.get(campo)
        if isinstance(valor, str) and valor:
            registro[campo] = desescapar(valor).replace("\xa0", " ").strip()
    # Los medios se etiquetan a sí mismos: Fox News marca sus noticias con
    # "fox news media", TechCrunch con "TechCrunch Disrupt 2026", Marca con
    # "Radio Marca". Eso va a `keywords` y a `mentions`, y el sitio que consume
    # esto no nombra ninguna fuente. Se quita el nombre del medio del que salió
    # **esa** noticia, no una lista fija: "Steam" es de dónde viene una noticia
    # de Steam, pero es el tema del que habla una de IGN.
    propio = tuple(
        n.lower() for n in (registro.get("source_name"), registro.get("source")) if n
    )
    registro["tags"] = [
        desescapar(t).strip()
        for t in registro.get("tags") or []
        if t and t.strip() and not any(n in t.lower() for n in propio)
    ]
    registro["authors"] = [desescapar(a).strip() for a in registro.get("authors") or []]
    for imagen in registro.get("images") or []:
        if imagen.get("caption"):
            imagen["caption"] = desescapar(imagen["caption"]).replace("\xa0", " ").strip()
    registro["word_count"] = len((registro.get("body") or "").split())
    return registro


def _texto(nodo) -> str:
    return re.sub(r"\s+", " ", nodo.get_text(" ", strip=True)).strip() if nodo else ""


# Un `&` escapado dos veces: `&amp;#8217;`. Al desescapar una vez queda
# `&#8217;`, que sigue siendo una entidad.
_ENTIDAD = re.compile(r"&(?:#\d+|#x[0-9a-fA-F]+|[a-zA-Z]{2,10});")


def desescapar(texto: str) -> str:
    """Deja el texto como se lee, sin entidades HTML a medio traducir.

    Cuatro de los medios publican el titular escapado dos veces: en el JSON-LD
    viene `&amp;#8217;`, que al pasar por el analizador de HTML se queda en
    `&#8217;` y así se guardaba. En la página se leía *Lorde says AI glasses
    are &#8216;not sexy&#8217;*, y eso mismo iba al `<title>`, al `headline`
    de los datos estructurados y a la tarjeta al compartir.

    Se desescapa hasta que deja de cambiar, con un tope: un texto que hable de
    entidades HTML no debe entrar en un bucle.
    """
    for _ in range(3):
        if not _ENTIDAD.search(texto):
            break
        siguiente = htmlmod.unescape(texto)
        if siguiente == texto:
            break
        texto = siguiente
    return texto


def limpiar_texto(texto: str) -> str:
    """Quita las URLs escritas dentro del texto y normaliza los espacios."""
    sin_urls = URL_EN_TEXTO.sub("", desescapar(texto or ""))
    # El `&nbsp;` desescapado es un espacio duro: se pasa a espacio normal para
    # que partir por palabras y contar el texto no dependa de él.
    return re.sub(r"\s{2,}", " ", sin_urls.replace("\xa0", " ")).strip(" |·-—,")


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
        if not texto or BASURA.match(texto):
            continue
        # Un bloque que es casi todo enlace es promocion, no noticia.
        enlazado = sum(len(_texto(a)) for a in hijo.find_all("a"))
        if len(texto) and enlazado / len(texto) > MAX_DENSIDAD_ENLACE:
            continue
        texto = limpiar_texto(texto)
        if len(texto) < MIN_PARRAFO:
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


def _es_consentimiento(texto: str) -> bool:
    """True si lo que hemos recogido es el aviso de cookies y no la noticia."""
    return bool(texto) and bool(CONSENTIMIENTO.search(texto[:600]))


def extraer_cuerpo(sopa: BeautifulSoup, fuente: Fuente, datos: dict) -> tuple[str, list[str]]:
    """Devuelve ``(cuerpo, parrafos)`` bajando por la escalera de respaldos."""
    # 1. El propio JSON-LD, cuando trae el articulo entero.
    bruto = datos.get("articleBody")
    if isinstance(bruto, str) and len(bruto.split()) >= MIN_CUERPO_JSONLD \
            and not _es_consentimiento(bruto):
        # Hay medios que meten HTML dentro de articleBody. Tomarlo por texto
        # plano publicaba las etiquetas en crudo en la pagina --y con ellas las
        # direcciones del propio medio--, asi que se parsea igual que el resto.
        if "<" in bruto and ">" in bruto:
            cuerpo, parrafos = _texto_plano(bruto)
            if parrafos:
                return cuerpo, parrafos

        parrafos = [
            limpiar_texto(re.sub(r"\s+", " ", trozo))
            for trozo in re.split(r"\n{2,}|\r\n{2,}", bruto)
        ]
        parrafos = [p for p in parrafos if len(p) >= MIN_PARRAFO]
        if not parrafos:
            suelto = limpiar_texto(re.sub(r"\s+", " ", bruto))
            parrafos = [suelto] if suelto else []
        if parrafos:
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
            cuerpo = "\n\n".join(parrafos)
            if not _es_consentimiento(cuerpo):
                return cuerpo, parrafos

    # 4. Densidad de texto.
    contenedor = _mejor_contenedor(sopa)
    if contenedor is not None:
        parrafos = _parrafos_de(contenedor)
        cuerpo = "\n\n".join(parrafos)
        # Mejor sin cuerpo que con el aviso de cookies: sin cuerpo la descarta
        # --min-words y se reintenta; con el aviso se guarda como si fuera una
        # noticia y ya nadie lo mira.
        if parrafos and not _es_consentimiento(cuerpo):
            return cuerpo, parrafos

    return "", []


# ---------------------------------------------------------------------------
# Imagenes
# ---------------------------------------------------------------------------

# Palabras que empiezan una frase en mayúscula sin ser un nombre propio. Sin
# esta lista, "The", "But" o "Este" salen como si fueran entidades.
_ARRANQUES = {
    # inglés
    "the", "a", "an", "this", "that", "these", "those", "but", "and", "for",
    "however", "meanwhile", "after", "before", "when", "while", "if", "as",
    "it", "he", "she", "they", "we", "you", "there", "here", "his", "her",
    "their", "our", "its", "at", "in", "on", "of", "to", "with", "by", "from",
    "what", "who", "why", "how", "now", "then", "so", "no", "not", "yes",
    "one", "two", "three", "first", "last", "next", "more", "most", "many",
    "all", "both", "each", "every", "some", "any", "other", "another", "such",
    "asked", "said", "added", "speaking", "according", "despite", "although",
    "read", "watch", "listen", "follow", "subscribe", "sign", "click", "get",
    # español y portugués
    "el", "la", "los", "las", "un", "una", "unos", "unas", "este", "esta",
    "ese", "esa", "aquel", "pero", "y", "o", "por", "para", "con", "sin",
    "que", "cuando", "mientras", "aunque", "además", "también", "tras",
    "os", "as", "um", "uma", "esse", "aquele", "mas", "e", "de",
    "do", "da", "dos", "das", "na", "nos", "nas", "em", "com", "sem",
    "porque", "quando", "enquanto", "embora", "segundo", "após", "antes",
}

# Un mes, un día de la semana o el pie de una foto no son entidades, y salen
# en mayúscula constantemente. "Foto" por sí sola aparecía 464 veces en el
# archivo, que es el crédito de las fotos de un medio, no un tema.
_NO_SON_ENTIDAD = {
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
    "septiembre", "octubre", "noviembre", "diciembre",
    "lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo",
    "janeiro", "fevereiro", "março", "maio", "junho", "julho", "setembro",
    "outubro", "novembro", "dezembro", "segunda", "terça", "quarta", "quinta",
    "sexta", "sábado", "domingo",
    "foto", "fotos", "vídeo", "video", "imagen", "imagem", "arquivo",
    "divulgação", "reprodução", "crédito", "getty", "reuters", "ap", "afp",
    "epa", "alamy", "pa media", "shutterstock",
}

# Un nombre propio: una o varias palabras capitalizadas seguidas, admitiendo
# los enlaces internos que llevan ("de", "of", "van", "dos"...). Se dejan fuera
# a propósito "and" e "y": no unen un nombre, separan dos, y con ellos dentro
# "Rodri and Bernardo Silva" salía como una sola entidad.
_NOMBRE_PROPIO = re.compile(
    r"\b([A-ZÁÉÍÓÚÑÀÂÃÇÊÔÕÜ][\wÁÉÍÓÚÑàáâãçéêíóôõúü'’-]{1,}"
    r"(?:[ \t]+(?:de|del|of|the|da|do|dos|das|van|von|di|la|le|el)?[ \t]*"
    r"[A-ZÁÉÍÓÚÑÀÂÃÇÊÔÕÜ][\wÁÉÍÓÚÑàáâãçéêíóôõúü'’-]{1,}){0,3})"
)

# Final de frase: lo que va justo antes de una mayúscula que solo lo es por
# abrir la oración.
_ARRANQUE_DE_FRASE = re.compile(r"(?:^|[.!?¿¡:;\n\r\"“”«»])\s*$")


def entidades_del_texto(
    titular: str, cuerpo: str, prohibidos: tuple[str, ...] = (), tope: int = 12
) -> list[str]:
    """Nombres propios de una noticia, cuando el medio no publica etiquetas.

    Un tercio del archivo llega sin etiquetas, y sin ellas la noticia sale sin
    ``mentions`` ni ``keywords``: justo lo que un buscador generativo mira para
    saber de qué va algo sin leérselo entero.

    No es reconocimiento de entidades de verdad --eso pide un modelo, y esto
    corre por cada artículo de cada pasada--, sino lo que se saca gratis del
    texto: secuencias capitalizadas, ordenadas por cuántas veces salen. En una
    noticia eso son las personas, los equipos, las empresas y los lugares.

    La prueba de que una mayúscula es un nombre propio y no el arranque de una
    frase es haberla visto alguna vez **en mitad** de una oración. "Bouaddi ha
    firmado" no demuestra nada; "el fichaje de Bouaddi" sí. Un candidato que
    solo aparece abriendo frase se descarta, salvo que venga en el titular.

    ``prohibidos`` se usa para que el nombre del medio no acabe de etiqueta:
    "Sky Sports News understands..." abre cientos de noticias, y publicar eso
    como entidad delataría de dónde salió la noticia.
    """
    texto = f"{titular}. {cuerpo[:6000]}"
    corte = len(titular) + 2  # donde acaba el titular dentro de `texto`
    # Medio archivo llega con el titular en mayúsculas iniciales al estilo
    # anglosajón ("Applied Materials Is Positioned to Capture More Growth").
    # Ahí la mayúscula no dice nada, y de un titular así salía "Capture More
    # Growth" como si fuera una empresa: cuando el titular es de este tipo, se
    # exige que la entidad aparezca también en el cuerpo.
    palabras = [p for p in titular.split() if len(p) > 3]
    titular_decorativo = bool(palabras) and sum(
        1 for p in palabras if p[:1].isupper()
    ) >= len(palabras) * 0.75
    veta = tuple(p.lower() for p in prohibidos if p)
    cuenta: dict[str, int] = {}
    suelta: dict[str, bool] = {}
    en_cuerpo: dict[str, bool] = {}
    forma: dict[str, str] = {}

    for coincidencia in _NOMBRE_PROPIO.finditer(texto):
        nombre = re.sub(r"['’]s\b", "", coincidencia.group(1)).strip(" .,'’-")
        if not (2 < len(nombre) < 50):
            continue
        # Lo que queda con apóstrofo es una contracción --"I'm", "it's"--, no
        # el nombre de nadie.
        if "'" in nombre or "’" in nombre:
            continue
        if nombre.split()[0].lower() in _ARRANQUES:
            continue
        if nombre.lower() in _NO_SON_ENTIDAD:
            continue
        if nombre.isupper() and len(nombre) > 6:
            continue  # un trozo de titular en mayúsculas, no una sigla
        clave = nombre.lower()
        if any(v in clave or clave in v for v in veta):
            continue
        cuenta[clave] = cuenta.get(clave, 0) + 1
        if coincidencia.start() >= corte:
            en_cuerpo[clave] = True
        if not _ARRANQUE_DE_FRASE.search(texto[max(0, coincidencia.start() - 40):coincidencia.start()]):
            suelta[clave] = True
        forma.setdefault(clave, nombre)

    titular_bajo = titular.lower()
    candidatos = [
        (clave, veces)
        for clave, veces in cuenta.items()
        if (suelta.get(clave) or clave in titular_bajo or veces > 2)
        and (en_cuerpo.get(clave) or not titular_decorativo)
        and (" " in clave or veces > 1 or clave in titular_bajo)
    ]
    # Primero lo que más se repite y, a igualdad, el nombre más específico.
    candidatos.sort(key=lambda par: (-par[1], -len(par[0])))

    elegidas: list[str] = []
    for clave, _ in candidatos:
        # "Manchester City" ya cubre a "City": no hacen falta las dos.
        if any(clave in otra or otra in clave for otra in (e.lower() for e in elegidas)):
            continue
        elegidas.append(forma[clave])
        if len(elegidas) == tope:
            break
    return elegidas


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

def _texto_plano(html: str) -> tuple[str, list[str]]:
    """Parrafos de un trozo de HTML suelto, sin pagina alrededor.

    Reutiliza el mismo filtrado que el cuerpo de una pagina --ruido, promocion
    disfrazada de parrafo, URLs escritas dentro del texto--, porque el HTML que
    llega en un `articleBody` o en un `content.rendered` trae exactamente la
    misma basura que el de la pagina.
    """
    sopa = BeautifulSoup(html or "", "lxml")
    parrafos = _parrafos_de(sopa)
    if not parrafos:
        suelto = limpiar_texto(_texto(sopa))
        if len(suelto) >= MIN_PARRAFO:
            parrafos = [suelto]
    return "\n\n".join(parrafos), parrafos


def _incrustados(entrada: dict, clave: str) -> list[dict]:
    incrustado = entrada.get("_embedded") or {}
    valores = incrustado.get(clave) or []
    salida: list[dict] = []
    for valor in valores:
        if isinstance(valor, list):
            salida.extend(v for v in valor if isinstance(v, dict))
        elif isinstance(valor, dict):
            salida.append(valor)
    return salida


def parsear_wordpress(entrada: dict, fuente: Fuente) -> dict | None:
    """Convierte una entrada del REST de WordPress en un registro nuestro.

    WordPress publica el articulo ya troceado --titulo, cuerpo, extracto,
    fechas, firma y terminos--, asi que no hay nada que adivinar: es el mismo
    registro que sale de una pagina HTML, pero sin heuristica de por medio.
    """
    enlace = entrada.get("link")
    if not isinstance(enlace, str) or not enlace.startswith("http"):
        return None

    titular = _texto(BeautifulSoup(
        (entrada.get("title") or {}).get("rendered", ""), "lxml"))
    if not titular or len(titular) < 8:
        return None

    canonica = urlutil.normalizar(enlace)
    cuerpo, parrafos = _texto_plano((entrada.get("content") or {}).get("rendered", ""))
    resumen = _texto(BeautifulSoup(
        (entrada.get("excerpt") or {}).get("rendered", ""), "lxml"))

    autores = [
        a["name"] for a in _incrustados(entrada, "author")
        if isinstance(a.get("name"), str)
    ]
    if not autores and isinstance(entrada.get("byline"), str):
        autores = [_texto(BeautifulSoup(entrada["byline"], "lxml"))]

    # `wp:term` trae categorias y etiquetas ya con su nombre.
    terminos = [
        t["name"] for t in _incrustados(entrada, "wp:term")
        if isinstance(t.get("name"), str)
    ]
    seccion = terminos[0] if terminos else ""

    imagenes: list[dict] = []
    for medio in _incrustados(entrada, "wp:featuredmedia"):
        fuente_medio = medio.get("source_url")
        if isinstance(fuente_medio, str) and fuente_medio.startswith("http"):
            imagenes.append({
                "url": fuente_medio,
                "caption": _texto(BeautifulSoup(
                    (medio.get("caption") or {}).get("rendered", ""), "lxml")),
            })
            break

    categoria = taxonomy.clasificar(
        segmentos=urlutil.segmentos(canonica),
        seccion=seccion,
        etiquetas=terminos,
        texto=f"{titular} {resumen}",
        vertical_por_defecto=fuente.vertical,
        tema_por_defecto=fuente.tema_por_defecto,
    )
    vertical, tema = categoria.split("/", 1)

    return pulir({
        "id": urlutil.identificador(canonica, fuente.clave),
        "url": canonica,
        "origen_externo": None,
        "category": categoria,
        "vertical": vertical,
        "topic": tema,
        "topic_name": taxonomy.nombre_tema(categoria),
        "section": seccion,
        "title": titular,
        "standfirst": "",
        "summary": resumen,
        "body": cuerpo,
        "word_count": len(cuerpo.split()),
        "authors": autores[:5],
        "tags": terminos[:20],
        "published_at": parsear_fecha(entrada.get("date_gmt") or entrada.get("date")),
        "modified_at": parsear_fecha(entrada.get("modified_gmt") or entrada.get("modified")),
        "language": fuente.idioma,
        "country": fuente.pais,
        "images": imagenes,
        "videos": [],
        "is_premium": False,
        "source": fuente.clave,
        "source_name": fuente.nombre,
        "scraped_at": _ahora(),
    })


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

    # Un tercio de los medios no publica etiquetas de ningún tipo. Antes esas
    # noticias se guardaban con la lista vacía y salían sin `mentions` ni
    # `keywords`, que es lo que mira un buscador generativo para citarlas.
    if not etiquetas:
        etiquetas = entidades_del_texto(
            titular, cuerpo, prohibidos=(fuente.nombre, fuente.clave)
        )

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

    return pulir({
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
    })
