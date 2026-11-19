from __future__ import annotations

import math
import random
from decimal import Decimal

import pytest

from schulz import calculo as c
from schulz.calculo import (ErroCalculo, Parecer, Ponto, Situacao, analisar,
                                        analisar_ponto, classificar, desvio_padrao_amostral,
                                        erro_sistematico, lea_aplicado, media, parecer_final)


def test_media_de_duas_leituras():
    assert media([10.0021, 10.0027]) == pytest.approx(10.0024, abs=1e-12)


def test_media_de_tres_leituras():
    assert media([1.0, 2.0, 6.0]) == pytest.approx(3.0)


def test_media_exige_ao_menos_duas_leituras():
    with pytest.raises(ErroCalculo):
        media([1.0])
    with pytest.raises(ErroCalculo):
        media([])


def test_erro_sistematico_positivo_e_negativo():
    assert erro_sistematico(10.004, 10.000) == pytest.approx(0.004)
    assert erro_sistematico(9.996, 10.000) == pytest.approx(-0.004)
    assert erro_sistematico(5.0, 5.0) == 0.0


def test_desvio_padrao_amostral_confere_com_a_planilha_da_schulz():
    assert desvio_padrao_amostral([0.002167, 0.002267]) == pytest.approx(7.0710678e-05, rel=1e-6)
    assert desvio_padrao_amostral([5.0, 5.0]) == 0.0
    assert desvio_padrao_amostral([1.0, 3.0]) == pytest.approx(math.sqrt(2))


def test_desvio_padrao_exige_duas_leituras():
    with pytest.raises(ErroCalculo):
        desvio_padrao_amostral([1.0])


@pytest.mark.parametrize("erro, lea, esperado", [
    (0.004, 0.010, Situacao.DENTRO),
    (-0.004, 0.010, Situacao.DENTRO),
    (0.0, 0.010, Situacao.DENTRO),
    (0.011, 0.010, Situacao.FORA),
    (-0.011, 0.010, Situacao.FORA),
    (0.010, 0.010, Situacao.DENTRO),
    (-0.010, 0.010, Situacao.DENTRO),
])
def test_classificacao_simetrica(erro, lea, esperado):
    assert classificar(erro, lea, lea) is esperado


def test_ruido_de_ponto_flutuante_nao_reprova_valor_no_limite():
    erro = erro_sistematico(1.01, 1.0)
    assert erro > 0.010
    assert classificar(erro, 0.010, 0.010) is Situacao.DENTRO


def test_margem_de_ponto_flutuante_nao_aprova_o_que_esta_realmente_fora():
    assert classificar(0.010 + 2e-7, 0.010, 0.010) is Situacao.FORA
    assert classificar(0.0101, 0.010, 0.010) is Situacao.FORA


def test_tolerancia_assimetrica_usa_o_lea_do_lado_do_erro():
    assert classificar(0.012, 0.05, 0.0) is Situacao.DENTRO
    assert classificar(-0.001, 0.05, 0.0) is Situacao.FORA
    assert lea_aplicado(0.012, 0.05, 0.0) == 0.05
    assert lea_aplicado(-0.001, 0.05, 0.0) == 0.0
    assert lea_aplicado(0.0, 0.05, 0.02) == 0.05


def test_equivalencia_com_limites_da_aula_02():
    r = analisar_ponto(Ponto("D", 6.075, 0.010, 0.020, (6.0652, 6.0652)))
    assert r.limite_minimo == pytest.approx(6.055)
    assert r.limite_maximo == pytest.approx(6.085)
    assert r.situacao is Situacao.DENTRO
    r2 = analisar_ponto(Ponto("D", 6.075, 0.010, 0.020, (6.0540, 6.0540)))
    assert r2.situacao is Situacao.FORA


def _ponto(nome, erro, lea=0.010):
    return Ponto(nome, 10.0, lea, lea, (10.0 + erro, 10.0 + erro))


