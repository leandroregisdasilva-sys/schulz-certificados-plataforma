from __future__ import annotations

from typing import Any

from botocore.exceptions import ClientError

from schulz import dados, regras
from schulz.api import ErroApi, Requisicao, despachar, rota
from schulz.autenticacao import TODOS, Usuario, autenticar
from schulz.incerteza import COMPONENTES_PADRAO_MMC, TEMPERATURA_PADRAO, ErroIncerteza, validar_componentes
from schulz.normalizacao import (agora_iso, codigo_instrumento, codigo_padrao, codigo_padrao_exibicao, data_valida,
                                 texto_ou_nulo)

SEMENTE_PADRAO = {
    "codigo": "500011",
    "descricao": "Máquina de medição por coordenadas",
    "modelo": "Contura G2",
    "fabricante": "Carl Zeiss",
    "nr_serie": "502390",
    "cert_calibracao": "241120502390 - Carl Zeiss",
    "data_ultima_cal": "2024-11-20",
    "data_proxima_cal": "2026-08-31",
}
SEMENTE_PROCEDIMENTO = {"codigo": "ITL-0026", "revisao": 2, "descricao": "Calibração de dispositivo de controle",
                        "tipo_instrumento": "Dispositivo de controle"}
SEMENTE_INSTRUMENTO = {"codigo": "703.1208", "denominacao": "Dispositivo de controle", "fabricante": None,
                       "setor": "Usinagem I", "tipo_criterio": 1}


def _sem_chaves(item: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in item.items() if k not in ("PK", "SK", "GSI1PK", "GSI1SK", "GSI2PK", "GSI2SK")}


def _numero_opcional(valor: Any, campo: str) -> float | None:
    if valor in (None, ""):
        return None
    try:
        return float(str(valor).replace(",", "."))
    except ValueError:
        raise ErroApi(400, "CAMPO_INVALIDO", f"O campo {campo} deve ser numérico.")


def _instrumento(corpo: dict[str, Any], codigo: str | None = None) -> dict[str, Any]:
    codigo = codigo or codigo_instrumento(corpo.get("codigo"))
    erros = []
    if not codigo:
        erros.append("Código do instrumento inválido (ex.: 703.1208).")
    denominacao = texto_ou_nulo(corpo.get("denominacao"))
    setor = texto_ou_nulo(corpo.get("setor"), 100)
    if not denominacao:
        erros.append("Informe a denominação.")
    if not setor:
        erros.append("Informe o setor (cliente solicitante).")
    if erros:
        raise ErroApi(400, "DADOS_INVALIDOS", "Corrija os campos do instrumento.", erros)
    item = {
        **dados.chave_instrumento(codigo),
        "tipo": "INSTRUMENTO",
        "codigo": codigo,
        "denominacao": denominacao,
        "fabricante": texto_ou_nulo(corpo.get("fabricante")),
        "setor": setor,
        "faixa_inicial": _numero_opcional(corpo.get("faixa_inicial"), "faixa_inicial"),
        "faixa_final": _numero_opcional(corpo.get("faixa_final"), "faixa_final"),
        "tipo_criterio": corpo.get("tipo_criterio") if isinstance(corpo.get("tipo_criterio"), int) else None,
        "ativo": corpo.get("ativo", True) is not False,
        "GSI2PK": f"SETOR#{setor}",
        "GSI2SK": f"INST#{codigo}",
        "atualizado_em": agora_iso(),
    }
    return item


