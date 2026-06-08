#!/bin/bash
# Push code to the Ubuntu server. Run from your Mac.
rsync -avz --progress \
  --exclude '.git' \
  --exclude '.claude' \
  --exclude '*.md' \
  --exclude '.env*' \
  --exclude 'deploy/server.env' \
  --exclude 'venv' \
  --exclude '.venv' \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  --exclude '.DS_Store' \
  -e "ssh -i /Users/newuser/Downloads/ec2-ai-grading-uat.pem" \
  /Users/newuser/Documents/GitHub/compasia-chatbot-hosting-sk-ai/ \
  ubuntu@43.217.101.210:/home/ubuntu/compasia-chatbot-hosting-sk-ai/
