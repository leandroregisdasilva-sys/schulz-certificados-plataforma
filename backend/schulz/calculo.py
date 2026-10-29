from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Sequence

EPSILON = 1e-7

MINIMO_LEITURAS = 2


class Situacao(str, Enum):
    DENTRO = "DENTRO_DA_TOLERANCIA"
    FORA = "FORA_DA_TOLERANCIA"


class Parecer(str, Enum):
    APROVADO = "APROVADO"
    REPROVADO = "REPROVADO"


class ErroCalculo(ValueError):
    pass


@dataclass(frozen=True)
class Ponto:
    nome: str
    valor_padrao: float
    lea_positivo: float
    lea_negativo: float
    leituras: tuple[float, ...]
    sequencia: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "leituras", tuple(self.leituras))
        for rotulo, valor in (
            ("valor_padrao", self.valor_padrao),
            ("lea_positivo", self.lea_positivo),
            ("lea_negativo", self.lea_negativo),
            *((f"leitura {i + 1}", v) for i, v in enumerate(self.leituras)),
        ):
            if not isinstance(valor, (int, float)) or not math.isfinite(valor):
                raise ErroCalculo(f"Ponto '{self.nome}': {rotulo} inválido ({valor!r}).")
        if self.lea_positivo < 0 or self.lea_negativo < 0:
            raise ErroCalculo(f"Ponto '{self.nome}': LEA não pode ser negativo.")


@dataclass(frozen=True)
class ResultadoPonto:
    sequencia: int
    ponto: str
    valor_padrao: float
    lea_positivo: float
    lea_negativo: float
    leituras: tuple[float, ...]
    media: float
    desvio_padrao: float
    erro_sistematico: float
    erro_absoluto: float
    lea_aplicado: float
    limite_minimo: float
    limite_maximo: float
    situacao: Situacao


@dataclass(frozen=True)
class ResultadoAnalise:
    parecer: Parecer
    pontos: tuple[ResultadoPonto, ...]
    avisos: tuple[str, ...] = field(default_factory=tuple)

    @property
    def total(self) -> int:
        return len(self.pontos)

    @property
    def fora(self) -> tuple[ResultadoPonto, ...]:
        return tuple(p for p in self.pontos if p.situacao is Situacao.FORA)

    @property
    def dentro(self) -> tuple[ResultadoPonto, ...]:
        return tuple(p for p in self.pontos if p.situacao is Situacao.DENTRO)


def media(leituras: Sequence[float]) -> float:
    if len(leituras) < MINIMO_LEITURAS:
        raise ErroCalculo(
            f"São necessárias ao menos {MINIMO_LEITURAS} leituras por ponto "
            f"(recebidas: {len(leituras)})."
        )
    return math.fsum(leituras) / len(leituras)


def desvio_padrao_amostral(leituras: Sequence[float]) -> float:
    n = len(leituras)
    if n < MINIMO_LEITURAS:
        raise ErroCalculo("Desvio padrão amostral exige ao menos 2 leituras.")
    m = math.fsum(leituras) / n
    return math.sqrt(math.fsum((x - m) ** 2 for x in leituras) / (n - 1))


def erro_sistematico(media_lida: float, valor_padrao: float) -> float:
    return media_lida - valor_padrao


def lea_aplicado(erro: float, lea_positivo: float, lea_negativo: float) -> float:
    return lea_positivo if erro >= 0 else lea_negativo


def classificar(erro: float, lea_positivo: float, lea_negativo: float) -> Situacao:
    limite = lea_aplicado(erro, lea_positivo, lea_negativo)
    return Situacao.DENTRO if abs(erro) <= limite + EPSILON else Situacao.FORA


def analisar_ponto(ponto: Ponto) -> ResultadoPonto:
    m = media(ponto.leituras)
    s = desvio_padrao_amostral(ponto.leituras)
    erro = erro_sistematico(m, ponto.valor_padrao)
    return ResultadoPonto(
        sequencia=ponto.sequencia,
        ponto=ponto.nome,
        valor_padrao=ponto.valor_padrao,
        lea_positivo=ponto.lea_positivo,
        lea_negativo=ponto.lea_negativo,
        leituras=tuple(ponto.leituras),
        media=m,
        desvio_padrao=s,
        erro_sistematico=erro,
        erro_absoluto=abs(erro),
        lea_aplicado=lea_aplicado(erro, ponto.lea_positivo, ponto.lea_negativo),
        limite_minimo=ponto.valor_padrao - ponto.lea_negativo,
        limite_maximo=ponto.valor_padrao + ponto.lea_positivo,
        situacao=classificar(erro, ponto.lea_positivo, ponto.lea_negativo),
    )


def parecer_final(resultados: Sequence[ResultadoPonto]) -> Parecer:
    if not resultados:
        raise ErroCalculo("Nenhum ponto de ensaio para emitir parecer.")
    todos_dentro = all(r.situacao is Situacao.DENTRO for r in resultados)
    return Parecer.APROVADO if todos_dentro else Parecer.REPROVADO


def analisar(pontos: Sequence[Ponto], avisos: Sequence[str] = ()) -> ResultadoAnalise:
    if not pontos:
        raise ErroCalculo("Nenhum ponto de ensaio informado.")
    avisos_finais = list(avisos)
    irregulares = [p.nome for p in pontos if len(p.leituras) != 2]
    if irregulares:
        lista = ", ".join(irregulares[:5]) + ("…" if len(irregulares) > 5 else "")
        avisos_finais.append(
            f"{len(irregulares)} ponto(s) com número de leituras diferente de 2 "
            f"(a RN05 da Schulz prevê exatamente 2): {lista}."
        )
    resultados = tuple(
        analisar_ponto(
            Ponto(
                nome=p.nome,
                valor_padrao=p.valor_padrao,
                lea_positivo=p.lea_positivo,
                lea_negativo=p.lea_negativo,
                leituras=tuple(p.leituras),
                sequencia=i,
            )
        )
        for i, p in enumerate(pontos, start=1)
    )
    return ResultadoAnalise(
        parecer=parecer_final(resultados), pontos=resultados, avisos=tuple(avisos_finais)
    )