def test_parecer_aprovado_quando_todos_dentro():
    res = analisar([_ponto("A", 0.001), _ponto("B", -0.002), _ponto("C", 0.009)])
    assert res.parecer is Parecer.APROVADO
    assert res.total == 3 and len(res.fora) == 0 and len(res.dentro) == 3


def test_parecer_reprovado_se_um_unico_ponto_fora():
    res = analisar([_ponto("A", 0.001), _ponto("B", 0.020), _ponto("C", 0.002)])
    assert res.parecer is Parecer.REPROVADO
    assert [p.ponto for p in res.fora] == ["B"]


def test_parecer_sem_pontos_e_erro():
    with pytest.raises(ErroCalculo):
        parecer_final([])
    with pytest.raises(ErroCalculo):
        analisar([])


def test_analisar_numera_pontos_e_calcula_todos_os_campos():
    res = analisar([Ponto("X", 10.0, 0.01, 0.01, (10.002, 10.004)),
                    Ponto("Y", 0.0, 0.05, 0.0, (0.01, 0.03))])
    x, y = res.pontos
    assert (x.sequencia, y.sequencia) == (1, 2)
    assert x.media == pytest.approx(10.003)
    assert x.erro_sistematico == pytest.approx(0.003)
    assert x.erro_absoluto == pytest.approx(0.003)
    assert x.desvio_padrao == pytest.approx(math.sqrt(2) * 0.001)
    assert x.lea_aplicado == 0.01 and x.situacao is Situacao.DENTRO
    assert y.media == pytest.approx(0.02) and y.lea_aplicado == 0.05


def test_aviso_quando_numero_de_leituras_difere_de_dois():
    res = analisar([Ponto("A", 1.0, 0.1, 0.1, (1.0, 1.0, 1.0)), Ponto("B", 1.0, 0.1, 0.1, (1.0, 1.0))])
    assert len(res.avisos) == 1 and "RN05" in res.avisos[0] and "A" in res.avisos[0]


@pytest.mark.parametrize("kwargs", [
    dict(valor_padrao=float("nan")), dict(valor_padrao=float("inf")),
    dict(lea_positivo=-0.1), dict(lea_negativo=-0.1), dict(leituras=(1.0, float("nan"))),
])
def test_ponto_rejeita_valores_invalidos(kwargs):
    base = dict(nome="P", valor_padrao=1.0, lea_positivo=0.1, lea_negativo=0.1, leituras=(1.0, 1.0))
    with pytest.raises(ErroCalculo):
        Ponto(**{**base, **kwargs})


def test_propriedade_contra_oraculo_decimal_exato():
    rnd = random.Random(2026)
    for _ in range(2000):
        n = rnd.choice([2, 2, 2, 3, 4])
        padrao = Decimal(rnd.randint(-5000, 150000)) / Decimal(10000)
        lea_p = Decimal(rnd.randint(0, 300)) / Decimal(10000)
        lea_n = Decimal(rnd.randint(0, 300)) / Decimal(10000)
        leituras = [padrao + Decimal(rnd.randint(-400, 400)) / Decimal(10000) for _ in range(n)]
        media_exata = sum(leituras) / n
        erro_exato = media_exata - padrao
        limite = lea_p if erro_exato >= 0 else lea_n
        esperado = Situacao.DENTRO if abs(erro_exato) <= limite else Situacao.FORA
        r = analisar_ponto(Ponto("P", float(padrao), float(lea_p), float(lea_n),
                                 tuple(float(x) for x in leituras)))
        assert r.situacao is esperado, (padrao, lea_p, lea_n, leituras)
        assert r.media == pytest.approx(float(media_exata), abs=1e-9)
        assert r.erro_sistematico == pytest.approx(float(erro_exato), abs=1e-9)


def test_constantes_documentadas():
    assert c.EPSILON == 1e-7 and c.MINIMO_LEITURAS == 2
