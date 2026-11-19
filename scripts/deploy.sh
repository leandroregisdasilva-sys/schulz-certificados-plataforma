#!/usr/bin/env bash
set -euo pipefail
EMAIL="${1:-}"
MODO="${MODO:-cloudfront}"
MFA="${MFA:-ON}"
PILHA="schulz"
REGIAO="${REGIAO:-sa-east-1}"
cd "$(dirname "$0")/.."

etapa() { printf '\n==> %s\n' "$1"; }
falhar() { printf '\nERRO: %s\n' "$1" >&2; exit 1; }

etapa "Conferindo ferramentas"
command -v aws >/dev/null || falhar "AWS CLI não encontrada."
command -v sam >/dev/null || falhar "AWS SAM CLI não encontrada."
[ -d camada/python/reportlab ] || falhar "Pasta camada/python incompleta. Use o projeto completo."

etapa "Conferindo credenciais"
CONTA=$(aws sts get-caller-identity --query Account --output text) || falhar "Credenciais inválidas. Rode 'aws configure'."
echo "Conta: $CONTA | Região: $REGIAO | Modo: $MODO | MFA: $MFA"

ESTADO=$(aws cloudformation describe-stacks --stack-name "$PILHA" --region "$REGIAO" --query "Stacks[0].StackStatus" --output text 2>/dev/null || true)
if [ "$ESTADO" = "ROLLBACK_COMPLETE" ] || [ "$ESTADO" = "ROLLBACK_FAILED" ]; then
  etapa "Tentativa anterior falhou ($ESTADO). Removendo a pilha antiga"
  aws cloudformation delete-stack --stack-name "$PILHA" --region "$REGIAO"
  aws cloudformation wait stack-delete-complete --stack-name "$PILHA" --region "$REGIAO"
fi

etapa "Implantando com AWS SAM"
PARAMS="ModoPublicacao=$MODO MfaObrigatorio=$MFA"
[ -n "$EMAIL" ] && PARAMS="$PARAMS EmailAlertas=$EMAIL"
sam deploy --region "$REGIAO" --parameter-overrides "$PARAMS" || falhar "sam deploy falhou. Copie as linhas CREATE_FAILED."

saida() { aws cloudformation describe-stacks --stack-name "$PILHA" --region "$REGIAO" --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text; }
sem_barra() { local v; v=$(saida "$1"); echo "${v%/}"; }

etapa "Gerando frontend/config.js"
API_PADRAO=""; API_UPLOADS=""; API_EMISSAO=""; API_CADASTROS=""
if [ "$MODO" = "direto" ]; then
  API_PADRAO=$(sem_barra UrlFuncaoConsulta); API_UPLOADS=$(sem_barra UrlFuncaoUploads)
  API_EMISSAO=$(sem_barra UrlFuncaoEmissao); API_CADASTROS=$(sem_barra UrlFuncaoCadastros)
fi
cat > frontend/config.js <<JS
window.SCHULZ_CONFIG = {
  "regiao": "$REGIAO",
  "userPoolId": "$(saida UserPoolId)",
  "clientId": "$(saida ClientId)",
  "modo": "$MODO",
  "api": { "padrao": "$API_PADRAO", "uploads": "$API_UPLOADS", "emissao": "$API_EMISSAO", "cadastros": "$API_CADASTROS" }
};
JS

etapa "Publicando o site"
BUCKET=$(saida BucketSite)
aws s3 sync frontend "s3://$BUCKET" --delete --region "$REGIAO" --exclude "*.html" --exclude "*.js" --exclude "*.css"
aws s3 cp frontend "s3://$BUCKET" --recursive --region "$REGIAO" --exclude "*" --include "*.html" --content-type "text/html; charset=utf-8" --cache-control no-cache
aws s3 cp frontend "s3://$BUCKET" --recursive --region "$REGIAO" --exclude "*" --include "*.js" --content-type "application/javascript; charset=utf-8" --cache-control no-cache
aws s3 cp frontend "s3://$BUCKET" --recursive --region "$REGIAO" --exclude "*" --include "*.css" --content-type "text/css; charset=utf-8" --cache-control no-cache

if [ "$MODO" = "cloudfront" ]; then
  etapa "Limpando o cache do CloudFront"
  aws cloudfront create-invalidation --distribution-id "$(saida DistribuicaoId)" --paths "/*" --query "Invalidation.Id" --output text
fi

URL=$(saida UrlSite)
printf '\n============================================================\n PLATAFORMA NO AR: %s\n Validação pública: %s/validar.html\n============================================================\n' "$URL" "$URL"
