# KT Assessment System

A production-ready **Reverse Knowledge Transfer Assessment System** that ingests PDF documents and Microsoft Teams meeting transcripts, extracts structured knowledge using AI, and generates comprehensive assessments with 25 questions (MCQs, subjective, and practical assignments).

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────┐
│                   Frontend (SPA)                     │
│           HTML/CSS/JS — Dark Glassmorphism UI        │
└────────────────────────┬────────────────────────────┘
                         │ REST API
┌────────────────────────▼────────────────────────────┐
│                 FastAPI Backend                       │
│                                                       │
│  ┌──────────┐  ┌──────────┐  ┌──────────────────┐   │
│  │ Ingestion │  │Processing│  │   AI Layer       │   │
│  │ ■ PDF     │→ │ ■ Clean  │→ │ ■ Knowledge Ext  │   │
│  │ ■ VTT     │  │ ■ Chunk  │  │ ■ Question Gen   │   │
│  │ ■ TXT     │  │ ■ Embed  │  │ ■ Evaluation     │   │
│  │ ■ DOCX    │  │          │  │                  │   │
│  └──────────┘  └──────────┘  └──────────────────┘   │
│                                                       │
│  ┌──────────────────────────────────────────────┐    │
│  │ Knowledge Layer: FAISS Vector DB + JSON Store │    │
│  └──────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────┘
```

## 🚀 Quick Start

### Prerequisites

- Python 3.11+
- OpenAI API key

### Setup

```bash
# Navigate to backend
cd backend

# Create virtual environment
python -m venv venv
venv\Scripts\activate  # Windows
# source venv/bin/activate  # Linux/Mac

# Install dependencies
pip install -r requirements.txt

# Configure environment
copy .env.example .env
# Edit .env and add your OPENAI_API_KEY

# Run the server
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### Access

- **Web UI**: http://localhost:8000
- **API Docs**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc

## 📁 Project Structure

```
AssessmentApp/
├── backend/
│   ├── app/
│   │   ├── api/routes/        # REST API endpoints
│   │   ├── services/          # Core business logic
│   │   ├── models/            # Pydantic schemas
│   │   ├── prompts/           # LLM prompt templates
│   │   ├── utils/             # Helper utilities
│   │   ├── config.py          # App configuration
│   │   └── main.py            # FastAPI entry point
│   ├── data/                  # Runtime data (uploads, indices)
│   ├── tests/                 # Test data & scripts
│   └── requirements.txt
├── frontend/
│   ├── index.html
│   ├── css/styles.css
│   └── js/app.js
└── README.md
```

## 🔌 API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/upload` | Upload PDF/transcript files |
| GET | `/api/v1/upload/{id}/status` | Check processing status |
| GET | `/api/v1/upload/{id}/knowledge` | View extracted knowledge |
| POST | `/api/v1/assessment/{id}/generate` | Generate 25 questions |
| GET | `/api/v1/assessment/{id}/questions` | Get questions (quiz mode) |
| POST | `/api/v1/assessment/{id}/evaluate` | Submit answers |
| GET | `/api/v1/assessment/{id}/results` | Get evaluation results |

## 📊 Question Distribution

- **10 MCQs**: Concept-based + scenario-based with plausible distractors
- **8 Subjective**: Why/How questions derived from real KT discussions
- **7 Practical**: Real-world task simulations from documented workflows

## ⚙️ Tech Stack

| Component | Technology |
|-----------|------------|
| Backend | FastAPI (Python 3.11+) |
| LLM | OpenAI GPT-4o |
| Embeddings | text-embedding-3-small |
| Vector DB | FAISS |
| PDF Parsing | PyMuPDF |
| Frontend | Vanilla HTML/CSS/JS |
