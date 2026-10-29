param(
    [string]$Email = "",
    [ValidateSet("cloudfront", "direto")][string]$Modo = "cloudfront",
    [ValidateSet("ON", "OFF")][string]$Mfa = "ON",
    [string]$Regiao = "sa-east-1"
)

$ErrorActionPreference = "Stop"
$Pilha = "schulz"
$Raiz = Split-Path -Parent $PSScriptRoot
Set-Location $Raiz

function Etapa($texto) { Write-Host "`n==> $texto" -ForegroundColor Cyan }
function Falhar($texto) { Write-Host "`nERRO: $texto" -ForegroundColor Red; exit 1 }

Etapa "Conferindo ferramentas"
foreach ($cmd in @("aws", "sam")) {
    if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) { Falhar "Comando '$cmd' nao encontrado. Instale conforme o guia e abra um novo PowerShell." }
}
if (-not (Test-Path "camada\python\reportlab")) { Falhar "Pasta camada\python incompleta. Use o projeto completo do zip." }

Etapa "Conferindo credenciais da AWS"
$conta = aws sts get-caller-identity --query Account --output text
if ($LASTEXITCODE -ne 0 -or -not $conta) { Falhar "Credenciais invalidas. Rode 'aws configure' (guia, passo 6)." }
Write-Host "Conta AWS: $conta  |  Regiao: $Regiao  |  Modo: $Modo  |  MFA: $Mfa"

$ErrorActionPreference = "Continue"
$estado = aws cloudformation describe-stacks --stack-name $Pilha --region $Regiao --query "Stacks[0].StackStatus" --output text 2>$null
$ErrorActionPreference = "Stop"
if ($estado -eq "ROLLBACK_COMPLETE" -or $estado -eq "ROLLBACK_FAILED") {
    Etapa "A tentativa anterior falhou ($estado). Removendo a pilha antiga antes de tentar de novo"
    aws cloudformation delete-stack --stack-name $Pilha --region $Regiao
    aws cloudformation wait stack-delete-complete --stack-name $Pilha --region $Regiao
}

Etapa "Implantando a infraestrutura com AWS SAM (primeira vez: 5 a 15 minutos)"
$parametros = "ModoPublicacao=$Modo MfaObrigatorio=$Mfa"
if ($Email) { $parametros = "$parametros EmailAlertas=$Email" }
sam deploy --region $Regiao --parameter-overrides $parametros
if ($LASTEXITCODE -ne 0) { Falhar "O 'sam deploy' falhou. Copie as linhas vermelhas acima (CREATE_FAILED) e envie ao grupo." }

Etapa "Lendo as saidas da pilha"
$saidas = (aws cloudformation describe-stacks --stack-name $Pilha --region $Regiao --query "Stacks[0].Outputs" --output json | Out-String) | ConvertFrom-Json
function Saida($nome) { ($saidas | Where-Object { $_.OutputKey -eq $nome }).OutputValue }

$api = [ordered]@{ padrao = ""; uploads = ""; emissao = ""; cadastros = "" }
if ($Modo -eq "direto") {
    $api.padrao = (Saida "UrlFuncaoConsulta").TrimEnd("/")
    $api.uploads = (Saida "UrlFuncaoUploads").TrimEnd("/")
    $api.emissao = (Saida "UrlFuncaoEmissao").TrimEnd("/")
    $api.cadastros = (Saida "UrlFuncaoCadastros").TrimEnd("/")
}
$config = [ordered]@{
    regiao     = $Regiao
    userPoolId = (Saida "UserPoolId")
    clientId   = (Saida "ClientId")
    modo       = $Modo
    api        = $api
}
$js = "window.SCHULZ_CONFIG = " + ($config | ConvertTo-Json -Depth 5) + ";"
[System.IO.File]::WriteAllText((Join-Path $Raiz "frontend\config.js"), $js, (New-Object System.Text.UTF8Encoding $false))

Etapa "Publicando o site no bucket S3"
$bucket = Saida "BucketSite"
aws s3 sync frontend "s3://$bucket" --delete --region $Regiao --exclude "*.html" --exclude "*.js" --exclude "*.css"
if ($LASTEXITCODE -ne 0) { Falhar "Falha ao enviar o site para o S3." }
$tipos = @{ "*.html" = "text/html; charset=utf-8"; "*.js" = "application/javascript; charset=utf-8"; "*.css" = "text/css; charset=utf-8" }
foreach ($padrao in $tipos.Keys) {
    aws s3 cp frontend "s3://$bucket" --recursive --region $Regiao --exclude "*" --include $padrao --content-type $tipos[$padrao] --cache-control "no-cache"
    if ($LASTEXITCODE -ne 0) { Falhar "Falha ao enviar arquivos $padrao." }
}

if ($Modo -eq "cloudfront") {
    Etapa "Limpando o cache do CloudFront"
    aws cloudfront create-invalidation --distribution-id (Saida "DistribuicaoId") --paths "/*" --query "Invalidation.Id" --output text
}

$url = Saida "UrlSite"
Write-Host "`n============================================================" -ForegroundColor Green
Write-Host " PLATAFORMA NO AR: $url" -ForegroundColor Green
Write-Host " Validacao publica: $url/validar.html" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
Write-Host "Proximo passo: criar os usuarios (guia, passo 9)."
