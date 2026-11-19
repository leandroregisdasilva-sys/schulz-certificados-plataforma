import re
import socket
import subprocess
import sys
from pathlib import Path

import app

RAIZ = Path(__file__).resolve().parent.parent
BAT = RAIZ / "iniciar_aplicacao_schulz.bat"


def test_porta_livre_pula_porta_ocupada():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as ocupada:
        ocupada.bind(("127.0.0.1", 0))
        ocupada.listen()
        porta = ocupada.getsockname()[1]
        assert app.porta_livre(porta) != porta


def test_aguardar_retorna_falso_sem_servidor():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        porta = s.getsockname()[1]
    assert app.aguardar(porta, limite=1.0) is False


def test_app_sobe_e_responde():
    porta = app.porta_livre(18080)
    processo = subprocess.Popen([sys.executable, str(RAIZ / "app.py"), "--porta", str(porta)],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        assert app.aguardar(porta, limite=60)
    finally:
        processo.terminate()
        processo.wait(timeout=10)


def test_requirements_na_raiz_cobre_as_dependencias():
    nomes = {re.split(r"[\[<>=]", linha)[0].strip().lower()
             for linha in (RAIZ / "requirements.txt").read_text().splitlines() if linha.strip()}
    assert {"openpyxl", "reportlab", "pillow", "boto3", "moto", "cryptography"} <= nomes


def test_bat_tem_os_quatro_requisitos_do_roteiro():
    bruto = BAT.read_bytes()
    assert bruto.isascii()
    assert b"\r\n" in bruto and b"\n" not in bruto.replace(b"\r\n", b"")
    texto = bruto.decode("ascii")
    assert "sys.version_info >= (3, 10)" in texto
    assert "-m venv .venv" in texto and "activate.bat" in texto
    assert "pip install -r requirements.txt" in texto
    assert "app.py --porta" in texto and 'start "" "http://' in texto
