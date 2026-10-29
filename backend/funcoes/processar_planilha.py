from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import urllib.parse
from typing import Any

from botocore.exceptions import ClientError

from schulz import dados, regras
from schulz.calculo import ErroCalculo, Situacao, analisar
from schulz.config import s3, sns, tabela, variavel
from schulz.incerteza import ErroIncerteza, calcular
from schulz.ingestao import ErroPlanilha, ler_planilha
from schulz.normalizacao import agora_iso, codigo_instrumento, codigo_padrao, de_dynamo, parametro_interno, texto_ou_nulo

log = logging.getLogger("schulz")
log.setLevel(logging.INFO)

TAMANHO_MAXIMO = 5 * 1024 * 1024
REPROCESSAR_APOS_SEGUNDOS = 120


class ErroValidacao(Exception):
    def __init__(self, codigo: str, mensagem: str, detalhes: list[str] | None = None):
        super().__init__(mensagem)
        self.codigo = codigo
        self.mensagem = mensagem
        self.detalhes = detalhes or []


def _metrica(nome: str, valor: float) -> None:
    print(json.dumps({
        "_aws": {"Timestamp": int(time.time() * 1000), "CloudWatchMetrics": [
            {"Namespace": "SchulzPoC", "Dimensions": [[]], "Metrics": [{"Name": nome, "Unit": "Count"}]}]},
        nome: valor,
    }))


def _marcar_processando(id_importacao: str) -> dict[str, Any] | None:
    limite = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - REPROCESSAR_APOS_SEGUNDOS))
    try:
        r = tabela().update_item(
            Key=dados.chave_importacao(id_importacao),
            UpdateExpression="SET #s = :novo, atualizado_em = :agora",
            ConditionExpression="attribute_exists(PK) AND (#s = :recebida OR (#s = :processando AND atualizado_em < :limite))",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":novo": "PROCESSANDO", ":agora": agora_iso(), ":recebida": "RECEBIDA",
                                       ":processando": "PROCESSANDO", ":limite": limite},
            ReturnValues="ALL_NEW",
        )
    except ClientError as erro:
        if dados.condicao_falhou(erro):
            return None
        raise
    return de_dynamo(r["Attributes"])


def _finalizar_importacao(id_importacao: str, status: str, extra: dict[str, Any]) -> None:
    dados.atualizar(dados.chave_importacao(id_importacao), {"status": status, "atualizado_em": agora_iso(), **extra})


