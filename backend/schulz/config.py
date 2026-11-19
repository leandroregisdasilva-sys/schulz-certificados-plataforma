from __future__ import annotations

import os
from functools import lru_cache

import boto3
from botocore.config import Config


def regiao() -> str:
    return os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "sa-east-1"


def variavel(nome: str, padrao: str | None = None) -> str:
    valor = os.environ.get(nome, padrao)
    if valor is None:
        raise RuntimeError(f"Variável de ambiente ausente: {nome}")
    return valor


@lru_cache(maxsize=None)
def tabela():
    return boto3.resource("dynamodb", region_name=regiao()).Table(variavel("TABELA"))


@lru_cache(maxsize=None)
def cliente_dynamodb():
    return boto3.client("dynamodb", region_name=regiao())


@lru_cache(maxsize=None)
def s3():
    return boto3.client(
        "s3",
        region_name=regiao(),
        endpoint_url=f"https://s3.{regiao()}.amazonaws.com",
        config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"}),
    )


@lru_cache(maxsize=None)
def sns():
    return boto3.client("sns", region_name=regiao())


def limpar_cache() -> None:
    for fabrica in (tabela, cliente_dynamodb, s3, sns):
        fabrica.cache_clear()