def _padrao(corpo: dict[str, Any], codigo: str | None = None) -> dict[str, Any]:
    codigo = codigo or codigo_padrao(corpo.get("codigo"))
    erros = []
    if not codigo:
        erros.append("Código do padrão inválido (ex.: 500.011).")
    campos = {c: texto_ou_nulo(corpo.get(c)) for c in ("descricao", "modelo", "fabricante", "nr_serie", "cert_calibracao")}
    for obrigatorio, rotulo in (("descricao", "descrição"), ("fabricante", "fabricante"), ("nr_serie", "número de série"),
                                ("cert_calibracao", "número do certificado de calibração do padrão")):
        if not campos[obrigatorio]:
            erros.append(f"Informe {rotulo}.")
    ultima, proxima = data_valida(corpo.get("data_ultima_cal")), data_valida(corpo.get("data_proxima_cal"))
    if not ultima or not proxima:
        erros.append("Informe as datas de última e próxima calibração (AAAA-MM-DD).")
    elif proxima <= ultima:
        erros.append("A próxima calibração deve ser posterior à última.")
    try:
        componentes = validar_componentes(corpo.get("componentes_incerteza") or [dict(c) for c in COMPONENTES_PADRAO_MMC])
    except ErroIncerteza as erro:
        erros.append(str(erro))
        componentes = []
    temperatura = {**TEMPERATURA_PADRAO}
    for chave in TEMPERATURA_PADRAO:
        valor = _numero_opcional((corpo.get("temperatura") or {}).get(chave), chave)
        if valor is not None:
            temperatura[chave] = valor
    if erros:
        raise ErroApi(400, "DADOS_INVALIDOS", "Corrija os campos do padrão.", erros)
    return {
        **dados.chave_padrao(codigo),
        "tipo": "PADRAO",
        "codigo": codigo,
        "codigo_exibicao": codigo_padrao_exibicao(codigo),
        **campos,
        "data_ultima_cal": ultima,
        "data_proxima_cal": proxima,
        "componentes_incerteza": componentes,
        "temperatura": temperatura,
        "ativo": corpo.get("ativo", True) is not False,
        "atualizado_em": agora_iso(),
    }


def _criar(item: dict[str, Any], usuario: Usuario, acao: str, rotulo: str) -> tuple[int, dict[str, Any]]:
    try:
        dados.gravar({**item, "criado_em": agora_iso()}, condicao="attribute_not_exists(PK)")
    except ClientError as erro:
        if dados.condicao_falhou(erro):
            raise ErroApi(409, "JA_CADASTRADO", f"{rotulo} {item['codigo']} já está cadastrado.")
        raise
    dados.registrar_auditoria(usuario, acao, item["PK"], detalhes={"codigo": item["codigo"]})
    return 201, _sem_chaves(item)


def _alterar(item: dict[str, Any], usuario: Usuario, acao: str, rotulo: str) -> dict[str, Any]:
    atual = dados.obter({"PK": item["PK"], "SK": item["SK"]})
    if atual is None:
        raise ErroApi(404, "NAO_ENCONTRADO", f"{rotulo} não encontrado.")
    novo = {**item, "criado_em": atual.get("criado_em")}
    dados.gravar(novo)
    alterados = sorted(k for k in novo if k not in ("atualizado_em",) and novo.get(k) != atual.get(k))
    dados.registrar_auditoria(usuario, acao, item["PK"], detalhes={"campos_alterados": alterados})
    return _sem_chaves(novo)


def listar_instrumentos(req: Requisicao, usuario: Usuario) -> dict[str, Any]:
    setor = (req.consulta.get("setor") or "").strip()
    itens = dados.consultar_indice("GSI2", f"SETOR#{setor}", prefixo_sk="INST#") if setor else dados.listar_por_tipo("INSTRUMENTO")
    return {"itens": sorted((_sem_chaves(i) for i in itens), key=lambda i: i["codigo"])}


def criar_instrumento(req: Requisicao, usuario: Usuario) -> tuple[int, dict[str, Any]]:
    return _criar(_instrumento(req.json()), usuario, "INSTRUMENTO_CADASTRADO", "Instrumento")


def alterar_instrumento(req: Requisicao, usuario: Usuario) -> dict[str, Any]:
    codigo = codigo_instrumento(req.parametros["codigo"])
    if not codigo:
        raise ErroApi(400, "CODIGO_INVALIDO", "Código de instrumento inválido.")
    return _alterar(_instrumento(req.json(), codigo), usuario, "INSTRUMENTO_ALTERADO", "Instrumento")


def listar_padroes(req: Requisicao, usuario: Usuario) -> dict[str, Any]:
    itens = []
    for p in dados.listar_por_tipo("PADRAO"):
        item = _sem_chaves(p)
        item["situacao"] = regras.situacao_padrao(p)
        itens.append(item)
    return {"itens": sorted(itens, key=lambda i: i["codigo"]), "componentes_sugeridos": list(COMPONENTES_PADRAO_MMC),
            "temperatura_sugerida": TEMPERATURA_PADRAO}


