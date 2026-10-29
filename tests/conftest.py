from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "backend"))
sys.path.insert(0, str(RAIZ / "scripts"))

import ambiente_local as amb

warnings.filterwarnings("ignore", category=UserWarning, module="openpyxl")
warnings.filterwarnings("ignore", category=DeprecationWarning)


@pytest.fixture(scope="session")
def emissor():
    return amb.EmissorTokens()


@pytest.fixture()
def aws(emissor):
    from moto import mock_aws

    from schulz import autenticacao, config

    amb.configurar_ambiente(emissor)
    with mock_aws():
        config.limpar_cache()
        autenticacao.limpar_cache_chaves()
        amb.criar_recursos()
        yield
    config.limpar_cache()


@pytest.fixture()
def tokens(emissor):
    return {
        "tecnico": emissor.emitir("tecnico@schulz.test", "Ana Técnica", ["tecnico"]),
        "tecnico2": emissor.emitir("tecnico2@schulz.test", "Bruno Técnico", ["tecnico"]),
        "signatario": emissor.emitir("signatario@schulz.test", "João Signatário", ["signatario"]),
        "auditor": emissor.emitir("auditor@schulz.test", "Carla Auditora", ["auditor"]),
        "admin": emissor.emitir("admin@schulz.test", "Diego Admin", ["admin"]),
    }


class Cliente:
    def __init__(self, tokens):
        from funcoes import cadastros, consultar_obter_certificado, emitir_certificado, gerar_url_upload

        self.tokens = tokens
        self.rotas = (
            ("/api/uploads", gerar_url_upload.handler),
            ("/api/emissao", emitir_certificado.handler),
            ("/api/cadastros", cadastros.handler),
            ("/api/", consultar_obter_certificado.handler),
        )

    def chamar(self, metodo, caminho, perfil=None, corpo=None, consulta=None, token=None):
        evento = amb.evento_http(metodo, caminho, corpo, token or (self.tokens[perfil] if perfil else None), consulta)
        for prefixo, funcao in self.rotas:
            if caminho.startswith(prefixo):
                r = funcao(evento, None)
                return r["statusCode"], json.loads(r["body"])
        raise AssertionError(f"sem função para {caminho}")


@pytest.fixture()
def cliente(aws, tokens):
    return Cliente(tokens)


def enviar_planilha(cliente, conteudo: bytes, nome: str = "planilha.xlsx", perfil: str = "tecnico"):
    import boto3

    from funcoes import processar_planilha

    status, corpo = cliente.chamar("POST", "/api/uploads", perfil, {"nome_arquivo": nome, "tamanho": len(conteudo)})
    assert status == 201, corpo
    chave = corpo["campos"]["key"]
    boto3.client("s3", region_name=amb.REGIAO).put_object(Bucket=amb.BUCKET_UPLOADS, Key=chave, Body=conteudo)
    processar_planilha.handler(amb.evento_s3(chave), None)
    return corpo["id"]


@pytest.fixture()
def semente(cliente):
    status, corpo = cliente.chamar("POST", "/api/cadastros/semente", "admin", {})
    assert status == 200, corpo
    return corpo


def padrao_vigente(cliente):
    status, atual = cliente.chamar("GET", "/api/cadastros/padroes", "admin")
    assert status == 200
    padrao = next(p for p in atual["itens"] if p["codigo"] == "500011")
    padrao.update({"data_ultima_cal": "2026-09-15", "data_proxima_cal": "2027-09-15",
                   "cert_calibracao": "260915502390 - Carl Zeiss"})
    status, corpo = cliente.chamar("PUT", "/api/cadastros/padroes/500011", "signatario", padrao)
    assert status == 200, corpo
    return corpo


@pytest.fixture()
def xlsx_aprovado() -> bytes:
    import gerar_exemplos as fab

    return fab.tabela_simples(fab.PONTOS_APROVADO)


@pytest.fixture()
def xlsx_reprovado() -> bytes:
    import gerar_exemplos as fab

    return fab.tabela_simples(fab.PONTOS_REPROVADO)
