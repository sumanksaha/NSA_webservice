import os
from app import create_app

# Create the Flask application instance
app = create_app()

if __name__ == '__main__':
    # Determine port from environment or default to 8000 to match original port
    port = int(os.environ.get("PORT", 8000))
    app.run(host='127.0.0.1', port=port, debug=True)
