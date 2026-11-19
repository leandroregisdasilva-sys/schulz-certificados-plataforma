#!/usr/bin/env bash
set -euo pipefail
[ $# -ge 3 ] || { echo "Uso: $0 email \"Nome Completo\" grupo1,grupo2"; exit 1; }
EMAIL="$1"; NOME="$2"; GRUPOS="$3"
REGIAO="${REGIAO:-sa-east-1}"
for g in ${GRUPOS//,/ }; do
  case "$g" in tecnico|signatario|auditor|admin) ;; *) echo "Grupo inválido: $g"; exit 1;; esac
done
POOL=$(aws cloudformation describe-stacks --stack-name schulz --region "$REGIAO" --query "Stacks[0].Outputs[?OutputKey=='UserPoolId'].OutputValue" --output text)
SENHA="Tmp-$(LC_ALL=C tr -dc 'A-HJ-NP-Za-km-z2-9' </dev/urandom | head -c 8)a7Z!"
aws cognito-idp admin-create-user --region "$REGIAO" --user-pool-id "$POOL" --username "$EMAIL" \
  --user-attributes "Name=email,Value=$EMAIL" "Name=email_verified,Value=true" "Name=name,Value=$NOME" \
  --temporary-password "$SENHA" --message-action SUPPRESS --query "User.Username" --output text
for g in ${GRUPOS//,/ }; do
  aws cognito-idp admin-add-user-to-group --region "$REGIAO" --user-pool-id "$POOL" --username "$EMAIL" --group-name "$g"
done
printf '\nUsuário criado: %s (%s)\nSenha temporária: %s\n' "$EMAIL" "$GRUPOS" "$SENHA"
