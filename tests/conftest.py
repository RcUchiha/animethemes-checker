"""
Permite que los tests importen los módulos del proyecto (comparador, modelos,
i18n) sin necesidad de instalar el paquete ni de depender del directorio
desde el que se invoque pytest.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
