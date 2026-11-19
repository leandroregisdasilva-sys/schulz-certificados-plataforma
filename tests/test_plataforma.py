from __future__ import annotations

import hashlib
import json

import boto3
import pytest

import ambiente_local as amb
import gerar_exemplos as fab
from conftest import enviar_planilha, padrao_vigente

DATA = fab.datetime(2026, 10, 20, 9, 30)


def calypso_aprovado(plano="703.1208_DEMO", mmc=500011):
    return fab.calypso(fab.blocos_calypso(fab.CALYPSO_APROVADO), plano=plano, metrologista="Técnico Demonstração",
                       mmc=mmc, fornecedor="Não consta", data=DATA)


def calypso_reprovado():
    return fab.calypso(fab.blocos_calypso(fab.CALYPSO_APROVADO, fab.FORA_DA_TOLERANCIA), plano="703.1208_DEMO",
                       metrologista="Técnico Demonstração", mmc=500011, fornecedor="Não consta", data=DATA)


def test_rotas_exigem_login(cliente):
    status, corpo = cliente.chamar("GET", "/api/calibracoes")
    assert status == 401 and corpo["erro"]["codigo"] == "NAO_AUTENTICADO"


def test_token_adulterado_e_recusado(cliente, tokens):
    cabecalho, carga, assinatura = tokens["tecnico"].split(".")
    import base64
    dados = json.loads(base64.urlsafe_b64decode(carga + "=" * (-len(carga) % 4)))
    dados["cognito:groups"] = ["admin"]
    falsa = base64.urlsafe_b64encode(json.dumps(dados).encode()).rstrip(b"=").decode()
    status, corpo = cliente.chamar("GET", "/api/eu", token=f"{cabecalho}.{falsa}.{assinatura}")
    assert status == 401 and "Assinatura" in corpo["erro"]["mensagem"]


def test_token_expirado_e_de_outro_app(cliente, emissor):
    expirado = emissor.emitir("x@schulz.test", "X", ["tecnico"], validade=-3600)
    assert cliente.chamar("GET", "/api/eu", token=expirado)[0] == 401
    outro = emissor.emitir("x@schulz.test", "X", ["tecnico"], aud="outro-app")
    assert cliente.chamar("GET", "/api/eu", token=outro)[0] == 401


def test_perfil_auditor_nao_envia_planilha(cliente):
    status, corpo = cliente.chamar("POST", "/api/uploads", "auditor", {"nome_arquivo": "a.xlsx", "tamanho": 10})
    assert status == 403 and corpo["erro"]["codigo"] == "PERFIL_SEM_PERMISSAO"


def test_upload_valida_extensao_e_tamanho(cliente):
    assert cliente.chamar("POST", "/api/uploads", "tecnico", {"nome_arquivo": "a.xlsm", "tamanho": 10})[0] == 400
    assert cliente.chamar("POST", "/api/uploads", "tecnico", {"nome_arquivo": "a.xlsx", "tamanho": 6 * 1024 * 1024})[0] == 413
    status, corpo = cliente.chamar("POST", "/api/uploads", "tecnico", {"nome_arquivo": "a.xlsx", "tamanho": 1000})
    assert status == 201
    assert corpo["campos"]["key"].startswith("uploads/") and corpo["campos"]["key"].endswith(".xlsx")
    assert "policy" in corpo["campos"] and corpo["url"].startswith("https://")


