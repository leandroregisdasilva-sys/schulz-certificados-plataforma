# Schulz · Plataforma Web de Certificados de Calibração (AV04)

Projeto Aplicado III — UniSENAI · Empresa parceira **Schulz S.A.** · Prof. MSc. Hugo Menezes Barra
Grupo 45: Daniel Paulo Linhares, Gabriel da Silva Marques, Guilherme Bianchini Giovanela, Leandro Régis da Silva

Plataforma web gestora com **validação por QR Code** para o Laboratório de Metrologia da Schulz, publicada na
**AWS em arquitetura serverless dentro da camada gratuita**. Ela consolida as entregas anteriores:

| Entrega | O que foi reaproveitado aqui |
|---|---|
| AV01 – Diagnóstico | Requisitos RF01–RF12, RNF01–RNF05, regras RN01–RN08, regras ETL-01 a ETL-09 e o dicionário de dados (INSTRUMENTO, PADRAO, CALIBRACAO, RESULTADO_MEDICAO) |
| AV02 – Arquitetura | Desenho AWS sa-east-1: CloudFront + OAC, S3 (site, uploads, certificados), Lambda Python arm64, DynamoDB tabela única, Cognito com grupos e MFA, SQS DLQ, SNS, CloudWatch, Budgets |
| AV03 – MVP | Motor de cálculo (`calculo.py`) e ingestão de planilhas (`ingestao.py`), sem alteração das regras |

## Arquitetura implantada

```
Navegador ──HTTPS──► CloudFront ──OAC──► S3 (site estático: index.html, validar.html)
    │                    └──/api/*──OAC──► Lambda Function URLs
    │                                        ├─ gerar-url-upload        (POST /api/uploads)
    │                                        ├─ emitir-certificado      (POST /api/emissao/{id}[/devolver|/anular])
    │                                        ├─ cadastros               (/api/cadastros/...)
    │                                        └─ consultar-obter-certificado (GET /api/...; /api/validacao/{código} é público)
    ├──login (USER_PASSWORD_AUTH + TOTP)──► Amazon Cognito (grupos tecnico, signatario, auditor, admin)
    └──URL pré-assinada (5 min, ≤ 5 MB)──► S3 uploads ──evento ObjectCreated──► Lambda processar-planilha
                                                                         ├─► DynamoDB schulz-calibracao
                                                                         ├─► SNS (alerta de reprovação)
                                                                         └─(falha após 2 tentativas)─► SQS DLQ ─► alarme ─► SNS
emitir-certificado ─► PDF (reportlab) com QR Code ─► S3 certificados (versionado + Object Lock) + SHA-256 no DynamoDB
```

Fluxo do caso de uso UC01 (AV01): o técnico envia a planilha do Calypso → a nuvem lê, saneia (ETL), calcula média,
desvio padrão, erro, incerteza (EA-4/02) e o resultado por característica → a calibração fica **PENDENTE_ASSINATURA** →
o signatário revisa, confirma reprovações (RN07) e emite → o sistema reserva o número **NNNNN/AAAA** (RN01), gera o
PDF com QR Code, grava no S3 e registra o hash → qualquer pessoa no chão de fábrica lê o QR Code e confere a
autenticidade em `validar.html`, sem login.

## Estrutura

```
template.yaml            Infraestrutura como código (AWS SAM / CloudFormation)
samconfig.toml           Pilha "schulz", região sa-east-1, etiqueta Projeto=Schulz-PoC
backend/
  funcoes/               Uma Lambda por responsabilidade (nomes da AV02)
  schulz/                Código compartilhado: cálculo, ingestão, incerteza, regras, PDF, acesso a dados, JWT
camada/                  Camada Lambda com openpyxl, reportlab e Pillow já compilados para Linux arm64
frontend/                Site estático: painel do laboratório (index.html) e validação por QR Code (validar.html)
scripts/
  deploy.ps1 | deploy.sh           Publica tudo na AWS (sam deploy + site no S3 + cache do CloudFront)
  criar-usuario.ps1 | .sh          Cria usuário no Cognito com senha temporária e grupos
  remover.ps1 | .sh                Remove a pilha
  servidor_local.py                Roda a plataforma no computador com a AWS simulada (moto)
  montar_camada.py                 Recria a pasta camada/
  gerar_exemplos.py                Gera as planilhas fictícias de exemplos/
exemplos/                Planilhas fictícias para demonstração
tests/                   Testes automatizados (pytest + moto + Playwright)
```

