"""
Permite que los tests importen los módulos del proyecto (comparador, modelos,
i18n) sin necesidad de instalar el paquete ni de depender del directorio
desde el que se invoque pytest.
"""

import os
import sys
import urllib.request

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(autouse=True)
def _bloquear_red_real(monkeypatch):
    """
    Red de seguridad para los tests de animethemes_client/jikan_client/
    mal_scraper: si algún test olvida mockear la llamada de red (_get_json o
    _descargar_html_con_throttle), urlopen revienta con un error claro en
    vez de intentar golpear la API/página real — así un mock faltante se ve
    como test roto, no como un hang o una llamada real silenciosa.

    Los tres módulos hacen `import urllib.request` y llaman
    `urllib.request.urlopen(...)`, así que parchar el atributo una sola vez
    aquí cubre a los tres.
    """
    def _fallar(*args, **kwargs):
        raise AssertionError(
            "Intento de llamada de red real en un test — falta mockear urlopen/_get_json."
        )

    monkeypatch.setattr(urllib.request, "urlopen", _fallar)
