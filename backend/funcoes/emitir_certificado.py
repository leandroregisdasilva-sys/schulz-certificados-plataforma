from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import time
import urllib.parse
from typing import Any

from boto3.dynamodb.conditions import Attr
from botocore.exceptions import ClientError

from schulz import certificado_pdf, dados, regras
from schulz.api import ErroApi, Requisicao, despachar, rota
from schulz.autenticacao import Usuario, autenticar
from schulz.config import s3, tabela, variavel
from schulz.incerteza import ErroIncerteza, calcular
from schulz.normalizacao import agora_iso, codigo_padrao, codigo_padrao_exibicao, texto_ou_nulo

log = logging.getLogger("schulz")
log.setLevel(logging.INFO)

LIMITE_EMISSAO_SEGUNDOS = 90
_ORIGEM_VALIDA = re.compile(r"^https?://[A-Za-z0-9.\-]+(:\d{1,5})?$")


def _metrica(nome: str, valor: float) -> None:
    print(json.dumps({
        "_aws": {"Timestamp": int(time.time() * 1000), "CloudWatchMetrics": [
            {"Namespace": "SchulzPoC", "Dimensions": [[]], "Metrics": [{"Name": nome, "Unit": "Count"}]}]},
        nome: valor,
    }))


def _url_site(req: Requisicao) -> str:
    configurada = os.environ.get("URL_SITE", "").rstrip("/")
    if configurada:
        return configurada
    origem = (req.origem or "").rstrip("/")
    if _ORIGEM_VALIDA.match(origem):
        return origem
    referencia = urllib.parse.urlsplit(req.cabecalhos.get("referer", ""))
    if referencia.scheme in ("http", "https") and referencia.netloc:
        candidata = f"{referencia.scheme}://{referencia.netloc}"
        if _ORIGEM_VALIDA.match(candidata):
            return candidata
    raise ErroApi(400, "ORIGEM_DESCONHECIDA", "Não foi possível determinar o endereço público do sistema para o QR Code.")


def _carregar(id_calibracao: str) -> dict[str, Any]:
    cal = dados.obter(dados.chave_calibracao(id_calibracao), consistente=True)
    if cal is None:
        raise ErroApi(404, "CALIBRACAO_NAO_ENCONTRADA", "Calibração não encontrada.")
    return cal


def _reservar(id_calibracao: str) -> None:
    limite = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - LIMITE_EMISSAO_SEGUNDOS))
    try:
        tabela().update_item(
            Key=dados.chave_calibracao(id_calibracao),
            UpdateExpression="SET emitindo_desde = :agora",
            ConditionExpression="#s = :pendente AND (attribute_not_exists(emitindo_desde) OR emitindo_desde < :limite)",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":agora": agora_iso(), ":pendente": "PENDENTE_ASSINATURA", ":limite": limite},
        )
    except ClientError as erro:
        if dados.condicao_falhou(erro):
            raise ErroApi(409, "EMISSAO_INDISPONIVEL",
                          "A calibração não está pendente de assinatura ou outra emissão está em andamento.")
        raise


def _liberar(id_calibracao: str) -> None:
    try:
        dados.atualizar(dados.chave_calibracao(id_calibracao), {"atualizado_em": agora_iso()}, remover=["emitindo_desde"])
    except ClientError:
        log.exception("falha ao liberar emissão")


