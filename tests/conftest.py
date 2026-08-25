"""Utilidades comunes a los tests.

Todo corre sin red: ``Fetcher.get`` se sustituye por el sitio de mentira, asi
que la bateria entera se ejecuta igual en un portatil sin conexion que en CI.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from scraper.fetcher import Fetcher            # noqa: E402
from tests.fake_site import FUENTES, SitioFalso  # noqa: E402


@pytest.fixture
def sitio() -> SitioFalso:
    return SitioFalso()


@pytest.fixture
def fetcher(monkeypatch, sitio) -> Fetcher:
    """Un Fetcher que responde con el sitio de mentira y no espera entre peticiones."""
    cliente = Fetcher(delay=0.0, retries=1, respetar_robots=False)
    monkeypatch.setattr(Fetcher, "get", lambda self, url: sitio.get(url))
    return cliente


@pytest.fixture
def fuentes_falsas(monkeypatch):
    """Hace que el runner vea solo las tres fuentes inventadas."""
    import scraper.sources as sources

    def activas(claves=None):
        if not claves:
            return list(FUENTES)
        pedidas = {c.strip().lower() for c in claves}
        return [f for f in FUENTES if f.clave in pedidas]

    monkeypatch.setattr(sources, "activas", activas)
    monkeypatch.setattr("scraper.runner.sources.activas", activas)
    return FUENTES
