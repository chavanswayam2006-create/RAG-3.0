"""Vercel serverless entrypoint — re-exports the FastAPI ASGI app.

Vercel's @vercel/python builder detects the `app` variable and serves it
as a serverless function. All routing is handled by FastAPI itself.
"""
from app.main import app  # noqa: F401
