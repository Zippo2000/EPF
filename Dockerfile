FROM python:3.9-slim

# Set working directory
WORKDIR /app

# Copy project files
COPY . /app/

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Exposed Flask port
EXPOSE 5000

# Environment variables
# IMMICH API KEY
ENV IMMICH_API_KEY="your-api-key"
ENV PATH=/home/app/.local/bin:$PATH

# Default command
CMD ["python", "app.py"]