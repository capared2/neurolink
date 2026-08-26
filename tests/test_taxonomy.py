"""Clasificacion: el pegamento que junta /futbol/ con /soccer/."""
import pytest

from scraper import taxonomy


@pytest.mark.parametrize("segmentos, seccion, etiquetas, texto, vertical, esperada", [
    (["sport", "football"], "Sport", [], "Match report", "news", "sports/soccer"),
    (["futbol", "real-madrid"], "Real Madrid", [], "Gol en el descuento", "sports", "sports/soccer"),
    (["esporte", "futebol"], "Futebol", [], "O jogo terminou", "news", "sports/soccer"),
    (["nfl", "story"], "NFL", [], "Quarterback trade", "sports", "sports/nfl"),
    (["baloncesto", "nba"], "NBA", [], "Triple sobre la bocina", "sports", "sports/basketball"),
    (["news"], "Technology", ["OpenAI"], "OpenAI raises a round", "news", "tech/ai"),
    (["articles"], "", ["Valorant"], "Valorant Champions roster shuffle", "gaming", "gaming/esports"),
    (["world"], "World", [], "Cumbre entre gobiernos", "news", "news/world"),
    (["business"], "Business", [], "La inflacion sube otra vez", "news", "news/business"),
])
def test_clasifica_donde_toca(segmentos, seccion, etiquetas, texto, vertical, esperada):
    assert taxonomy.clasificar(segmentos, seccion, etiquetas, texto, vertical) == esperada


def test_un_tema_concreto_gana_al_paraguas():
    """/sport/football/ tiene que ser futbol, no 'mas deporte'."""
    assert taxonomy.clasificar(["sport", "football"], "Sport", [], "", "news") == "sports/soccer"
    # Y sin mas pistas que el paraguas, se queda en el cajon.
    assert taxonomy.clasificar(["sport"], "Sport", [], "Una cronica", "news") == "sports/more"


def test_sin_ninguna_pista_usa_el_defecto_de_la_fuente():
    assert taxonomy.clasificar([], "", [], "", "gaming", "gaming/esports") == "gaming/esports"
    assert taxonomy.clasificar([], "", [], "", "tech") == "tech/gadgets"


def test_toda_clave_devuelta_existe_de_verdad():
    clave = taxonomy.clasificar(["futbol"], "", [], "", "sports")
    assert clave in taxonomy.TEMAS_POR_CLAVE
    assert clave.split("/")[0] in taxonomy.VERTICALES


def test_los_temas_no_se_repiten():
    claves = [t.clave for t in taxonomy.TEMAS]
    assert len(claves) == len(set(claves))


def test_es_reproducible():
    """Dos ejecuciones iguales no pueden clasificar distinto."""
    argumentos = (["news"], "Sport", ["football"], "Arsenal wins", "news")
    assert taxonomy.clasificar(*argumentos) == taxonomy.clasificar(*argumentos)
