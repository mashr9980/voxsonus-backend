# run.py
import os
import argparse
import uvicorn
import asyncio
import logging
from app.core.utils import create_output_directory

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("ai_subtitles.log")
    ]
)

logger = logging.getLogger(__name__)

def main():
    parser = argparse.ArgumentParser(description="Run AI Subtitles Platform API")
    parser.add_argument(
        "--host", 
        type=str, 
        default="0.0.0.0", 
        help="Host to bind the server to"
    )
    parser.add_argument(
        "--port", 
        type=int, 
        default=8000, 
        help="Port to bind the server to"
    )
    parser.add_argument(
        "--reload", 
        action="store_true", 
        help="Enable auto-reload for development"
    )
    
    args = parser.parse_args()
    
    # Create required directories
    os.makedirs("uploads", exist_ok=True)
    os.makedirs("outputs", exist_ok=True)
    
    # logger.info(f"Starting server on {args.host}:{args.port}")
    
    # Start the server
    uvicorn.run(
        "app.main:app", 
        host="127.0.0.1", 
        port=5100
    )

if __name__ == "__main__":
    main()