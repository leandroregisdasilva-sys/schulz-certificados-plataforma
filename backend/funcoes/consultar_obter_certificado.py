from __future__ import annotations

import re
from typing import Any

from schulz import dados, regras
from schulz.api import ErroApi, Requisicao, despachar, rota
from schulz.autenticacao import TODOS, Usuario, autenticar
from schulz.config import s3, variavel
from schulz.normalizacao import agora_iso, codigo_instrumento, data_valida

VALIDADE_DOWNLOAD = 300
_MES = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
_NR = re.compile(r"^(\d{1,5})/(\d{4})$")
_CODIGO_VALIDACAO = re.compile(r"^[A-Za-z0-9_\-]{8,40}$")

CAMPOS_LISTA = ("id_calibracao", "status", "nr_certificado", "data_calibracao", "data_emissao", "codigo_instrumento",
                "denominacao", "cliente", "solicitante", "tecnico", "resultado_geral", "total_caracteristicas",
                "nome_arquivo", "criado_em", "nome_signatario")


def _resumo(cal: dict[str, Any]) -> dict[str, Any]:
    item = {k: cal.get(k) for k in CAMPOS_LISTA}
    item["qtd_reprovadas"] = len(cal.get("reprovadas") or [])
    item["qtd_pendencias"] = len(cal.get("pendencias") or [])
    return item


def _visivel_na_lista(usuario: Usuario, cal: dict[str, Any]) -> bool:
    return regras.pode_ver_previa(usuario, cal)


def eu(req: Requisicao, usuario: Usuario) -> dict[str, Any]:
    return usuario.resumo()


def obter_importacao(req: Requisicao, usuario: Usuario) -> dict[str, Any]:
    imp = dados.obter(dados.chave_importacao(req.parametros["id"]), consistente=True)
    if imp is None:
        raise ErroApi(404, "IMPORTACAO_NAO_ENCONTRADA", "Importação não encontrada.")
    if imp.get("id_tecnico") != usuario.sub and not usuario.tem_algum(("admin", "signatario")):
        raise ErroApi(403, "PERFIL_SEM_PERMISSAO", "Esta importação pertence a outro usuário.")
    return {k: imp.get(k) for k in ("id_importacao", "status", "nome_arquivo", "id_calibracao", "erro", "criado_em",
                                     "atualizado_em")}


def listar_calibracoes(req: Requisicao, usuario: Usuario) -> dict[str, Any]:
    q = req.consulta
    nr = (q.get("nr") or "").strip()
    if nr:
        achado = _NR.match(nr)
        if not achado:
            raise ErroApi(400, "NUMERO_INVALIDO", "Informe o número no formato NNNNN/AAAA.")
        cert = dados.obter(dados.chave_certificado(int(achado.group(2)), int(achado.group(1))))
        itens = []
        if cert and cert.get("id_calibracao"):
            cal = dados.obter(dados.chave_calibracao(cert["id_calibracao"]))
            itens = [cal] if cal else []
    elif q.get("instrumento"):
        codigo = codigo_instrumento(q["instrumento"])
        if not codigo:
            raise ErroApi(400, "INSTRUMENTO_INVALIDO", "Código de instrumento inválido.")
        itens = _por_indice("GSI1", f"INST#{codigo}", q)
    elif q.get("setor"):
        itens = _por_indice("GSI2", f"SETOR#{q['setor'].strip()}", q)
    else:
        itens = dados.listar_por_tipo("CALIBRACAO", 500)
    status = (q.get("status") or "").strip().upper()
    de, ate = data_valida(q.get("de")), data_valida(q.get("ate"))
    filtrados = []
    for cal in itens:
        if status and cal.get("status") != status:
            continue
        referencia = str(cal.get("data_emissao") or cal.get("data_referencia") or "")[:10]
        if de and referencia < de:
            continue
        if ate and referencia > ate:
            continue
        if _visivel_na_lista(usuario, cal):
            filtrados.append(_resumo(cal))
    filtrados.sort(key=lambda c: str(c.get("data_emissao") or c.get("criado_em") or ""), reverse=True)
    return {"itens": filtrados[:200], "total": len(filtrados)}


