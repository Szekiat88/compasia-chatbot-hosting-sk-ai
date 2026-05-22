# CompAsia AI Customer Support Chatbot

An AI-powered customer support chatbot for CompAsia Malaysia, integrated with WhatsApp Business API. The bot handles customer enquiries, order tracking, warranty claims, store locations, and product recommendations — automatically, in both English and Bahasa Malaysia.

---

## What It Does

Customers send a WhatsApp message to CompAsia. The bot reads the message, understands what the customer needs, and replies instantly — without any human agent involved.

**Supported topics include:**
- Order status and shipment tracking
- Warranty claim process
- ReNewNGo (trade-in) programme
- Device repair enquiries
- Payment and installment queries
- Product recommendations (refurbished phones)
- Store locations
- General FAQ

If the bot cannot handle the request, it automatically creates a support ticket in Zoho Desk and notifies the customer.

---

## How It Works (Non-Technical Overview)

```
Customer sends WhatsApp message
        ↓
Bot detects the language (English or Malay)
        ↓
Bot understands the intent (what the customer is asking)
        ↓
Bot looks up the answer from the knowledge base
        ↓
Bot personalises and translates the reply if needed
        ↓
Customer receives a reply on WhatsApp (within seconds)
```

Each step is powered by a Large Language Model (LLM) — specifically Google Gemini — which understands natural language the same way a human agent would.

---

## Key Features

| Feature | Description |
|---|---|
| **Bilingual support** | Understands and replies in English and Bahasa Malaysia automatically |
| **Intent classification** | Identifies what the customer is asking, even if phrased informally |
| **Knowledge base lookup** | Matches questions to the correct answer from a curated FAQ database |
| **Order tracking** | Retrieves live order status from Shopify |
| **Ticket creation** | Raises a Zoho Desk ticket for issues requiring human follow-up |
| **Product recommendations** | Suggests relevant refurbished devices based on the customer's query |
| **Empathy detection** | Detects frustrated or worried customers and adjusts the tone of reply |
| **Conversation memory** | Remembers context within the same conversation for follow-up questions |
| **Store locator** | Finds the nearest CompAsia outlet based on the customer's location |

---

## Technology Stack

| Component | Technology | Purpose |
|---|---|---|
| **LLM (Primary)** | Google Gemini 2.5 Flash / Pro | Intent classification, answer generation, translation |
| **LLM (Translation)** | OpenAI GPT-4o Mini | English ↔ Bahasa Malaysia translation |
| **Messaging channel** | WhatsApp Business API (Meta) | Customer communication |
| **Product search** | FAISS (vector similarity search) + Sentence Transformers | Semantic product recommendations |
| **Knowledge base** | Microsoft Excel (Samples.xlsx) | FAQ answers maintained by the team |
| **E-commerce** | Shopify Admin GraphQL API | Live order and stock data |
| **Ticketing** | Zoho Desk REST API | Escalation and support ticket creation |
| **Backend API** | Python + Flask | Core chatbot service |
| **UI (internal)** | Streamlit | Internal testing interface |
| **Database** | PostgreSQL (AWS RDS) | Conversation history and customer records |
| **NLU engine** | `nlu_core` (compiled Python extension) | Proprietary intent matching and KB routing |
| **Hosting** | Railway (cloud) + local Flask server | Engine matching service + webhook server |

---

