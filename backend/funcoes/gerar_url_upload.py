from __future__ import annotations

from typing import Any

from schulz import dados
from schulz.api import ErroApi, Requisicao, despachar, rota
from schulz.autenticacao import Usuario, autenticar
from schulz.config import s3, variavel
from schulz.ingestao import ErroPlanilha, validar_nome_arquivo
from schulz.normalizacao import agora_iso

TAMANHO_MAXIMO = 5 * 1024 * 1024
VALIDADE_SEGUNDOS = 300


def criar_upload(req: Requisicao, usuario: Usuario) -> tuple[int, dict[str, Any]]:
    corpo = req.json()
    nome = str(corpo.get("nome_arquivo") or "").strip()
    try:
        validar_nome_arquivo(nome)
    except ErroPlanilha as erro:
        raise ErroApi(400, erro.codigo, erro.mensagem, erro.detalhes)
    tamanho = corpo.get("tamanho")
    if not isinstance(tamanho, int) or isinstance(tamanho, bool) or tamanho <= 0:
        raise ErroApi(400, "TAMANHO_INVALIDO", "Informe o tamanho do arquivo em bytes.")
    if tamanho > TAMANHO_MAXIMO:
        raise ErroApi(413, "ARQUIVO_GRANDE", "O arquivo excede o limite de 5 MB.", [f"Tamanho informado: {tamanho} bytes"])
    id_importacao = dados.novo_id()
    chave_s3 = f"uploads/{usuario.sub}/{id_importacao}.xlsx"
    dados.gravar({
        **dados.chave_importacao(id_importacao),
        "tipo": "IMPORTACAO",
        "id_importacao": id_importacao,
        "status": "RECEBIDA",
        "nome_arquivo": nome[:200],
        "s3_key": chave_s3,
        "id_tecnico": usuario.sub,
        "nome_usuario_envio": usuario.nome,
        "criado_em": agora_iso(),
        "expira_em": dados.expiracao_staging(),
    })
    assinatura = s3().generate_presigned_post(
        Bucket=variavel("BUCKET_UPLOADS"),
        Key=chave_s3,
        Fields={"Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
        Conditions=[
            ["content-length-range", 1, TAMANHO_MAXIMO],
            {"Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
        ],
        ExpiresIn=VALIDADE_SEGUNDOS,
    )
    dados.registrar_auditoria(usuario, "UPLOAD_SOLICITADO", f"IMP#{id_importacao}", detalhes={"arquivo": nome[:200]})
    return 201, {
        "id": id_importacao,
        "url": assinatura["url"],
        "campos": assinatura["fields"],
        "validade_segundos": VALIDADE_SEGUNDOS,
        "tamanho_maximo": TAMANHO_MAXIMO,
    }


ROTAS = [rota("POST", "/api/uploads", criar_upload, ("tecnico", "admin"))]


def handler(evento: dict[str, Any], contexto: Any) -> dict[str, Any]:
    return despachar(evento, ROTAS, autenticar)
