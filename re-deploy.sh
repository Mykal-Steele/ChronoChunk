#!/bin/bash

LOG_FILE="redeploy.log"
TIMESTAMP=$(date +"%Y-%m-%d %H:%M:%S")
RENDER_SERVICE_ID="$RENDER_SERVICE_ID"
RENDER_API_KEY="$RENDER_API_KEY"

echo "[$TIMESTAMP] Starting redeployment..." | tee -a $LOG_FILE

JSON_DATA='{"clearCache": "do_not_clear"}'

RESPONSE=$(curl -s -w "\nHTTP_STATUS:%{http_code}" -X POST "https://api.render.com/v1/services/$RENDER_SERVICE_ID/deploys" \
  -H "Authorization: Bearer $RENDER_API_KEY" \
  -H "Content-Type: application/json" \
  --data "$JSON_DATA")

HTTP_STATUS=$(echo "$RESPONSE" | grep "HTTP_STATUS" | cut -d':' -f2)
DEPLOY_RESPONSE=$(echo "$RESPONSE" | sed -E 's/HTTP_STATUS:[0-9]+//')

if [ "$HTTP_STATUS" -eq 201 ]; then
  echo "[$TIMESTAMP] ✅ Redeployment successful!" | tee -a $LOG_FILE
else
  echo "[$TIMESTAMP] ❌ Redeployment failed! Status: $HTTP_STATUS" | tee -a $LOG_FILE
  echo "[$TIMESTAMP] Response: $DEPLOY_RESPONSE" | tee -a $LOG_FILE
fi

echo "[$TIMESTAMP] --- Done ---" | tee -a $LOG_FILE