## System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        Customer (WhatsApp)                       │
└────────────────────────────┬────────────────────────────────────┘
                             │ WhatsApp Cloud API webhook
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                    whatsapp_webhook.py                           │
│  • Receives and deduplicates incoming messages                   │
│  • Splits long replies into 3,900-character chunks               │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                 gemini_shipping_services.py  (search)            │
│                                                                  │
│  1. Language detection   → detect English or Malay               │
│  2. Order ID extraction  → query Shopify for live status         │
│  3. Escalation check     → is this an urgent complaint?          │
│  4. Intent routing:                                              │
│       PRODUCT_ENQUIRE    → FAISS semantic product search         │
│       TICKET_LOGGED      → create Zoho Desk ticket               │
│       FAQ keyword        → look up answer in Excel KB            │
│       NO_MATCH           → sales redirect                        │
│  5. Emotion detection    → add empathy prefix if needed          │
│  6. Translation          → translate reply back to Malay         │
│  7. Summarization        → update conversation memory in DB      │
└────────────────────────────┬────────────────────────────────────┘
                             │
           ┌─────────────────┼──────────────────┐
           ▼                 ▼                  ▼
    ┌─────────────┐  ┌──────────────┐  ┌───────────────┐
    │  nlu_core   │  │   Shopify    │  │  Zoho Desk    │
    │  (NLU +     │  │  GraphQL API │  │  REST API     │
    │  KB match)  │  └──────────────┘  └───────────────┘
    └─────────────┘
           │
           ▼
    ┌─────────────┐
    │  Samples    │
    │  .xlsx      │
    │ (Knowledge  │
    │   Base)     │
    └─────────────┘
```

---

## Knowledge Base

The FAQ knowledge base is maintained in `data/Samples.xlsx` (sheet: **Main DB**).

Each row has two columns:
- **keyword** — the topic name (e.g. "Warranty claim process")
- **answer** — the reply text sent to the customer

To add or update a bot response, **edit this Excel file directly** — no code changes required.

The NLU engine matches customer questions to the correct keyword using AI-based semantic similarity, so the phrasing of the answer does not need to match the customer's exact words.

---

## NLU Engine (`nlu_core`)

`nlu_core` is the proprietary Natural Language Understanding module that handles:

- **Intent classification** — maps a customer message to the correct knowledge base keyword using a Gemini LLM call with a curated prompt
- **Escalation detection** — identifies messages that require urgent human attention
- **Conversation summarization** — compresses the conversation history into a short context summary
- **History recall** — checks if a previous reply in the same conversation already answers the current question

This module is distributed as a **compiled Python extension** (`.so` binary) rather than readable source code. This is standard practice for protecting proprietary AI prompt engineering — the same approach used by commercial NLP libraries.

---

## Product Recommendation Engine

When a customer asks about buying a phone, the bot uses **semantic search** (FAISS vector index) to find the most relevant products from the CompAsia Shopify catalogue.

Process:
1. Shopify product data is synced to PostgreSQL (`shopify_stock_sync.py`)
2. Products are embedded using a Sentence Transformer model (`build_vectors.py`)
3. The FAISS index is deployed to Railway (`upload_vectors.py`)
4. At query time, the customer's message is embedded and matched against the index
5. Gemini generates a human-friendly recommendation reply with product cards

---

## Conversation Flow Example

**Customer:** "Saya nak tahu pasal trade-in iPhone saya"
*(Translation: "I want to know about trading in my iPhone")*

**Bot internally:**
1. Detects language → Malay
2. Translates to English → "I want to know about trading in my iPhone"
3. Matches intent → "ReNewNGo Program"
4. Retrieves answer from Excel KB
5. Detects emotion → neutral
6. Translates reply back to Malay
7. Sends reply via WhatsApp

**Customer receives:** A full Malay-language explanation of the ReNewNGo trade-in programme.

---

## Running the Bot

**Prerequisites:** Python 3.11+, `.env` file with API keys

```bash
# Install dependencies
pip install -r requirements.txt

# Start the chatbot API
python gemini_shipping_flask_api.py

# Start the WhatsApp webhook (separate terminal)
FLASK_APP=whatsapp_webhook.py flask run

# Internal testing UI
streamlit run streamlit_chat.py
```

---

## Maintenance

| Task | How |
|---|---|
| Add/edit a bot answer | Edit `data/Samples.xlsx` → Main DB sheet |
| Sync new Shopify products | Run `python sync_products.py` |
| View conversation history | PostgreSQL `chat_message_log` table |
| Run automated KB tests | `python run_kb_tests.py` |
| Add a new FAQ topic | Add a row to `data/Samples.xlsx` and add test questions to `data/Test_Cases_MainDB_new.xlsx` |
