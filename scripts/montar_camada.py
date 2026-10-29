from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DESTINO = RAIZ / "camada" / "python"
REQUISITOS = RAIZ / "camada" / "requirements.txt"


def main() -> None:
    if DESTINO.exists():
        shutil.rmtree(DESTINO)
    DESTINO.mkdir(parents=True)
    subprocess.check_call([
        sys.executable, "-m", "pip", "install", "--quiet", "--no-compile",
        "--target", str(DESTINO),
        "--platform", "manylinux2014_aarch64", "--platform", "manylinux_2_28_aarch64",
        "--implementation", "cp", "--python-version", "3.12", "--only-binary=:all:",
        "-r", str(REQUISITOS),
    ])
    for lixo in list(DESTINO.rglob("__pycache__")) + list(DESTINO.glob("*.dist-info/RECORD")):
        if lixo.is_dir():
            shutil.rmtree(lixo)
        else:
            lixo.unlink()
    tamanho = sum(p.stat().st_size for p in DESTINO.rglob("*") if p.is_file())
    print(f"Camada pronta em {DESTINO.relative_to(RAIZ)} ({tamanho / 1024 / 1024:.1f} MB, Linux arm64, Python 3.12)")


if __name__ == "__main__":
    main()
