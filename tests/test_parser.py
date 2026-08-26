"""La escalera de respaldos del parser, peldaño a peldaño."""
from scraper.parser import extraer_cuerpo, parsear, parsear_fecha
from bs4 import BeautifulSoup

from tests.fake_site import CANCHA, DIARIO, PIXEL, PARRAFOS, SitioFalso


def _pagina(sitio: SitioFalso, url: str) -> str:
    return sitio.paginas[url.rstrip("/")][0]


def test_peldano_1_json_ld(sitio):
    url = "https://diario.test/2026/08/25/cumbre-sin-acuerdo"
    articulo = parsear(_pagina(sitio, url), url, DIARIO)

    assert articulo["title"] == "La cumbre termina sin acuerdo tras cuatro horas"
    assert articulo["published_at"] == "2026-08-25T18:00:00Z"
    assert articulo["authors"] == ["Redaccion"]
    assert "cumbre" in articulo["tags"]
    assert articulo["word_count"] > 50
    assert articulo["images"], "la imagen del JSON-LD deberia estar"
    assert articulo["category"] == "news/world"


def test_peldano_2_selectores_de_la_fuente(sitio):
    """Sin JSON-LD: el cuerpo tiene que salir del selector declarado."""
    url = "https://cancha.test/futbol/1002-victoria-en-el-clasico"
    articulo = parsear(_pagina(sitio, url), url, CANCHA)

    assert articulo["title"] == "Victoria en el clasico con un gol en el descuento"
    assert articulo["word_count"] > 50
    assert articulo["category"] == "sports/soccer"
    assert articulo["published_at"] == "2026-08-25T20:00:00Z"


def test_peldano_4_densidad_de_texto(sitio):
    """Ni JSON-LD ni selectores: solo queda elegir el bloque con mas texto."""
    url = "https://pixel.test/news/a-long-awaited-game-finally-arrives"
    articulo = parsear(_pagina(sitio, url), url, PIXEL)

    assert articulo["word_count"] > 50
    assert PARRAFOS[0][:40] in articulo["body"]
    # Y lo que rodea al cuerpo se queda fuera.
    assert "Trending story" not in articulo["body"]
    assert "All rights reserved" not in articulo["body"]
    assert "Home" not in articulo["paragraphs"]


def test_el_ruido_no_entra_en_el_cuerpo(sitio):
    url = "https://diario.test/2026/08/25/cumbre-sin-acuerdo"
    articulo = parsear(_pagina(sitio, url), url, DIARIO)
    assert "no debe colarse" not in articulo["body"]


def test_una_pagina_sin_cuerpo_no_revienta():
    html = "<html><head><title>Un directo cualquiera</title></head><body><div id='app'></div></body></html>"
    articulo = parsear(html, "https://cancha.test/futbol/1-directo", CANCHA)
    assert articulo is not None
    assert articulo["word_count"] == 0     # lo descartara --min-palabras


def test_sin_titular_devuelve_none():
    assert parsear("<html><body><p>solo texto</p></body></html>",
                   "https://cancha.test/futbol/1-x", CANCHA) is None


def test_canonical_de_otro_dominio_se_anota_pero_no_manda():
    """Es el caso de los agregadores que sindican a terceros."""
    html = """<html><head><title>Una noticia sindicada larga</title>
    <link rel="canonical" href="https://otromedio.test/original">
    </head><body><h1>Una noticia sindicada larga</h1>
    <article class="nota"><p>%s</p><p>%s</p></article></body></html>""" % (PARRAFOS[0], PARRAFOS[1])
    articulo = parsear(html, "https://cancha.test/futbol/1-sindicada", CANCHA)
    assert articulo["url"].startswith("https://cancha.test")
    assert articulo["origen_externo"] == "https://otromedio.test/original"


