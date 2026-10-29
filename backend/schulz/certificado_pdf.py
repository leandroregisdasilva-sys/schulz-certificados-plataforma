from __future__ import annotations

import io
from typing import Any

from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .normalizacao import data_br, data_hora_br, numero_br

AZUL = colors.HexColor("#1F3A5F")
CINZA = colors.HexColor("#5B6573")
LINHA = colors.HexColor("#C9D1DC")
FUNDO_REPROVADO = colors.HexColor("#FBE3E1")
VERMELHO = colors.HexColor("#B42318")
VERDE = colors.HexColor("#1E7B45")

_base = ParagraphStyle("base", fontName="Helvetica", fontSize=9, leading=12)
_negrito = ParagraphStyle("negrito", parent=_base, fontName="Helvetica-Bold")
_secao = ParagraphStyle("secao", parent=_base, fontName="Helvetica-Bold", fontSize=10.5, leading=14,
                        textColor=AZUL, spaceBefore=8, spaceAfter=3)
_pequeno = ParagraphStyle("pequeno", parent=_base, fontSize=7.5, leading=10, textColor=CINZA)
_celula = ParagraphStyle("celula", parent=_base, fontSize=8, leading=10)
_celula_centro = ParagraphStyle("celula_centro", parent=_celula, alignment=TA_CENTER)
_assinatura = ParagraphStyle("assinatura", parent=_base, alignment=TA_CENTER)


def _p(texto: str, estilo: ParagraphStyle = _base) -> Paragraph:
    return Paragraph(texto, estilo)


def _escapar(texto: Any) -> str:
    return str(texto if texto is not None else "-").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class _CanvasNumerado(rl_canvas.Canvas):
    def __init__(self, *args: Any, cabecalho: dict[str, str], **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._paginas: list[dict[str, Any]] = []
        self._cabecalho = cabecalho

    def showPage(self) -> None:
        self._paginas.append(dict(self.__dict__))
        self._startPage()

    def save(self) -> None:
        total = len(self._paginas)
        for estado in self._paginas:
            self.__dict__.update(estado)
            self._moldura(total)
            super().showPage()
        super().save()

    def _moldura(self, total: int) -> None:
        largura, altura = A4
        self.setFillColor(AZUL)
        self.setFont("Helvetica-Bold", 15)
        self.drawCentredString(largura / 2, altura - 1.6 * cm, "Certificado de Calibração")
        self.setFont("Helvetica-Bold", 10)
        self.setFillColor(colors.black)
        self.drawString(2 * cm, altura - 2.35 * cm, f"Certificado de Calibração nº {self._cabecalho['numero']}")
        self.drawRightString(largura - 2 * cm, altura - 2.35 * cm, f"Data: {self._cabecalho['data']}")
        self.setStrokeColor(LINHA)
        self.setLineWidth(0.8)
        self.line(2 * cm, altura - 2.6 * cm, largura - 2 * cm, altura - 2.6 * cm)
        self.line(2 * cm, 1.9 * cm, largura - 2 * cm, 1.9 * cm)
        self.setFont("Helvetica", 7)
        self.setFillColor(CINZA)
        self.drawString(2 * cm, 1.45 * cm, self._cabecalho["rodape"])
        self.drawString(2 * cm, 1.1 * cm, f"Código de validação: {self._cabecalho['codigo']}  •  {self._cabecalho['url']}")
        self.setFont("Helvetica-Bold", 8)
        self.setFillColor(colors.black)
        self.drawRightString(largura - 2 * cm, 1.1 * cm, f"Folha {self._pageNumber:02d}/{total:02d}")


def _qr(conteudo: str, lado: float) -> Drawing:
    widget = QrCodeWidget(conteudo, barLevel="M")
    x0, y0, x1, y1 = widget.getBounds()
    desenho = Drawing(lado, lado, transform=[lado / (x1 - x0), 0, 0, lado / (y1 - y0), 0, 0])
    desenho.add(widget)
    return desenho


def _tabela_resultados(resultados: list[dict[str, Any]]) -> Table:
    cabecalho = ["Parâmetro avaliado", "Valor nominal", "Tol. +", "Tol. -", "Valor obtido", "Erro", "Resultado"]
    linhas: list[list[Any]] = [[_p(f"<b>{c}</b>", _celula_centro) for c in cabecalho]]
    estilo = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8EEF5")),
        ("GRID", (0, 0), (-1, -1), 0.4, LINHA),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
    ]
    for i, r in enumerate(resultados, start=1):
        reprovado = r["resultado"] == "REPROVADO"
        cor = "#B42318" if reprovado else "#1E7B45"
        linhas.append([
            _p(_escapar(r["parametro_exibicao"]), _celula),
            _p(numero_br(r["valor_nominal"], 4), _celula_centro),
            _p(numero_br(r["tolerancia_pos"], 4), _celula_centro),
            _p(numero_br(r["tolerancia_neg"], 4), _celula_centro),
            _p(numero_br(r["media"], 4), _celula_centro),
            _p(numero_br(r["erro"], 4), _celula_centro),
            _p(f'<font color="{cor}"><b>{r["resultado"]}</b></font>', _celula_centro),
        ])
        if reprovado:
            estilo.append(("BACKGROUND", (0, i), (-1, i), FUNDO_REPROVADO))
    tabela = Table(linhas, colWidths=[4.6 * cm, 2.0 * cm, 1.75 * cm, 1.75 * cm, 2.1 * cm, 1.75 * cm, 2.55 * cm],
                   repeatRows=1)
    tabela.setStyle(TableStyle(estilo))
    return tabela


