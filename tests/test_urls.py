"""Normalizacion de URLs: lo que evita guardar dos veces la misma noticia."""
import pytest

from scraper import urls


@pytest.mark.parametrize("entrada, esperada", [
    ("http://WWW.BBC.com/news/articles/abc/", "https://bbc.com/news/articles/abc"),
    ("https://bbc.com/news/articles/abc#seccion", "https://bbc.com/news/articles/abc"),
    ("https://bbc.com/news?utm_source=twitter&utm_medium=social",
     "https://bbc.com/news"),
    # Un parametro que si cambia la pagina se conserva.
    ("https://timesofindia.indiatimes.com/x.cms?id=42", "https://timesofindia.indiatimes.com/x.cms?id=42"),
    ("https://techcrunch.com/2026/08/25/algo/amp", "https://techcrunch.com/2026/08/25/algo"),
    ("https://cnn.com//world//europe/", "https://cnn.com/world/europe"),
])
def test_normalizar(entrada, esperada):
    assert urls.normalizar(entrada) == esperada


def test_normalizar_es_idempotente():
    una_vez = urls.normalizar("http://WWW.Marca.com/futbol/?utm_campaign=x")
    assert urls.normalizar(una_vez) == una_vez


def test_segmentos_quita_fecha_y_fichero():
    assert urls.segmentos("https://marca.com/futbol/real-madrid/2026/08/19/68a1.html") == [
        "futbol", "real-madrid"
    ]
    assert urls.segmentos("https://theverge.com/tech/123456/un-titular") == ["tech"]


def test_fecha_en_ruta():
    assert urls.fecha_en_ruta("https://nytimes.com/2026/08/25/world/x.html") == "2026-08-25"
    assert urls.fecha_en_ruta("https://aljazeera.com/news/2026/8/5/algo") == "2026-08-05"
    assert urls.fecha_en_ruta("https://bbc.com/news/articles/abc") is None


def test_identificador_estable_y_propio_de_cada_fuente():
    url = "https://bbc.com/news/articles/abc"
    assert urls.identificador(url, "bbc") == urls.identificador(url + "/", "bbc")
    # La misma URL en dos fuentes distintas nunca colisiona.
    assert urls.identificador(url, "bbc") != urls.identificador(url, "cnn")