def _por_indice(indice: str, pk: str, q: dict[str, str]) -> list[dict[str, Any]]:
    de, ate = data_valida(q.get("de")), data_valida(q.get("ate"))
    if de or ate:
        return dados.consultar_indice(indice, pk, f"DATA#{de or '0000-00-00'}", f"DATA#{ate or '9999-12-31'}~")
    return dados.consultar_indice(indice, pk, prefixo_sk="DATA#")


def obter_calibracao(req: Requisicao, usuario: Usuario) -> dict[str, Any]:
    cal = dados.obter(dados.chave_calibracao(req.parametros["id"]))
    if cal is None:
        raise ErroApi(404, "CALIBRACAO_NAO_ENCONTRADA", "Calibração não encontrada.")
    if not regras.pode_ver_previa(usuario, cal):
        raise ErroApi(403, "PERFIL_SEM_PERMISSAO", "Seu perfil não pode ver a prévia desta calibração.")
    instrumento = dados.obter(dados.chave_instrumento(cal["codigo_instrumento"])) if cal.get("codigo_instrumento") else None
    corpo = {k: v for k, v in cal.items() if k not in ("PK", "SK", "GSI1PK", "GSI1SK", "GSI2PK", "GSI2SK", "s3_key_planilha")}
    if cal.get("status") == "PENDENTE_ASSINATURA":
        padrao = dados.obter(dados.chave_padrao(cal["codigo_padrao"])) if cal.get("codigo_padrao") else None
        efetivos = regras.campos_efetivos(cal, instrumento)
        procedimento = regras.procedimento_para(efetivos.get("denominacao"), dados.listar_por_tipo("PROCEDIMENTO"))
        corpo["pendencias"] = regras.avaliar_pendencias(cal, instrumento, padrao, procedimento)
        corpo["campos_efetivos"] = efetivos
        corpo["procedimento_previsto"] = procedimento
    corpo["instrumento_cadastrado"] = instrumento
    corpo["resultados"] = [{k: v for k, v in r.items() if k not in ("PK", "SK")} for r in dados.resultados(cal["id_calibracao"])]
    return corpo


def baixar_certificado(req: Requisicao, usuario: Usuario) -> dict[str, Any]:
    cal = dados.obter(dados.chave_calibracao(req.parametros["id"]))
    if cal is None or not cal.get("s3_key_certificado"):
        raise ErroApi(404, "CERTIFICADO_NAO_ENCONTRADO", "Certificado não encontrado.")
    url = s3().generate_presigned_url(
        "get_object",
        Params={"Bucket": variavel("BUCKET_CERTIFICADOS"), "Key": cal["s3_key_certificado"],
                "ResponseContentType": "application/pdf"},
        ExpiresIn=VALIDADE_DOWNLOAD,
    )
    dados.registrar_auditoria(usuario, "CERTIFICADO_BAIXADO", f"CAL#{cal['id_calibracao']}",
                              detalhes={"nr_certificado": cal.get("nr_certificado")})
    return {"url": url, "validade_segundos": VALIDADE_DOWNLOAD, "nr_certificado": cal.get("nr_certificado"),
            "sha256": cal.get("sha256")}


def listar_pendentes(req: Requisicao, usuario: Usuario) -> dict[str, Any]:
    itens = []
    for p in dados.consultar_particao("PENDENTES", "DATA#"):
        cal = dados.obter(dados.chave_calibracao(p["id_calibracao"]))
        if cal and cal.get("status") == "PENDENTE_ASSINATURA":
            itens.append(_resumo(cal))
    return {"itens": itens, "total": len(itens)}


def listar_auditoria(req: Requisicao, usuario: Usuario) -> dict[str, Any]:
    mes = (req.consulta.get("mes") or agora_iso()[:7]).strip()
    if not _MES.match(mes):
        raise ErroApi(400, "MES_INVALIDO", "Informe o mês no formato AAAA-MM.")
    itens = dados.consultar_particao(f"AUD#{mes}", decrescente=True, limite=500)
    return {"mes": mes, "itens": [{k: v for k, v in i.items() if k not in ("PK", "SK", "tipo")} for i in itens]}


