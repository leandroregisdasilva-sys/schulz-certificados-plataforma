from __future__ import annotations

import secrets
import time
import uuid
from typing import Any, Iterable

from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError

from .config import tabela
from .normalizacao import agora_iso, de_dynamo, para_dynamo

META = "META"
STATUS_EMITIDOS = ("EMITIDA", "ANULADA")
DIAS_STAGING = 30


def chave_instrumento(codigo: str) -> dict[str, str]:
    return {"PK": f"INST#{codigo}", "SK": META}


def chave_padrao(codigo: str) -> dict[str, str]:
    return {"PK": f"PAD#{codigo}", "SK": META}


def chave_procedimento(codigo: str, revisao: int) -> dict[str, str]:
    return {"PK": f"PROC#{codigo}", "SK": f"REV#{revisao}"}


def chave_calibracao(id_calibracao: str) -> dict[str, str]:
    return {"PK": f"CAL#{id_calibracao}", "SK": META}


def chave_importacao(id_importacao: str) -> dict[str, str]:
    return {"PK": f"IMP#{id_importacao}", "SK": META}


def chave_certificado(ano: int, numero: int) -> dict[str, str]:
    return {"PK": f"CERT#{ano}-{numero:05d}", "SK": META}


def chave_validacao(codigo: str) -> dict[str, str]:
    return {"PK": f"VAL#{codigo}", "SK": META}


def chave_pendente(data_iso: str, id_calibracao: str) -> dict[str, str]:
    return {"PK": "PENDENTES", "SK": f"DATA#{data_iso}#{id_calibracao}"}


def novo_id() -> str:
    return uuid.uuid4().hex


def novo_codigo_validacao() -> str:
    return secrets.token_urlsafe(12)


def obter(chave: dict[str, str], consistente: bool = False) -> dict[str, Any] | None:
    item = tabela().get_item(Key=chave, ConsistentRead=consistente).get("Item")
    return de_dynamo(item) if item else None


def gravar(item: dict[str, Any], condicao: str | None = None) -> None:
    argumentos: dict[str, Any] = {"Item": para_dynamo(item)}
    if condicao:
        argumentos["ConditionExpression"] = condicao
    tabela().put_item(**argumentos)


def atualizar(chave: dict[str, str], campos: dict[str, Any], condicao: Any = None,
              remover: Iterable[str] = ()) -> dict[str, Any]:
    nomes: dict[str, str] = {}
    valores: dict[str, Any] = {}
    partes = []
    for i, (campo, valor) in enumerate(campos.items()):
        nomes[f"#campo{i}"] = campo
        valores[f":valor{i}"] = para_dynamo(valor)
        partes.append(f"#campo{i} = :valor{i}")
    expressao = "SET " + ", ".join(partes)
    remocoes = list(remover)
    if remocoes:
        for j, campo in enumerate(remocoes):
            nomes[f"#remover{j}"] = campo
        expressao += " REMOVE " + ", ".join(f"#remover{j}" for j in range(len(remocoes)))
    argumentos: dict[str, Any] = {
        "Key": chave,
        "UpdateExpression": expressao,
        "ExpressionAttributeNames": nomes,
        "ExpressionAttributeValues": valores,
        "ReturnValues": "ALL_NEW",
    }
    if condicao is not None:
        argumentos["ConditionExpression"] = condicao
    return de_dynamo(tabela().update_item(**argumentos)["Attributes"])


def excluir_pendente(data_iso: str, id_calibracao: str) -> None:
    tabela().delete_item(Key=chave_pendente(data_iso, id_calibracao))


def condicao_falhou(erro: ClientError) -> bool:
    return erro.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException"


def consultar_particao(pk: str, prefixo_sk: str | None = None, decrescente: bool = False,
                       limite: int | None = None) -> list[dict[str, Any]]:
    condicao = Key("PK").eq(pk)
    if prefixo_sk:
        condicao = condicao & Key("SK").begins_with(prefixo_sk)
    argumentos: dict[str, Any] = {"KeyConditionExpression": condicao, "ScanIndexForward": not decrescente}
    itens: list[dict[str, Any]] = []
    while True:
        r = tabela().query(**argumentos)
        itens.extend(r.get("Items", []))
        if limite and len(itens) >= limite:
            itens = itens[:limite]
            break
        if "LastEvaluatedKey" not in r:
            break
        argumentos["ExclusiveStartKey"] = r["LastEvaluatedKey"]
    return [de_dynamo(i) for i in itens]


def consultar_indice(indice: str, pk: str, sk_de: str | None = None, sk_ate: str | None = None,
                     prefixo_sk: str | None = None) -> list[dict[str, Any]]:
    campo_pk, campo_sk = f"{indice}PK", f"{indice}SK"
    condicao = Key(campo_pk).eq(pk)
    if sk_de and sk_ate:
        condicao = condicao & Key(campo_sk).between(sk_de, sk_ate)
    elif prefixo_sk:
        condicao = condicao & Key(campo_sk).begins_with(prefixo_sk)
    argumentos: dict[str, Any] = {"IndexName": indice, "KeyConditionExpression": condicao, "ScanIndexForward": False}
    itens: list[dict[str, Any]] = []
    while True:
        r = tabela().query(**argumentos)
        itens.extend(r.get("Items", []))
        if "LastEvaluatedKey" not in r or len(itens) >= 500:
            break
        argumentos["ExclusiveStartKey"] = r["LastEvaluatedKey"]
    return [de_dynamo(i) for i in itens]


def listar_por_tipo(tipo: str, limite: int = 1000) -> list[dict[str, Any]]:
    argumentos: dict[str, Any] = {"FilterExpression": Attr("tipo").eq(tipo)}
    itens: list[dict[str, Any]] = []
    while True:
        r = tabela().scan(**argumentos)
        itens.extend(r.get("Items", []))
        if "LastEvaluatedKey" not in r or len(itens) >= limite:
            break
        argumentos["ExclusiveStartKey"] = r["LastEvaluatedKey"]
    return [de_dynamo(i) for i in itens[:limite]]


def gravar_lote(itens: list[dict[str, Any]]) -> None:
    with tabela().batch_writer(overwrite_by_pkeys=["PK", "SK"]) as lote:
        for item in itens:
            lote.put_item(Item=para_dynamo(item))


def resultados(id_calibracao: str) -> list[dict[str, Any]]:
    return consultar_particao(f"CAL#{id_calibracao}", "RES#")


def proximo_numero(ano: int) -> int:
    r = tabela().update_item(
        Key={"PK": f"SEQ#{ano}", "SK": "CERT"},
        UpdateExpression="ADD ultimo :um SET atualizado_em = :agora",
        ExpressionAttributeValues={":um": 1, ":agora": agora_iso()},
        ReturnValues="UPDATED_NEW",
    )
    return int(r["Attributes"]["ultimo"])


def registrar_auditoria(usuario: Any, acao: str, alvo: str, resultado: str = "SUCESSO",
                        detalhes: dict[str, Any] | None = None) -> None:
    instante = agora_iso()
    item = {
        "PK": f"AUD#{instante[:7]}",
        "SK": f"{instante}#{uuid.uuid4().hex[:8]}",
        "tipo": "AUDITORIA",
        "instante": instante,
        "usuario_sub": getattr(usuario, "sub", None) or "sistema",
        "usuario_nome": getattr(usuario, "nome", None) or "sistema",
        "acao": acao,
        "alvo": alvo,
        "resultado": resultado,
        "detalhes": detalhes or {},
    }
    gravar(item)


def expiracao_staging() -> int:
    return int(time.time()) + DIAS_STAGING * 86400
