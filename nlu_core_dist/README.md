# nlu-core

Multilingual Natural Language Understanding (NLU) engine for customer support chatbots.

Supports English and Bahasa Malaysia (Malay).

## Features

- **Intent classification** — maps customer messages to knowledge-base topics
- **Escalation detection** — identifies customers who need a human agent
- **Conversation summarisation** — produces compact conversation summaries for context
- **History reply** — finds the most relevant previous answer in a conversation
- **Product enquiry prompts** — builds structured prompts for product recommendation flows

## Installation

```bash
pip install nlu-core
```

## Quick Start

```python
import pandas as pd
from nlu_core import engine_match, detect_escalation, summarize_conversation

# Load your knowledge base
kb = pd.read_excel("knowledge_base.xlsx", sheet_name="Main DB")

# Classify a customer message
intent, confidence, matched_row = engine_match(
    user_question="How do I track my order?",
    knowledge_df=kb,
)

# Check for escalation
should_escalate, response = detect_escalation("I want to speak to a manager!")

# Summarise a conversation
summary = summarize_conversation(
    history=["Customer: My phone is broken.", "Agent: I can help with that."],
)
```

## API Reference

| Function | Description |
|---|---|
| `engine_match(user_question, knowledge_df, ...)` | Returns `(intent, confidence, matched_row)` |
| `detect_escalation(question)` | Returns `(should_escalate: bool, response: str)` |
| `summarize_conversation(history, ...)` | Returns a summary string |
| `find_relevant_history_reply(history, question, ...)` | Returns the best matching previous reply |
| `build_product_enquiry_prompt(user_message, stock_json)` | Returns a structured LLM prompt |

## Constants

| Name | Description |
|---|---|
| `DEFAULT_GEMINI_MODEL` | Default primary language model |
| `DEFAULT_OPENAI_MODEL` | Default translation model |
| `FALLBACK_GEMINI_MODEL` | Fallback model if primary is unavailable |
| `LOG_TICKET` | Intent token for support ticket creation |
| `MATCH_GEMINI_MODEL` | Intent token for product matching |

## Requirements

- Python 3.10+
- macOS (Apple Silicon or Intel)
- `google-genai`
- `openai`
- `pandas`