def test_parsear_fecha_traga_de_todo():
    assert parsear_fecha("2026-08-25T18:00:00Z") == "2026-08-25T18:00:00Z"
    assert parsear_fecha("Mon, 25 Aug 2026 18:00:00 GMT") == "2026-08-25T18:00:00Z"
    assert parsear_fecha("2026-08-25") == "2026-08-25T00:00:00Z"
    assert parsear_fecha(["2026-08-25T18:00:00+00:00"]) == "2026-08-25T18:00:00Z"
    assert parsear_fecha("") is None
    assert parsear_fecha("no es una fecha") is None


def test_json_ld_con_coma_colgando():
    """Hay medios que emiten JSON invalido; no puede costar la noticia."""
    html = """<html><head><script type="application/ld+json">
    {"@type":"NewsArticle","headline":"Un titular que sobrevive al JSON roto",}
    </script></head><body><article class="nota"><p>%s</p><p>%s</p></article></body></html>""" % (
        PARRAFOS[0], PARRAFOS[1])
    articulo = parsear(html, "https://cancha.test/futbol/1-roto", CANCHA)
    assert articulo["title"] == "Un titular que sobrevive al JSON roto"


def test_el_json_ld_con_cuerpo_gana_al_que_no_lo_tiene():
    html = """<html><head>
    <script type="application/ld+json">{"@type":"WebPage","headline":"Bloque pobre"}</script>
    <script type="application/ld+json">{"@type":"NewsArticle","headline":"Bloque rico",
      "articleBody":"%s"}</script>
    </head><body></body></html>""" % (" ".join(PARRAFOS))
    sopa = BeautifulSoup(html, "lxml")
    from scraper.parser import _articulo_jsonld
    assert _articulo_jsonld(sopa)["headline"] == "Bloque rico"


def test_entrada_de_wordpress():
    """WordPress entrega el artículo ya troceado: no hay nada que adivinar."""
    from scraper.parser import parsear_wordpress
    from tests.fake_site import PRENSA, _entrada_wp

    entrada = _entrada_wp(1, "El Senado aprueba la enmienda tras un debate largo",
                          "2026-08-25T14:00:00", PARRAFOS, ["Politics", "Senate"])
    articulo = parsear_wordpress(entrada, PRENSA)

    assert articulo["title"].startswith("El Senado")
    assert articulo["word_count"] > 50
    assert articulo["published_at"] == "2026-08-25T14:00:00Z"
    assert articulo["authors"] == ["Firma Invitada"]
    assert "Politics" in articulo["tags"]
    assert articulo["images"][0]["caption"] == "Pie de foto."
    assert articulo["category"] == "news/politics"


def test_una_entrada_sin_enlace_se_descarta():
    from scraper.parser import parsear_wordpress
    from tests.fake_site import PRENSA

    assert parsear_wordpress({"title": {"rendered": "Sin enlace ninguno aqui"}}, PRENSA) is None


def test_el_aviso_de_cookies_no_cuela_como_cuerpo():
    """Es largo, sale en todas las páginas y pasa cualquier mínimo de palabras.

    Guardarlo no es fallar: es publicar basura en silencio, que es peor. Pasó de
    verdad con FIFA leída con navegador: veinticinco noticias con el mismo
    cuerpo de 419 palabras y ninguna fecha.
    """
    aviso = ("When you visit any website, it may store or retrieve information on your "
             "browser, mostly in the form of cookies. This information might be about you, "
             "your preferences or your device and is mostly used to make the site work as "
             "you expect it to. ")
    html = f"""<html><head><title>Un titular perfectamente normal</title>
    <meta property="og:title" content="Un titular perfectamente normal"></head>
    <body><div class="envoltorio-desconocido">
      <p>{aviso}</p><p>{aviso}</p><p>{aviso}</p><p>{aviso}</p>
    </div></body></html>"""

    articulo = parsear(html, "https://pixel.test/news/un-titular-perfectamente-normal", PIXEL)
    assert articulo is not None
    assert articulo["word_count"] == 0, "el aviso no puede contar como cuerpo"
