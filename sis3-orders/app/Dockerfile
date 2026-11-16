FROM public.ecr.aws/docker/library/python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY server.py .
USER 10001:10001
ENV PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["python", "server.py"]
