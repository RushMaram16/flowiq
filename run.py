"""
SmartTrip AI — API server entry point.
Run from the project root:  python run.py
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from api.app import app

if __name__ == "__main__":
    print("SmartTrip AI API starting on http://localhost:5000")
    app.run(host="0.0.0.0", port=5000, debug=True)
