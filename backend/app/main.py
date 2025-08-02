# backend/app/main.py
from fastapi import FastAPI

app = FastAPI(title="AI Quiz Generator")

@app.get("/")
def read_root():
    return {"message": "Welcome to the AI Quiz Generator API"}