# run.py
import os
import uvicorn
import logging

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
    
    # Create required directories
    os.makedirs("uploads", exist_ok=True)
    os.makedirs("outputs", exist_ok=True)
    
    # Start the server
    uvicorn.run(
        "app.main:app", 
        host="0.0.0.0", 
        ssl_certfile="cert.pem",
        ssl_keyfile="key.pem",
        port=5100
    )

if __name__ == "__main__":
    main()