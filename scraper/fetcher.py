"""Capa HTTP: espaciado por host, reintentos y respeto a robots.txt.

La diferencia con un scraper de un solo medio esta en el espaciado. Con
veintiun dominios, un unico reloj global convertiria la ejecucion en una fila
india: cada host lleva el suyo, de modo que se puede ir rapido en conjunto sin
apretar a ninguno en particular.
"""
from __future__ import annotations

import logging
import random
import threading
import time
from dataclasses import dataclass
from urllib.robotparser import RobotFileParser

import requests

from . import config
from .urls import host_de, origen_de

log = logging.getLogger(__name__)

ESTADOS_REINTENTABLES = {408, 425, 429, 500, 502, 503, 504}
TIPOS_ACEPTADOS = ("html", "xml", "rss", "atom", "json", "text/plain")


@dataclass
class Respuesta:
    url: str
    status: int
    text: str
    content_type: str


class RelojPorHost:
    """Un minimo de segundos entre peticiones al mismo dominio."""

    def __init__(self, defecto: float):
        self.defecto = max(0.0, defecto)
        self._lock = threading.Lock()
        self._proxima: dict[str, float] = {}
        self._espera: dict[str, float] = {}

    def espera_de(self, host: str) -> float:
        return self._espera.get(host, self.defecto)

    def fijar(self, host: str, segundos: float) -> None:
        with self._lock:
            self._espera[host] = max(0.0, segundos)

    def esperar(self, host: str) -> None:
        with self._lock:
            retardo = self._espera.get(host, self.defecto)
            if retardo <= 0:
                return
            ahora = time.monotonic()
            proxima = self._proxima.get(host, 0.0)
            dormir = max(0.0, proxima - ahora)
            self._proxima[host] = max(ahora, proxima) + retardo
        if dormir > 0:
            time.sleep(dormir)


def _fijar_codificacion(resp) -> None:
    """Decide con qué codificación leer la respuesta.

    `requests` no deja `encoding` a `None` cuando el servidor manda
    `Content-Type: text/html` sin charset: pone ISO-8859-1, que es lo que dice
    el RFC 2616 y que casi nunca es verdad. Como la guarda solo miraba `None`,
    nunca saltaba, y los bytes UTF-8 se leían como latin-1: `£` salía `Â£` y
    `ñ` salía `Ã±`. Eran 162 noticias del archivo, titulares incluidos.

    Solo se respeta ISO-8859-1 si la cabecera lo dice con todas las letras;
    si no, se detecta a partir de los propios bytes.
    """
    declarada = "charset=" in resp.headers.get("Content-Type", "").lower()
    if resp.encoding is None or (not declarada and (resp.encoding or "").lower() in (
        "iso-8859-1", "latin-1", "latin1"
    )):
        resp.encoding = resp.apparent_encoding or "utf-8"