def processar(bucket: str, chave_s3: str) -> dict[str, Any]:
    partes = chave_s3.split("/")
    if len(partes) != 3 or partes[0] != "uploads" or not partes[2].endswith(".xlsx"):
        log.warning("chave ignorada: %s", chave_s3)
        return {"status": "IGNORADO"}
    id_importacao = partes[2][:-5]
    importacao = _marcar_processando(id_importacao)
    if importacao is None:
        log.info("importação %s já processada ou inexistente", id_importacao)
        return {"status": "IGNORADO"}
    try:
        objeto = s3().get_object(Bucket=bucket, Key=chave_s3)
        if int(objeto.get("ContentLength", 0)) > TAMANHO_MAXIMO:
            raise ErroPlanilha("ARQUIVO_GRANDE", "O arquivo excede o limite de 5 MB.", status=413)
        conteudo = objeto["Body"].read(TAMANHO_MAXIMO + 1)
        if len(conteudo) > TAMANHO_MAXIMO:
            raise ErroPlanilha("ARQUIVO_GRANDE", "O arquivo excede o limite de 5 MB.", status=413)
        planilha = ler_planilha(conteudo, str(importacao.get("nome_arquivo") or "planilha.xlsx"))
        irregulares = [p.nome for p in planilha.pontos if len(p.leituras) != 2]
        if irregulares:
            raise ErroValidacao("RN05_DUPLA_MEDICAO", "Cada característica deve ter exatamente duas medições (RN05).",
                                [f"Características com quantidade diferente de 2: {', '.join(irregulares[:10])}"])
        analise = analisar(planilha.pontos, [a for a in planilha.avisos if "RN05" not in a])
    except ErroPlanilha as erro:
        _finalizar_importacao(id_importacao, "ERRO_ARQUIVO" if erro.status in (400, 413) or erro.codigo in (
            "ARQUIVO_INVALIDO", "ARQUIVO_CORROMPIDO", "LAYOUT_NAO_RECONHECIDO") else "ERRO_VALIDACAO",
            {"erro": {"codigo": erro.codigo, "mensagem": erro.mensagem, "detalhes": erro.detalhes}})
        _metrica("FalhasProcessamento", 1)
        return {"status": "ERRO", "codigo": erro.codigo}
    except (ErroValidacao, ErroCalculo) as erro:
        codigo = getattr(erro, "codigo", "DADOS_INVALIDOS")
        _finalizar_importacao(id_importacao, "ERRO_VALIDACAO", {"erro": {
            "codigo": codigo, "mensagem": getattr(erro, "mensagem", str(erro)), "detalhes": getattr(erro, "detalhes", [])}})
        _metrica("FalhasProcessamento", 1)
        return {"status": "ERRO", "codigo": codigo}

    meta = planilha.metadados
    cod_inst = codigo_instrumento(meta.get("codigo_instrumento"))
    cod_pad = codigo_padrao(meta.get("padrao_utilizado"))
    instrumento = dados.obter(dados.chave_instrumento(cod_inst)) if cod_inst else None
    padrao = dados.obter(dados.chave_padrao(cod_pad)) if cod_pad else None
    data_cal = meta.get("data_calibracao")
    data_ref = data_cal or str(importacao.get("criado_em") or agora_iso())[:10]
    agora = agora_iso()
    resultados = []
    for r in analise.pontos:
        resultados.append({
            "PK": f"CAL#{id_importacao}",
            "SK": f"RES#{r.sequencia:03d}",
            "tipo": "RESULTADO_MEDICAO",
            "id_calibracao": id_importacao,
            "sequencia": r.sequencia,
            "parametro": parametro_interno(r.ponto),
            "parametro_exibicao": r.ponto,
            "valor_nominal": r.valor_padrao,
            "tolerancia_pos": r.lea_positivo,
            "tolerancia_neg": -r.lea_negativo if r.lea_negativo else 0.0,
            "medida_1": r.leituras[0],
            "medida_2": r.leituras[1],
            "media": r.media,
            "desvio_padrao": r.desvio_padrao,
            "erro": r.erro_sistematico,
            "limite_minimo": r.limite_minimo,
            "limite_maximo": r.limite_maximo,
            "resultado": "APROVADO" if r.situacao is Situacao.DENTRO else "REPROVADO",
        })
    reprovadas = [r["parametro_exibicao"] for r in resultados if r["resultado"] == "REPROVADO"]
    cal: dict[str, Any] = {
        **dados.chave_calibracao(id_importacao),
        "tipo": "CALIBRACAO",
        "id_calibracao": id_importacao,
        "status": "PENDENTE_ASSINATURA",
        "nome_arquivo": importacao.get("nome_arquivo"),
        "s3_key_planilha": chave_s3,
        "sha256_planilha": hashlib.sha256(conteudo).hexdigest(),
        "formato": planilha.formato,
        "aba": planilha.aba,
        "data_calibracao": data_cal,
        "codigo_instrumento": cod_inst,
        "denominacao": texto_ou_nulo(meta.get("denominacao")),
        "fabricante": texto_ou_nulo(meta.get("fabricante")),
        "cliente": texto_ou_nulo(meta.get("cliente")),
        "tecnico": texto_ou_nulo(meta.get("tecnico")),
        "temperatura_ambiente": texto_ou_nulo(meta.get("temperatura_ambiente"), 20),
        "plano_medicao": texto_ou_nulo(meta.get("plano_medicao")),
        "codigo_padrao": cod_pad,
        "id_tecnico": importacao.get("id_tecnico"),
        "nome_usuario_envio": importacao.get("nome_usuario_envio"),
        "resultado_geral": analise.parecer.value,
        "total_caracteristicas": len(resultados),
        "reprovadas": reprovadas,
        "avisos": list(analise.avisos),
        "criado_em": importacao.get("criado_em") or agora,
        "processado_em": agora,
        "GSI1PK": f"INST#{cod_inst or 'SEM_CODIGO'}",
        "GSI1SK": f"DATA#{data_ref}#{id_importacao}",
        "data_referencia": data_ref,
    }
    efetivos = regras.campos_efetivos(cal, instrumento)
    setor = efetivos.get("cliente")
    if setor:
        cal["GSI2PK"] = f"SETOR#{setor}"
        cal["GSI2SK"] = f"DATA#{data_ref}#{id_importacao}"
    procedimento = regras.procedimento_para(efetivos.get("denominacao"), dados.listar_por_tipo("PROCEDIMENTO"))
    cal["pendencias"] = regras.avaliar_pendencias(cal, instrumento, padrao, procedimento)
    if padrao is not None:
        try:
            inc = calcular([r["desvio_padrao"] for r in resultados], [r["valor_nominal"] for r in resultados], 2,
                           padrao.get("componentes_incerteza") or [], padrao.get("temperatura"))
            cal["incerteza"] = inc.como_dict()
            cal["incerteza_expandida"] = inc.incerteza_expandida_mm
            cal["fator_k"] = inc.fator_k
            cal["veff"] = inc.veff
        except ErroIncerteza as erro:
            cal["pendencias"].append(regras.pendencia("INCERTEZA_INDISPONIVEL", "RF04", str(erro)))
    dados.gravar_lote(resultados)
    dados.gravar(cal)
    dados.gravar({**dados.chave_pendente(data_ref, id_importacao), "tipo": "PENDENTE", "id_calibracao": id_importacao,
                  "codigo_instrumento": cod_inst, "resultado_geral": cal["resultado_geral"], "criado_em": agora})
    _finalizar_importacao(id_importacao, "PENDENTE_ASSINATURA", {"id_calibracao": id_importacao})
    dados.registrar_auditoria(None, "PLANILHA_PROCESSADA", f"CAL#{id_importacao}",
                              detalhes={"resultado": cal["resultado_geral"], "pendencias": len(cal["pendencias"])})
    _metrica("CaracteristicasReprovadas", len(reprovadas))
    topico = os.environ.get("TOPICO_ALERTAS")
    if reprovadas and topico:
        try:
            sns().publish(TopicArn=topico, Subject="Calibração com resultado REPROVADO",
                          Message=f"Calibração {id_importacao} do instrumento {cod_inst or '-'} tem {len(reprovadas)} "
                                  f"característica(s) reprovada(s): {', '.join(reprovadas[:15])}.")
        except ClientError:
            log.exception("falha ao publicar alerta")
    return {"status": "PENDENTE_ASSINATURA", "id_calibracao": id_importacao}


def handler(evento: dict[str, Any], contexto: Any) -> dict[str, Any]:
    saidas = []
    for registro in evento.get("Records", []):
        bucket = registro["s3"]["bucket"]["name"]
        chave = urllib.parse.unquote_plus(registro["s3"]["object"]["key"])
        if bucket != variavel("BUCKET_UPLOADS"):
            continue
        saidas.append(processar(bucket, chave))
    return {"processados": saidas}
