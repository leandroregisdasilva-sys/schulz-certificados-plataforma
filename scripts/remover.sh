#!/usr/bin/env bash
set -uo pipefail
REGIAO="${REGIAO:-sa-east-1}"
read -r -p "Isto apaga a plataforma da AWS. Digite REMOVER para confirmar: " R
[ "$R" = "REMOVER" ] || { echo "Cancelado."; exit 0; }
CONTA=$(aws sts get-caller-identity --query Account --output text)
for b in "schulz-site-$CONTA" "schulz-uploads-$CONTA"; do aws s3 rm "s3://$b" --recursive --region "$REGIAO"; done
sam delete --stack-name schulz --region "$REGIAO" --no-prompts
echo "O bucket schulz-certificados-$CONTA foi mantido (Object Lock). Apague-o pelo console após 1 dia."