class Fetcher:
    """Cliente HTTP seguro entre hilos y educado con el origen."""

    def __init__(
        self,
        user_agent: str = config.DEFAULT_USER_AGENT,
        delay: float = config.DEFAULT_DELAY,
        timeout: int = config.DEFAULT_TIMEOUT,
        retries: int = config.DEFAULT_RETRIES,
        respetar_robots: bool = True,
    ):
        self.user_agent = user_agent
        self.timeout = timeout
        self.retries = retries
        self.respetar_robots = respetar_robots
        self.reloj = RelojPorHost(delay)
        self.sesion = requests.Session()
        self.sesion.headers.update({
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "es-ES,es;q=0.9,en;q=0.8,pt;q=0.7",
        })
        # Un pool por defecto de 10 conexiones estrangula a ocho hilos repartidos
        # entre veintiun dominios.
        adaptador = requests.adapters.HTTPAdapter(pool_connections=32, pool_maxsize=32)
        self.sesion.mount("https://", adaptador)
        self.sesion.mount("http://", adaptador)

        self._robots: dict[str, RobotFileParser | None] = {}
        self._robots_lock = threading.Lock()
        self.stats = {"peticiones": 0, "errores": 0, "bloqueadas": 0}
        self._stats_lock = threading.Lock()

    def _contar(self, clave: str) -> None:
        with self._stats_lock:
            self.stats[clave] += 1

    # -- robots -----------------------------------------------------------
    def _robots_de(self, url: str) -> RobotFileParser | None:
        origen = origen_de(url)
        with self._robots_lock:
            if origen in self._robots:
                return self._robots[origen]

        parser: RobotFileParser | None = None
        try:
            self._contar("peticiones")
            resp = self.sesion.get(f"{origen}/robots.txt", timeout=self.timeout)
            if resp.status_code == 200:
                parser = RobotFileParser()
                parser.parse(resp.text.splitlines())
                retardo = parser.crawl_delay(self.user_agent)
                host = host_de(url)
                if retardo and float(retardo) > self.reloj.espera_de(host):
                    log.info("%s pide crawl-delay=%ss; se respeta", host, retardo)
                    self.reloj.fijar(host, float(retardo))
        except requests.RequestException as exc:
            log.debug("sin robots.txt utilizable en %s (%s)", origen, exc)

        with self._robots_lock:
            self._robots[origen] = parser
        return parser

    def permitido(self, url: str) -> bool:
        if not self.respetar_robots:
            return True
        parser = self._robots_de(url)
        # Sin robots.txt legible no hay prohibicion que respetar.
        return True if parser is None else parser.can_fetch(self.user_agent, url)

    def sitemaps_de(self, url: str) -> list[str]:
        parser = self._robots_de(url)
        return list(getattr(parser, "sitemaps", None) or []) if parser else []

    # -- descarga ---------------------------------------------------------
    def get(self, url: str) -> Respuesta | None:
        if not self.permitido(url):
            self._contar("bloqueadas")
            log.debug("robots.txt no permite %s", url)
            return None

        host = host_de(url)
        ultimo: str | None = None

        for intento in range(1, self.retries + 1):
            self.reloj.esperar(host)
            try:
                self._contar("peticiones")
                resp = self.sesion.get(url, timeout=self.timeout, allow_redirects=True)
            except requests.RequestException as exc:
                ultimo = str(exc)
            else:
                if resp.status_code == 200:
                    _fijar_codificacion(resp)
                    return Respuesta(
                        url=str(resp.url),
                        status=resp.status_code,
                        text=resp.text,
                        content_type=resp.headers.get("Content-Type", ""),
                    )
                if resp.status_code == 429:
                    # Nos han pedido calma: se dobla la espera de ese host para
                    # el resto de la ejecucion, no solo para este reintento.
                    self.reloj.fijar(host, min(30.0, self.reloj.espera_de(host) * 2 or 2.0))
                if resp.status_code not in ESTADOS_REINTENTABLES:
                    log.debug("GET %s -> HTTP %s", url, resp.status_code)
                    self._contar("errores")
                    return None
                ultimo = f"HTTP {resp.status_code}"

            if intento < self.retries:
                espera = min(30.0, (2 ** intento) + random.uniform(0, 0.75))
                log.debug("reintento %s/%s de %s en %.1fs (%s)", intento, self.retries, url, espera, ultimo)
                time.sleep(espera)

        self._contar("errores")
        log.warning("se abandona %s (%s)", url, ultimo)
        return None

    def inspeccionar(self, url: str) -> dict:
        """Una sola peticion, sin reintentos, contando lo que de verdad pasa.

        ``get`` esconde el motivo de un fallo: devuelve ``None`` tanto si el
        medio contesto 403 como si se cayo la red. Cuando una fuente deja de
        rendir, ese motivo es justo lo unico que hace falta saber, asi que aqui
        se devuelve en crudo.
        """
        detalle: dict = {"url": url, "status": None, "error": None,
                         "content_type": "", "bytes": 0, "servidor": ""}
        try:
            self.reloj.esperar(host_de(url))
            resp = self.sesion.get(url, timeout=self.timeout, allow_redirects=True)
        except requests.RequestException as exc:
            detalle["error"] = type(exc).__name__
            return detalle

        detalle["status"] = resp.status_code
        detalle["content_type"] = resp.headers.get("Content-Type", "")
        detalle["bytes"] = len(resp.content)
        # Quien contesta suele delatar que hay un cortafuegos por delante.
        detalle["servidor"] = ", ".join(
            f"{c}={resp.headers[c]}"
            for c in ("Server", "CF-Ray", "X-Served-By", "Akamai-Grn")
            if c in resp.headers
        )
        detalle["final"] = str(resp.url)
        if resp.status_code == 200 and "json" in detalle["content_type"].lower():
            # Que devuelva JSON no basta: hay que saber si trae el cuerpo del
            # articulo o solo el titular, que es lo que decide si merece la pena
            # escribir un adaptador para esa API.
            try:
                datos = resp.json()
            except ValueError:
                datos = None
            muestra = datos[0] if isinstance(datos, list) and datos else datos
            if isinstance(muestra, dict):
                detalle["claves"] = sorted(muestra)[:25]
                largos = {}
                for clave, valor in muestra.items():
                    if isinstance(valor, str) and len(valor) > 200:
                        largos[clave] = len(valor)
                    elif isinstance(valor, dict):
                        for sub, subvalor in valor.items():
                            if isinstance(subvalor, str) and len(subvalor) > 200:
                                largos[f"{clave}.{sub}"] = len(subvalor)
                detalle["campos_largos"] = dict(sorted(largos.items(), key=lambda kv: -kv[1])[:5])

        if resp.status_code == 200:
            texto = resp.text
            detalle["json_incrustado"] = sorted(
                marca for marca in ("__NEXT_DATA__", "__NUXT__", "application/ld+json",
                                    "window.__INITIAL_STATE__", "self.__next_f")
                if marca in texto
            )
            detalle["tiene_h1"] = "<h1" in texto
        return detalle

    def es_texto(self, resp: Respuesta) -> bool:
        tipo = resp.content_type.lower()
        return any(t in tipo for t in TIPOS_ACEPTADOS) or "<" in resp.text[:200]

    def close(self) -> None:
        self.sesion.close()
