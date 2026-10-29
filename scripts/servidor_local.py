from __future__ import annotations

import argparse
import base64
import email.parser
import email.policy
import hashlib
import hmac
import json
import mimetypes
import secrets
import struct
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import ambiente_local as amb

FRONTEND = amb.RAIZ / "frontend"
SENHA_PADRAO = "Local@12345"


def totp(segredo: str, instante: float | None = None, passo: int = 30) -> str:
    chave = base64.b32decode(segredo + "=" * (-len(segredo) % 8))
    contador = int((instante or time.time()) // passo)
    resumo = hmac.new(chave, struct.pack(">Q", contador), hashlib.sha1).digest()
    deslocamento = resumo[-1] & 0x0F
    valor = struct.unpack(">I", resumo[deslocamento:deslocamento + 4])[0] & 0x7FFFFFFF
    return f"{valor % 1_000_000:06d}"


def totp_valido(segredo: str, codigo: str) -> bool:
    agora = time.time()
    return any(hmac.compare_digest(totp(segredo, agora + d), codigo) for d in (-30, 0, 30))


class CognitoLocal:
    def __init__(self, emissor: amb.EmissorTokens) -> None:
        self.emissor = emissor
        self.usuarios = {
            "tecnico@local.test": {"nome": "Ana Técnica", "grupos": ["tecnico"]},
            "signatario@local.test": {"nome": "João Carlos Signatário", "grupos": ["signatario"]},
            "admin@local.test": {"nome": "Diego Administrador", "grupos": ["admin"]},
            "auditor@local.test": {"nome": "Carla Auditora", "grupos": ["auditor"]},
            "novo@local.test": {"nome": "Novo Usuário", "grupos": ["tecnico"], "trocar_senha": True, "exigir_mfa": True},
        }
        for u in self.usuarios.values():
            u.setdefault("senha", SENHA_PADRAO)
        self.sessoes: dict[str, dict] = {}

    def _erro(self, tipo: str, mensagem: str) -> tuple[int, dict]:
        return 400, {"__type": tipo, "message": mensagem}

    def _resultado(self, email: str) -> dict:
        u = self.usuarios[email]
        return {"AuthenticationResult": {"IdToken": self.emissor.emitir(email, u["nome"], u["grupos"]),
                                         "AccessToken": secrets.token_urlsafe(24), "RefreshToken": f"ref-{email}",
                                         "ExpiresIn": 3600, "TokenType": "Bearer"}}

    def _proximo(self, email: str) -> dict:
        u = self.usuarios[email]
        sessao = secrets.token_urlsafe(16)
        self.sessoes[sessao] = {"email": email}
        if u.get("trocar_senha"):
            return {"ChallengeName": "NEW_PASSWORD_REQUIRED", "Session": sessao,
                    "ChallengeParameters": {"USER_ID_FOR_SRP": email}}
        if u.get("exigir_mfa") and not u.get("segredo"):
            return {"ChallengeName": "MFA_SETUP", "Session": sessao, "ChallengeParameters": {"USER_ID_FOR_SRP": email}}
        if u.get("segredo"):
            return {"ChallengeName": "SOFTWARE_TOKEN_MFA", "Session": sessao, "ChallengeParameters": {"USER_ID_FOR_SRP": email}}
        self.sessoes.pop(sessao, None)
        return self._resultado(email)

    def chamar(self, alvo: str, corpo: dict) -> tuple[int, dict]:
        acao = alvo.split(".")[-1]
        if acao == "InitiateAuth":
            if corpo.get("AuthFlow") == "REFRESH_TOKEN_AUTH":
                email = str(corpo.get("AuthParameters", {}).get("REFRESH_TOKEN", ""))[4:]
                return (200, self._resultado(email)) if email in self.usuarios else self._erro("NotAuthorizedException", "Refresh inválido")
            p = corpo.get("AuthParameters", {})
            u = self.usuarios.get(p.get("USERNAME", ""))
            if not u or u["senha"] != p.get("PASSWORD"):
                return self._erro("NotAuthorizedException", "Incorrect username or password.")
            return 200, self._proximo(p["USERNAME"])
        sessao = self.sessoes.get(corpo.get("Session", ""))
        if sessao is None:
            return self._erro("NotAuthorizedException", "Invalid session")
        email = sessao["email"]
        u = self.usuarios[email]
        if acao == "AssociateSoftwareToken":
            sessao["pendente"] = base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")
            return 200, {"SecretCode": sessao["pendente"], "Session": corpo["Session"]}
        if acao == "VerifySoftwareToken":
            if not totp_valido(sessao.get("pendente", ""), str(corpo.get("UserCode", ""))):
                return self._erro("EnableSoftwareTokenMFAException", "Code mismatch")
            u["segredo"] = sessao.pop("pendente")
            return 200, {"Status": "SUCCESS", "Session": corpo["Session"]}
        if acao == "RespondToAuthChallenge":
            nome = corpo.get("ChallengeName")
            r = corpo.get("ChallengeResponses", {})
            if nome == "NEW_PASSWORD_REQUIRED":
                nova = str(r.get("NEW_PASSWORD", ""))
                if len(nova) < 10 or nova.lower() == nova or not any(ch.isdigit() for ch in nova):
                    return self._erro("InvalidPasswordException", "Password does not conform to policy")
                u["senha"], u["trocar_senha"] = nova, False
            elif nome == "MFA_SETUP":
                if not u.get("segredo"):
                    return self._erro("NotAuthorizedException", "MFA not configured")
                self.sessoes.pop(corpo["Session"], None)
                return 200, self._resultado(email)
            elif nome == "SOFTWARE_TOKEN_MFA":
                if not totp_valido(u.get("segredo", ""), str(r.get("SOFTWARE_TOKEN_MFA_CODE", ""))):
                    return self._erro("CodeMismatchException", "Invalid code")
                self.sessoes.pop(corpo["Session"], None)
                return 200, self._resultado(email)
            self.sessoes.pop(corpo["Session"], None)
            return 200, self._proximo(email)
        return self._erro("InvalidParameterException", f"Ação não suportada: {acao}")


def criar_servidor(porta: int, demo: bool):
    from moto import mock_aws

    emissor = amb.EmissorTokens()
    amb.configurar_ambiente(emissor)
    mock = mock_aws()
    mock.start()
    from schulz import autenticacao, config

    config.limpar_cache()
    autenticacao.limpar_cache_chaves()
    amb.criar_recursos()
    from funcoes import cadastros, consultar_obter_certificado, emitir_certificado, gerar_url_upload, processar_planilha
    import boto3

    s3 = boto3.client("s3", region_name=amb.REGIAO)
    cognito = CognitoLocal(emissor)
    rotas = (("/api/uploads", gerar_url_upload.handler), ("/api/emissao", emitir_certificado.handler),
             ("/api/cadastros", cadastros.handler), ("/api/", consultar_obter_certificado.handler))
    config_js = ("window.SCHULZ_CONFIG = " + json.dumps({
        "regiao": amb.REGIAO, "userPoolId": amb.USER_POOL_ID, "clientId": amb.CLIENT_ID, "modo": "local",
        "cognitoEndpoint": "/_cognito", "api": {"padrao": "", "uploads": "", "emissao": "", "cadastros": ""}}) + ";\n").encode()

    if demo:
        import json as _json
        token = emissor.emitir("admin@local.test", "Diego Administrador", ["admin"])
        cadastros.handler(amb.evento_http("POST", "/api/cadastros/semente", {}, token), None)
        lista = _json.loads(cadastros.handler(amb.evento_http("GET", "/api/cadastros/padroes", None, token), None)["body"])
        padrao = lista["itens"][0]
        padrao.update({"data_ultima_cal": "2026-09-15", "data_proxima_cal": "2027-09-15"})
        cadastros.handler(amb.evento_http("PUT", "/api/cadastros/padroes/500011", padrao, token), None)

    class Manipulador(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, formato, *args):
            pass

        def _enviar(self, status: int, corpo: bytes, tipo: str = "application/json; charset=utf-8", extra=None):
            self.send_response(status)
            self.send_header("content-type", tipo)
            self.send_header("content-length", str(len(corpo)))
            self.send_header("cache-control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(corpo)

        def _corpo(self) -> bytes:
            tamanho = int(self.headers.get("content-length") or 0)
            return self.rfile.read(tamanho) if tamanho else b""

        def _api(self, metodo: str):
            partes = urllib.parse.urlsplit(self.path)
            corpo = self._corpo()
            consulta = dict(urllib.parse.parse_qsl(partes.query))
            evento = {"rawPath": partes.path, "queryStringParameters": consulta or None,
                      "headers": {k.lower(): v for k, v in self.headers.items()},
                      "requestContext": {"http": {"method": metodo, "path": partes.path}},
                      "body": corpo.decode("utf-8") if corpo else "", "isBase64Encoded": False}
            evento["headers"].setdefault("origin", f"http://localhost:{porta}")
            for prefixo, funcao in rotas:
                if partes.path.startswith(prefixo):
                    r = funcao(evento, None)
                    dados = json.loads(r["body"])
                    if partes.path == "/api/uploads" and r["statusCode"] == 201:
                        dados["url"] = "/_s3/upload"
                    if partes.path.endswith("/certificado") and r["statusCode"] == 200:
                        url = urllib.parse.urlsplit(dados["url"])
                        dados["url"] = "/_s3/obter?" + urllib.parse.urlencode({"chave": url.path.lstrip("/")})
                    return self._enviar(r["statusCode"], json.dumps(dados, ensure_ascii=False).encode("utf-8"))
            self._enviar(404, b'{"erro":{"codigo":"RECURSO_NAO_ENCONTRADO","mensagem":"Recurso nao encontrado"}}')

        def _upload(self):
            bruto = self._corpo()
            if len(bruto) > 6 * 1024 * 1024:
                return self._enviar(400, b"EntityTooLarge", "text/plain")
            cabecalho = f"Content-Type: {self.headers.get('content-type')}\r\n\r\n".encode()
            mensagem = email.parser.BytesParser(policy=email.policy.default).parsebytes(cabecalho + bruto)
            campos, arquivo = {}, None
            for parte in mensagem.iter_parts():
                nome = parte.get_param("name", header="content-disposition")
                if nome == "file":
                    arquivo = parte.get_payload(decode=True)
                else:
                    campos[nome] = parte.get_content()
            if not arquivo or "key" not in campos:
                return self._enviar(400, b"MalformedPOSTRequest", "text/plain")
            s3.put_object(Bucket=amb.BUCKET_UPLOADS, Key=campos["key"], Body=arquivo)
            threading.Thread(target=processar_planilha.handler, args=(amb.evento_s3(campos["key"]), None), daemon=True).start()
            self._enviar(204, b"", "text/plain")

        def do_GET(self):
            partes = urllib.parse.urlsplit(self.path)
            if partes.path.startswith("/api/"):
                return self._api("GET")
            if partes.path == "/_s3/obter":
                chave = urllib.parse.parse_qs(partes.query).get("chave", [""])[0]
                pdf = s3.get_object(Bucket=amb.BUCKET_CERTIFICADOS, Key=chave)["Body"].read()
                return self._enviar(200, pdf, "application/pdf")
            if partes.path == "/config.js":
                return self._enviar(200, config_js, "application/javascript; charset=utf-8")
            caminho = (FRONTEND / (partes.path.lstrip("/") or "index.html")).resolve()
            if FRONTEND not in caminho.parents and caminho != FRONTEND or not caminho.is_file():
                return self._enviar(404, b"nao encontrado", "text/plain")
            tipo = mimetypes.guess_type(str(caminho))[0] or "application/octet-stream"
            if tipo.startswith("text/") or tipo.endswith("javascript"):
                tipo += "; charset=utf-8"
            self._enviar(200, caminho.read_bytes(), tipo)

        def do_POST(self):
            partes = urllib.parse.urlsplit(self.path)
            if partes.path == "/_cognito":
                status, dados = cognito.chamar(self.headers.get("x-amz-target", ""), json.loads(self._corpo() or b"{}"))
                return self._enviar(status, json.dumps(dados).encode(), "application/x-amz-json-1.1")
            if partes.path == "/_s3/upload":
                return self._upload()
            return self._api("POST")

        def do_PUT(self):
            return self._api("PUT")

        def do_DELETE(self):
            return self._api("DELETE")

    servidor = ThreadingHTTPServer(("127.0.0.1", porta), Manipulador)
    servidor.cognito = cognito
    return servidor, mock


def main() -> None:
    ap = argparse.ArgumentParser(description="Simula a plataforma na sua máquina (AWS simulada com moto).")
    ap.add_argument("--porta", type=int, default=8080)
    ap.add_argument("--demo", action="store_true", help="carrega cadastros iniciais com o padrão vigente")
    args = ap.parse_args()
    servidor, _ = criar_servidor(args.porta, args.demo)
    print(f"Plataforma local em http://localhost:{args.porta}  (Ctrl+C para parar)")
    print(f"Usuários: tecnico@local.test, signatario@local.test, admin@local.test, auditor@local.test  senha {SENHA_PADRAO}")
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print("\nencerrado")


if __name__ == "__main__":
    sys.exit(main())
