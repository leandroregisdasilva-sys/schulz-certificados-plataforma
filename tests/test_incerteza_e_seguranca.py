from __future__ import annotations

import math

import pytest

from schulz import autenticacao, incerteza, normalizacao


def test_modelo_reproduz_a_planilha_da_schulz():
    r = incerteza.calcular([0.0], [0.0], 2, incerteza.COMPONENTES_PADRAO_MMC)
    assert math.isclose(r.incerteza_combinada_um, 2.72741941524707, rel_tol=1e-12)
    assert r.fator_k == 2.0
    assert math.isclose(r.incerteza_expandida_um, 5.45483883049414, rel_tol=1e-12)


@pytest.mark.parametrize("veff,k", [(1.5, 13.97), (2, 4.53), (7.9, 2.43), (12, 2.23), (13.99, 2.23), (29.9, 2.11),
                                    (30, 2.09), (99.9, 2.03), (100, 2.02), (100.1, 2.0), (5000, 2.0)])
def test_tabela_t_de_student_da_aba_incerteza(veff, k):
    assert incerteza.fator_k(veff) == k


def test_dispersao_grande_reduz_veff_e_aumenta_k():
    r = incerteza.calcular([0.005], [16.964], 2, incerteza.COMPONENTES_PADRAO_MMC)
    assert r.veff < 30 and r.fator_k > 2
    assert math.isclose(r.incerteza_expandida_um, r.fator_k * r.incerteza_combinada_um)


def test_componentes_invalidos():
    with pytest.raises(incerteza.ErroIncerteza):
        incerteza.validar_componentes([{"descricao": "x", "valor_um": 1, "divisor": 0}])
    with pytest.raises(incerteza.ErroIncerteza):
        incerteza.validar_componentes([])


def test_normalizacoes_etl():
    assert normalizacao.parametro_interno("62P_DIÂMETRO 1") == "62P_DIÂMETRO_1"
    assert normalizacao.parametro_interno("  entre centros (ref) ") == "ENTRE_CENTROS_REF"
    assert normalizacao.codigo_padrao("500.011") == "500011"
    assert normalizacao.codigo_padrao(500011.0) == "500011"
    assert normalizacao.codigo_padrao_exibicao("500011") == "500.011"
    assert normalizacao.codigo_instrumento("703.1208") == "703.1208"
    assert normalizacao.codigo_instrumento("../etc") is None
    assert normalizacao.numero_br(1234.5, 2) == "1.234,50"


def test_rsa_pkcs1_v15_real_e_rejeicoes(emissor):
    import base64
    import json

    token = emissor.emitir("a@b.c", "A", ["admin"])
    cab, carga, ass = token.split(".")
    chave = emissor.jwks()["keys"][0]
    n = int.from_bytes(base64.urlsafe_b64decode(chave["n"] + "=="), "big")
    e = int.from_bytes(base64.urlsafe_b64decode(chave["e"] + "=="), "big")
    assinatura = base64.urlsafe_b64decode(ass + "=" * (-len(ass) % 4))
    assert autenticacao.assinatura_rs256_valida(f"{cab}.{carga}".encode(), assinatura, n, e)
    assert not autenticacao.assinatura_rs256_valida(f"{cab}.{carga}x".encode(), assinatura, n, e)
    assert not autenticacao.assinatura_rs256_valida(f"{cab}.{carga}".encode(), assinatura[:-1], n, e)
    assert not autenticacao.assinatura_rs256_valida(f"{cab}.{carga}".encode(), b"\x00" * len(assinatura), n, e)


def test_algoritmo_none_e_recusado(aws):
    import base64
    import json

    def b64(d):
        return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()

    falso = f"{b64({'alg': 'none'})}.{b64({'sub': 'x', 'cognito:groups': ['admin']})}."
    with pytest.raises(Exception) as info:
        autenticacao.validar_token(falso)
    assert getattr(info.value, "status", None) == 401
