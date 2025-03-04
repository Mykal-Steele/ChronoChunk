#!/bin/bash

LOG_FILE="redeploy.log"
TIMESTAMP=$(date +"%Y-%m-%d %H:%M:%S")
RENDER_SERVICE_ID="$RENDER_SERVICE_ID"  # Use the env variable for service ID
RENDER_API_KEY="$RENDER_API_KEY"  # Use the env variable for API key

echo "[$TIMESTAMP] Starting redeployment..." | tee -a $LOG_FILE

# JSON Data
JSON_DATA='{"clearCache": "do_not_clear"}'  # Using string "do_not_clear"

# Debugging: Check the JSON being sent
echo "Sending request with data: $JSON_DATA"

# Send the request to Render API
RESPONSE=$(curl -s -w "\nHTTP_STATUS:%{http_code}" -X POST "https://api.render.com/v1/services/$RENDER_SERVICE_ID/deploys" \
  -H "Authorization: Bearer $RENDER_API_KEY" \
  -H "Content-Type: application/json" \
  --data "$JSON_DATA")

# Extract HTTP status code and the actual response
HTTP_STATUS=$(echo "$RESPONSE" | grep "HTTP_STATUS" | cut -d':' -f2)
DEPLOY_RESPONSE=$(echo "$RESPONSE" | sed -E 's/HTTP_STATUS:[0-9]+//')

# Print the HTTP status and raw response for debugging
echo "[$TIMESTAMP] HTTP Status: $HTTP_STATUS" | tee -a $LOG_FILE
echo "[$TIMESTAMP] Response: $DEPLOY_RESPONSE" | tee -a $LOG_FILE

# Handle success or failure based on HTTP status code
if [ "$HTTP_STATUS" -eq 201 ]; then
  echo "[$TIMESTAMP] ✅ Redeployment successful!" | tee -a $LOG_FILE
else
  echo "[$TIMESTAMP] ❌ Redeployment failed! Status: $HTTP_STATUS" | tee -a $LOG_FILE
  echo "[$TIMESTAMP] Response: $DEPLOY_RESPONSE" | tee -a $LOG_FILE
fi

echo "[$TIMESTAMP] --- Done ---" | tee -a $LOG_FILE
