# PRATIKSHYA FASHON — Feature-Based Backend API

Modular FastAPI application backing the PRATIKSHYA FASHON ladies-priority retail platform.

## Features & Modules

- **Authentication & Identity**: JWT-based login for Customer, Employee, and Admin surfaces.
- **RBAC & Permissions**: Dynamic roles & fine-grained permission checks.
- **Product Catalog & Variants**: Authoritative single source of truth for products, variants, and attributes.
- **Pricing & Tax**: Server-validated prices, discounts, GST calculation.
- **Media & Review**: Media asset upload, approval workflow, and CDN delivery.
- **Cart, Wishlist & Coupons**: Shopping cart, coupon validations, wishlist management.
- **Checkout & Payments**: Order creation and Razorpay gateway integration.
- **Orders & Returns**: Order lifecycle, immutable snapshots, and return workflows.
- **Inventory & Warehouse**: Stock tracking, reservations, multi-warehouse transfers.
- **Employee Operations**: Attendance tracking, sales targets, performance scores.
- **AI RAG Chatbot**: Shopping assistant using LangChain, Groq/OpenAI, and pgvector.

## Project Structure

```
app/
├── api/v1/          # Modular API routers for all 18 feature domains
├── core/            # Infrastructure (database, security, rbac, errors, pagination)
├── models/          # SQLAlchemy async domain models
├── repositories/    # Data access layer
├── schemas/         # Pydantic validation schemas
├── services/        # Domain business logic services
├── ai/              # LLM providers, prompts, AI tools
├── rag/             # Vector retrieval, ingestion, context generation
└── workers/         # Celery background task workers
```

## Quick Start — local development (no Docker required)

The normal development target is a plain Python virtual environment plus
your existing PostgreSQL server. Docker Compose remains available for the
future production/deployment phase but is **not** required to develop.

1. Copy `.env.example` to `.env` and set `DATABASE_URL` to your existing
   PostgreSQL server:
   ```bash
   cp .env.example .env
   # DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/pratikshya_fashon
   ```

2. Create a virtual environment and install dependencies:
   ```bash
   python -m venv .venv
   source .venv/bin/activate      # Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   ```

3. Start the API (no Redis, no Celery, no Docker needed):
   ```bash
   uvicorn app.main:app --reload
   ```

4. Access Interactive API Documentation:
   - Swagger UI: `http://localhost:8000/docs`
   - ReDoc: `http://localhost:8000/redoc`
   - Health check: `http://localhost:8000/health`

5. The existing server database schema is authoritative — do **not** run
   `alembic upgrade head` against a live schema unless explicitly required
   and verified. Redis-backed caching runs through an in-process LRU shim in
   development; Celery configuration is left untouched for a later phase.

6. CORS: `ALLOWED_ORIGINS` accepts a plain comma-separated list or a JSON
   array (both parse identically). Add your frontend dev origin
   (Vite defaults to `http://localhost:5173`) and restart the server.