def emitir(req: Requisicao, usuario: Usuario) -> tuple[int, dict[str, Any]]:
    id_calibracao = req.parametros["id"]
    corpo = req.json()
    cal = _carregar(id_calibracao)
    if cal.get("status") != "PENDENTE_ASSINATURA":
        raise ErroApi(409, "STATUS_INVALIDO", f"Calibração com status {cal.get('status')} não pode ser emitida.")
    escolhido = codigo_padrao(corpo.get("padrao")) if corpo.get("padrao") else None
    if escolhido:
        cal["codigo_padrao"] = escolhido
    instrumento = dados.obter(dados.chave_instrumento(cal["codigo_instrumento"]), True) if cal.get("codigo_instrumento") else None
    padrao = dados.obter(dados.chave_padrao(cal["codigo_padrao"]), True) if cal.get("codigo_padrao") else None
    efetivos = regras.campos_efetivos(cal, instrumento)
    procedimento = regras.procedimento_para(efetivos.get("denominacao"), dados.listar_por_tipo("PROCEDIMENTO"))
    pendencias = regras.avaliar_pendencias(cal, instrumento, padrao, procedimento)
    if pendencias:
        raise ErroApi(422, "EMISSAO_BLOQUEADA", "A emissão está bloqueada até que as pendências sejam resolvidas (RF09).",
                      [p["mensagem"] for p in pendencias])
    if cal.get("resultado_geral") == "REPROVADO" and corpo.get("ciente_reprovacao") is not True:
        raise ErroApi(409, "CONFIRMACAO_NECESSARIA",
                      "Há características reprovadas. Confirme explicitamente que está ciente para emitir (RF12/RN07).",
                      list(cal.get("reprovadas") or []))
    resultados = dados.resultados(id_calibracao)
    try:
        incerteza = calcular([r["desvio_padrao"] for r in resultados], [r["valor_nominal"] for r in resultados], 2,
                             padrao.get("componentes_incerteza") or [], padrao.get("temperatura")).como_dict()
    except ErroIncerteza as erro:
        raise ErroApi(422, "INCERTEZA_INDISPONIVEL", str(erro))
    url_site = _url_site(req)
    observacao = texto_ou_nulo(corpo.get("observacao"), 400)
    _reservar(id_calibracao)
    numero_reservado: tuple[int, int] | None = None
    try:
        emissao = agora_iso()
        ano = int(emissao[:4])
        numero = dados.proximo_numero(ano)
        numero_reservado = (ano, numero)
        nr = f"{numero:05d}/{ano}"
        codigo_validacao = dados.novo_codigo_validacao()
        url_validacao = f"{url_site}/validar.html?c={codigo_validacao}"
        dados.gravar({**dados.chave_certificado(ano, numero), "tipo": "CERTIFICADO", "nr_certificado": nr,
                      "id_calibracao": id_calibracao, "situacao": "RESERVADO", "criado_em": emissao},
                     condicao="attribute_not_exists(PK)")
        padrao_exib = {**padrao, "codigo_exibicao": padrao.get("codigo_exibicao") or codigo_padrao_exibicao(padrao["codigo"])}
        instrumento_exib = {"codigo": cal["codigo_instrumento"], "denominacao": efetivos["denominacao"],
                            "fabricante": efetivos.get("fabricante")}
        pdf = certificado_pdf.gerar({
            "calibracao": cal, "padrao": padrao_exib, "procedimento": procedimento, "instrumento": instrumento_exib,
            "resultados": resultados, "incerteza": incerteza, "nr_certificado": nr, "data_emissao": emissao,
            "codigo_validacao": codigo_validacao, "url_validacao": url_validacao, "solicitante": efetivos["cliente"],
            "signatario": usuario.nome, "observacao": observacao,
        })
        sha256 = hashlib.sha256(pdf).hexdigest()
        chave_pdf = f"certificados/{ano}/{numero:05d}.pdf"
        s3().put_object(
            Bucket=variavel("BUCKET_CERTIFICADOS"), Key=chave_pdf, Body=pdf, ContentType="application/pdf",
            ContentMD5=base64.b64encode(hashlib.md5(pdf).digest()).decode("ascii"),
            Metadata={"sha256": sha256, "certificado": nr.replace("/", "-")},
            ContentDisposition=f'inline; filename="certificado_{numero:05d}_{ano}.pdf"',
        )
    except Exception:
        if numero_reservado:
            try:
                dados.atualizar(dados.chave_certificado(*numero_reservado), {"situacao": "ANULADO_SEM_ARQUIVO",
                                                                           "atualizado_em": agora_iso()})
                dados.registrar_auditoria(usuario, "NUMERO_ANULADO_SEM_ARQUIVO", f"CERT#{numero_reservado[0]}-{numero_reservado[1]:05d}",
                                          "FALHA", {"id_calibracao": id_calibracao})
            except ClientError:
                log.exception("falha ao registrar número anulado")
        _liberar(id_calibracao)
        raise
    snapshot_padrao = {k: padrao.get(k) for k in ("codigo", "codigo_exibicao", "descricao", "modelo", "fabricante", "nr_serie",
                                                  "cert_calibracao", "data_ultima_cal", "data_proxima_cal")}
    snapshot_padrao["codigo_exibicao"] = padrao_exib["codigo_exibicao"]
    atualizado = dados.atualizar(dados.chave_calibracao(id_calibracao), {
        "status": "EMITIDA",
        "nr_certificado": nr,
        "ano_certificado": ano,
        "numero_certificado": numero,
        "data_emissao": emissao,
        "id_signatario": usuario.sub,
        "nome_signatario": usuario.nome,
        "codigo_padrao": padrao["codigo"],
        "padrao_utilizado": snapshot_padrao,
        "instrumento_certificado": instrumento_exib,
        "solicitante": efetivos["cliente"],
        "procedimento": {k: procedimento.get(k) for k in ("codigo", "revisao", "descricao")},
        "incerteza": incerteza,
        "incerteza_expandida": incerteza["incerteza_expandida_mm"],
        "fator_k": incerteza["fator_k"],
        "veff": incerteza["veff"],
        "s3_key_certificado": chave_pdf,
        "sha256": sha256,
        "tamanho_pdf": len(pdf),
        "codigo_validacao": codigo_validacao,
        "url_validacao": url_validacao,
        "ciente_reprovacao": bool(corpo.get("ciente_reprovacao")),
        "observacao": observacao,
        "pendencias": [],
        "atualizado_em": agora_iso(),
    }, remover=["emitindo_desde"])
    dados.atualizar(dados.chave_certificado(ano, numero), {"situacao": "EMITIDO", "s3_key": chave_pdf, "sha256": sha256,
                                                          "atualizado_em": agora_iso()})
    dados.gravar({**dados.chave_validacao(codigo_validacao), "tipo": "VALIDACAO", "id_calibracao": id_calibracao,
                  "nr_certificado": nr, "criado_em": emissao})
    dados.excluir_pendente(cal.get("data_referencia") or cal.get("data_calibracao") or "", id_calibracao)
    dados.registrar_auditoria(usuario, "CERTIFICADO_EMITIDO", f"CAL#{id_calibracao}",
                              detalhes={"nr_certificado": nr, "sha256": sha256, "resultado": cal.get("resultado_geral"),
                                        "ciente_reprovacao": bool(corpo.get("ciente_reprovacao"))})
    _metrica("CertificadosEmitidos", 1)
    return 201, {
        "id_calibracao": id_calibracao,
        "nr_certificado": nr,
        "status": atualizado["status"],
        "sha256": sha256,
        "codigo_validacao": codigo_validacao,
        "url_validacao": url_validacao,
    }