def test_fluxo_completo_emissao_e_validacao(cliente, semente):
    id_cal = enviar_planilha(cliente, calypso_aprovado(), "relatorio_703.xlsx")
    status, imp = cliente.chamar("GET", f"/api/importacoes/{id_cal}", "tecnico")
    assert status == 200 and imp["status"] == "PENDENTE_ASSINATURA"
    status, cal = cliente.chamar("GET", f"/api/calibracoes/{id_cal}", "signatario")
    assert status == 200
    assert cal["resultado_geral"] == "APROVADO" and cal["total_caracteristicas"] == 14
    assert cal["solicitante"] if "solicitante" in cal else cal["campos_efetivos"]["cliente"] == "Usinagem I"
    assert [p["codigo"] for p in cal["pendencias"]] == ["PADRAO_VENCIDO"]
    r1 = cal["resultados"][0]
    assert r1["parametro"] == "PLANICIDADE_REF" and r1["resultado"] == "APROVADO"
    assert abs(r1["media"] - 0.0022) < 1e-12 and abs(r1["erro"] - 0.0022) < 1e-12
    status, corpo = cliente.chamar("POST", f"/api/emissao/{id_cal}", "signatario", {})
    assert status == 422 and corpo["erro"]["codigo"] == "EMISSAO_BLOQUEADA"
    assert "vencida" in corpo["erro"]["detalhes"][0]
    padrao_vigente(cliente)
    status, emitido = cliente.chamar("POST", f"/api/emissao/{id_cal}", "signatario", {})
    assert status == 201, emitido
    assert emitido["nr_certificado"].endswith("/2026") and emitido["nr_certificado"].startswith("00001")
    status, baixar = cliente.chamar("GET", f"/api/calibracoes/{id_cal}/certificado", "auditor")
    assert status == 200 and baixar["url"].startswith("https://")
    objeto = boto3.client("s3", region_name=amb.REGIAO).get_object(
        Bucket=amb.BUCKET_CERTIFICADOS, Key="certificados/2026/00001.pdf")
    pdf = objeto["Body"].read()
    assert pdf[:5] == b"%PDF-" and hashlib.sha256(pdf).hexdigest() == emitido["sha256"]
    status, val = cliente.chamar("GET", f"/api/validacao/{emitido['codigo_validacao']}")
    assert status == 200 and val["situacao"] == "VALIDO" and val["resultado_geral"] == "APROVADO"
    assert val["sha256"] == emitido["sha256"] and val["instrumento"]["codigo"] == "703.1208"
    assert val["solicitante"] == "Usinagem I" and val["total_caracteristicas"] == 14
    status, segunda = cliente.chamar("POST", f"/api/emissao/{id_cal}", "signatario", {})
    assert status == 409
    status, pend = cliente.chamar("GET", "/api/pendentes", "signatario")
    assert pend["total"] == 0
    status, anulado = cliente.chamar("POST", f"/api/emissao/{id_cal}/anular", "signatario", {"motivo": "Teste de anulação"})
    assert status == 200 and anulado["status"] == "ANULADA"
    status, val = cliente.chamar("GET", f"/api/validacao/{emitido['codigo_validacao']}")
    assert val["situacao"] == "ANULADO" and val["motivo_anulacao"] == "Teste de anulação"
    status, aud = cliente.chamar("GET", "/api/auditoria", "auditor")
    acoes = {i["acao"] for i in aud["itens"]}
    assert {"CERTIFICADO_EMITIDO", "CERTIFICADO_ANULADO", "CERTIFICADO_BAIXADO", "PADRAO_ALTERADO"} <= acoes


def test_reprovado_exige_confirmacao_explicita(cliente, semente):
    padrao_vigente(cliente)
    id_cal = enviar_planilha(cliente, calypso_reprovado())
    status, cal = cliente.chamar("GET", f"/api/calibracoes/{id_cal}", "signatario")
    assert cal["resultado_geral"] == "REPROVADO"
    assert sorted(cal["reprovadas"]) == ["13P_POSIÇÃO 1", "62P_DIÂMETRO 2"]
    status, corpo = cliente.chamar("POST", f"/api/emissao/{id_cal}", "signatario", {})
    assert status == 409 and corpo["erro"]["codigo"] == "CONFIRMACAO_NECESSARIA"
    status, corpo = cliente.chamar("POST", f"/api/emissao/{id_cal}", "signatario", {"ciente_reprovacao": True})
    assert status == 201
    status, val = cliente.chamar("GET", f"/api/validacao/{corpo['codigo_validacao']}")
    assert val["resultado_geral"] == "REPROVADO" and len(val["reprovadas"]) == 2


