"""Run once to create database tables. Usage: python init_db.py"""
from app import init_db

if __name__ == "__main__":
    init_db()
    print("Database tables created successfully.")
