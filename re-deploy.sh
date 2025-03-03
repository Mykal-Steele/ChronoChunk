#!/bin/bash

curl -X POST "https://api.render.com/v1/services/srv-cv0c6shopnds73b7idg0/deploys" \
  -H "Authorization: Bearer $RENDER_API_KEY" \
  -H "Content-Type: application/json" \
  --data '{"clearCache": false, "commitId": "56c9cd3ae2c029a54f65ff04beeb1c50460257b4"}'
