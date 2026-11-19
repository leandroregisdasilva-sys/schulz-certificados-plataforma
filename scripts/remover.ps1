param([string]$Regiao = "sa-east-1")

$ErrorActionPreference = "Continue"
$Pilha = "schulz"

Write-Host "Isto apaga a plataforma da AWS (site, funcoes, banco e usuarios)." -ForegroundColor Yellow
$resposta = Read-Host "Digite REMOVER para confirmar"
if ($resposta -ne "REMOVER") { Write-Host "Cancelado."; exit 0 }

$conta = aws sts get-caller-identity --query Account --output text
foreach ($b in @("$Pilha-site-$conta", "$Pilha-uploads-$conta")) {
    Write-Host "Esvaziando s3://$b"
    aws s3 rm "s3://$b" --recursive --region $Regiao
}
sam delete --stack-name $Pilha --region $Regiao --no-prompts
Write-Host "`nO bucket $Pilha-certificados-$conta foi mantido de proposito (certificados imutaveis, Object Lock)." -ForegroundColor Yellow
Write-Host "Para apaga-lo depois de 1 dia: remova a politica do bucket no console do S3, esvazie (inclusive versoes) e exclua."
