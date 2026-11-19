from __future__ import annotations

import base64
import json
import logging
import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Callable, Iterable

log = logging.getLogger("schulz")
log.setLevel(logging.INFO)

LIMITE_CORPO = 64 * 1024


class ErroApi(Exception):
    def __init__(self, status: int, codigo: str, mensagem: str, detalhes: Iterable[str] | None = None):
        super().__init__(mensagem)
        self.status = status
        self.codigo = codigo
        self.mensagem = mensagem
        self.detalhes = list(detalhes or [])


@dataclass
class Requisicao:
    metodo: str
    caminho: str
    cabecalhos: dict[str, str]
    consulta: dict[str, str]
    corpo_bruto: bytes
    parametros: dict[str, str] = field(default_factory=dict)

    @property
    def origem(self) -> str | None:
        return self.cabecalhos.get("origin")

    def json(self) -> dict[str, Any]:
        if not self.corpo_bruto:
            return {}
        if len(self.corpo_bruto) > LIMITE_CORPO:
            raise ErroApi(413, "CORPO_GRANDE", "O corpo da requisição excede o limite permitido.")
        try:
            dados = json.loads(self.corpo_bruto.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ErroApi(400, "JSON_INVALIDO", "O corpo da requisição não é um JSON válido.")
        if not isinstance(dados, dict):
            raise ErroApi(400, "JSON_INVALIDO", "O corpo da requisição deve ser um objeto JSON.")
        return dados


def _serializar(valor: Any) -> Any:
    if isinstance(valor, Decimal):
        return int(valor) if valor == valor.to_integral_value() else float(valor)
    if isinstance(valor, (set, frozenset)):
        return sorted(valor)
    raise TypeError(f"Tipo não serializável: {type(valor).__name__}")


def resposta(status: int, corpo: Any) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {
            "content-type": "application/json; charset=utf-8",
            "cache-control": "no-store",
            "x-content-type-options": "nosniff",
        },
        "body": json.dumps(corpo, ensure_ascii=False, default=_serializar),
    }


def resposta_erro(erro: ErroApi) -> dict[str, Any]:
    return resposta(erro.status, {"erro": {"codigo": erro.codigo, "mensagem": erro.mensagem, "detalhes": erro.detalhes}})


def ler_evento(evento: dict[str, Any]) -> Requisicao:
    contexto = evento.get("requestContext") or {}
    http = contexto.get("http") or {}
    corpo = evento.get("body") or ""
    if evento.get("isBase64Encoded"):
        bruto = base64.b64decode(corpo)
    else:
        bruto = corpo.encode("utf-8") if isinstance(corpo, str) else bytes(corpo)
    cabecalhos = {str(k).lower(): str(v) for k, v in (evento.get("headers") or {}).items()}
    caminho = evento.get("rawPath") or http.get("path") or "/"
    return Requisicao(
        metodo=(http.get("method") or "GET").upper(),
        caminho=caminho.rstrip("/") or "/",
        cabecalhos=cabecalhos,
        consulta={str(k): str(v) for k, v in (evento.get("queryStringParameters") or {}).items()},
        corpo_bruto=bruto,
    )


@dataclass(frozen=True)
class Rota:
    metodo: str
    padrao: re.Pattern
    funcao: Callable[..., Any]
    perfis: frozenset[str] | None


def rota(metodo: str, caminho: str, funcao: Callable[..., Any], perfis: Iterable[str] | None) -> Rota:
    regex = "^" + re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", caminho) + "$"
    return Rota(metodo.upper(), re.compile(regex), funcao, None if perfis is None else frozenset(perfis))


def despachar(evento: dict[str, Any], rotas: list[Rota], autenticar: Callable[[Requisicao], Any]) -> dict[str, Any]:
    try:
        req = ler_evento(evento)
        encontrada = None
        metodo_errado = False
        for r in rotas:
            achado = r.padrao.match(req.caminho)
            if not achado:
                continue
            if r.metodo != req.metodo:
                metodo_errado = True
                continue
            encontrada, req.parametros = r, achado.groupdict()
            break
        if encontrada is None:
            if metodo_errado:
                raise ErroApi(405, "METODO_NAO_PERMITIDO", "Método não permitido para este recurso.")
            raise ErroApi(404, "RECURSO_NAO_ENCONTRADO", "Recurso não encontrado.")
        usuario = None
        if encontrada.perfis is not None:
            usuario = autenticar(req)
            if not usuario.tem_algum(encontrada.perfis):
                raise ErroApi(403, "PERFIL_SEM_PERMISSAO", "Seu perfil não tem permissão para esta ação.",
                              [f"Perfis permitidos: {', '.join(sorted(encontrada.perfis))}"])
        resultado = encontrada.funcao(req, usuario)
        if isinstance(resultado, tuple):
            status, corpo = resultado
        else:
            status, corpo = 200, resultado
        return resposta(status, corpo)
    except ErroApi as erro:
        return resposta_erro(erro)
    except Exception:
        log.exception("falha inesperada")
        return resposta_erro(ErroApi(500, "ERRO_INTERNO", "Erro interno. A falha foi registrada nos logs."))