def gerar(dados: dict[str, Any]) -> bytes:
    cal = dados["calibracao"]
    padrao = dados["padrao"]
    procedimento = dados["procedimento"]
    instrumento = dados["instrumento"]
    resultados = dados["resultados"]
    inc = dados["incerteza"]
    buffer = io.BytesIO()
    cabecalho = {
        "numero": dados["nr_certificado"],
        "data": data_br(dados["data_emissao"]),
        "codigo": dados["codigo_validacao"],
        "url": dados["url_validacao"],
        "rodape": "Documento gerado pelo protótipo acadêmico do Projeto Aplicado III (UniSENAI) para a Schulz S.A. "
                  "Sem valor metrológico oficial.",
    }
    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm, topMargin=3.0 * cm,
                            bottomMargin=2.3 * cm, title=f"Certificado de Calibração {dados['nr_certificado']}",
                            author="Laboratório de Metrologia", subject="Certificado de Calibração")
    reprovadas = [r["parametro_exibicao"] for r in resultados if r["resultado"] == "REPROVADO"]
    fabricante = instrumento.get("fabricante") or "Não consta"
    conteudo: list[Any] = [
        _p(f"<b>Solicitante:</b> {_escapar(dados['solicitante'])}"),
        _p("1. Descrição do instrumento calibrado", _secao),
        Table([
            [_p("<b>Denominação:</b>"), _p(_escapar(instrumento.get("denominacao")))],
            [_p("<b>Código:</b>"), _p(_escapar(instrumento.get("codigo")))],
            [_p("<b>Fabricante:</b>"), _p(_escapar(fabricante))],
        ], colWidths=[3 * cm, 13.5 * cm], style=[("LEFTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 1),
                                                   ("BOTTOMPADDING", (0, 0), (-1, -1), 1)]),
        _p("2. Padrão utilizado", _secao),
        _p(f"*{_escapar(padrao.get('descricao'))} {_escapar(padrao.get('codigo_exibicao'))} - {_escapar(padrao.get('fabricante'))}"),
        _p(f"Certificado de Calibração nº {_escapar(padrao.get('cert_calibracao'))}; de {data_br(padrao.get('data_ultima_cal'))} - "
           f"Próxima Calibração: {data_br(padrao.get('data_proxima_cal'))}"),
        _p("3. Procedimento de calibração", _secao),
        _p(f"Nr. do procedimento / revisão: {_escapar(procedimento.get('descricao'))} - {_escapar(procedimento.get('codigo'))} "
           f"(revisão {_escapar(procedimento.get('revisao'))})."),
        _p("O instrumento foi medido na máquina de medição por coordenadas através de elementos geométricos definidos, "
           "sendo o resultado apresentado a média de duas medições realizadas."),
        _p("Os resultados estão referenciados à temperatura de 20 °C."),
        _p("4. Resultados (mm)", _secao),
        _tabela_resultados(resultados),
        Spacer(1, 4),
    ]
    if reprovadas:
        conteudo.append(_p(f'<font color="#B42318"><b>Resultado geral: REPROVADO</b></font> — {len(reprovadas)} característica(s) '
                       f"fora da tolerância: {_escapar(', '.join(reprovadas))}."))
    else:
        conteudo.append(_p('<font color="#1E7B45"><b>Resultado geral: APROVADO</b></font> — todas as características dentro da tolerância.'))
    conteudo += [
        _p("5. Incerteza de medição", _secao),
        _p(f"<b>U = {numero_br(inc['incerteza_expandida_mm'], 4)} mm</b> &nbsp;&nbsp; k = {numero_br(inc['fator_k'], 2)} "
           f"&nbsp;&nbsp; veff = {numero_br(min(inc['veff'], 9999), 0)}"),
        _p("6. Observações", _secao),
        _p("*Os parâmetros avaliados foram obtidos da Ficha de Auto Controle do produto que o dispositivo controla."),
    ]
    if reprovadas:
        conteudo.append(_p("A emissão deste certificado com característica(s) reprovada(s) foi confirmada explicitamente pelo "
                       "signatário autorizado (RN07)."))
    if dados.get("observacao"):
        conteudo.append(_p(_escapar(dados["observacao"])))
    lado_qr = 3.1 * cm
    bloco_final = Table([
        [
            [
                _p(f"<b>Técnico executante:</b> {_escapar(cal.get('tecnico'))}"),
                Spacer(1, 16),
                _p("______________________________________", _assinatura),
                _p(f"<b>{_escapar(dados['signatario'])}</b>", _assinatura),
                _p("Signatário Autorizado", _assinatura),
                _p(f"Assinado eletronicamente em {data_hora_br(dados['data_emissao'])} (horário de Brasília)", _pequeno),
            ],
            [
                _qr(dados["url_validacao"], lado_qr),
                _p("Valide a autenticidade lendo o QR Code", _pequeno),
            ],
        ]
    ], colWidths=[12.6 * cm, 3.9 * cm], style=[("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                                              ("ALIGN", (1, 0), (1, 0), "CENTER")])
    notas = [
        _p("Notas:", _negrito),
        _p(f"* A incerteza expandida de medição relatada é declarada como incerteza padrão da medição multiplicada pelo fator de "
           f"abrangência k = {numero_br(inc['fator_k'], 2)}, o qual para uma distribuição t com veff = "
           f"{numero_br(min(inc['veff'], 9999), 0)} graus de liberdade efetivos corresponde a uma probabilidade de abrangência "
           f"de aproximadamente 95%. A incerteza padrão de medição foi determinada de acordo com a publicação EA-4/02.", _pequeno),
        _p("* Os resultados deste certificado referem-se exclusivamente ao instrumento submetido à calibração, nas condições "
           "especificadas, não sendo extensivos a quaisquer lotes.", _pequeno),
        _p("* Não é permitida a reprodução parcial deste certificado.", _pequeno),
        _p(f"* A integridade deste arquivo pode ser conferida pelo hash SHA-256 publicado na página de validação "
           f"(código {dados['codigo_validacao']}).", _pequeno),
    ]
    conteudo += [Spacer(1, 10), KeepTogether([bloco_final, Spacer(1, 8), *notas])]
    doc.build(conteudo, canvasmaker=lambda *a, **k: _CanvasNumerado(*a, cabecalho=cabecalho, **k))
    return buffer.getvalue()
