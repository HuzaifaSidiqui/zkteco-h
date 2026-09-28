import sys
import os

# Ensure repository root is on sys.path for Vercel Serverless runtime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.main import app