def painel(req: Requisicao, usuario: Usuario) -> dict[str, Any]:
    padroes = dados.listar_por_tipo("PADRAO")
    alertas = []
    for p in padroes:
        situacao = regras.situacao_padrao(p)
        if situacao in ("VENCIDO", "VENCENDO", "SEM_DATA") and p.get("ativo", True):
            alertas.append({"codigo": p.get("codigo"), "codigo_exibicao": p.get("codigo_exibicao"),
                            "descricao": p.get("descricao"), "data_proxima_cal": p.get("data_proxima_cal"),
                            "situacao": situacao})
    pendentes = len(dados.consultar_particao("PENDENTES", "DATA#")) if usuario.tem_algum(("signatario", "admin")) else None
    return {"usuario": usuario.resumo(), "alertas_padroes": alertas, "pendentes_assinatura": pendentes,
            "cadastros_vazios": not padroes}


def validar(req: Requisicao, usuario: Usuario | None) -> dict[str, Any]:
    codigo = req.parametros["codigo"]
    if not _CODIGO_VALIDACAO.match(codigo):
        raise ErroApi(404, "CERTIFICADO_NAO_ENCONTRADO", "Código de validação não encontrado.")
    ref = dados.obter(dados.chave_validacao(codigo))
    cal = dados.obter(dados.chave_calibracao(ref["id_calibracao"])) if ref else None
    if cal is None or cal.get("status") not in ("EMITIDA", "ANULADA"):
        raise ErroApi(404, "CERTIFICADO_NAO_ENCONTRADO", "Código de validação não encontrado.")
    inst = cal.get("instrumento_certificado") or {}
    padrao = cal.get("padrao_utilizado") or {}
    resultados = dados.resultados(cal["id_calibracao"])
    return {
        "situacao": "VALIDO" if cal["status"] == "EMITIDA" else "ANULADO",
        "nr_certificado": cal.get("nr_certificado"),
        "data_emissao": cal.get("data_emissao"),
        "data_calibracao": cal.get("data_calibracao"),
        "resultado_geral": cal.get("resultado_geral"),
        "instrumento": {"codigo": inst.get("codigo"), "denominacao": inst.get("denominacao"),
                        "fabricante": inst.get("fabricante") or "Não consta"},
        "solicitante": cal.get("solicitante"),
        "padrao": {"codigo": padrao.get("codigo_exibicao"), "descricao": padrao.get("descricao"),
                   "data_proxima_cal": padrao.get("data_proxima_cal")},
        "incerteza_expandida_mm": cal.get("incerteza_expandida"),
        "fator_k": cal.get("fator_k"),
        "signatario": cal.get("nome_signatario"),
        "tecnico": cal.get("tecnico"),
        "sha256": cal.get("sha256"),
        "motivo_anulacao": cal.get("motivo_anulacao"),
        "anulado_em": cal.get("anulado_em"),
        "caracteristicas": [{"parametro": r["parametro_exibicao"], "resultado": r["resultado"]} for r in resultados],
        "total_caracteristicas": len(resultados),
        "reprovadas": [r["parametro_exibicao"] for r in resultados if r["resultado"] == "REPROVADO"],
        "consultado_em": agora_iso(),
    }


ROTAS = [
    rota("GET", "/api/validacao/{codigo}", validar, None),
    rota("GET", "/api/eu", eu, TODOS),
    rota("GET", "/api/painel", painel, TODOS),
    rota("GET", "/api/importacoes/{id}", obter_importacao, ("tecnico", "admin", "signatario")),
    rota("GET", "/api/calibracoes", listar_calibracoes, TODOS),
    rota("GET", "/api/calibracoes/{id}", obter_calibracao, TODOS),
    rota("GET", "/api/calibracoes/{id}/certificado", baixar_certificado, TODOS),
    rota("GET", "/api/pendentes", listar_pendentes, ("signatario", "admin")),
    rota("GET", "/api/auditoria", listar_auditoria, ("signatario", "auditor", "admin")),
]


def handler(evento: dict[str, Any], contexto: Any) -> dict[str, Any]:
    return despachar(evento, ROTAS, autenticar)
