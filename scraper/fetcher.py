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
                    if resp.encoding is None:
                        resp.encoding = resp.apparent_encoding or "utf-8"
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

    def es_texto(self, resp: Respuesta) -> bool:
        tipo = resp.content_type.lower()
        return any(t in tipo for t in TIPOS_ACEPTADOS) or "<" in resp.text[:200]

    def close(self) -> None:
        self.sesion.close()
