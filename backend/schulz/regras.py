from __future__ import annotations

from datetime import date
from typing import Any

from .autenticacao import Usuario
from .ingestao import normalizar
from .normalizacao import hoje_brasilia

DIAS_ALERTA_PADRAO = 30

CAMPOS_OBRIGATORIOS = (
    ("data_calibracao", "Data da calibração"),
    ("codigo_instrumento", "Código do instrumento"),
    ("denominacao", "Denominação do instrumento"),
    ("tecnico", "Técnico executante"),
    ("cliente", "Cliente solicitante"),
)


def pendencia(codigo: str, regra: str, mensagem: str) -> dict[str, str]:
    return {"codigo": codigo, "regra": regra, "mensagem": mensagem}


def situacao_padrao(padrao: dict[str, Any], referencia: date | None = None) -> str:
    hoje = referencia or hoje_brasilia()
    try:
        proxima = date.fromisoformat(str(padrao.get("data_proxima_cal"))[:10])
    except ValueError:
        return "SEM_DATA"
    if proxima < hoje:
        return "VENCIDO"
    if (proxima - hoje).days <= DIAS_ALERTA_PADRAO:
        return "VENCENDO"
    return "VIGENTE"


def procedimento_para(denominacao: str | None, procedimentos: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not denominacao:
        return None
    alvo = normalizar(denominacao)
    ativos = [p for p in procedimentos if p.get("ativo", True)]
    candidatos = [p for p in ativos if normalizar(str(p.get("tipo_instrumento") or "")) == alvo]
    if not candidatos:
        return None
    return max(candidatos, key=lambda p: int(p.get("revisao", 0)))


def campos_efetivos(cal: dict[str, Any], instrumento: dict[str, Any] | None) -> dict[str, Any]:
    inst = instrumento or {}
    return {
        "data_calibracao": cal.get("data_calibracao"),
        "codigo_instrumento": cal.get("codigo_instrumento"),
        "denominacao": inst.get("denominacao") or cal.get("denominacao"),
        "fabricante": inst.get("fabricante") or cal.get("fabricante"),
        "tecnico": cal.get("tecnico"),
        "cliente": cal.get("cliente") or inst.get("setor"),
    }


def avaliar_pendencias(cal: dict[str, Any], instrumento: dict[str, Any] | None, padrao: dict[str, Any] | None,
                       procedimento: dict[str, Any] | None) -> list[dict[str, str]]:
    lista: list[dict[str, str]] = []
    efetivos = campos_efetivos(cal, instrumento)
    for campo, rotulo in CAMPOS_OBRIGATORIOS:
        if not efetivos.get(campo):
            lista.append(pendencia("CAMPO_OBRIGATORIO", "RF09/ETL-09", f"Campo obrigatório ausente: {rotulo}."))
    if cal.get("codigo_instrumento") and instrumento is None:
        lista.append(pendencia("INSTRUMENTO_NAO_CADASTRADO", "UC01-FA-A",
                               f"Instrumento {cal['codigo_instrumento']} não encontrado no cadastro."))
    elif instrumento is not None and not instrumento.get("ativo", True):
        lista.append(pendencia("INSTRUMENTO_INATIVO", "RF08", f"Instrumento {instrumento['codigo']} está inativo."))
    if not cal.get("codigo_padrao"):
        lista.append(pendencia("PADRAO_NAO_INFORMADO", "RF09", "A planilha não informa o padrão (MMC) utilizado."))
    elif padrao is None:
        lista.append(pendencia("PADRAO_NAO_CADASTRADO", "RN02", f"Padrão {cal['codigo_padrao']} não encontrado no cadastro."))
    else:
        if not padrao.get("cert_calibracao"):
            lista.append(pendencia("CAMPO_OBRIGATORIO", "ETL-09", "Número do certificado de calibração do padrão ausente."))
        if not padrao.get("ativo", True):
            lista.append(pendencia("PADRAO_INATIVO", "RN02", f"Padrão {padrao['codigo']} está inativo."))
        data_cal = cal.get("data_calibracao")
        proxima = padrao.get("data_proxima_cal")
        if data_cal and proxima and str(proxima) < str(data_cal):
            lista.append(pendencia("PADRAO_VENCIDO", "RN02",
                                   f"ATENÇÃO: O padrão {padrao.get('codigo_exibicao') or padrao['codigo']} está com calibração "
                                   f"vencida desde {proxima}. A emissão do certificado foi bloqueada."))
    if efetivos.get("denominacao") and procedimento is None:
        lista.append(pendencia("PROCEDIMENTO_NAO_MAPEADO", "RN08",
                               f"Nenhum procedimento cadastrado para o tipo '{efetivos['denominacao']}'."))
    return lista


def pode_ver_previa(usuario: Usuario, cal: dict[str, Any]) -> bool:
    if cal.get("status") in ("EMITIDA", "ANULADA"):
        return True
    if usuario.tem_algum(("signatario", "admin")):
        return True
    return usuario.tem("tecnico") and cal.get("id_tecnico") == usuario.sub
