from __future__ import annotations

import argparse
import socket
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

VERSAO_MINIMA = (3, 10)
RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ / "scripts"))
sys.path.insert(0, str(RAIZ / "backend"))


def porta_livre(inicial: int, tentativas: int = 20) -> int:
    for porta in range(inicial, inicial + tentativas):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", porta))
                return porta
            except OSError:
                continue
    raise SystemExit(f"Nenhuma porta livre entre {inicial} e {inicial + tentativas - 1}.")


def ip_da_rede() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
        except OSError:
            return "127.0.0.1"


def aguardar(porta: int, limite: float = 90.0) -> bool:
    fim = time.time() + limite
    while time.time() < fim:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{porta}/config.js", timeout=2) as r:
                if r.status == 200:
                    return True
        except OSError:
            time.sleep(0.5)
    return False


class SemMetricas:
    def __init__(self, destino) -> None:
        self.destino = destino
        self.pendente = ""

    def write(self, texto: str) -> int:
        self.pendente += texto
        while "\n" in self.pendente:
            linha, self.pendente = self.pendente.split("\n", 1)
            if not linha.startswith('{"_aws"'):
                self.destino.write(linha + "\n")
        return len(texto)

    def flush(self) -> None:
        self.destino.flush()

    def __getattr__(self, nome):
        return getattr(self.destino, nome)


def verificar_dependencias() -> None:
    faltando = []
    for modulo in ("boto3", "moto", "openpyxl", "reportlab", "PIL", "cryptography"):
        try:
            __import__(modulo)
        except ImportError:
            faltando.append(modulo)
    if faltando:
        raise SystemExit("Bibliotecas não instaladas: " + ", ".join(faltando) +
                         "\nExecute iniciar_aplicacao_schulz.bat ou: pip install -r requirements.txt")


def iniciar(porta: int, rede: bool, abrir: bool) -> None:
    verificar_dependencias()
    import servidor_local

    host = "0.0.0.0" if rede else "127.0.0.1"
    endereco = f"http://{ip_da_rede() if rede else 'localhost'}:{porta}"
    print("Preparando banco de dados, armazenamento e login simulados...", flush=True)
    servidor, _ = servidor_local.criar_servidor(porta, demo=True, host=host)
    linha = "=" * 64
    print(linha)
    print(" Schulz S.A. - Certificados de Calibração (Grupo 45 - PA III)")
    print(linha)
    print(f" Interface web ......: {endereco}")
    print(f" API backend ........: {endereco}/api/")
    print(f" Validação (QR Code) : {endereco}/validar.html")
    print(f" Planilhas de teste .: {RAIZ / 'exemplos'}")
    print(" Usuários (senha Local@12345):")
    print("   tecnico@local.test    -> envia a planilha")
    print("   signatario@local.test -> revisa e emite o certificado")
    print("   admin@local.test      -> cadastros")
    print("   auditor@local.test    -> consulta e auditoria")
    print(" Dados ficam em memória: ao fechar esta janela tudo é apagado.")
    print(" Para encerrar: feche esta janela ou pressione Ctrl+C.")
    print(linha, flush=True)
    sys.stdout = SemMetricas(sys.stdout)
    if abrir:
        webbrowser.open(endereco)
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print("\nAplicação encerrada.")


def main() -> int:
    if sys.version_info < VERSAO_MINIMA:
        print(f"Python {VERSAO_MINIMA[0]}.{VERSAO_MINIMA[1]} ou superior é necessário "
              f"(encontrado {sys.version.split()[0]}).")
        return 1
    ap = argparse.ArgumentParser(description="Plataforma Schulz de certificados de calibração em modo local.")
    ap.add_argument("--porta", type=int, default=8080, help="porta HTTP (padrão 8080)")
    ap.add_argument("--rede", action="store_true", help="aceita conexões da rede local (validar QR Code no celular)")
    ap.add_argument("--abrir", action="store_true", help="abre o navegador padrão ao iniciar")
    ap.add_argument("--porta-livre", type=int, metavar="INICIAL", help="mostra a primeira porta livre e sai")
    ap.add_argument("--aguardar", type=int, metavar="PORTA", help="espera a aplicação responder na porta e sai")
    ap.add_argument("--endereco", action="store_true", help="mostra o IP da rede local e sai")
    args = ap.parse_args()
    if args.porta_livre:
        print(porta_livre(args.porta_livre))
        return 0
    if args.aguardar:
        return 0 if aguardar(args.aguardar) else 1
    if args.endereco:
        print(ip_da_rede())
        return 0
    iniciar(args.porta, args.rede, args.abrir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
