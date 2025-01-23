# Use a lightweight Python base image
FROM python:3.10-slim

# Set the working directory
WORKDIR /app

# Install system dependencies (git, ffmpeg)
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    ffmpeg && \
    rm -rf /var/lib/apt/lists/*

# Upgrade pip to the latest version
RUN pip install --no-cache-dir --upgrade pip

# Install a compatible version of httpx
RUN pip install --no-cache-dir "httpx<0.28"

# Install the youtube-search-python library
RUN pip install --no-cache-dir git+https://github.com/alexmercerind/youtube-search-python

# Copy requirements.txt first to cache the dependencies layer
COPY requirements.txt /app/

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the remaining application files
COPY . /app

# Expose the port your application runs on
EXPOSE 5000

# Specify the default command to run the application
CMD ["python", "main.py"]
