"""Lectura con un navegador de verdad, para las paginas escritas en JavaScript.

Hay medios cuyo servidor devuelve un armazon de cuatro kilobytes y montan el
texto despues, ya en el navegador. Ahi no hay nada que interpretar mejor: el
HTML que llega sencillamente no contiene la noticia, y da igual cuantos
selectores se prueben. La unica forma de leerlo es dejar que un navegador haga
su trabajo y mirar el resultado. Es el caso de FIFA.

Esto abre Chromium una vez por ejecucion y lo reutiliza. Es caro --de uno a
tres segundos por pagina, frente a doscientos milisegundos-- asi que solo lo
usan las fuentes que lo declaran con ``navegador=True``, y por eso el coste
queda acotado.

Playwright es opcional: si no esta instalado, las fuentes que lo necesiten se
saltan con un aviso y el resto de la ejecucion sigue igual.

Lo que esto **no** es: un ariete. Renderizar una pagina que el servidor nos
entrega de buena gana es lo que hace cualquier lector. Resolver desafios,
rotar direcciones o falsificar credenciales para entrar donde a un cliente
automatico se le ha cerrado la puerta es otra cosa, y aqui no se hace.
"""
from __future__ import annotations

import logging
import re

from .fetcher import Fetcher, Respuesta
from .urls import host_de

log = logging.getLogger(__name__)

# Segundos de espera por pagina. Mas que esto casi nunca es una pagina lenta,
# es una que no va a cargar.
ESPERA = 20_000
# Lo que se espera despues de cargar, para que la aplicacion pinte su contenido.
ASENTARSE = 1_200

_ES_DATO = re.compile(r"\.(xml|rss|atom|json)(\?|$)|/rss/|/feeds?/", re.I)

# Botones de "aceptar cookies", por si la pagina no pinta el articulo hasta que
# alguien responde. Es lo primero que hace cualquier lector, y sin ello lo unico
# que se recoge es el propio aviso --que es largo y pasa cualquier minimo de
# palabras, asi que se cuela como si fuera la noticia--.
CONSENTIMIENTO = (
    "#onetrust-accept-btn-handler",
    "button#didomi-notice-agree-button",
    "button[aria-label*='Accept' i]",
    "button:has-text('Accept all')",
    "button:has-text('Accept All')",
    "button:has-text('I accept')",
    "button:has-text('Aceptar todas')",
    "button:has-text('Aceitar todos')",
)


class NavegadorNoDisponible(RuntimeError):
    """Playwright no esta instalado o Chromium no arranca."""


class Navegador:
    """Un Chromium compartido que responde con la misma forma que ``Fetcher``.

    Expone ``get`` devolviendo una ``Respuesta``, asi que el descubrimiento y el
    parseo funcionan sin enterarse de que detras hay un navegador. Lo que no
    tiene que ver con descargar --robots.txt, los sitemaps declarados-- se
    delega en el ``Fetcher`` de siempre, incluido el respeto a robots.txt.
    """

    def __init__(self, fetcher: Fetcher):
        self.fetcher = fetcher
        self._play = None
        self._navegador = None
        self._contexto = None
        self._pagina = None
        self.stats = {"paginas": 0, "datos": 0, "errores": 0}

    # -- ciclo de vida ----------------------------------------------------
    def abrir(self) -> None:
        if self._contexto is not None:
            return
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:      # pragma: no cover - depende del entorno
            raise NavegadorNoDisponible(
                "falta playwright: pip install playwright && playwright install chromium"
            ) from exc

        try:
            self._play = sync_playwright().start()
            self._navegador = self._play.chromium.launch(
                args=["--no-sandbox", "--disable-dev-shm-usage"]
            )
            # Se mantiene nuestra identificacion: el navegador esta aqui para
            # pintar la pagina, no para disimular quien la pide.
            self._contexto = self._navegador.new_context(
                user_agent=self.fetcher.user_agent,
                locale="en-US",
                viewport={"width": 1366, "height": 900},
            )
            self._pagina = self._contexto.new_page()
        except Exception as exc:        # pragma: no cover - depende del entorno
            self.cerrar()
            raise NavegadorNoDisponible(f"Chromium no arranco: {exc}") from exc

        log.info("navegador listo")

    def cerrar(self) -> None:
        for cerrar in (
            getattr(self._contexto, "close", None),
            getattr(self._navegador, "close", None),
            getattr(self._play, "stop", None),
        ):
            if cerrar:
                try:
                    cerrar()
                except Exception:
                    pass
        self._play = self._navegador = self._contexto = self._pagina = None

    def __enter__(self) -> "Navegador":
        self.abrir()
        return self

    def __exit__(self, *_) -> None:
        self.cerrar()

    # -- descarga ---------------------------------------------------------
    def get(self, url: str) -> Respuesta | None:
        """Misma firma que ``Fetcher.get``, pero pasando por el navegador."""
        if self._contexto is None:
            self.abrir()

        if not self.fetcher.permitido(url):
            return None

        self.fetcher.reloj.esperar(host_de(url))

        # Un feed o un sitemap ya viene escrito: no hay nada que pintar, y
        # abrirlo en una pestaña solo gastaria tiempo.
        if _ES_DATO.search(url):
            return self._pedir_dato(url)

        return self._pintar(url)

    def _pedir_dato(self, url: str) -> Respuesta | None:
        try:
            resp = self._contexto.request.get(url, timeout=ESPERA)
        except Exception as exc:
            self.stats["errores"] += 1
            log.debug("el navegador no pudo pedir %s (%s)", url, exc)
            return None

        self.stats["datos"] += 1
        if resp.status != 200:
            log.debug("GET %s -> HTTP %s (navegador)", url, resp.status)
            return None
        return Respuesta(url=resp.url, status=resp.status, text=resp.text(),
                         content_type=resp.headers.get("content-type", ""))

    def _pintar(self, url: str) -> Respuesta | None:
        try:
            respuesta = self._pagina.goto(url, timeout=ESPERA, wait_until="domcontentloaded")
            self._descartar_aviso()
            # Lo que la pagina monta despues de cargar es justo lo que venimos a
            # buscar, asi que hay que darle un momento.
            self._pagina.wait_for_timeout(ASENTARSE)
            html = self._pagina.content()
        except Exception as exc:
            self.stats["errores"] += 1
            log.debug("el navegador no pudo pintar %s (%s)", url, exc)
            return None

        self.stats["paginas"] += 1
        estado = respuesta.status if respuesta is not None else 200
        if estado >= 400:
            log.debug("GET %s -> HTTP %s (navegador)", url, estado)
            return None

        return Respuesta(url=self._pagina.url, status=estado, text=html,
                         content_type="text/html")

    def _descartar_aviso(self) -> None:
        """Cierra el aviso de cookies si lo hay. Si no lo hay, no pasa nada."""
        for selector in CONSENTIMIENTO:
            try:
                boton = self._pagina.locator(selector).first
                if boton.is_visible(timeout=600):
                    boton.click(timeout=1_500)
                    self._pagina.wait_for_timeout(400)
                    return
            except Exception:
                continue

    # -- lo que no es descargar se delega ---------------------------------
    def permitido(self, url: str) -> bool:
        return self.fetcher.permitido(url)

    def sitemaps_de(self, url: str) -> list[str]:
        return self.fetcher.sitemaps_de(url)

    def es_texto(self, resp: Respuesta) -> bool:
        return self.fetcher.es_texto(resp)

    @property
    def user_agent(self) -> str:
        return self.fetcher.user_agent
