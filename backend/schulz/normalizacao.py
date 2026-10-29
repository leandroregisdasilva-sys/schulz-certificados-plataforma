from __future__ import annotations

import math
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

FUSO_BRASILIA = timezone(timedelta(hours=-3))
_CODIGO_INSTRUMENTO = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.\-_/]{1,48}$")


def agora_utc() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def agora_iso() -> str:
    return agora_utc().isoformat().replace("+00:00", "Z")


def hoje_brasilia() -> date:
    return datetime.now(FUSO_BRASILIA).date()


def parametro_interno(nome: str) -> str:
    texto = re.sub(r"\s+", "_", nome.strip().upper())
    texto = re.sub(r"[^\w]", "", texto)
    return re.sub(r"_+", "_", texto).strip("_") or "SEM_NOME"


def codigo_padrao(valor: Any) -> str | None:
    if valor is None:
        return None
    if isinstance(valor, float) and valor.is_integer():
        valor = int(valor)
    texto = re.sub(r"[\s.]", "", str(valor)).upper()
    return texto or None


def codigo_padrao_exibicao(codigo: str) -> str:
    return f"{codigo[:3]}.{codigo[3:]}" if codigo.isdigit() and len(codigo) == 6 else codigo


def codigo_instrumento(valor: Any) -> str | None:
    if valor is None:
        return None
    texto = str(valor).strip()
    return texto if _CODIGO_INSTRUMENTO.match(texto) else None


def texto_ou_nulo(valor: Any, limite: int = 200) -> str | None:
    if valor is None:
        return None
    texto = str(valor).strip()
    return texto[:limite] if texto else None


def data_valida(valor: Any) -> str | None:
    if not isinstance(valor, str):
        return None
    try:
        return date.fromisoformat(valor.strip()[:10]).isoformat()
    except ValueError:
        return None


def para_dynamo(valor: Any) -> Any:
    if isinstance(valor, bool) or valor is None:
        return valor
    if isinstance(valor, float):
        if not math.isfinite(valor):
            raise ValueError("Número não finito não pode ser gravado.")
        return Decimal(repr(round(valor, 10)))
    if isinstance(valor, int):
        return Decimal(valor)
    if isinstance(valor, dict):
        return {k: para_dynamo(v) for k, v in valor.items() if v is not None}
    if isinstance(valor, (list, tuple)):
        return [para_dynamo(v) for v in valor]
    return valor


def de_dynamo(valor: Any) -> Any:
    if isinstance(valor, Decimal):
        return int(valor) if valor == valor.to_integral_value() else float(valor)
    if isinstance(valor, dict):
        return {k: de_dynamo(v) for k, v in valor.items()}
    if isinstance(valor, (list, tuple, set)):
        return [de_dynamo(v) for v in valor]
    return valor


def numero_br(valor: float | None, casas: int = 4) -> str:
    if valor is None:
        return "-"
    texto = f"{valor:,.{casas}f}"
    return texto.replace(",", "_").replace(".", ",").replace("_", ".")


def data_br(iso: str | None) -> str:
    if not iso:
        return "-"
    try:
        return date.fromisoformat(iso[:10]).strftime("%d/%m/%Y")
    except ValueError:
        return iso


def data_hora_br(iso: str | None) -> str:
    if not iso:
        return "-"
    instante = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return instante.astimezone(FUSO_BRASILIA).strftime("%d/%m/%Y %H:%M")