def test_numeracao_sequencial_sem_repeticao(cliente, semente):
    padrao_vigente(cliente)
    numeros = []
    for _ in range(3):
        id_cal = enviar_planilha(cliente, calypso_aprovado())
        numeros.append(cliente.chamar("POST", f"/api/emissao/{id_cal}", "signatario", {})[1]["nr_certificado"])
    assert numeros == ["00001/2026", "00002/2026", "00003/2026"]
    status, busca = cliente.chamar("GET", "/api/calibracoes", "auditor", consulta={"nr": "00002/2026"})
    assert busca["total"] == 1 and busca["itens"][0]["nr_certificado"] == "00002/2026"


def test_tecnico_nao_emite_e_nao_ve_previa_de_outro(cliente, semente):
    padrao_vigente(cliente)
    id_cal = enviar_planilha(cliente, calypso_aprovado())
    assert cliente.chamar("POST", f"/api/emissao/{id_cal}", "tecnico", {})[0] == 403
    assert cliente.chamar("GET", f"/api/calibracoes/{id_cal}", "tecnico")[0] == 200
    assert cliente.chamar("GET", f"/api/calibracoes/{id_cal}", "tecnico2")[0] == 403
    assert cliente.chamar("GET", f"/api/calibracoes/{id_cal}", "auditor")[0] == 403
    assert cliente.chamar("GET", "/api/calibracoes", "tecnico2")[1]["total"] == 0


def test_instrumento_nao_cadastrado_bloqueia_ate_cadastro(cliente, semente):
    padrao_vigente(cliente)
    planilha = fab.para_estrito(fab.registro_schulz(fab.PONTOS_REGISTRO_NOVO, cliente="Montagem Compressores",
                                                    tecnico="Técnico Demonstração", codigo="900.0001",
                                                    denominacao="Dispositivo de controle", data=DATA))
    id_cal = enviar_planilha(cliente, planilha, "registro.xlsx")
    status, cal = cliente.chamar("GET", f"/api/calibracoes/{id_cal}", "signatario")
    codigos = {p["codigo"] for p in cal["pendencias"]}
    assert {"INSTRUMENTO_NAO_CADASTRADO", "PADRAO_NAO_INFORMADO"} <= codigos
    assert any("strict" in a.lower() or "estrito" in a.lower() for a in cal["avisos"])
    status, corpo = cliente.chamar("POST", f"/api/emissao/{id_cal}", "signatario", {"padrao": "500.011"})
    assert status == 422 and any("900.0001" in d for d in corpo["erro"]["detalhes"])
    assert cliente.chamar("POST", "/api/cadastros/instrumentos", "tecnico",
                          {"codigo": "900.0001", "denominacao": "Dispositivo de controle", "setor": "Montagem"})[0] == 403
    status, inst = cliente.chamar("POST", "/api/cadastros/instrumentos", "admin",
                                  {"codigo": "900.0001", "denominacao": "Dispositivo de controle", "setor": "Montagem"})
    assert status == 201
    status, corpo = cliente.chamar("POST", f"/api/emissao/{id_cal}", "signatario", {"padrao": "500.011"})
    assert status == 201, corpo
    status, cal = cliente.chamar("GET", f"/api/calibracoes/{id_cal}", "auditor")
    assert cal["codigo_padrao"] == "500011" and cal["padrao_utilizado"]["codigo_exibicao"] == "500.011"
    assert cal["solicitante"] == "Montagem Compressores"


def test_arquivo_invalido_vira_erro_arquivo(cliente):
    id_imp = enviar_planilha(cliente, b"isto nao e um xlsx", "falso.xlsx")
    status, imp = cliente.chamar("GET", f"/api/importacoes/{id_imp}", "tecnico")
    assert imp["status"] == "ERRO_ARQUIVO" and imp["erro"]["codigo"]
    assert cliente.chamar("GET", f"/api/calibracoes/{id_imp}", "signatario")[0] == 404


