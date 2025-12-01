# SageAI Universal

A FastAPI application with JWT authentication and LangChain integration.

## Setup

1. Create a virtual environment:
```bash
python3 -m venv venv
source venv/bin/activate  # On Windows use: .\venv\Scripts\activate
```

2. Install dependencies:
```bash
python3 -m pip install -r requirements.txt
```

3. Configure environment variables:
```bash
cp .env.example .env
```
Edit the `.env` file with your configuration.

4. Run the application:
```bash
python3 -m uvicorn app.main:app --reload
```

The API will be available at http://localhost:8000

## Docker

To run with Docker:

```bash
docker build -t sageai-universal .
docker run -p 8000:8000 sageai-universal
```

## API Documentation

Once running, visit:
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

## Authentication

The API uses JWT tokens for authentication. To get a token:

1. Make a POST request to `/token` with username and password
2. Use the returned token in the Authorization header: `Bearer <token>`
3. Validate tokens using the `/validate-token` endpoint

## Project Structure

```
sageai-universal/
├── app/
│   ├── __init__.py
│   ├── main.py          # FastAPI application
│   ├── config.py        # Configuration management
│   └── security.py      # JWT and security utilities
├── .env                 # Environment variables
├── .env.example         # Example environment variables
├── requirements.txt     # Python dependencies
├── Dockerfile          # Docker configuration
└── README.md           # This file
```