FROM python:3.12-slim
WORKDIR /app
# Only the low-privilege agent goes into the image. No elevated_agent.py,
# no raw data, no gateway secret.
COPY segmentation_agent.py /app/segmentation_agent.py
ENTRYPOINT ["python", "/app/segmentation_agent.py"]
