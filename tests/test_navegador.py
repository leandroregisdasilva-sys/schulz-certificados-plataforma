from __future__ import annotations

import os
import re
import socket
import threading
from pathlib import Path

import pytest

playwright = pytest.importorskip("playwright.sync_api")

import servidor_local

pytestmark = pytest.mark.e2e
EXEMPLOS = Path(__file__).resolve().parent.parent / "exemplos"
SAIDA = os.environ.get("CAPTURAS")


def _porta() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def site():
    porta = _porta()
    servidor, mock = servidor_local.criar_servidor(porta, demo=False)
    t = threading.Thread(target=servidor.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{porta}", servidor
    servidor.shutdown()
    mock.stop()


@pytest.fixture(scope="module")
def navegador():
    with playwright.sync_playwright() as p:
        try:
            nav = p.chromium.launch()
        except Exception as erro:
            pytest.skip(f"Chromium do Playwright indisponível (rode: python -m playwright install chromium): {erro}")
        yield nav
        nav.close()


def foto(pagina, nome, inteira=True):
    if SAIDA:
        Path(SAIDA).mkdir(parents=True, exist_ok=True)
        pagina.screenshot(path=str(Path(SAIDA) / f"{nome}.png"), full_page=inteira)


def entrar(pagina, url, email, senha=servidor_local.SENHA_PADRAO):
    pagina.goto(url + "/")
    pagina.fill("#login-email", email)
    pagina.fill("#login-senha", senha)
    pagina.click("#form-senha button[type=submit]")


def sair(pagina):
    pagina.click("#sair")
    pagina.wait_for_selector("#tela-login:not([hidden])")


def test_fluxo_completo_no_navegador(site, navegador, tmp_path):
    url, servidor = site
    erros = []
    contexto = navegador.new_context(viewport={"width": 1280, "height": 860}, accept_downloads=True)
    pagina = contexto.new_page()
    pagina.on("pageerror", lambda e: erros.append(str(e)))

    pagina.goto(url + "/")
    pagina.wait_for_selector("#form-senha")
    foto(pagina, "01_login", False)
    entrar(pagina, url, "novo@local.test")
    pagina.wait_for_selector("#form-nova-senha:not([hidden])")
    pagina.fill("#nova-senha", "NovaSenha@2026")
    pagina.fill("#nova-senha2", "NovaSenha@2026")
    pagina.click("#form-nova-senha button[type=submit]")
    pagina.wait_for_selector("#form-mfa-config:not([hidden])")
    pagina.wait_for_selector("#mfa-qr img", state="attached")
    foto(pagina, "02_configurar_mfa", False)
    segredo = pagina.inner_text("#mfa-segredo").strip()
    pagina.fill("#mfa-codigo-config", servidor_local.totp(segredo))
    pagina.click("#form-mfa-config button[type=submit]")
    pagina.wait_for_selector("#app:not([hidden])")
    assert "Novo Usuário" in pagina.inner_text("#usuario-nome")
    sair(pagina)
    entrar(pagina, url, "novo@local.test", "NovaSenha@2026")
    pagina.wait_for_selector("#form-mfa:not([hidden])")
    pagina.fill("#mfa-codigo", servidor_local.totp(servidor.cognito.usuarios["novo@local.test"]["segredo"]))
    pagina.click("#form-mfa button[type=submit]")
    pagina.wait_for_selector("#app:not([hidden])")
    sair(pagina)

    entrar(pagina, url, "admin@local.test")
    pagina.wait_for_selector("#btn-semente")
    pagina.click("#btn-semente")
    pagina.wait_for_selector(".alerta.erro >> text=vencida")
    foto(pagina, "03_painel_alerta_padrao_vencido")
    sair(pagina)

    entrar(pagina, url, "tecnico@local.test")
    pagina.wait_for_selector("#menu a[href='#/nova']")
    pagina.click("#menu a[href='#/nova']")
    pagina.set_input_files("#arquivo", str(EXEMPLOS / "02_calypso_703-1208_reprovado.xlsx"))
    foto(pagina, "04_nova_calibracao", False)
    pagina.click("#enviar")
    pagina.wait_for_selector(".parecer.reprovado", timeout=30000)
    assert "vencida" in pagina.inner_text(".alerta.erro")
    foto(pagina, "05_previa_reprovada_tecnico")
    sair(pagina)

    entrar(pagina, url, "signatario@local.test")
    pagina.wait_for_selector("#menu a[href='#/cadastros']")
    pagina.goto(url + "/#/cadastros?aba=padroes")
    pagina.click("[data-editar='500011']")
    pagina.fill("input[name=data_ultima_cal]", "2026-09-15")
    pagina.fill("input[name=data_proxima_cal]", "2027-09-15")
    pagina.fill("input[name=cert_calibracao]", "260915502390 - Carl Zeiss")
    foto(pagina, "06_cadastro_padrao_incerteza")
    pagina.click("#padrao-form button[type=submit]")
    pagina.wait_for_selector(".status.VIGENTE")
    pagina.click("#menu a[href='#/pendentes']")
    pagina.wait_for_selector(".lista tbody tr")
    foto(pagina, "07_pendentes_assinatura")
    pagina.click(".lista tbody tr")
    pagina.wait_for_selector("#emitir")
    pagina.click("#emitir")
    pagina.wait_for_selector("#emissao-msg .alerta.erro >> text=ciente")
    pagina.check("#ciente")
    foto(pagina, "08_confirmacao_reprovado")
    pagina.click("#emitir")
    pagina.wait_for_selector("#qr-cert img", state="attached", timeout=30000)
    texto = pagina.inner_text("#conteudo h1")
    assert re.search(r"00001/2026", texto)
    foto(pagina, "09_certificado_emitido")
    link = pagina.get_attribute("#conteudo a[href*='validar.html?c=']", "href")
    with pagina.expect_popup() as aberto:
        pagina.click("#baixar")
    pdf_pagina = aberto.value
    pdf = contexto.request.get(url + "/_s3/obter?chave=certificados/2026/00001.pdf").body()
    assert pdf[:5] == b"%PDF-"
    arquivo = tmp_path / "certificado.pdf"
    arquivo.write_bytes(pdf)
    pdf_pagina.close()

    pagina.goto(url + "/#/auditoria")
    pagina.wait_for_selector("text=CERTIFICADO_EMITIDO")
    foto(pagina, "10_auditoria")
    sair(pagina)

    celular = navegador.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    tela = celular.new_page()
    tela.on("pageerror", lambda e: erros.append(str(e)))
    tela.goto(link.replace("https://d111111abcdef8.cloudfront.net", url))
    tela.wait_for_selector(".selo.valido.reprovado")
    tela.set_input_files("#pdf", str(arquivo))
    tela.wait_for_selector("text=Arquivo íntegro")
    foto(tela, "11_validacao_celular")
    adulterado = tmp_path / "adulterado.pdf"
    adulterado.write_bytes(pdf + b"\n%")
    tela.set_input_files("#pdf", str(adulterado))
    tela.wait_for_selector("text=Arquivo diferente do original")
    tela.goto(url + "/validar.html?c=codigoQueNaoExiste")
    tela.wait_for_selector(".selo.inexistente")
    celular.close()

    entrar(pagina, url, "auditor@local.test")
    pagina.wait_for_selector("#menu a[href='#/calibracoes']")
    pagina.click("#menu a[href='#/calibracoes']")
    pagina.wait_for_selector(".lista tbody tr")
    assert "00001/2026" in pagina.inner_text(".lista")
    assert pagina.locator("#menu a[href='#/nova']").count() == 0
    foto(pagina, "12_consulta_auditor")
    contexto.close()
    assert erros == []