def _transicao(id_calibracao: str, campos: dict[str, Any], condicao: Any, mensagem: str) -> None:
    try:
        dados.atualizar(dados.chave_calibracao(id_calibracao), campos, condicao=condicao)
    except ClientError as erro:
        if dados.condicao_falhou(erro):
            raise ErroApi(409, "STATUS_INVALIDO", mensagem)
        raise


def devolver(req: Requisicao, usuario: Usuario) -> dict[str, Any]:
    id_calibracao = req.parametros["id"]
    motivo = texto_ou_nulo(req.json().get("motivo"), 400)
    if not motivo:
        raise ErroApi(400, "MOTIVO_OBRIGATORIO", "Informe o motivo da devolução.")
    cal = _carregar(id_calibracao)
    _transicao(id_calibracao, {"status": "DEVOLVIDA", "motivo_devolucao": motivo, "devolvido_por": usuario.nome,
                               "devolvido_em": agora_iso(), "atualizado_em": agora_iso()},
               Attr("status").eq("PENDENTE_ASSINATURA") & Attr("emitindo_desde").not_exists(),
               "Somente calibrações pendentes de assinatura podem ser devolvidas.")
    dados.excluir_pendente(cal.get("data_referencia") or cal.get("data_calibracao") or "", id_calibracao)
    dados.registrar_auditoria(usuario, "CALIBRACAO_DEVOLVIDA", f"CAL#{id_calibracao}", detalhes={"motivo": motivo})
    return {"id_calibracao": id_calibracao, "status": "DEVOLVIDA"}


def anular(req: Requisicao, usuario: Usuario) -> dict[str, Any]:
    id_calibracao = req.parametros["id"]
    motivo = texto_ou_nulo(req.json().get("motivo"), 400)
    if not motivo:
        raise ErroApi(400, "MOTIVO_OBRIGATORIO", "Informe o motivo da anulação.")
    cal = _carregar(id_calibracao)
    _transicao(id_calibracao, {"status": "ANULADA", "motivo_anulacao": motivo, "anulado_por": usuario.nome,
                               "anulado_em": agora_iso(), "atualizado_em": agora_iso()},
               Attr("status").eq("EMITIDA"), "Somente certificados emitidos podem ser anulados.")
    dados.atualizar(dados.chave_certificado(int(cal["ano_certificado"]), int(cal["numero_certificado"])),
                    {"situacao": "ANULADO", "atualizado_em": agora_iso()})
    dados.registrar_auditoria(usuario, "CERTIFICADO_ANULADO", f"CAL#{id_calibracao}",
                              detalhes={"nr_certificado": cal.get("nr_certificado"), "motivo": motivo})
    return {"id_calibracao": id_calibracao, "status": "ANULADA", "nr_certificado": cal.get("nr_certificado")}


ROTAS = [
    rota("POST", "/api/emissao/{id}", emitir, ("signatario",)),
    rota("POST", "/api/emissao/{id}/devolver", devolver, ("signatario",)),
    rota("POST", "/api/emissao/{id}/anular", anular, ("signatario",)),
]


def handler(evento: dict[str, Any], contexto: Any) -> dict[str, Any]:
    return despachar(evento, ROTAS, autenticar)
