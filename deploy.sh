#!/bin/bash
rsync -avz \
  --exclude='venv' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='.env' \
  --exclude='db.env' \
  --exclude='*.db' \
  -e "ssh -i /Users/newuser/Downloads/ec2-ai-grading-uat.pem" \
  /Users/newuser/Documents/GitHub/compasia-chatbot-hosting-sk-ai/ \
  ubuntu@43.217.101.210:/home/ubuntu/compasia-chatbot-hosting-sk-ai/
