# Use a lightweight Python base image
FROM python:3.10-slim

# Set the working directory
WORKDIR /app

# Copy only the necessary files for dependency installation first (to take advantage of Docker's layer caching)
COPY requirements.txt /app/

# Update package list and install dependencies in a single layer to reduce image size
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

# Install dependencies from requirements.txt and the additional library
RUN pip install --no-cache-dir git+https://github.com/alexmercerind/youtube-search-python && \
    pip install --no-cache-dir -r requirements.txt

# Copy the remaining application files
COPY . /app

# Expose the port your application runs on
EXPOSE 5000

# Specify the default command to run the application
CMD ["python", "main.py"]
