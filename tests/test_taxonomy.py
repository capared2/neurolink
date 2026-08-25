"""Clasificacion: el pegamento que junta /futbol/ con /soccer/."""
import pytest

from scraper import taxonomy


@pytest.mark.parametrize("segmentos, seccion, etiquetas, texto, vertical, esperada", [
    (["sport", "football"], "Sport", [], "Match report", "noticias", "deportes/futbol"),
    (["futbol", "real-madrid"], "Real Madrid", [], "Gol en el descuento", "deportes", "deportes/futbol"),
    (["esporte", "futebol"], "Futebol", [], "O jogo terminou", "noticias", "deportes/futbol"),
    (["nfl", "story"], "NFL", [], "Quarterback trade", "deportes", "deportes/nfl"),
    (["baloncesto", "nba"], "NBA", [], "Triple sobre la bocina", "deportes", "deportes/baloncesto"),
    (["news"], "Technology", ["OpenAI"], "OpenAI raises a round", "noticias", "tecnologia/ia"),
    (["articles"], "", ["Valorant"], "Valorant Champions roster shuffle", "gamer", "gamer/esports"),
    (["world"], "World", [], "Cumbre entre gobiernos", "noticias", "noticias/mundo"),
    (["business"], "Business", [], "La inflacion sube otra vez", "noticias", "noticias/economia"),
])
def test_clasifica_donde_toca(segmentos, seccion, etiquetas, texto, vertical, esperada):
    assert taxonomy.clasificar(segmentos, seccion, etiquetas, texto, vertical) == esperada


def test_un_tema_concreto_gana_al_paraguas():
    """/sport/football/ tiene que ser futbol, no 'mas deporte'."""
    assert taxonomy.clasificar(["sport", "football"], "Sport", [], "", "noticias") == "deportes/futbol"
    # Y sin mas pistas que el paraguas, se queda en el cajon.
    assert taxonomy.clasificar(["sport"], "Sport", [], "Una cronica", "noticias") == "deportes/otros"


def test_sin_ninguna_pista_usa_el_defecto_de_la_fuente():
    assert taxonomy.clasificar([], "", [], "", "gamer", "gamer/esports") == "gamer/esports"
    assert taxonomy.clasificar([], "", [], "", "tecnologia") == "tecnologia/gadgets"


def test_toda_clave_devuelta_existe_de_verdad():
    clave = taxonomy.clasificar(["futbol"], "", [], "", "deportes")
    assert clave in taxonomy.TEMAS_POR_CLAVE
    assert clave.split("/")[0] in taxonomy.VERTICALES


def test_los_temas_no_se_repiten():
    claves = [t.clave for t in taxonomy.TEMAS]
    assert len(claves) == len(set(claves))


def test_es_reproducible():
    """Dos ejecuciones iguales no pueden clasificar distinto."""
    argumentos = (["news"], "Sport", ["football"], "Arsenal wins", "noticias")
    assert taxonomy.clasificar(*argumentos) == taxonomy.clasificar(*argumentos)