def test_rn05_exige_duas_medicoes(cliente):
    planilha = fab.tabela_simples(fab.PONTOS_APROVADO)
    id_imp = enviar_planilha(cliente, planilha)
    status, imp = cliente.chamar("GET", f"/api/importacoes/{id_imp}", "tecnico")
    assert imp["status"] == "ERRO_VALIDACAO" and imp["erro"]["codigo"] == "RN05_DUPLA_MEDICAO"


def test_processamento_e_idempotente(cliente, semente):
    from funcoes import processar_planilha

    id_cal = enviar_planilha(cliente, calypso_aprovado())
    chave = f"uploads/{json.loads(json.dumps(cliente.chamar('GET', f'/api/calibracoes/{id_cal}', 'signatario')[1]))['id_tecnico']}/{id_cal}.xlsx"
    saida = processar_planilha.handler(amb.evento_s3(chave), None)
    assert saida["processados"][0]["status"] == "IGNORADO"
    assert cliente.chamar("GET", "/api/pendentes", "signatario")[1]["total"] == 1


def test_devolver_calibracao(cliente, semente):
    id_cal = enviar_planilha(cliente, calypso_aprovado())
    assert cliente.chamar("POST", f"/api/emissao/{id_cal}/devolver", "signatario", {})[0] == 400
    status, corpo = cliente.chamar("POST", f"/api/emissao/{id_cal}/devolver", "signatario", {"motivo": "Refazer medição"})
    assert status == 200 and corpo["status"] == "DEVOLVIDA"
    assert cliente.chamar("POST", f"/api/emissao/{id_cal}/devolver", "signatario", {"motivo": "x"})[0] == 409
    assert cliente.chamar("GET", "/api/pendentes", "signatario")[1]["total"] == 0


def test_cadastros_e_alerta_de_vencimento(cliente, semente):
    status, padroes = cliente.chamar("GET", "/api/cadastros/padroes", "tecnico")
    assert padroes["itens"][0]["situacao"] == "VENCIDO"
    status, painel = cliente.chamar("GET", "/api/painel", "tecnico")
    assert painel["alertas_padroes"][0]["situacao"] == "VENCIDO"
    padrao_vigente(cliente)
    status, painel = cliente.chamar("GET", "/api/painel", "signatario")
    assert painel["alertas_padroes"] == [] and painel["pendentes_assinatura"] == 0
    assert cliente.chamar("POST", "/api/cadastros/padroes", "tecnico", {"codigo": "500.012"})[0] == 403
    status, corpo = cliente.chamar("POST", "/api/cadastros/padroes", "admin", {"codigo": "500.012", "descricao": "MMC"})
    assert status == 400 and corpo["erro"]["detalhes"]
    status, procs = cliente.chamar("GET", "/api/cadastros/procedimentos", "auditor")
    assert procs["itens"][0]["codigo"] == "ITL-0026" and procs["itens"][0]["revisao"] == 2
    status, repetida = cliente.chamar("POST", "/api/cadastros/semente", "admin", {})
    assert repetida["criados"] == []


def test_validacao_de_codigo_inexistente(cliente):
    assert cliente.chamar("GET", "/api/validacao/naoexiste123")[0] == 404
    assert cliente.chamar("GET", "/api/validacao/<script>")[0] == 404


def test_rota_inexistente_e_metodo_errado(cliente):
    assert cliente.chamar("GET", "/api/nao-existe", "admin")[0] == 404
    assert cliente.chamar("DELETE", "/api/calibracoes", "admin")[0] == 405


def test_json_invalido(cliente):
    evento = amb.evento_http("POST", "/api/uploads", "{nao-json", cliente.tokens["tecnico"])
    from funcoes import gerar_url_upload
    r = gerar_url_upload.handler(evento, None)
    assert r["statusCode"] == 400


@pytest.mark.parametrize("perfil,esperado", [("signatario", 200), ("auditor", 200), ("admin", 200), ("tecnico", 403)])
def test_auditoria_por_perfil(cliente, perfil, esperado):
    assert cliente.chamar("GET", "/api/auditoria", perfil)[0] == esperado
