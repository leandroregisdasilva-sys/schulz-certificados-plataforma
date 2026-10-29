param(
    [Parameter(Mandatory = $true)][string]$Email,
    [Parameter(Mandatory = $true)][string]$Nome,
    [Parameter(Mandatory = $true)][string]$Grupos,
    [string]$Regiao = "sa-east-1"
)

$ErrorActionPreference = "Stop"
$ListaGrupos = @($Grupos -split "[,; ]+" | Where-Object { $_ })
foreach ($g in $ListaGrupos) {
    if (@("tecnico", "signatario", "auditor", "admin") -notcontains $g) { Write-Host "Grupo invalido: $g (use tecnico, signatario, auditor ou admin)" -ForegroundColor Red; exit 1 }
}
$Pilha = "schulz"

$pool = aws cloudformation describe-stacks --stack-name $Pilha --region $Regiao --query "Stacks[0].Outputs[?OutputKey=='UserPoolId'].OutputValue" --output text
if ($LASTEXITCODE -ne 0 -or -not $pool) { Write-Host "Pilha '$Pilha' nao encontrada. Rode o deploy antes." -ForegroundColor Red; exit 1 }

$alfabeto = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789".ToCharArray()
$miolo = -join (1..8 | ForEach-Object { $alfabeto | Get-Random })
$senha = "Tmp-$miolo" + "a7Z!"

aws cognito-idp admin-create-user --region $Regiao --user-pool-id $pool --username $Email `
    --user-attributes "Name=email,Value=$Email" "Name=email_verified,Value=true" "Name=name,Value=$Nome" `
    --temporary-password $senha --message-action SUPPRESS --query "User.Username" --output text
if ($LASTEXITCODE -ne 0) { Write-Host "Falha ao criar o usuario (ja existe?)." -ForegroundColor Red; exit 1 }

foreach ($grupo in $ListaGrupos) {
    aws cognito-idp admin-add-user-to-group --region $Regiao --user-pool-id $pool --username $Email --group-name $grupo
    if ($LASTEXITCODE -ne 0) { Write-Host "Falha ao incluir no grupo $grupo." -ForegroundColor Red; exit 1 }
}

Write-Host "`nUsuario criado:" -ForegroundColor Green
Write-Host "  E-mail ........: $Email"
Write-Host "  Grupos ........: $($ListaGrupos -join ', ')"
Write-Host "  Senha temporaria: $senha" -ForegroundColor Yellow
Write-Host "No primeiro login o sistema pede uma senha nova e a configuracao do aplicativo autenticador."
