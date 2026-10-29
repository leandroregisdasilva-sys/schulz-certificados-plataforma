from __future__ import annotations

import base64
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "backend"))

REGIAO = "sa-east-1"
TABELA = "schulz-calibracao"
BUCKET_SITE = "schulz-local-site"
BUCKET_UPLOADS = "schulz-local-uploads"
BUCKET_CERTIFICADOS = "schulz-local-certificados"
USER_POOL_ID = "sa-east-1_LOCAL"
CLIENT_ID = "cliente-local"
EMISSOR = "https://cognito-idp.sa-east-1.amazonaws.com/sa-east-1_LOCAL"


def _b64(dados: bytes) -> str:
    return base64.urlsafe_b64encode(dados).rstrip(b"=").decode("ascii")


class EmissorTokens:
    def __init__(self) -> None:
        from cryptography.hazmat.primitives.asymmetric import rsa

        self._chave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.kid = uuid.uuid4().hex[:12]

    def jwks(self) -> dict[str, Any]:
        numeros = self._chave.public_key().public_numbers()
        tamanho = (numeros.n.bit_length() + 7) // 8
        return {"keys": [{"kty": "RSA", "kid": self.kid, "alg": "RS256", "use": "sig",
                          "n": _b64(numeros.n.to_bytes(tamanho, "big")), "e": _b64(numeros.e.to_bytes(3, "big"))}]}

    def emitir(self, email: str, nome: str, grupos: list[str], sub: str | None = None, validade: int = 3600,
               **extra: Any) -> str:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding

        agora = int(time.time())
        cabecalho = {"alg": "RS256", "kid": self.kid, "typ": "JWT"}
        corpo = {"sub": sub or str(uuid.uuid5(uuid.NAMESPACE_DNS, email)), "email": email, "name": nome,
                 "cognito:groups": grupos, "iss": EMISSOR, "aud": CLIENT_ID, "token_use": "id",
                 "iat": agora, "exp": agora + validade, **extra}
        mensagem = f"{_b64(json.dumps(cabecalho).encode())}.{_b64(json.dumps(corpo).encode())}"
        assinatura = self._chave.sign(mensagem.encode("ascii"), padding.PKCS1v15(), hashes.SHA256())
        return f"{mensagem}.{_b64(assinatura)}"


def configurar_ambiente(emissor: EmissorTokens) -> None:
    os.environ.update({
        "AWS_ACCESS_KEY_ID": "testing", "AWS_SECRET_ACCESS_KEY": "testing", "AWS_SESSION_TOKEN": "testing",
        "AWS_DEFAULT_REGION": REGIAO, "AWS_REGION": REGIAO, "TABELA": TABELA, "BUCKET_UPLOADS": BUCKET_UPLOADS,
        "BUCKET_CERTIFICADOS": BUCKET_CERTIFICADOS, "USER_POOL_ID": USER_POOL_ID, "CLIENT_ID": CLIENT_ID,
        "JWKS_JSON": json.dumps(emissor.jwks()),
    })
    os.environ.pop("URL_SITE", None)
    os.environ.pop("TOPICO_ALERTAS", None)


def criar_recursos() -> None:
    import boto3

    ddb = boto3.client("dynamodb", region_name=REGIAO)
    ddb.create_table(
        TableName=TABELA,
        AttributeDefinitions=[{"AttributeName": n, "AttributeType": "S"} for n in
                              ("PK", "SK", "GSI1PK", "GSI1SK", "GSI2PK", "GSI2SK")],
        KeySchema=[{"AttributeName": "PK", "KeyType": "HASH"}, {"AttributeName": "SK", "KeyType": "RANGE"}],
        ProvisionedThroughput={"ReadCapacityUnits": 10, "WriteCapacityUnits": 10},
        GlobalSecondaryIndexes=[
            {"IndexName": i, "KeySchema": [{"AttributeName": f"{i}PK", "KeyType": "HASH"},
                                           {"AttributeName": f"{i}SK", "KeyType": "RANGE"}],
             "Projection": {"ProjectionType": "ALL"},
             "ProvisionedThroughput": {"ReadCapacityUnits": 5, "WriteCapacityUnits": 5}}
            for i in ("GSI1", "GSI2")
        ],
    )
    s3 = boto3.client("s3", region_name=REGIAO)
    for nome in (BUCKET_SITE, BUCKET_UPLOADS):
        s3.create_bucket(Bucket=nome, CreateBucketConfiguration={"LocationConstraint": REGIAO})
    s3.create_bucket(Bucket=BUCKET_CERTIFICADOS, CreateBucketConfiguration={"LocationConstraint": REGIAO},
                     ObjectLockEnabledForBucket=True)
    s3.put_object_lock_configuration(
        Bucket=BUCKET_CERTIFICADOS,
        ObjectLockConfiguration={"ObjectLockEnabled": "Enabled",
                                 "Rule": {"DefaultRetention": {"Mode": "GOVERNANCE", "Days": 1}}},
    )


def evento_http(metodo: str, caminho: str, corpo: Any = None, token: str | None = None,
                consulta: dict[str, str] | None = None, origem: str = "https://d111111abcdef8.cloudfront.net") -> dict[str, Any]:
    cabecalhos = {"origin": origem, "content-type": "application/json"}
    if token:
        cabecalhos["x-token-acesso"] = token
    texto = json.dumps(corpo) if isinstance(corpo, (dict, list)) else (corpo or "")
    return {
        "version": "2.0",
        "rawPath": caminho,
        "rawQueryString": "&".join(f"{k}={v}" for k, v in (consulta or {}).items()),
        "queryStringParameters": consulta or None,
        "headers": cabecalhos,
        "requestContext": {"http": {"method": metodo, "path": caminho}},
        "body": texto,
        "isBase64Encoded": False,
    }


def evento_s3(chave: str, bucket: str = BUCKET_UPLOADS) -> dict[str, Any]:
    return {"Records": [{"eventSource": "aws:s3", "s3": {"bucket": {"name": bucket}, "object": {"key": chave}}}]}