def criar_padrao(req: Requisicao, usuario: Usuario) -> tuple[int, dict[str, Any]]:
    return _criar(_padrao(req.json()), usuario, "PADRAO_CADASTRADO", "Padrão")


def alterar_padrao(req: Requisicao, usuario: Usuario) -> dict[str, Any]:
    codigo = codigo_padrao(req.parametros["codigo"])
    if not codigo:
        raise ErroApi(400, "CODIGO_INVALIDO", "Código de padrão inválido.")
    return _alterar(_padrao(req.json(), codigo), usuario, "PADRAO_ALTERADO", "Padrão")


def listar_procedimentos(req: Requisicao, usuario: Usuario) -> dict[str, Any]:
    return {"itens": sorted((_sem_chaves(p) for p in dados.listar_por_tipo("PROCEDIMENTO")),
                            key=lambda p: (p["codigo"], p.get("revisao", 0)))}


def criar_procedimento(req: Requisicao, usuario: Usuario) -> tuple[int, dict[str, Any]]:
    corpo = req.json()
    codigo = texto_ou_nulo(corpo.get("codigo"), 30)
    revisao = corpo.get("revisao")
    descricao = texto_ou_nulo(corpo.get("descricao"))
    tipo = texto_ou_nulo(corpo.get("tipo_instrumento"))
    if not codigo or not isinstance(revisao, int) or isinstance(revisao, bool) or revisao < 0 or not descricao or not tipo:
        raise ErroApi(400, "DADOS_INVALIDOS", "Informe código, revisão (inteiro), descrição e tipo de instrumento.")
    item = {**dados.chave_procedimento(codigo.upper(), revisao), "tipo": "PROCEDIMENTO", "codigo": codigo.upper(),
            "revisao": revisao, "descricao": descricao, "tipo_instrumento": tipo, "ativo": True, "atualizado_em": agora_iso()}
    return _criar(item, usuario, "PROCEDIMENTO_CADASTRADO", "Procedimento")


def semente(req: Requisicao, usuario: Usuario) -> dict[str, Any]:
    criados = []
    candidatos = [
        _padrao(SEMENTE_PADRAO),
        {**dados.chave_procedimento(SEMENTE_PROCEDIMENTO["codigo"], SEMENTE_PROCEDIMENTO["revisao"]), "tipo": "PROCEDIMENTO",
         **SEMENTE_PROCEDIMENTO, "ativo": True, "atualizado_em": agora_iso()},
        _instrumento(SEMENTE_INSTRUMENTO),
    ]
    for item in candidatos:
        try:
            dados.gravar({**item, "criado_em": agora_iso()}, condicao="attribute_not_exists(PK)")
            criados.append(item["PK"])
        except ClientError as erro:
            if not dados.condicao_falhou(erro):
                raise
    dados.registrar_auditoria(usuario, "CADASTROS_INICIAIS", "SEMENTE", detalhes={"criados": criados})
    return {"criados": criados}


ROTAS = [
    rota("GET", "/api/cadastros/instrumentos", listar_instrumentos, TODOS),
    rota("POST", "/api/cadastros/instrumentos", criar_instrumento, ("admin",)),
    rota("PUT", "/api/cadastros/instrumentos/{codigo}", alterar_instrumento, ("admin",)),
    rota("GET", "/api/cadastros/padroes", listar_padroes, TODOS),
    rota("POST", "/api/cadastros/padroes", criar_padrao, ("admin", "signatario")),
    rota("PUT", "/api/cadastros/padroes/{codigo}", alterar_padrao, ("admin", "signatario")),
    rota("GET", "/api/cadastros/procedimentos", listar_procedimentos, TODOS),
    rota("POST", "/api/cadastros/procedimentos", criar_procedimento, ("admin",)),
    rota("POST", "/api/cadastros/semente", semente, ("admin",)),
]


def handler(evento: dict[str, Any], contexto: Any) -> dict[str, Any]:
    return despachar(evento, ROTAS, autenticar)