## Publicar na AWS (resumo)

Pré-requisitos: conta AWS com alerta de orçamento, usuário IAM com chave de acesso, **AWS CLI v2** e **AWS SAM CLI**.
O passo a passo completo está no guia em PDF que acompanha o projeto.

Windows (PowerShell):

```powershell
aws configure
powershell -ExecutionPolicy Bypass -File scripts\deploy.ps1 -Email seu-email@exemplo.com
powershell -ExecutionPolicy Bypass -File scripts\criar-usuario.ps1 -Email admin@exemplo.com -Nome "Nome Completo" -Grupos admin
```

macOS / Linux:

```bash
aws configure
bash scripts/deploy.sh seu-email@exemplo.com
bash scripts/criar-usuario.sh admin@exemplo.com "Nome Completo" admin
```

O deploy não precisa de Python nem de Docker: as dependências já estão na pasta `camada/`.
Se a conta for nova e o CloudFront ainda não estiver liberado, use `-Modo direto` (contingência: site no S3 Website e
Function URLs públicas, mantendo a validação do token do Cognito em cada função).

## Rodar no computador (sem AWS)

Requer Python 3.11 ou superior.

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows   (macOS/Linux: source .venv/bin/activate)
pip install -r requirements-dev.txt
python scripts/servidor_local.py --demo
```

Abra http://localhost:8080 e entre com `tecnico@local.test`, `signatario@local.test`, `admin@local.test` ou
`auditor@local.test` (senha `Local@12345`). DynamoDB, S3 e Cognito são simulados em memória.

## Testes

```bash
pip install -r requirements-dev.txt
python -m playwright install chromium
pytest
```

Sem o Chromium do Playwright, o teste de navegador é pulado e os demais rodam normalmente.

São 122 testes: cálculo e ingestão herdados da AV03, incerteza (reproduz a aba Incerteza da planilha da Schulz),
segurança do token (assinatura RS256, expiração, adulteração, algoritmo `none`), todos os endpoints com DynamoDB e S3
simulados e um teste de ponta a ponta no navegador (login com troca de senha e MFA, upload, bloqueio por padrão vencido,
emissão com confirmação de reprovação, PDF, validação no celular e conferência do hash).

## Perfis (Cognito)

| Ação | tecnico | signatario | auditor | admin |
|---|:-:|:-:|:-:|:-:|
| Enviar planilha | ✓ | | | ✓ |
| Ver prévia (pendente) | só as próprias | ✓ | | ✓ |
| Emitir, devolver, anular (RN06) | | ✓ | | |
| Consultar e baixar emitidos | ✓ | ✓ | ✓ | ✓ |
| Trilha de auditoria | | ✓ | ✓ | ✓ |
| Cadastrar instrumentos e procedimentos | | | | ✓ |
| Atualizar padrões (MMC) | | ✓ | | ✓ |

## Modelo de dados (DynamoDB, tabela única)

| Item | PK | SK |
|---|---|---|
| INSTRUMENTO | `INST#codigo` | `META` (GSI2: `SETOR#setor` / `INST#codigo`) |
| PADRAO | `PAD#codigo` | `META` |
| PROCEDIMENTO | `PROC#codigo` | `REV#n` |
| CALIBRACAO | `CAL#id` | `META` (GSI1: `INST#codigo` / `DATA#…`; GSI2: `SETOR#setor` / `DATA#…`) |
| RESULTADO_MEDICAO | `CAL#id` | `RES#001…` |
| Importação (staging, TTL 30 dias) | `IMP#id` | `META` |
| Número emitido | `CERT#aaaa-nnnnn` | `META` |
| Contador anual | `SEQ#aaaa` | `CERT` |
| Fila do signatário | `PENDENTES` | `DATA#…#id` |
| Validação pública (QR Code) | `VAL#código` | `META` |
| Auditoria | `AUD#aaaa-mm` | `instante#id` |

## Observações

- As planilhas de `exemplos/` são fictícias. Planilhas reais da Schulz não fazem parte do repositório.
- Os parâmetros de incerteza do padrão 500.011 vêm do modelo Linear da aba Incerteza; os valores oficiais devem ser
  cadastrados pelo laboratório.
- O certificado gerado traz no rodapé a indicação de protótipo acadêmico, sem valor metrológico oficial.
- Biblioteca de terceiros no frontend: qrcodejs (MIT), em `frontend/vendor/`.
