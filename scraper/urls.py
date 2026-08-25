"""Normalizacion de URLs e identificadores de noticia.

Veinte medios significan veinte formas de escribir la misma direccion. Aqui se
reducen todas a una sola forma canonica para que el estado (``seen.txt``) y la
deduplicacion no cuenten dos veces la misma noticia.
"""
from __future__ import annotations

import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Parametros que solo sirven para medir campanas y ensucian la URL.
PREFIJOS_SEGUIMIENTO = (
    "utm_", "cmpid", "intcmp", "icid", "ncid", "xtor", "s_kw", "fb_", "gclid",
    "smid", "smtyp", "partner", "taid", "cmp", "ito", "at_", "sh_", "ref_src",
    "ref_url", "mc_cid", "mc_eid", "spm", "__twitter", "guccounter", "guce_",
)

# Parametros que si cambian la pagina y hay que conservar.
PARAMETROS_UTILES = {"id", "p", "page", "story", "appid", "articleshow", "curid", "view"}

_FECHA = re.compile(r"/((?:19|20)\d{2})/(\d{1,2})(?:/(\d{1,2}))?/")


def normalizar(url: str) -> str:
    """Forma canonica: https, sin fragmento, sin rastreo y sin barra final."""
    partes = urlsplit(url.strip())
    esquema = "https" if partes.scheme in ("", "http", "https") else partes.scheme
    host = partes.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    if "@" in host:                      # credenciales incrustadas: fuera
        host = host.rsplit("@", 1)[-1]
    host = host.removesuffix(":443").removesuffix(":80")

    consulta = urlencode(
        [
            (clave, valor)
            for clave, valor in parse_qsl(partes.query, keep_blank_values=False)
            if clave.lower() in PARAMETROS_UTILES
            or not clave.lower().startswith(PREFIJOS_SEGUIMIENTO)
        ]
    )

    ruta = partes.path or "/"
    if len(ruta) > 1:
        ruta = ruta.rstrip("/") or "/"
    ruta = re.sub(r"/{2,}", "/", ruta)
    # Variantes de AMP: son la misma noticia en otra plantilla.
    ruta = re.sub(r"(\.amp|/amp)$", "", ruta, flags=re.I)

    return urlunsplit((esquema, host, ruta, consulta, ""))


def host_de(url: str) -> str:
    return urlsplit(url).netloc.lower().removeprefix("www.")


def origen_de(url: str) -> str:
    partes = urlsplit(url)
    return f"{partes.scheme or 'https'}://{partes.netloc}"


def segmentos(url: str) -> list[str]:
    """Segmentos de la ruta, sin la fecha ni el nombre del fichero.

    De ``/futbol/real-madrid/2026/08/19/68a1.html`` salen ``['futbol',
    'real-madrid']``, que es lo que la taxonomia necesita para clasificar.
    """
    ruta = urlsplit(url).path
    corte = _FECHA.search(ruta)
    if corte:
        ruta = ruta[: corte.start()]
    else:
        ruta = ruta.rsplit("/", 1)[0]
    limpios = []
    for bruto in ruta.split("/"):
        if not bruto or re.fullmatch(r"\d+", bruto):
            continue
        limpios.append(bruto.removesuffix(".html").removesuffix(".ghtml"))
    return limpios


def fecha_en_ruta(url: str) -> str | None:
    """Fecha ISO codificada en la URL, si la lleva."""
    encontrada = _FECHA.search(urlsplit(url).path)
    if not encontrada:
        return None
    ano, mes, dia = encontrada.group(1), encontrada.group(2), encontrada.group(3) or "01"
    return f"{ano}-{int(mes):02d}-{int(dia):02d}"


def identificador(url: str, clave_fuente: str) -> str:
    """Identificador estable, unico entre fuentes y opaco.

    La clave de la fuente entra en el hash, no delante de el. Asi dos medios
    que usen el mismo numero de noticia nunca colisionan, pero el identificador
    --que acaba siendo parte de la URL publica del sitio-- no dice de donde
    salio la noticia. Es un hash de la URL canonica: cabe en una ruta, no
    cambia entre ejecuciones y no depende de que el medio publique un id propio.
    """
    semilla = f"{clave_fuente}\0{normalizar(url)}".encode("utf-8")
    return hashlib.sha1(semilla).hexdigest()[:14]
