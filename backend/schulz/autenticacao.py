from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
import urllib.request
from dataclasses import dataclass
from typing import Any, Iterable

from .api import ErroApi, Requisicao
from .config import regiao

CABECALHO_TOKEN = "x-token-acesso"
GRUPOS = ("tecnico", "signatario", "auditor", "admin")
TODOS = GRUPOS
_PREFIXO_SHA256 = bytes.fromhex("3031300d060960864801650304020105000420")
_TOLERANCIA_RELOGIO = 60
_cache_chaves: dict[str, dict[str, Any]] = {}
_cache_instante = 0.0


@dataclass(frozen=True)
class Usuario:
    sub: str
    email: str
    nome: str
    grupos: frozenset[str]

    def tem(self, grupo: str) -> bool:
        return grupo in self.grupos

    def tem_algum(self, grupos: Iterable[str]) -> bool:
        return bool(self.grupos.intersection(grupos))

    def resumo(self) -> dict[str, Any]:
        return {"sub": self.sub, "email": self.email, "nome": self.nome, "grupos": sorted(self.grupos)}


def _b64url(texto: str) -> bytes:
    return base64.urlsafe_b64decode(texto + "=" * (-len(texto) % 4))


def _inteiro(texto: str) -> int:
    return int.from_bytes(_b64url(texto), "big")


def emissor() -> str:
    return os.environ.get("EMISSOR_TOKEN") or f"https://cognito-idp.{regiao()}.amazonaws.com/{os.environ['USER_POOL_ID']}"


def _baixar_chaves() -> dict[str, dict[str, Any]]:
    embutidas = os.environ.get("JWKS_JSON")
    if embutidas:
        conteudo = json.loads(embutidas)
    else:
        with urllib.request.urlopen(f"{emissor()}/.well-known/jwks.json", timeout=4) as r:
            conteudo = json.loads(r.read().decode("utf-8"))
    return {k["kid"]: k for k in conteudo.get("keys", []) if k.get("kty") == "RSA"}


def _chave(kid: str) -> dict[str, Any] | None:
    global _cache_chaves, _cache_instante
    if kid not in _cache_chaves or time.time() - _cache_instante > 3600:
        _cache_chaves = _baixar_chaves()
        _cache_instante = time.time()
    return _cache_chaves.get(kid)


def limpar_cache_chaves() -> None:
    global _cache_chaves, _cache_instante
    _cache_chaves, _cache_instante = {}, 0.0


def assinatura_rs256_valida(mensagem: bytes, assinatura: bytes, n: int, e: int) -> bool:
    tamanho = (n.bit_length() + 7) // 8
    if len(assinatura) != tamanho or tamanho < 128:
        return False
    s = int.from_bytes(assinatura, "big")
    if s >= n:
        return False
    recuperado = pow(s, e, n).to_bytes(tamanho, "big")
    resumo = _PREFIXO_SHA256 + hashlib.sha256(mensagem).digest()
    esperado = b"\x00\x01" + b"\xff" * (tamanho - 3 - len(resumo)) + b"\x00" + resumo
    return hmac.compare_digest(recuperado, esperado)


def _negar(mensagem: str) -> ErroApi:
    return ErroApi(401, "NAO_AUTENTICADO", mensagem)


def validar_token(token: str) -> Usuario:
    partes = token.split(".")
    if len(partes) != 3:
        raise _negar("Token de acesso malformado.")
    try:
        cabecalho = json.loads(_b64url(partes[0]))
        reivindicacoes = json.loads(_b64url(partes[1]))
        assinatura = _b64url(partes[2])
    except (ValueError, json.JSONDecodeError):
        raise _negar("Token de acesso malformado.")
    if cabecalho.get("alg") != "RS256":
        raise _negar("Algoritmo de assinatura não aceito.")
    chave = _chave(str(cabecalho.get("kid")))
    if chave is None:
        raise _negar("Chave de assinatura desconhecida.")
    mensagem = f"{partes[0]}.{partes[1]}".encode("ascii")
    if not assinatura_rs256_valida(mensagem, assinatura, _inteiro(chave["n"]), _inteiro(chave["e"])):
        raise _negar("Assinatura do token inválida.")
    agora = time.time()
    if float(reivindicacoes.get("exp", 0)) < agora - _TOLERANCIA_RELOGIO:
        raise _negar("Sessão expirada. Entre novamente.")
    if reivindicacoes.get("iss") != emissor():
        raise _negar("Emissor do token inválido.")
    if reivindicacoes.get("token_use") != "id":
        raise _negar("Tipo de token inválido.")
    if reivindicacoes.get("aud") != os.environ.get("CLIENT_ID"):
        raise _negar("Token emitido para outro aplicativo.")
    grupos = reivindicacoes.get("cognito:groups") or []
    if isinstance(grupos, str):
        grupos = [grupos]
    email = str(reivindicacoes.get("email") or reivindicacoes.get("cognito:username") or "")
    return Usuario(
        sub=str(reivindicacoes.get("sub")),
        email=email,
        nome=str(reivindicacoes.get("name") or email),
        grupos=frozenset(g for g in grupos if g in GRUPOS),
    )


def autenticar(req: Requisicao) -> Usuario:
    token = req.cabecalhos.get(CABECALHO_TOKEN, "").strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    if not token:
        raise _negar("Faça login para continuar.")
    return validar_token(token)
