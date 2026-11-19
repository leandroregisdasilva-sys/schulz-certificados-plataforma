from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Sequence

RAIZ3 = math.sqrt(3)

TABELA_K = (
    (2, 13.97), (3, 4.53), (4, 3.31), (5, 2.87), (6, 2.65), (7, 2.52), (8, 2.43),
    (10, 2.37), (12, 2.28), (14, 2.23), (16, 2.20), (18, 2.17), (20, 2.15), (25, 2.13),
    (30, 2.11), (35, 2.09), (40, 2.07), (50, 2.06), (60, 2.05), (80, 2.04), (100, 2.03),
)

COMPONENTES_PADRAO_MMC = (
    {"descricao": "Incerteza da MMC - eixo X", "valor_um": 2.31, "divisor": 2.0, "distribuicao": "Normal", "ci": 1.0, "vi": 1000},
    {"descricao": "Incerteza da MMC - eixo Y", "valor_um": 2.70, "divisor": 2.0, "distribuicao": "Normal", "ci": 1.0, "vi": 1000},
    {"descricao": "Incerteza da MMC - eixo Z", "valor_um": 2.15, "divisor": 2.0, "distribuicao": "Normal", "ci": 1.0, "vi": 1000},
    {"descricao": "Incerteza da esfera padrão", "valor_um": 0.50, "divisor": 2.0, "distribuicao": "Normal", "ci": 1.0, "vi": 1000},
    {"descricao": "Repetitividade do apalpador", "valor_um": 2.00, "divisor": RAIZ3, "distribuicao": "Retangular", "ci": 1.0, "vi": 1000},
    {"descricao": "Resolução da máquina", "valor_um": 0.50, "divisor": 2 * RAIZ3, "distribuicao": "Retangular", "ci": 1.0, "vi": 1000},
    {"descricao": "Erro sistemático da MMC - eixo X", "valor_um": 1.50, "divisor": RAIZ3, "distribuicao": "Retangular", "ci": 1.0, "vi": 1000},
    {"descricao": "Erro sistemático da MMC - eixo Y", "valor_um": 1.20, "divisor": RAIZ3, "distribuicao": "Retangular", "ci": 1.0, "vi": 1000},
    {"descricao": "Erro sistemático da MMC - eixo Z", "valor_um": 1.20, "divisor": RAIZ3, "distribuicao": "Retangular", "ci": 1.0, "vi": 1000},
)

TEMPERATURA_PADRAO = {"variacao_temp_20": 1.0, "dif_temp_padrao_objeto": 0.2, "coef_dilatacao_objeto": 11.5}


class ErroIncerteza(ValueError):
    pass


@dataclass(frozen=True)
class Componente:
    descricao: str
    valor_um: float
    divisor: float
    distribuicao: str
    ci: float
    vi: float

    @property
    def u(self) -> float:
        return self.valor_um / self.divisor

    @property
    def ui(self) -> float:
        return self.u * self.ci


@dataclass(frozen=True)
class ResultadoIncerteza:
    componentes: tuple[Componente, ...]
    incerteza_combinada_um: float
    veff: float
    fator_k: float
    incerteza_expandida_um: float

    @property
    def incerteza_expandida_mm(self) -> float:
        return self.incerteza_expandida_um / 1000.0

    def como_dict(self) -> dict[str, Any]:
        return {
            "componentes": [
                {"descricao": c.descricao, "valor_um": c.valor_um, "divisor": c.divisor, "distribuicao": c.distribuicao,
                 "ci": c.ci, "ui_um": c.ui, "vi": c.vi}
                for c in self.componentes
            ],
            "incerteza_combinada_um": self.incerteza_combinada_um,
            "veff": self.veff,
            "fator_k": self.fator_k,
            "incerteza_expandida_um": self.incerteza_expandida_um,
            "incerteza_expandida_mm": self.incerteza_expandida_mm,
        }


def fator_k(veff: float) -> float:
    for limite, k in TABELA_K:
        if veff < limite:
            return k
    return 2.02 if veff == 100 else 2.00


def validar_componentes(brutos: Any) -> list[dict[str, Any]]:
    if not isinstance(brutos, list) or not brutos:
        raise ErroIncerteza("Informe ao menos um componente de incerteza do padrão.")
    validos = []
    for i, c in enumerate(brutos, start=1):
        if not isinstance(c, dict):
            raise ErroIncerteza(f"Componente {i}: formato inválido.")
        try:
            item = {
                "descricao": str(c.get("descricao") or "").strip()[:120],
                "valor_um": float(c["valor_um"]),
                "divisor": float(c["divisor"]),
                "distribuicao": str(c.get("distribuicao") or "Normal").strip()[:30],
                "ci": float(c.get("ci", 1.0)),
                "vi": float(c.get("vi", 1000)),
            }
        except (KeyError, TypeError, ValueError):
            raise ErroIncerteza(f"Componente {i}: valor, divisor, ci e vi devem ser numéricos.")
        if not item["descricao"]:
            raise ErroIncerteza(f"Componente {i}: informe a descrição.")
        if not all(math.isfinite(item[k]) for k in ("valor_um", "divisor", "ci", "vi")):
            raise ErroIncerteza(f"Componente {i}: valores devem ser finitos.")
        if item["valor_um"] < 0 or item["divisor"] <= 0 or item["vi"] <= 0:
            raise ErroIncerteza(f"Componente {i}: valor ≥ 0, divisor > 0 e vi > 0.")
        validos.append(item)
    return validos


def calcular(desvios_mm: Sequence[float], nominais_mm: Sequence[float], n_medicoes: int,
             componentes: Sequence[dict[str, Any]], temperatura: dict[str, Any] | None = None) -> ResultadoIncerteza:
    if not desvios_mm:
        raise ErroIncerteza("Sem desvios padrão para calcular a incerteza.")
    if n_medicoes < 2:
        raise ErroIncerteza("São necessárias ao menos 2 medições para a incerteza (RN05).")
    temp = {**TEMPERATURA_PADRAO, **(temperatura or {})}
    s_max_um = max(desvios_mm) * 1000.0
    comprimento_mm = max((abs(v) for v in nominais_mm), default=0.0)
    lista = [Componente("Dispersão das medidas (s)", s_max_um, math.sqrt(n_medicoes), "Normal", 1.0, float(n_medicoes - 1))]
    lista += [Componente(**c) for c in validar_componentes(list(componentes))]
    lista.append(Componente("Dif. de temp. em relação a 20 °C", float(temp["variacao_temp_20"]) * 0.000002,
                            RAIZ3 * math.sqrt(6), "Retangular", comprimento_mm * 1000.0, 1000.0))
    lista.append(Componente("Dif. de temp. entre padrão e objeto", float(temp["dif_temp_padrao_objeto"]), RAIZ3,
                            "Retangular", comprimento_mm * float(temp["coef_dilatacao_objeto"]) * 0.001, 1000.0))
    uc = math.sqrt(math.fsum(c.ui ** 2 for c in lista))
    denominador = math.fsum(c.ui ** 4 / c.vi for c in lista)
    veff = (uc ** 4) / denominador if denominador > 0 else float("inf")
    k = fator_k(veff)
    return ResultadoIncerteza(tuple(lista), uc, veff if math.isfinite(veff) else 1e9, k, k * uc)
